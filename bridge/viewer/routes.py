"""SAP2000 Viewer en el puente compartido: rutas ``/v1/sap/actions/viewer/*``.

Port de los endpoints de ``web_bridge/server.py`` (SAP2000_AI_ASSISTANT@572b684). La
lógica de lectura, resultados, factores K y snapshot es la del núcleo copiado en
``bridge/viewer/core`` y ``bridge/viewer/sap2000``; aquí sólo se enlazan rutas,
modelos de cuerpo y el mapeo de errores a HTTP, igual que en el original.

No se portan ``web_bridge/access.py`` ni la middleware ``local_origin``: la
autenticación, el emparejamiento por origen, el CORS y la comprobación de
``Host``/``Origin`` las pone el puente compartido (``bridge/pairing.py``,
``bridge/alcances.py``, ``bridge/sap2000_agent.py``).

El ``LocalBridge`` del Viewer tiene su propio hilo COM con un único trabajador, así
que no se envuelve en el actor STA de ``bridge.actions.install_routes``.
"""

from __future__ import annotations

import atexit
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from bridge.viewer import foundations as fund
from bridge.viewer.core import results as res
from bridge.viewer.core import results_bulk as bulk
from bridge.viewer.core.bridge import BridgeError, LocalBridge
from bridge.viewer.core.k_factors import KFactorService, KServiceError
from bridge.viewer.core.section_props import SeccionesError, read_section_props
from runtime_paths import user_data_dir

PREFIJO = "/v1/sap/actions/viewer"
APP_NAME = "EngineeringWorkbench"

_puente: LocalBridge | None = None
_puente_lock = threading.Lock()
_servicio_k: KFactorService | None = None
_servicio_k_lock = threading.Lock()


class KPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: str
    changes: dict[str, float | Literal["auto"]]


class KApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str
    unlock: bool = False


class FoundationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    joints: list[str] = Field(default_factory=list, max_length=20_000)
    shells: list[str] = Field(default_factory=list, max_length=20_000)


class FoundationResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[FoundationGroup] = Field(min_length=1, max_length=200)
    cases: list[str] | None = Field(default=None, max_length=500)
    combos: list[str] | None = Field(default=None, max_length=500)


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["demo", "sap2000"]


def _obtener_puente() -> LocalBridge:
    """Un único ``LocalBridge`` por proceso, creado al primer uso."""
    global _puente
    with _puente_lock:
        if _puente is None:
            _puente = LocalBridge()
        return _puente


def _obtener_servicio_k() -> KFactorService:
    """Servicio de factores K (nonces de vista previa y auditoría) en la carpeta de datos del puente."""
    global _servicio_k
    with _servicio_k_lock:
        if _servicio_k is None:
            _servicio_k = KFactorService(Path(user_data_dir(APP_NAME)))
        return _servicio_k


def _redirigir_alias_a_datos_de_usuario() -> None:
    """El núcleo copiado guarda ``table_aliases.json`` junto al paquete; en un
    ejecutable instalado esa carpeta puede ser de sólo lectura. Se redirige a la
    carpeta de datos del usuario sin tocar el código copiado."""
    from bridge.viewer.sap2000 import table_aliases

    table_aliases.alias_path = lambda: user_data_dir(APP_NAME) / "viewer" / "table_aliases.json"


def _cerrar_puente() -> None:
    """Libera el hilo COM del Viewer al terminar el proceso (el original lo hacía en ``lifespan``)."""
    if _puente is not None:
        try:
            _puente.close()
        except Exception:
            pass


