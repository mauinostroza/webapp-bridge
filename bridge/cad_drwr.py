"""
bridge.cad_drwr — Rutas del puente local hacia ZWCAD / AutoCAD (CAD-DRWR).

Monta bajo /v1/sap/actions/cad/:
- status      detecta el CAD abierto (nunca falla por "no hay CAD": responde 200).
- pick        pide un punto por clic en el CAD (comando nativo, ver cad_com_live).
- draw/begin  abre una sesión de dibujo (exige confirmed=true).
- draw/batch  envía un lote de entidades (<= 1000) a la sesión activa.
- draw/end    regenera, hace zoom a la extensión y libera la sesión.
- ifc         genera un IFC4 desde el modelo semántico estructura-v1 (bridge.cad_ifc);
              no usa el CAD ni COM, solo ifcopenshell (503 si falta).

Todas las llamadas COM pasan por CadActor, un hilo daemon con apartamento
STA propio. No se usa el actor del puente SAP porque su timeout de 30 s no
sirve para el clic interactivo. El parámetro `call` de install_routes se
acepta por compatibilidad y NO se usa.

El token Bearer lo valida el middleware global de la aplicación; aquí no se
gestiona autenticación.

Errores: validación = 422 con mensaje en español (sólo para estas rutas);
COM / sesión / timeout = 409 con el detalle real de la excepción.
"""

import base64
import math
import queue
import threading
import time
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as _FuturoTimeout
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bridge import cad_com_live as cc
from bridge import cad_ifc
from bridge import cad_prims as ir

PREFIJO = "/v1/sap/actions/cad"

TTL_SESION_S = 600.0  # una sesión de dibujo vence tras 10 min sin uso
TIMEOUT_STATUS_S = 15.0
TIMEOUT_BEGIN_S = 60.0
TIMEOUT_BATCH_S = 120.0
TIMEOUT_END_S = 120.0
MAX_TOTAL = 20000  # n_total máximo de una sesión
MAX_LOTE = 1000  # entidades por lote
MAX_PUNTOS = 2000  # puntos por poly / filled
MAX_TEXTO = 500
MAX_NOMBRE = 128
MAX_MENSAJE = 200
MAX_ERRORES = 50  # errores listados por lote
COORD_MAX = 1e9

_CFG = ConfigDict(extra="forbid", allow_inf_nan=False)
_NOMBRE_PROGRAMA = dict(cc.PROGIDS)  # ProgID -> nombre legible

Coord = Annotated[float, Field(ge=-COORD_MAX, le=COORD_MAX)]
Punto = tuple[Coord, Coord]
Radio = Annotated[float, Field(gt=0, le=COORD_MAX)]
Altura = Annotated[float, Field(gt=0, le=1e6)]
Grosor = Annotated[float, Field(ge=0, le=1e6)]
Capa = Annotated[str, Field(min_length=1, max_length=MAX_NOMBRE)]


# ----------------------------------------------------------- modelos --


class StatusIn(BaseModel):
    model_config = _CFG


class PickIn(BaseModel):
    model_config = _CFG
    mensaje: str | None = Field(default=None, max_length=MAX_MENSAJE)
    timeout_s: int = Field(default=120, ge=5, le=300)


