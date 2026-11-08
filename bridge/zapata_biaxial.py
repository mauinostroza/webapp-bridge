"""Zapatas biaxiales: lectura y escritura en SAP2000 para el puente compartido.

Porte de la capa COM de ``fundaciones_app/sap2000`` (Fundaciones@837a7ca) al patrón de
``bridge/placa_base.py``: sin conexión propia; cada función recibe el ``SapModel`` ya conectado a
través del actor STA y las rutas se instalan desde ``bridge.actions.install_routes`` con el mismo
envoltorio ``call``. La lógica sin COM (equivalentes, modos de importación, signos, combinaciones y
plan de exportación) vive en el motor del Workbench, no en el puente.

Rutas (``/v1/sap/actions/zapata-biaxial/``):
- ``GET estado``, ``GET casos``: sólo lectura.
- ``POST nodos``: nodos seleccionados (o, sin selección, los restringidos) con sus coordenadas.
  Sólo lectura; no usa ``SupportedPoints`` (el escritorio alteraba la selección del usuario).
- ``POST nodos`` con ``{"nodos": [...]}``: X, Y, Z de esos nodos (sin selección) y dispersión de cotas.
- ``POST reacciones``: marca los casos/combinaciones indicados para salida, lee ``Results.JointReact``
  de los nodos (tonf y m) y restaura la selección de salida del usuario. Última columna: StepNum;
  CaseType sale de ``LoadCases.GetTypeOAPI_1`` (JointReact no lo devuelve).
- ``POST reacciones`` con ``"sonda": true``: respuesta cruda de JointReact (envolventes, espectros, ejes).
- ``POST exportar/preview``: comprueba, sin escribir, bloqueo del modelo y colisiones de nombres.
- ``POST exportar/apply`` (``confirmed: true``): crea material, secciones, nodos, shells, pedestal,
  patrones, cargas y resortes del plan, y relee lo creado (los resortes no se releen: ver más abajo).

Reutiliza la capa de escritura verificada en SAP2000 real de ``bridge/str2fund.py``
(``_add_point``, ``_add_area``, ``_add_frame``, ``_ensure_*_section``, ``_set_load_force``,
``_verify_applied``), sin modificarla. Diferencias respecto al exportador del escritorio, que
tenía firmas COM incorrectas (ver docs/MODULES_SOURCE.md): nombre en ``UserName`` y no en el
parámetro de salida, secciones en metros con shell ``thick``, ``SetLoadForce`` con la firma
``(nodo, patrón, fuerzas[6], reemplazar, sistema)``, ``LoadPatterns.Add(nombre, 8, selfwt, True)``,
pedestal apoyado en un nodo de la malla, error (no advertencia) ante cualquier fallo y relectura final.

Sin validar contra SAP2000 real: ``PointObj.SetSpring`` (resortes del suelo), que además
``_verify_applied`` no relee: se comprueban a mano (docs/SAP2000_ACCEPTANCE.md). Se prueba sólo con
COM falso (``tests/test_bridge_zapata_biaxial.py``).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bridge import placa_base as pb
from bridge import str2fund as sf
from bridge.actions import _get_name_list, _ret, _valid_name
from bridge.com_orden import normalizar

MAX_NODOS_LECTURA = 500
MAX_FILAS_REACCIONES = 50_000
MAX_NODOS_ESCANEO = 20_000
MAX_OBJETOS_PLAN = 25_000
# StepNum va al final para no mover las columnas que ya consume la web.
COLUMNAS_REACCIONES = [
    "Joint",
    "OutputCase",
    "CaseType",
    "StepType",
    "F1",
    "F2",
    "F3",
    "M1",
    "M2",
    "M3",
    "StepNum",
]
# eLoadCaseType de la API (sin validar contra SAP2000 real; un código fuera de la tabla sale como «Tipo<n>»).
_TIPOS_CASO = {
    1: "LinStatic",
    2: "NonStatic",
    3: "Modal",
    4: "LinRespSpec",
    5: "LinModHist",
    6: "NonModHist",
    7: "LinDirHist",
    8: "NonDirHist",
    9: "Moving",
    10: "Buckling",
    11: "SteadyState",
    12: "PSD",
    13: "LinStatMultiStep",
    14: "HyperStatic",
}
_TOL_ANGULO_DEG = 1e-6


class ZapataBiaxialError(RuntimeError):
    """Fallo de lectura o escritura; llega al cliente como 409 con ``detail``."""

    def __init__(self, mensaje: str, code: str = "sap_error", **extra: Any):
        super().__init__(mensaje)
        self.detail = {"code": code, "message": mensaje, **extra}


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


def estado(model: Any) -> dict[str, Any]:
    model = normalizar(model, destino_primero=False)
    return {"modelo": pb._nombre_modelo(model), "bloqueado": sf._model_locked(model)}


def casos(model: Any) -> dict[str, Any]:
    """Casos y combinaciones del modelo (los nombres decidirán ``CaseType`` en las reacciones)."""
    return pb.listar_cargas(model)


def _tipo_de_caso(model: Any, nombre: str) -> str | None:
    """Tipo real del caso (``LoadCases.GetTypeOAPI_1``); ``None`` si SAP no lo informa.

    ``Results.JointReact`` no devuelve el tipo del caso (su cuarta columna es ``StepType``), así que se
    consulta aparte.
    """
    try:
        salidas = pb._salida(model.LoadCases.GetTypeOAPI_1(nombre), f"LoadCases.GetTypeOAPI_1({nombre!r})")
    except Exception:
        return None
    codigo = next((x for x in salidas if pb._es_entero(x)), None)
    if codigo is None:
        return None
    return _TIPOS_CASO.get(int(codigo), f"Tipo{int(codigo)}")


def _ejes_locales_girados(model: Any, nodo: str) -> bool | None:
    """``True`` si el nodo tiene ejes locales girados (o avanzados); ``None`` si no se pudieron leer."""
    try:
        salidas = pb._salida(model.PointObj.GetLocalAxes(nodo), f"PointObj.GetLocalAxes({nodo!r})")
    except Exception:
        return None
    angulos = [float(x) for x in salidas if pb._es_numero(x)]
    avanzado = any(x is True for x in salidas)
    if len(angulos) < 3:
        return None
    return avanzado or any(abs(a) > _TOL_ANGULO_DEG for a in angulos[:3])


@contextmanager
def _seleccion_de_salida_acotada(
    model: Any, casos_: list[str], combinaciones: list[str], avisos: list[str]
) -> Iterator[None]:
    """Marca los casos pedidos para salida y, al terminar, devuelve la selección que tenía el usuario."""
    todos_casos, todas_combos = pb._nombres_de_carga(model)
    previos_c, previos_k, desconocida = pb._seleccion_de_salida(model, todos_casos, todas_combos)
    if desconocida:
        avisos.append(
            "SAP2000 no informó la selección de salida previa de algunos casos: no se puede restaurar "
            "y queda la selección pedida. Revísela en SAP2000."
        )
    try:
        pb._fijar_seleccion_de_salida(model, casos_, combinaciones)
        yield
    finally:
        if not desconocida:
            try:
                pb._fijar_seleccion_de_salida(model, previos_c, previos_k)
            except Exception as exc:
                avisos.append(f"No se pudo restaurar la selección de salida de SAP2000: {exc}")


def nodos(model: Any) -> dict[str, Any]:
    """Nodos seleccionados con sus coordenadas (m); sin selección, los restringidos."""
    model = normalizar(model, destino_primero=False)
    elegidos = pb._nodos_seleccionados(model)
    origen = "seleccion"
    if not elegidos:
        origen = "restringidos"
        todos = pb._nombres(model.PointObj.GetNameList(), "PointObj.GetNameList")
        if len(todos) > MAX_NODOS_ESCANEO:
            raise ZapataBiaxialError(
                f"El modelo tiene {len(todos)} nodos y ninguno seleccionado; seleccione los nodos de la fundación.",
                code="demasiados_nodos",
            )
        elegidos = [n for n in todos if pb._estado_restriccion(model, n) is not False]
    if not elegidos:
        raise ZapataBiaxialError(
            "No hay nodos seleccionados ni restringidos en el modelo de SAP2000.", code="sin_nodos"
        )
    if len(elegidos) > MAX_NODOS_LECTURA:
        raise ZapataBiaxialError(
            f"Hay {len(elegidos)} nodos (máximo {MAX_NODOS_LECTURA}); seleccione sólo los de la fundación.",
            code="demasiados_nodos",
        )
    with pb._unidades_tonf_m(model):
        filas = [[n, *pb._coordenadas(model, n)] for n in elegidos]
    return {
        "modelo": pb._nombre_modelo(model),
        "origen": origen,
        "unidades": "tonf-m",
        "columns": ["Joint", "X", "Y", "Z"],
        "data": filas,
    }


def coordenadas(model: Any, nodos_: Sequence[str]) -> dict[str, Any]:
    """Coordenadas (m) de nodos concretos, sin depender de la selección de SAP2000.

    Incluye la dispersión de cotas (Z) del grupo para comprobar que la fundación es plana.
    """
    model = normalizar(model, destino_primero=False)
    nombres = list(dict.fromkeys(nodos_))
    existentes = set(pb._nombres(model.PointObj.GetNameList(), "PointObj.GetNameList"))
    faltan = [n for n in nombres if n not in existentes]
    if faltan:
        raise ZapataBiaxialError(
            f"Nodos que no existen en el modelo: {', '.join(faltan[:5])}.", code="nodo_desconocido"
        )
    with pb._unidades_tonf_m(model):
        filas = [[n, *pb._coordenadas(model, n)] for n in nombres]
    cotas = [f[3] for f in filas]
    return {
        "modelo": pb._nombre_modelo(model),
        "unidades": "tonf-m",
        "columns": ["Joint", "X", "Y", "Z"],
        "data": filas,
        "z_min": min(cotas),
        "z_max": max(cotas),
        "dispersion_z": max(cotas) - min(cotas),
    }


def reacciones(
    model: Any, nodos_: Sequence[str], casos_: Sequence[str], combinaciones: Sequence[str]
) -> dict[str, Any]:
    """Reacciones de los nodos para los casos/combinaciones indicados (tonf, tonf·m)."""
    model = normalizar(model, destino_primero=False)
    disponibles_casos, disponibles_combos = pb._nombres_de_carga(model)
    desconocidos = [
        n for n in (*casos_, *combinaciones) if n not in {*disponibles_casos, *disponibles_combos}
    ]
    if desconocidos:
        raise ZapataBiaxialError(
            f"Casos o combinaciones que no existen en el modelo: {', '.join(desconocidos[:5])}.",
            code="caso_desconocido",
        )
    combos = set(disponibles_combos)
    filas: list[list[Any]] = []
    avisos: list[str] = []
    tipos: dict[str, str] = {}
    sin_tipo: set[str] = set()
    girados: list[str] = []
    with (
        _seleccion_de_salida_acotada(model, list(casos_), list(combinaciones), avisos),
        pb._unidades_tonf_m(model),
    ):
        for nodo in dict.fromkeys(nodos_):
            if _ejes_locales_girados(model, nodo):
                girados.append(nodo)
            for r in pb._reacciones_de_nodo(model, nodo):
                nombre = r["load_name"]
                if nombre not in tipos:
                    if nombre in combos:
                        tipos[nombre] = "Combination"
                    else:
                        tipo = _tipo_de_caso(model, nombre)
                        if tipo is None:
                            sin_tipo.add(nombre)
                        tipos[nombre] = tipo or "LoadCase"
                filas.append(
                    [
                        r["node"],
                        nombre,
                        tipos[nombre],
                        r["step_type"],
                        r["Vx"],
                        r["Vy"],
                        r["P"],
                        r["Mx"],
                        r["My"],
                        r["Mz"],
                        r["step_num"],
                    ]
                )
                if len(filas) > MAX_FILAS_REACCIONES:
                    raise ZapataBiaxialError(
                        f"Más de {MAX_FILAS_REACCIONES} filas de reacciones; reduzca nodos o casos.",
                        code="demasiadas_filas",
                    )
    if sin_tipo:
        avisos.append(
            "SAP2000 no informó el tipo de algunos casos (se rotulan «LoadCase»): "
            + ", ".join(sorted(sin_tipo)[:5])
            + "."
        )
    if girados:
        avisos.append(
            "Nodos con ejes locales girados: " + ", ".join(girados[:10]) + ". Las reacciones se entregan "
            "tal como las devuelve Results.JointReact; confirme con la sonda si vienen en ejes globales."
        )
    if not filas:
        raise ZapataBiaxialError(
            "SAP2000 no devolvió reacciones. Ejecute el análisis y revise la selección de resultados.",
            code="sin_resultados",
        )
    return {
        "modelo": pb._nombre_modelo(model),
        "unidades": "tonf-m",
        "columns": COLUMNAS_REACCIONES,
        "data": filas,
        "avisos": avisos,
        "nodos_ejes_locales": girados,
    }


def _plano(valor: Any) -> Any:
    """Valor COM a JSON (sondas): secuencias recursivas, números y textos tal cual, el resto como texto."""
    if isinstance(valor, (list, tuple)):
        return [_plano(x) for x in valor]
    if isinstance(valor, (bool, int, str)) or valor is None:
        return valor
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else str(valor)
    return repr(valor)


def sonda_joint_react(
    model: Any, nodo: str, caso: str | None, combinacion: str | None, max_filas: int = 40
) -> dict[str, Any]:
    """Respuesta cruda de ``Results.JointReact`` de un nodo para un caso o combinación.

    Pensada para diagnosticar sin acceso al equipo: forma de la respuesta, filas (p. ej. Max/Min de una
    envolvente, pasos de un espectro), tipo del caso y ejes locales del nodo. Restaura la selección de salida.
    """
    model = normalizar(model, destino_primero=False)
    disp_casos, disp_combos = pb._nombres_de_carga(model)
    elegido = caso if caso is not None else combinacion
    if elegido not in (disp_casos if caso is not None else disp_combos):
        raise ZapataBiaxialError(f"«{elegido}» no existe en el modelo.", code="caso_desconocido")
    avisos: list[str] = []
    with (
        _seleccion_de_salida_acotada(
            model, [caso] if caso is not None else [], [combinacion] if combinacion is not None else [], avisos
        ),
        pb._unidades_tonf_m(model),
    ):
        crudo = model.Results.JointReact(nodo, 0)
        try:
            ejes = _plano(model.PointObj.GetLocalAxes(nodo))
        except Exception as exc:
            ejes = f"error: {exc}"
        try:
            tipo_combo = _plano(model.RespCombo.GetTypeOAPI(combinacion)) if combinacion else None
        except Exception as exc:
            tipo_combo = f"error: {exc}"
    salidas = list(crudo) if isinstance(crudo, (list, tuple)) else [crudo]
    return {
        "modelo": pb._nombre_modelo(model),
        "unidades": "tonf-m",
        "nodo": nodo,
        "caso": caso,
        "combinacion": combinacion,
        "n_salidas": len(salidas),
        "tipos_salidas": [type(x).__name__ for x in salidas],
        "salidas": [_plano(x)[:max_filas] if isinstance(x, (list, tuple)) else _plano(x) for x in salidas],
        "tipo_caso": _tipo_de_caso(model, caso) if caso is not None else None,
        "tipo_combinacion_raw": tipo_combo,
        "ejes_locales_raw": ejes,
        "avisos": avisos,
    }


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------


def _cf(nombres: Sequence[str]) -> set[str]:
    """SAP2000 no distingue mayúsculas en los nombres."""
    return {str(n).casefold() for n in nombres}


def _nombres_plan(plan: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "nodos": [p["name"] for p in plan["points"]],
        "shells": [a["name"] for a in plan["areas"]],
        "frames": [f["name"] for f in plan["frames"]],
        "secciones_shell": [s["name"] for s in plan["area_sections"]],
        "patrones": [p["name"] for p in plan["patterns"]],
    }


def _huella(plan: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


def preview(model: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Revisa el plan contra el modelo abierto sin escribir nada.

    Como ``bridge/str2fund.py``, la ruta de escritura usa el modelo tal cual llega (sin
    ``normalizar``): los helpers verificados en SAP2000 real que se reutilizan leen los códigos
    de retorno con sus propias comprobaciones y no esperan el orden normalizado.
    """
    bloqueado = sf._model_locked(model)
    existentes = {
        "nodos": _cf(_get_name_list(model.PointObj, "PointObj.GetNameList")),
        "shells": _cf(_get_name_list(model.AreaObj, "AreaObj.GetNameList")),
        "frames": _cf(_get_name_list(model.FrameObj, "FrameObj.GetNameList")),
        "patrones": _cf(_get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")),
    }
    conflictos: list[str] = []
    for clave, etiqueta in (
        ("nodos", "nodo"),
        ("shells", "shell"),
        ("frames", "frame"),
        ("patrones", "patrón de carga"),
    ):
        for nombre in _nombres_plan(plan)[clave]:
            if nombre.casefold() in existentes[clave]:
                conflictos.append(f"Ya existe {etiqueta} «{nombre}» en el modelo.")
    if not plan.get("crear_load_case", True):
        # Se carga un patrón existente: debe estar en el modelo.
        usados = {ld["pattern"] for ld in plan["loads"]} - {p["name"] for p in plan["patterns"]}
        for nombre in sorted(usados):
            if nombre.casefold() not in existentes["patrones"]:
                conflictos.append(f"El patrón de carga «{nombre}» no existe en el modelo.")
    material = plan["material"]["name"]
    material_existe = material.casefold() in _cf(
        _get_name_list(model.PropMaterial, "PropMaterial.GetNameList")
    )
    avisos = []
    if bloqueado:
        avisos.append(sf._LOCKED_MESSAGE)
    if plan.get("springs"):
        avisos.append("Los resortes del suelo (PointObj.SetSpring) no se han validado en un SAP2000 real.")
    return {
        "ok": not conflictos and not bloqueado,
        "modelo": pb._nombre_modelo(model),
        "bloqueado": bloqueado,
        "conflictos": conflictos,
        "avisos": avisos,
        "material_existe": material_existe,
        "resumen": plan["resumen"],
        "huella": _huella(plan),
    }


def _crear_resorte(model: Any, joint: str, kz: float) -> None:
    # SetSpring(Name, K[6], ItemType, IsLocalCSys, Replace): sólo rigidez vertical (U3).
    vector = [0.0, 0.0, float(kz), 0.0, 0.0, 0.0]
    sf._call_set(
        model.PointObj.SetSpring,
        f"PointObj.SetSpring({joint!r})",
        (joint, vector, 0, True, True),
        (joint, vector),
    )


def _escribir(model: Any, plan: dict[str, Any], creado: dict[str, int]) -> None:
    material = plan["material"]
    if material["name"].casefold() not in _cf(_get_name_list(model.PropMaterial, "PropMaterial.GetNameList")):
        resultado = sf._create_materials_ton_m_c(
            model,
            [{k: material[k] for k in ("name", "type", "E", "U", "A", "weight_per_volume")}],
        )
        if resultado["failed"]:
            raise ZapataBiaxialError(f"No se pudo crear el material: {resultado['failed'][0]['reason']}")
        creado["material"] = 1
    for sec in plan["frame_sections"]:
        creado["secciones_frame"] += int(sf._ensure_frame_section(model, sec))
    for sec in plan["area_sections"]:
        creado["secciones_shell"] += int(sf._ensure_area_section(model, sec))
    for punto in plan["points"]:
        sf._add_point(model, punto)
        creado["nodos"] += 1
    for area in plan["areas"]:
        sf._add_area(model, area)
        creado["shells"] += 1
    for frame in plan["frames"]:
        sf._add_frame(model, frame)
        creado["frames"] += 1
    for patron in plan["patterns"]:
        # eLoadPatternType: Dead = 1, Other = 8 (verificado); el multiplicador de peso propio va tercero.
        _ret(
            model.LoadPatterns.Add(patron["name"], int(patron["type"]), float(patron["selfwt"]), True),
            f"LoadPatterns.Add({patron['name']!r})",
        )
        creado["patrones"] += 1
    for carga in plan["loads"]:
        sf._set_load_force(model, carga)
        creado["cargas"] += 1
    for resorte in plan["springs"]:
        _crear_resorte(model, resorte["joint"], resorte["kz"])
        creado["resortes"] += 1


def apply(model: Any, plan: dict[str, Any], huella: str | None = None) -> dict[str, Any]:
    """Crea la zapata en el modelo abierto y relee lo creado (sin ``normalizar``, ver ``preview``)."""
    if huella and huella != _huella(plan):
        raise ZapataBiaxialError("El plan cambió desde la revisión; repita «Revisar».", code="plan_cambiado")
    previa = preview(model, plan)
    if not previa["ok"]:
        motivo = sf._LOCKED_MESSAGE if previa["bloqueado"] else "; ".join(previa["conflictos"][:5])
        raise ZapataBiaxialError(
            motivo, code="bloqueado" if previa["bloqueado"] else "conflictos", conflictos=previa["conflictos"]
        )
    creado = dict.fromkeys(
        (
            "material",
            "secciones_frame",
            "secciones_shell",
            "nodos",
            "shells",
            "frames",
            "patrones",
            "cargas",
            "resortes",
        ),
        0,
    )
    try:
        sf._with_ton_m_c(model, lambda: _escribir(model, plan, creado))
    except Exception as exc:
        raise ZapataBiaxialError(
            f"SAP2000 aceptó cambios parciales y luego falló: {exc}. Creado: {creado}.",
            code="parcial",
            partial=True,
            creado=creado,
        ) from exc
    try:
        fallos = sf._verify_applied(model, plan)
    except Exception as exc:
        fallos = [f"la relectura falló: {exc}"]
    try:
        model.View.RefreshView(0, False)
    except Exception:
        pass
    if fallos:
        raise ZapataBiaxialError(
            "El modelo se escribió pero la relectura no coincide con el plan.",
            code="verificacion",
            partial=True,
            creado=creado,
            fallos=fallos[:20],
        )
    return {
        "ok": True,
        "verificado": True,
        "creado": creado,
        "resumen": plan["resumen"],
        "modelo": pb._nombre_modelo(model),
    }


# ---------------------------------------------------------------------------
# Cuerpos de las rutas
# ---------------------------------------------------------------------------


def _nombre(valor: str, etiqueta: str) -> str:
    try:
        return _valid_name(valor, etiqueta)
    except ValueError as exc:
        raise ValueError(f"{etiqueta} no es un nombre válido de SAP2000.") from exc


class _Cuerpo(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class NodosBody(_Cuerpo):
    nodos: list[str] = Field(min_length=1, max_length=MAX_NODOS_LECTURA)

    @field_validator("nodos")
    @classmethod
    def _nodos(cls, v: list[str]) -> list[str]:
        return [_nombre(n, "nodo") for n in v]


class ReaccionesBody(NodosBody):
    casos: list[str] = Field(default_factory=list, max_length=500)
    combinaciones: list[str] = Field(default_factory=list, max_length=500)
    # Diagnóstico: respuesta cruda de JointReact del primer nodo y del primer caso (o combinación).
    sonda: bool = False

    @field_validator("casos", "combinaciones")
    @classmethod
    def _nombres(cls, v: list[str]) -> list[str]:
        return [_nombre(n, "caso") for n in v]

    @model_validator(mode="after")
    def _alguno(self) -> ReaccionesBody:
        if not self.casos and not self.combinaciones:
            raise ValueError("Indique al menos un caso o una combinación.")
        return self


class PlanBody(_Cuerpo):
    plan: dict[str, Any]
    huella: str | None = Field(None, max_length=32)
    confirmed: bool = False

    @field_validator("plan")
    @classmethod
    def _plan(cls, p: dict[str, Any]) -> dict[str, Any]:
        for clave in (
            "points",
            "areas",
            "frames",
            "patterns",
            "loads",
            "springs",
            "frame_sections",
            "area_sections",
        ):
            if not isinstance(p.get(clave), list):
                raise ValueError(f"El plan no tiene la lista «{clave}».")
        if not isinstance(p.get("material"), dict) or not isinstance(p.get("resumen"), dict):
            raise ValueError("El plan no tiene material ni resumen.")
        total = sum(len(p[c]) for c in ("points", "areas", "frames", "patterns", "loads", "springs"))
        if total > MAX_OBJETOS_PLAN:
            raise ValueError(f"El plan tiene {total} objetos (máximo {MAX_OBJETOS_PLAN}).")
        if p.get("unidades") != "Ton_m_C":
            raise ValueError("El plan debe estar en Ton_m_C.")
        try:
            for punto in p["points"]:
                _nombre(punto["name"], "nodo")
                if not all(math.isfinite(float(punto[k])) for k in ("x", "y", "z")):
                    raise ValueError("Coordenada no finita.")
            for obj in (*p["areas"], *p["frames"], *p["patterns"]):
                _nombre(obj["name"], "objeto")
            for carga in p["loads"]:
                if not all(math.isfinite(float(carga[k])) for k in ("f1", "f2", "f3", "m1", "m2", "m3")):
                    raise ValueError("Carga no finita.")
            for resorte in p["springs"]:
                if not math.isfinite(float(resorte["kz"])) or float(resorte["kz"]) < 0:
                    raise ValueError("Rigidez de resorte inválida.")
        except (KeyError, TypeError) as exc:
            raise ValueError(f"El plan tiene una entrada mal formada: {exc}") from exc
        return p


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/zapata-biaxial/*`` con el envoltorio ``call`` de ``bridge.actions``."""
    prefix = "/v1/sap/actions/zapata-biaxial"

    @app.get(f"{prefix}/estado")
    def zapata_estado():
        return call(estado)

    @app.get(f"{prefix}/casos")
    def zapata_casos():
        return call(casos)

    @app.post(f"{prefix}/nodos")
    def zapata_nodos(body: NodosBody | None = None):
        # Sin cuerpo: nodos seleccionados/restringidos. Con ``nodos``: coordenadas de esos nodos.
        if body is not None:
            return call(lambda model: coordenadas(model, body.nodos))
        return call(nodos)

    @app.post(f"{prefix}/reacciones")
    def zapata_reacciones(body: ReaccionesBody):
        if body.sonda:
            return call(
                lambda model: sonda_joint_react(
                    model,
                    body.nodos[0],
                    body.casos[0] if body.casos else None,
                    None if body.casos else body.combinaciones[0],
                )
            )
        return call(lambda model: reacciones(model, body.nodos, body.casos, body.combinaciones))

    @app.post(f"{prefix}/exportar/preview")
    def zapata_exportar_preview(body: PlanBody):
        return call(lambda model: preview(model, body.plan))

    @app.post(f"{prefix}/exportar/apply")
    def zapata_exportar_apply(body: PlanBody):
        if not body.confirmed:
            raise HTTPException(
                422, "Falta la confirmación explícita (confirmed: true) para escribir en SAP2000."
            )
        return call(lambda model: apply(model, body.plan, body.huella))
