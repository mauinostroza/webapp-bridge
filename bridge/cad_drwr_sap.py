"""
bridge.cad_drwr_sap — Rutas del puente local para leer fundaciones de SAP2000.

Monta bajo /v1/sap/actions/cad/sap/:
- groups               grupos de SAP2000 que tienen shells (nombre, n_shells).
- foundation/start     inicia la lectura de un grupo en un hilo de fondo.
- foundation/status    estado, progreso y, si terminó, el dato crudo leído (`sap-crudo-v1`).
- foundation/cancel    cancelación cooperativa (la siguiente operación COM
                       lanza TrabajoCancelado).

Las llamadas COM van por el `call(fn)` que recibe install_routes: el puente
anfitrión ejecuta `fn(sap_model)` en su hilo STA único con timeout fijo de
30 s. Si ese timeout vence, la tarea SIGUE viva en el hilo STA y `call`
lanza HTTPException con detalle vacío: el hilo de fondo de la lectura espera
entonces su final (hasta ESPERA_TIMEOUT_S) en vez de fallar. Si `call` lanza
con detalle no vacío (p. ej. SAP2000 no adjunto), la lectura termina en
'error' con ese texto.

Un único trabajo activo a la vez. Los terminados se conservan TTL_TRABAJO_S
segundos y se purgan perezosamente.

El token Bearer lo valida el middleware global de la aplicación; aquí no se
gestiona autenticación. Errores de validación = 422 en español (ruta
_RutaCad de bridge.cad_drwr); COM / trabajo / conexión = 409 con el texto
real de la excepción.
"""

import json
import threading
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from bridge.cad_drwr import _RutaCad
from bridge.cad_sap_link import (
    ControlTrabajo,
    ModelLink,
    TrabajoCancelado,
    leer_fundacion_cruda,
    listar_grupos,
    miembros_de_grupo,
)

PREFIJO = "/v1/sap/actions/cad/sap"

TTL_TRABAJO_S = 600.0  # un trabajo terminado se conserva 10 min
ESPERA_TIMEOUT_S = 600.0  # espera del hilo de fondo tras vencer el actor
MAX_GRUPO = 128
MAX_JOB = 64

MENSAJE_SIN_RESPUESTA = (
    "SAP2000 no respondió a tiempo (30 s). La operación "
    "puede seguir en curso en SAP2000; reintente en un "
    "momento."
)
MENSAJE_EN_CURSO = "Ya hay una lectura de SAP2000 en curso"
MENSAJE_NO_EXISTE = "El trabajo no existe o venció"

_CFG = ConfigDict(extra="forbid", allow_inf_nan=False)


# ----------------------------------------------------------- modelos --


class VacioIn(BaseModel):
    model_config = _CFG


class GrupoIn(BaseModel):
    model_config = _CFG
    grupo: Annotated[str, Field(min_length=1, max_length=MAX_GRUPO)]


class JobIn(BaseModel):
    model_config = _CFG
    job: Annotated[str, Field(min_length=1, max_length=MAX_JOB)]


# ------------------------------------------------------------ ayudas --


def _texto(exc: BaseException) -> str:
    """Texto real de la excepción; RuntimeError ya trae mensaje en español."""
    if isinstance(exc, RuntimeError) and str(exc):
        return str(exc)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _llamar(call, fn):
    """Ejecuta una operación corta por `call` y traduce sus fallos a 409."""
    try:
        return call(fn)
    except HTTPException as exc:
        if exc.detail:
            raise
        raise HTTPException(409, MENSAJE_SIN_RESPUESTA) from exc
    except Exception as exc:
        raise HTTPException(409, _texto(exc)) from exc


# ------------------------------------------------------------ trabajo --


