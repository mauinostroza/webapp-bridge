"""Fuerzas que cada elemento de shell ejerce en sus nudos (solo lectura): ``POST viewer/foundations/shell-joint-forces``.

Usa ``Results.AreaJointForceShell`` (verificado con SAP2000 v23.3.1, variante 0, 12 arreglos tras el código de retorno y el
conteo: ``Obj, Elm, PointElm, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3``). Sirve para verificar por equilibrio
nodal los totales de un corte recreado en la web a partir de ``shell-forces`` (en la sonda dio 12 tonf y 27/36 tonf·m exactos).

Comparte con ``shell-forces`` la validación de áreas y casos, la malla de análisis (``elements``, ``nodes``, ``mesh_hash``),
las unidades (Ton_m_C restauradas), la selección de salida (restaurada), los límites (``max_elements`` y ``MAX_FILAS``), los
errores y los avisos (ver ``bridge.viewer.shell_forces``). A diferencia de él no hay tabla de respaldo: si
``AreaJointForceShell("ALL")`` no devuelve filas se piden por área y por elemento.

Ejes de los valores (verificado con SAP2000 v23.3.1, sonda de malla automática, prueba B2): ``AreaJointForceShell`` entrega
F1..F3 y M1..M3 en ejes GLOBALES, no en los del elemento (ΣF3 de una carga gravitatoria es +48 tonf en L1, L2 y L3 aunque L3
tenga la normal invertida; en muros los valores crudos coinciden con la estática). Por eso ``frame: "global"`` (por defecto)
devuelve los valores tal como los entrega SAP2000, sin rotarlos, y ``frame: "element"`` los lleva de globales a los ejes
locales del elemento con ``local_k = v_k · global`` (``v1, v2, v3`` de ``shell-forces``, de su caché).
"""

from __future__ import annotations

from typing import Any

from bridge.actions import _with_ton_m_c, model_is_locked
from bridge.foundations_shells import MSG_MODELO_NO_CORRIDO
from bridge.viewer.core import results_bulk as bulk
from bridge.viewer.core.results import ResultsError
from bridge.viewer.shell_forces import (
    _ORDEN_API_NUDOS,
    COMPONENTES_NUDOS,
    _leer,
    campos_espesor,
    _validar_areas,
    _validar_casos,
)

FRAMES = ("global", "element")


def shell_joint_forces(model: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Fuerzas de elemento en sus nudos por elemento × nudo × caso × paso (globales por defecto). Solo lectura; ver el docstring del módulo."""
    frame = body.get("frame") or "global"
    if frame not in FRAMES:
        raise ResultsError(422, "invalid_frame", f"frame debe ser uno de {list(FRAMES)}.")
    incluir_ejes = bool(body.get("include_axes", False))
    max_elementos = int(body.get("max_elements") or 20_000)
    if not model_is_locked(model):
        raise ResultsError(409, "model_not_run", MSG_MODELO_NO_CORRIDO)
    avisos: list[str] = []
    areas = _validar_areas(model, list(body.get("areas") or []), avisos)
    casos, combos = _validar_casos(model, body.get("cases"), body.get("combos"), avisos)
    componentes = list(COMPONENTES_NUDOS)
    try:
        with bulk.preserve_output_selection(model, avisos):
            datos = _with_ton_m_c(
                model,
                lambda: _leer(
                    model,
                    areas,
                    casos,
                    combos,
                    componentes,
                    incluir_ejes,
                    max_elementos,
                    avisos,
                    metodo_api="AreaJointForceShell",
                    orden=_ORDEN_API_NUDOS,
                    con_tabla=False,
                    a_elemento=frame == "element",
                ),
            )
    except RuntimeError as exc:
        if "present units" in str(exc) or "SetPresentUnits" in str(exc):
            raise ResultsError(409, "units_not_restored", f"Unidades de SAP2000: {exc}") from exc
        raise
    if frame == "element":
        avisos.append(
            "F1..M3 se llevaron de los ejes globales (como los entrega SAP2000) a los ejes locales del elemento con los "
            "ejes de shell-forces."
        )
    return {
        "ok": True,
        "units": {"force": "tonf", "length": "m"},
        "model_locked": True,
        "truncated": datos["truncated"],
        "warnings": avisos,
        "mesh_hash": datos["mesh_hash"],
        "forces_frame": frame,
        "elements": datos["elements"],
        "nodes": datos["nodes"],
        "axes": datos["axes"],
        "columns": ["elm", "node", "case", "step", *componentes],
        "rows": datos["rows"],
        **campos_espesor(datos),  # opcionales: solo si hay datos
    }
