"""Alcance de rutas por origen emparejado.

El Workbench (y los orígenes de desarrollo o añadidos por el usuario) conservan
acceso completo. Las aplicaciones independientes que comparten el puente sólo
pueden llamar a las rutas de su familia, de modo que una aplicación de sólo
lectura no pueda escribir en el modelo aunque su secreto se filtre.
"""

from __future__ import annotations

from bridge.hosted import PLACA_BASE_ORIGIN, SAP_GRAPH_ORIGIN, VIEWER_ORIGIN

# Rutas comunes a toda aplicación emparejada.
_COMUNES = ("/v1/sap/status", "/v1/sap/attach", "/v1/sap/revoke")

ALCANCES: dict[str, tuple[str, ...]] = {
    SAP_GRAPH_ORIGIN: (*_COMUNES, "/v1/sap/actions/graph/"),
    VIEWER_ORIGIN: (*_COMUNES, "/v1/sap/actions/viewer/"),
    PLACA_BASE_ORIGIN: (*_COMUNES, "/v1/sap/actions/placa-base/"),
}


def ruta_permitida(origin: str, path: str) -> bool:
    """``True`` si ``origin`` puede llamar a ``path`` (sin alcance definido: acceso completo)."""
    prefijos = ALCANCES.get(origin)
    if prefijos is None:
        return True
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefijos)
