"""Fundaciones SAP2000 en el visor: resultados de grupos de nudos y shells (solo lectura).

Misma lectura que ``POST /v1/sap/snapshot`` (``bridge.sap2000_agent._snapshot_from_model``: Ton_m_C,
mismos ``step_type`` y ``step_num``), pero acotada a los nudos y shells de los grupos pedidos y envuelta
en ``preserve_output_selection``: la selección de salida del usuario queda como estaba.

Los nombres de nudos y shells son los de ``PointObj`` y ``AreaObj`` (los mismos que ``nodes.ids`` y
``areas[].id`` del snapshot del visor y que los de ``bridge.actions.groups_from_model``).
"""

from __future__ import annotations

import os
from typing import Any

from bridge.viewer.core import results_bulk as bulk

MAX_FILAS = 50_000
_LISTAS_CON_TOPE = ("reactions", "displacements", "soil_pressures")


def _union(grupos: list[dict[str, Any]], clave: str) -> list[str]:
    return list(dict.fromkeys(str(n) for g in grupos for n in g.get(clave, [])))


def resultados(
    model: Any,
    grupos: list[dict[str, Any]],
    cases: list[str] | None = None,
    combos: list[str] | None = None,
) -> dict[str, Any]:
    """Coordenadas, reacciones, desplazamientos y presiones de suelo de los grupos, en Ton_m_C.

    ``grupos``: ``[{name, joints, shells}]``. Sin ``cases`` ni ``combos`` se leen todos los casos (como el
    snapshot). Cada lista de filas se corta en ``MAX_FILAS`` y ``truncated`` pasa a ``True``.
    """
    from bridge.sap2000_agent import _snapshot_from_model

    joints = _union(grupos, "joints")
    shells = _union(grupos, "shells")
    echo = [
        {
            "id": str(g["name"]),
            "name": str(g["name"]),
            "nodes": [str(n) for n in g.get("joints", [])],
            "shells": [str(s) for s in g.get("shells", [])],
        }
        for g in grupos
    ]
    avisos: list[str] = []
    with bulk.preserve_output_selection(model, avisos):
        # shells=None con nudos dados = no se lee la tabla de presiones (no hay shells que pedir).
        datos = _snapshot_from_model(model, echo, joints, shells or None, cases, combos)
    truncado = False
    for clave in _LISTAS_CON_TOPE:
        if len(datos[clave]) > MAX_FILAS:
            datos[clave] = datos[clave][:MAX_FILAS]
            truncado = True
    ruta = str(datos["model"].get("path", ""))
    return {
        "units": datos["model"]["units"],
        "model": {"name": os.path.basename(ruta.replace("\\", "/")), "path": ruta},
        "coordinates": datos["coordinates"],
        "reactions": datos["reactions"],
        "displacements": datos["displacements"],
        "soil_pressures": datos["soil_pressures"],
        "groups": datos["groups"],
        "model_locked": datos["model_locked"],
        "truncated": truncado,
        "warnings": avisos,
    }


def grupos(model: Any) -> dict[str, Any]:
    """Grupos de SAP2000 con al menos un nudo o shell, con sus miembros (``PointObj`` y ``AreaObj``)."""
    from bridge.actions import groups_from_model

    return {
        "groups": [
            {"name": g["name"], "joints": g["nodes"], "shells": g["shells"]} for g in groups_from_model(model)
        ]
    }