class BeginIn(BaseModel):
    model_config = _CFG
    confirmed: bool
    origen: Punto | None = None
    n_total: int = Field(ge=0, le=MAX_TOTAL)
    th: Altura

    @field_validator("confirmed")
    @classmethod
    def _confirmado(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Debe enviar confirmed=true para iniciar el dibujo")
        return v


class EndIn(BaseModel):
    model_config = _CFG
    sesion: Annotated[str, Field(min_length=1, max_length=MAX_NOMBRE)]


class IfcIn(BaseModel):
    model_config = _CFG
    modelo: dict[str, Any]


class _Ent(BaseModel):
    """Base de entidades: capa obligatoria y sólo capas conocidas."""

    model_config = _CFG
    l: Capa

    @field_validator("l")
    @classmethod
    def _capa_conocida(cls, v: str) -> str:
        if v not in ir.LAYER_COLORS:
            raise ValueError(f"Capa desconocida: {v}")
        return v


class EntLinea(_Ent):
    t: Literal["line"]
    a: Punto
    b: Punto
    w: Grosor = 0.0


class EntCirculo(_Ent):
    t: Literal["circle"]
    c: Punto
    r: Radio
    f: bool = False


class EntArco(_Ent):
    t: Literal["arc"]
    c: Punto
    r: Radio
    a1: Coord
    a2: Coord
    ccw: bool = True


class EntPoli(_Ent):
    t: Literal["poly"]
    p: list[Punto] = Field(min_length=2, max_length=MAX_PUNTOS)
    z: bool = False
    w: Grosor = 0.0


class EntRelleno(_Ent):
    t: Literal["filled"]
    p: list[Punto] = Field(min_length=3, max_length=MAX_PUNTOS)


class EntTexto(_Ent):
    t: Literal["text"]
    p: Punto
    s: str = Field(max_length=MAX_TEXTO)
    h: Altura
    rot: Coord = 0.0
    ha: Literal["l", "c", "r"] = "c"
    va: Literal["b", "m", "t"] = "m"


class EntCota(_Ent):
    t: Literal["dim"]
    a: Punto
    b: Punto
    base: Punto
    v: bool = False
    txt: Annotated[str | None, Field(max_length=MAX_TEXTO)] = None
    th: Grosor = 0.0


Entidad = Annotated[
    EntLinea | EntCirculo | EntArco | EntPoli | EntRelleno | EntTexto | EntCota,
    Field(discriminator="t"),
]


class BatchIn(BaseModel):
    model_config = _CFG
    sesion: Annotated[str, Field(min_length=1, max_length=MAX_NOMBRE)]
    ents: list[Entidad] = Field(min_length=1, max_length=MAX_LOTE)


def _a_ir(e):
    """Entidad validada -> primitiva IR (mismo contrato que serializar)."""
    if isinstance(e, EntLinea):
        return ir.Line(e.a, e.b, e.l, e.w)
    if isinstance(e, EntCirculo):
        return ir.Circle(e.c, e.r, e.l, e.f)
    if isinstance(e, EntArco):
        return ir.Arc(e.c, e.r, e.a1, e.a2, e.ccw, e.l)
    if isinstance(e, EntPoli):
        return ir.Poly(list(e.p), e.z, e.l, e.w)
    if isinstance(e, EntRelleno):
        return ir.Filled(list(e.p), e.l)
    if isinstance(e, EntTexto):
        return ir.Text(e.p, e.s, e.h, e.rot, e.l, e.ha, e.va)
    return ir.Dim(e.a, e.b, e.base, e.v, e.l, e.txt, e.th)


# ------------------------------------------------- errores 422 en español --

_MENSAJES = {
    "missing": "Campo obligatorio ausente",
    "extra_forbidden": "Campo no admitido",
    "finite_number": "Debe ser un número finito (NaN e infinitos no se admiten)",
    "greater_than": "Debe ser mayor que {gt}",
    "greater_than_equal": "Debe ser mayor o igual que {ge}",
    "less_than": "Debe ser menor que {lt}",
    "less_than_equal": "Debe ser menor o igual que {le}",
    "string_too_short": "Texto demasiado corto (mínimo {min_length} caracteres)",
    "string_too_long": "Texto demasiado largo (máximo {max_length} caracteres)",
    "too_short": "Lista demasiado corta (mínimo {min_length} elementos)",
    "too_long": "Lista demasiado larga (máximo {max_length} elementos)",
    "literal_error": "Valor no admitido",
    "union_tag_invalid": "Tipo de entidad no admitido",
    "union_tag_not_found": "Falta el campo de tipo de entidad (t)",
}


class _Faltan(dict):
    def __missing__(self, clave):
        return "?"


def _mensaje_es(err: dict) -> str:
    tipo = err.get("type", "")
    ctx = err.get("ctx") or {}
    if tipo == "value_error":
        return str(ctx.get("error", err.get("msg", "Valor no válido")))
    plantilla = _MENSAJES.get(tipo)
    if plantilla is None:
        plantilla = "Tipo de dato no válido" if tipo.endswith(("_type", "_parsing")) else "Valor no válido"
    return plantilla.format_map(_Faltan(ctx))


class _RutaCad(APIRoute):
    """Ruta que responde los errores de validación en español (422) sin
    registrar manejadores globales en la app anfitriona."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def manejador(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                detalle = [
                    {"loc": list(e.get("loc", ())), "msg": _mensaje_es(e), "type": e.get("type", "")}
                    for e in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detalle})

        return manejador


def _error_422(loc, msg: str, tipo: str = "value_error") -> HTTPException:
    return HTTPException(status_code=422, detail=[{"loc": list(loc), "msg": msg, "type": tipo}])


# ----------------------------------------------------- actor COM propio --


class CadActor:
    """Hilo daemon único que ejecuta todas las llamadas COM al CAD."""

    def __init__(self, nombre: str = "cad-drwr-com"):
        self._cola = queue.Queue()
        self._hilo = threading.Thread(target=self._bucle, name=nombre, daemon=True)
        self._hilo.start()

    def _bucle(self):
        try:
            import pythoncom  # perezoso: sólo existe en Windows

            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        except Exception:
            # Fuera de Windows (o sin pywin32) el hilo sigue vivo; las
            # funciones de cad_com_live informan del motivo real.
            pass
        while True:
            fn, futuro = self._cola.get()
            try:
                futuro.set_result(fn())
            except BaseException as exc:  # se reenvía al llamador
                futuro.set_exception(exc)

    def call(self, fn, timeout: float):
        """Ejecuta `fn` en el hilo COM y espera hasta `timeout` segundos."""
        futuro = Future()
        self._cola.put((fn, futuro))
        try:
            return futuro.result(timeout=timeout)
        except _FuturoTimeout:
            if futuro.done():
                raise
            raise TimeoutError(
                f"El CAD no respondió en {timeout:g} s. Si hay un comando de "
                "selección abierto en el CAD, pulse Esc y reintente."
            ) from None


def _leer(fn):
    try:
        valor = fn()
    except Exception:
        return None
    return None if valor is None else str(valor)


def _nombre_doc(doc):
    return _leer(lambda: doc.Name)


def _a_409(exc: BaseException) -> HTTPException:
    if isinstance(exc, (RuntimeError, TimeoutError)):
        texto = str(exc)
    else:
        texto = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return HTTPException(status_code=409, detail=texto or type(exc).__name__)


def _emitir(doc, ents, tipos):
    """Emite entidades IR en el espacio modelo. Devuelve (creadas, fallos)."""
    pythoncom, _, VARIANT = cc._com()
    msp = doc.ModelSpace

    def VPT(p, z=0.0):
        return cc._pt(VARIANT, pythoncom, p, z)

    def VF(pts):
        return cc._flat(VARIANT, pythoncom, pts)

    creadas = 0
    fallos = []
    for i, (e, tipo) in enumerate(zip(ents, tipos)):
        try:
            if isinstance(e, ir.Dim):
                cc._dim(msp, e, VPT)
            else:
                cc._emit(msp, e, VPT, VF, pythoncom, VARIANT)
            creadas += 1
        except Exception as exc:
            motivo = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            fallos.append({"indice": i, "tipo": tipo, "motivo": motivo})
    return creadas, fallos


# ------------------------------------------------------------ servicio --


class ServicioCad:
    """Estado del puente: actor COM y la sesión de dibujo activa (una sola)."""

    def __init__(self):
        self.actor = CadActor()
        self._lock = threading.Lock()
        self._sesion = None

    def _ejecutar(self, fn, timeout: float):
        try:
            return self.actor.call(fn, timeout)
        except Exception as exc:
            raise _a_409(exc) from exc

    def _vigente(self, sesion_id: str) -> dict:
        """Devuelve la sesión si existe, coincide y no ha vencido (llamar con el lock)."""
        s = self._sesion
        if s is None or s["id"] != sesion_id:
            raise HTTPException(409, "La sesión de dibujo venció; vuelva a enviar")
        if time.monotonic() - s["ultimo"] > TTL_SESION_S:
            self._sesion = None
            raise HTTPException(409, "La sesión de dibujo venció; vuelva a enviar")
        s["ultimo"] = time.monotonic()
        return s

    def status(self) -> dict:
        def fn():
            try:
                app, pid = cc.detectar()
            except RuntimeError as exc:
                return {
                    "conectado": False,
                    "programa": None,
                    "version": None,
                    "documento": None,
                    "detalle": str(exc),
                }
            programa = _NOMBRE_PROGRAMA.get(pid, pid)
            version = _leer(lambda: app.Version)
            documento = _leer(lambda: app.ActiveDocument.Name)
            detalle = (
                f"{programa} | versión {version or '(desconocida)'} | documento: {documento or '(ninguno)'}"
            )
            return {
                "conectado": True,
                "programa": programa,
                "version": version,
                "documento": documento,
                "detalle": detalle,
            }

        res = self._ejecutar(fn, TIMEOUT_STATUS_S)
        res["ifc"] = cad_ifc.disponible()  # la exportación IFC no depende del CAD, solo de ifcopenshell
        return res

    def pick(self, mensaje: str | None, timeout_s: int) -> dict:
        def fn():
            x, y = cc.pedir_punto(mensaje) if mensaje else cc.pedir_punto()
            if not (math.isfinite(x) and math.isfinite(y)):
                raise RuntimeError("El CAD devolvió coordenadas no válidas.")
            return {"x": float(x), "y": float(y)}

        return self._ejecutar(fn, float(timeout_s))

    def begin(self, origen, n_total: int, th: float) -> dict:
        def fn():
            app, pid = cc.detectar()
            doc = cc._documento(app)
            cc._capas(doc)
            cc._vars_cota(doc, ir.Drawing(ents=[ir.Text((0.0, 0.0), "", th)]))
            return {
                "app": app,
                "doc": doc,
                "programa": _NOMBRE_PROGRAMA.get(pid, pid),
                "documento": _nombre_doc(doc),
            }

        with self._lock:
            res = self._ejecutar(fn, TIMEOUT_BEGIN_S)
            sid = uuid.uuid4().hex
            self._sesion = {
                "id": sid,
                "app": res["app"],
                "doc": res["doc"],
                "programa": res["programa"],
                "origen": tuple(origen) if origen is not None else (0.0, 0.0),
                "n_total": n_total,
                "recibidas": 0,
                "creadas": 0,
                "omitidas": 0,
                "ultimo": time.monotonic(),
            }
        return {"sesion": sid, "programa": res["programa"], "documento": res["documento"]}

    def batch(self, sesion_id: str, pares) -> dict:
        """`pares` = [(tipo_wire, entidad_ir), ...] ya validadas por Pydantic."""
        with self._lock:
            s = self._vigente(sesion_id)
            restan = s["n_total"] - s["recibidas"]
            if len(pares) > restan:
                raise _error_422(
                    ("body", "ents"),
                    f"El lote supera el total declarado (n_total={s['n_total']}); quedan {restan} entidades",
                )
            tipos = [t for t, _ in pares]
            dwg = ir.translate(ir.Drawing(ents=[e for _, e in pares]), s["origen"][0], s["origen"][1])
            doc = s["doc"]
            try:
                creadas, fallos = self.actor.call(lambda: _emitir(doc, dwg.ents, tipos), TIMEOUT_BATCH_S)
            except TimeoutError as exc:
                # El lote puede haberse aplicado en parte: la sesión ya no es fiable.
                self._sesion = None
                raise HTTPException(
                    409,
                    "El CAD no terminó el lote a tiempo; el dibujo puede haber "
                    "quedado parcial. Revise el CAD y vuelva a enviar el dibujo.",
                ) from exc
            except Exception as exc:
                raise _a_409(exc) from exc
            s["recibidas"] += len(pares)
            s["creadas"] += creadas
            s["omitidas"] += len(fallos)
            return {"creadas": creadas, "omitidas": len(fallos), "errores": fallos[:MAX_ERRORES]}

    def end(self, sesion_id: str) -> dict:
        with self._lock:
            s = self._vigente(sesion_id)
            app, doc = s["app"], s["doc"]

            def fn():
                try:
                    doc.Regen(1)
                except Exception:
                    try:
                        doc.Regen()
                    except Exception:
                        pass
                try:
                    app.ZoomExtents()
                except Exception:
                    pass
                return _nombre_doc(doc)

            try:
                documento = self.actor.call(fn, TIMEOUT_END_S)
            except Exception as exc:
                raise _a_409(exc) from exc
            finally:
                self._sesion = None  # se libera siempre, también si falla
        creadas, omitidas = s["creadas"], s["omitidas"]
        nombre = documento or "(sin nombre)"
        return {
            "documento": documento,
            "creadas": creadas,
            "omitidas": omitidas,
            "resumen": (
                f"{s['programa']} — documento: {nombre} — {creadas} entidades creadas, {omitidas} omitidas"
            ),
        }


# ------------------------------------------------------------- rutas --


def install_routes(app, call=None):
    """Monta las rutas del puente CAD en `app`.

    Las rutas CAD usan siempre el CadActor propio de este módulo; `call`
    NO se usa para ellas. Si se pasa `call` (actor STA del puente
    anfitrión), además se montan las rutas de lectura de SAP2000
    (bridge.cad_drwr_sap). Sin `call` solo se montan las rutas CAD.
    """
    servicio = ServicioCad()

    router = APIRouter(route_class=_RutaCad)

    @router.post(PREFIJO + "/status")
    def cad_status(body: StatusIn | None = None):
        return servicio.status()

    @router.post(PREFIJO + "/pick")
    def cad_pick(body: PickIn):
        return servicio.pick(body.mensaje, body.timeout_s)

    @router.post(PREFIJO + "/draw/begin")
    def cad_draw_begin(body: BeginIn):
        return servicio.begin(body.origen, body.n_total, body.th)

    @router.post(PREFIJO + "/draw/batch")
    def cad_draw_batch(body: BatchIn):
        pares = [(e.t, _a_ir(e)) for e in body.ents]
        return servicio.batch(body.sesion, pares)

    @router.post(PREFIJO + "/draw/end")
    def cad_draw_end(body: EndIn):
        return servicio.end(body.sesion)

    @router.post(PREFIJO + "/ifc")
    def cad_ifc_generar(body: IfcIn):
        try:
            nombre, datos = cad_ifc.generar_ifc(body.modelo)
        except cad_ifc.FaltaIfcopenshell as exc:
            raise HTTPException(503, str(exc)) from exc
        except cad_ifc.ErrorModelo as exc:
            raise _error_422(("body", "modelo"), str(exc)) from exc
        return {
            "nombre": nombre,
            "tamano": len(datos),
            "contenido_base64": base64.b64encode(datos).decode("ascii"),
        }

    app.include_router(router)
    if call is not None:
        # Importación perezosa: bridge.cad_drwr_sap importa _RutaCad de este
        # módulo, así que no puede importarse al cargar cad_drwr.
        from bridge import cad_drwr_sap

        cad_drwr_sap.install_routes(app, call)
    return servicio