def install_routes(app: Any) -> None:
    """Instala las rutas del Viewer en ``app``. No necesita el envoltorio ``call`` del puente."""
    _redirigir_alias_a_datos_de_usuario()
    atexit.register(_cerrar_puente)
    design_stale = {"value": False}

    def sin_cache(data: Any) -> JSONResponse:
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    def respuesta_snapshot() -> Response:
        payload, stale = _obtener_puente().cached()
        if payload is None:
            raise HTTPException(404, "Sin modelo cargado. Conecta a SAP2000 o elige Demo.")
        return Response(
            payload,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Snapshot-Stale": str(stale).lower()},
        )

    async def en_com(fn: Any) -> Any:
        try:
            return await _obtener_puente().run_on_com(fn)
        except BridgeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KServiceError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, **exc.extra}) from exc
        except res.ResultsError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc

    @app.get(f"{PREFIJO}/snapshot")
    async def snapshot():
        payload, _ = _obtener_puente().cached()
        if payload is None:
            # Sin modelo todavía no es un error: 204 evita el 404 rojo en consola.
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return respuesta_snapshot()

    @app.post(f"{PREFIJO}/connect")
    async def connect(body: ConnectRequest):
        if body.mode == "demo":
            raise HTTPException(422, "El modo demo corre en la web")
        try:
            await _obtener_puente().connect(body.mode)
        except BridgeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return respuesta_snapshot()

    @app.post(f"{PREFIJO}/refresh")
    async def refresh():
        try:
            await _obtener_puente().refresh()
        except BridgeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return respuesta_snapshot()

    @app.get(f"{PREFIJO}/status")
    async def status():
        return sin_cache({**_obtener_puente().status(), "designResultsStale": design_stale["value"]})

    @app.get(f"{PREFIJO}/sections/props")
    async def sections_props():
        """Propiedades de las secciones de barra en mm y MPa (solo lectura; no cambia unidades ni estado)."""
        if _obtener_puente().cached()[0] is None:
            raise HTTPException(404, "Sin modelo cargado. Conecta a SAP2000 o elige Demo.")
        try:
            return sin_cache(await en_com(lambda c: read_section_props(c.model)))
        except SeccionesError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get(f"{PREFIJO}/results/cases")
    async def results_cases():
        return sin_cache(await en_com(lambda c: res.list_cases(c.model)))

    @app.get(f"{PREFIJO}/results/design")
    async def results_design():
        return sin_cache(await en_com(lambda c: res.design_ratios(c.model)))

    def conservando_seleccion(modelo: Any, fn: Any) -> Any:
        """Lee resultados y deja la selección de salida del usuario como estaba."""
        with bulk.preserve_output_selection(modelo):
            return fn()

    @app.get(f"{PREFIJO}/results/area")
    async def results_area(case: str, component: str):
        return sin_cache(
            await en_com(
                lambda c: conservando_seleccion(c.model, lambda: res.area_stress(c.model, case, component))
            )
        )

    @app.get(f"{PREFIJO}/results/displacements")
    async def results_displacements(case: str):
        return sin_cache(
            await en_com(lambda c: conservando_seleccion(c.model, lambda: res.displacements(c.model, case)))
        )

    @app.get(f"{PREFIJO}/results/frame/{{frame}}")
    async def results_frame(frame: str, case: str):
        return sin_cache(
            await en_com(
                lambda c: conservando_seleccion(c.model, lambda: res.frame_forces(c.model, case, frame))
            )
        )

    async def en_com_sap(fn: Any) -> Any:
        from bridge.sap2000_agent import SapComError

        try:
            return await en_com(fn)
        except SapComError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get(f"{PREFIJO}/foundations/groups")
    async def foundations_groups():
        """Grupos de SAP2000 con sus nudos y shells (solo lectura)."""
        return sin_cache(await en_com_sap(lambda c: fund.grupos(c.model)))

    @app.post(f"{PREFIJO}/foundations/results")
    async def foundations_results(body: FoundationResultsRequest):
        """Resultados de fundaciones (Ton_m_C) de los grupos pedidos; no deja cambiada la selección de salida."""
        grupos = [g.model_dump() for g in body.groups]
        return sin_cache(
            await en_com_sap(lambda c: fund.resultados(c.model, grupos, body.cases, body.combos))
        )

    # --- Resultados en bloque (solo lectura; ver core/results_bulk.py) ---------------
    @app.get(f"{PREFIJO}/results/reactions")
    async def results_reactions(case: str):
        return sin_cache(await en_com(lambda c: bulk.reactions(c.model, case)))

    @app.get(f"{PREFIJO}/results/frames")
    async def results_frames(
        case: str, ids: str | None = None, limit: int | None = None, cursor: str | None = None
    ):
        return sin_cache(await en_com(lambda c: bulk.frames_block(c.model, case, ids, limit, cursor)))

    @app.get(f"{PREFIJO}/results/envelope")
    async def results_envelope(
        kind: str, cases: str, ids: str | None = None, limit: int | None = None, cursor: str | None = None
    ):
        return sin_cache(await en_com(lambda c: bulk.envelope(c.model, kind, cases, ids, limit, cursor)))

    @app.get(f"{PREFIJO}/k")
    async def k_read():
        servicio = _obtener_servicio_k()
        return sin_cache(await en_com(lambda c: servicio.read(c.model)))

    @app.get(f"{PREFIJO}/k/design")
    async def k_design():
        """K y L calculados por el diseño de acero (solo lectura; sin diseño: available false)."""
        servicio = _obtener_servicio_k()
        return sin_cache(await en_com(lambda c: servicio.read_design(c.model)))

    @app.get(f"{PREFIJO}/k/effective")
    async def k_effective():
        """K y L efectivos: overwrite numérico o valor de diseño si es automático (solo lectura)."""
        servicio = _obtener_servicio_k()
        return sin_cache(await en_com(lambda c: servicio.read_effective(c.model)))

    @app.get(f"{PREFIJO}/k/diagnose")
    async def k_diagnose():
        servicio = _obtener_servicio_k()
        return sin_cache(await en_com(lambda c: servicio.diagnose(c.model)))

    @app.post(f"{PREFIJO}/k/preview")
    async def k_preview(body: KPreviewRequest):
        servicio = _obtener_servicio_k()
        return sin_cache(await en_com(lambda c: servicio.preview(c.model, body.frame, body.changes)))

    @app.post(f"{PREFIJO}/k/apply")
    async def k_apply(body: KApplyRequest):
        servicio = _obtener_servicio_k()
        result = await en_com(lambda c: servicio.apply(c, body.nonce, body.unlock))
        design_stale["value"] = True
        return sin_cache(result)
