"""Orígenes de las webs alojadas; módulo ligero para importarlo sin exigir el token del puente.

El repositorio público no lleva las direcciones de ningún despliegue. Se leen de
``bridge/origenes_alojados.json`` (opcional, no versionado), que ``scripts/build_bridge.ps1`` incorpora al
ejecutable si existe el secreto ``ORIGENES_ALOJADOS``. Sin ese archivo los orígenes son marcadores
``*.invalid`` (nunca coinciden con un sitio real) y los orígenes propios se añaden con ``origins.json`` o
``SAP2000_AGENT_ORIGINS``. Claves del JSON: ``hosted``, ``fusion``, ``graph``, ``viewer``, ``placa_base``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ARCHIVO = "origenes_alojados.json"


def _cargar() -> dict[str, str]:
    base = getattr(sys, "_MEIPASS", None)
    carpetas = [Path(base) / "bridge"] if base else []
    carpetas.append(Path(__file__).resolve().parent)
    for carpeta in carpetas:
        try:
            datos = json.loads((carpeta / ARCHIVO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(datos, dict):
            return {
                k: v.strip().rstrip("/")
                for k, v in datos.items()
                if isinstance(v, str) and v.strip().startswith(("http://", "https://"))
            }
    return {}


_ORIGENES = _cargar()

HOSTED_ORIGIN = _ORIGENES.get("hosted", "https://hosted.invalid")

# Segundo despliegue de la misma web. Como la primera, no tiene alcance acotado en bridge/alcances.py.
FUSION_ORIGIN = _ORIGENES.get("fusion", "https://fusion.invalid")

# Aplicaciones web independientes que comparten este puente (ver docs/PUENTE_COMPARTIDO.md). Cada una usa
# su propio emparejamiento y un alcance de rutas acotado (bridge/alcances.py).
SAP_GRAPH_ORIGIN = _ORIGENES.get("graph", "https://sap-graph.invalid")
VIEWER_ORIGIN = _ORIGENES.get("viewer", "https://viewer.invalid")
PLACA_BASE_ORIGIN = _ORIGENES.get("placa_base", "https://placa-base.invalid")
SHARED_APP_ORIGINS = (SAP_GRAPH_ORIGIN, VIEWER_ORIGIN, PLACA_BASE_ORIGIN)