class Trabajo:
    """Una lectura de un grupo de SAP2000 en curso o terminada."""

    def __init__(self, grupo: str):
        self.id = uuid.uuid4().hex
        self.grupo = grupo
        self.control = ControlTrabajo()
        self.estado = "en_curso"
        self.resultado = None
        self.error = None
        self.inicio = time.monotonic()
        self.fin = None
        self.terminado = threading.Event()
        self._lock = threading.Lock()

    def cerrar(self, estado: str, resultado=None, error=None) -> None:
        """Fija el estado final. Idempotente: gana el primer cierre."""
        with self._lock:
            if self.terminado.is_set():
                return
            self.estado = estado
            self.resultado = resultado
            self.error = error
            self.fin = time.monotonic()
            self.terminado.set()

    def ejecutar(self, model) -> None:
        """Corre en el hilo STA del puente (`call`). Nunca lanza: captura todo
        y deja el resultado en el trabajo."""
        try:
            link = ModelLink(model, self.control)
            dic = leer_fundacion_cruda(link, self.grupo)
            try:
                json.dumps(dic, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.cerrar("error", error=f"El resultado no es JSON válido: {exc}")
                return
            if self.control.cancelado:
                self.cerrar("cancelado")
                return
            self.cerrar("listo", resultado=dic)
        except TrabajoCancelado:
            self.cerrar("cancelado")
        except BaseException as exc:
            self.cerrar("error", error=_texto(exc))

    def instantanea(self) -> dict:
        with self._lock:
            fin = self.fin if self.fin is not None else time.monotonic()
            salida = {
                "estado": self.estado,
                "etapa": "leyendo" if not self.terminado.is_set() else "finalizado",
                "hechas": self.control.hechas,
                "total": None,
                "transcurrido_s": round(fin - self.inicio, 3),
            }
            if self.estado == "listo":
                salida["resultado"] = self.resultado
            if self.estado == "error":
                salida["error"] = self.error
            return salida


def _correr(trabajo: Trabajo, call) -> None:
    """Hilo de fondo: lanza la tarea en el actor y espera su final."""
    try:
        call(trabajo.ejecutar)
    except HTTPException as exc:
        if exc.detail:
            trabajo.cerrar("error", error=str(exc.detail))
            return
        # Vencimiento del actor con detalle vacío: la tarea sigue viva.
    except Exception as exc:
        trabajo.cerrar("error", error=_texto(exc))
        return
    if not trabajo.terminado.wait(ESPERA_TIMEOUT_S):
        trabajo.control.cancelar()
        trabajo.cerrar(
            "error",
            error=(f"La lectura de SAP2000 no terminó en {ESPERA_TIMEOUT_S:g} s; se pidió cancelarla."),
        )


class _Trabajos:
    """Registro de trabajos: como mucho uno en curso; purga perezosa."""

    def __init__(self):
        self._lock = threading.Lock()
        self._trabajos: dict = {}

    def _purgar(self) -> None:
        ahora = time.monotonic()
        for tid in list(self._trabajos):
            t = self._trabajos[tid]
            if t.fin is not None and ahora - t.fin > TTL_TRABAJO_S:
                del self._trabajos[tid]

    def iniciar(self, grupo: str, call) -> str:
        with self._lock:
            self._purgar()
            if any(not t.terminado.is_set() for t in self._trabajos.values()):
                raise HTTPException(409, MENSAJE_EN_CURSO)
            trabajo = Trabajo(grupo)
            self._trabajos[trabajo.id] = trabajo
        hilo = threading.Thread(target=_correr, args=(trabajo, call), name="sap-cad-lectura", daemon=True)
        hilo.start()
        return trabajo.id

    def obtener(self, job: str) -> Trabajo:
        with self._lock:
            self._purgar()
            t = self._trabajos.get(job)
        if t is None:
            raise HTTPException(409, MENSAJE_NO_EXISTE)
        return t


# ------------------------------------------------------------- rutas --


def install_routes(app, call):
    """Monta las rutas SAP del puente CAD en `app`, usando `call` (el actor
    STA del puente anfitrión). Devuelve el registro de trabajos."""
    trabajos = _Trabajos()
    router = APIRouter(route_class=_RutaCad)

    @router.post(PREFIJO + "/groups")
    def sap_groups(body: VacioIn | None = None):
        def fn(model):
            link = ModelLink(model, ControlTrabajo())
            grupos = []
            for nombre in listar_grupos(link):
                try:
                    n = len(miembros_de_grupo(link, nombre)["shells"])
                except Exception:
                    continue  # un grupo que falla no tumba la lista
                if n > 0:
                    grupos.append({"nombre": nombre, "n_shells": n})
            return {"grupos": grupos}

        return _llamar(call, fn)

    @router.post(PREFIJO + "/foundation/start")
    def sap_foundation_start(body: GrupoIn):
        return {"job": trabajos.iniciar(body.grupo, call)}

    @router.post(PREFIJO + "/foundation/status")
    def sap_foundation_status(body: JobIn):
        return trabajos.obtener(body.job).instantanea()

    @router.post(PREFIJO + "/foundation/cancel")
    def sap_foundation_cancel(body: JobIn):
        t = trabajos.obtener(body.job)
        with t._lock:
            if t.terminado.is_set():
                raise HTTPException(409, f"El trabajo ya terminó (estado: {t.estado}); no se puede cancelar")
            t.control.cancelar()
        return {"cancelado": True}

    app.include_router(router)
    return trabajos
