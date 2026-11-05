"""Carpeta de datos por usuario del sistema operativo.

Extraído de ``run_workbench.py`` para que el puente SAP2000 (``bridge/``) y el
backend web compartan la misma noción de "carpeta del usuario" sin duplicar la
lógica de elegir ``LOCALAPPDATA``/``Application Support``/``XDG_DATA_HOME``: es
el mismo límite de confianza que ya justifica ``WORKBENCH_DATA_DIR``.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Tamaño y cantidad de respaldos de cada archivo de registro: generoso para
# depurar un problema puntual, acotado para no crecer sin límite en un equipo
# que nadie revisa a diario.
_LOG_MAX_BYTES = 1_000_000
_LOG_BACKUP_COUNT = 3
# Loggers de uvicorn que, sin esto, siguen escribiendo a una consola que en
# una build --windowed no existe (ver el "arreglo devnull" en cada lanzador).
_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _kebab(app_name: str) -> str:
    """``EngineeringWorkbench`` -> ``engineering-workbench``.

    Sólo se usa para la carpeta de Linux: es la convención que este proyecto
    ya usaba a mano (``engineering-workbench``) cuando la lógica vivía, sin
    parametrizar, en ``run_workbench.py::_default_data_dir``.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "-", app_name).lower().replace(" ", "-")


def user_data_dir(app_name: str) -> Path:
    """Carpeta de datos por usuario del sistema operativo para ``app_name``.

    - Windows: ``%LOCALAPPDATA%/<app_name>`` (o ``~/AppData/Local`` si la
      variable no está definida).
    - macOS: ``~/Library/Application Support/<app_name>``.
    - Resto (Linux y similares): ``$XDG_DATA_HOME/<slug>`` (o
      ``~/.local/share/<slug>`` si la variable no está definida), con
      ``slug`` en formato kebab-case (ver :func:`_kebab`).
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / _kebab(app_name)


def logs_dir(app_name: str) -> Path:
    """Carpeta de registros persistentes de ``app_name``: ``user_data_dir(app_name) / "logs"``.

    Mismo límite de confianza y misma base que ``user_data_dir`` (por eso no
    reimplementa la elección de sistema operativo). Es el único canal de
    diagnóstico disponible para los ejecutables ``--windowed`` (sin consola):
    ver :func:`configure_file_logging`.
    """
    return user_data_dir(app_name) / "logs"


def configure_file_logging(name: str, app_name: str = "EngineeringWorkbench") -> Path:
    """Enruta el logger raíz y los de uvicorn a un archivo rotativo.

    ``name`` da el nombre del archivo (``f"{name}.log"``) bajo
    ``logs_dir(app_name)``; los tres lanzadores lo llaman con un ``name``
    distinto (``workbench``, ``sap2000_bridge``, ``engineering_workbench``)
    pero comparten la misma carpeta de registros.

    No toca ``sys.stdout``/``sys.stderr`` (el ``RotatingFileHandler`` nunca
    los mira), así que es seguro llamarla incluso cuando son ``None`` en una
    build ``--windowed`` — ese caso lo cubre aparte el arreglo "devnull" que
    cada lanzador aplica antes de importar uvicorn.

    Devuelve la ruta del archivo de registro, para que quien llame pueda
    mostrarla (p. ej. "Abrir carpeta de registros" en la bandeja).
    """
    directory = logs_dir(app_name)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"{name}.log"

    handler = RotatingFileHandler(
        log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    if root_logger.level == logging.NOTSET or root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)

    for logger_name in _UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
        uvicorn_logger.setLevel(logging.INFO)

    return log_path
