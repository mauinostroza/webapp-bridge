"""Rastro en disco de qué puente SAP2000 local está vivo y cómo hablarle.

Deliberadamente sin ninguna dependencia de FastAPI ni de ``bridge.sap2000_agent``:
ese módulo exige ``SAP2000_AGENT_TOKEN`` al importarse y lanza ``RuntimeError`` si
falta, lo que lo hace inadecuado para importar desde el backend web sólo para
leer esta ruta. Este módulo debe poder importarse siempre, sin efectos
secundarios.

El puente escribe este archivo al arrancar y lo borra al salir limpiamente
("Salir" en la bandeja). Si el proceso muere sin pasar por ahí, el archivo
queda huérfano: quien lo lee no debe confiar ciegamente en que el puente siga
vivo, sólo en que existió — un intento de conexión fallido es la única prueba
real de que ya no está disponible.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from runtime_paths import user_data_dir

_REQUIRED_KEYS = ('port', 'token', 'pid')


def agent_file_path() -> Path:
    return user_data_dir('EngineeringWorkbench') / 'bridge' / 'agent.json'


def write_agent_file(port: int, token: str) -> None:
    """Escribe el archivo de forma atómica.

    El backend puede estar leyendo el mismo archivo a la vez, así que se
    escribe primero a un archivo temporal en el mismo directorio y se
    reemplaza con ``os.replace`` (atómico en el mismo sistema de archivos),
    en vez de escribir directamente sobre el destino final.
    """
    path = agent_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'port': port, 'token': token, 'pid': os.getpid()}
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix='.agent-', suffix='.json.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_agent_file() -> dict | None:
    """Lee el archivo. ``None`` si no existe, está corrupto o incompleto.

    Nunca lanza: quien llama siempre puede tratar el resultado como "el
    puente no está disponible ahora mismo".
    """
    try:
        raw = agent_file_path().read_text(encoding='utf-8')
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or any(key not in data for key in _REQUIRED_KEYS):
        return None
    return data


def remove_agent_file() -> None:
    agent_file_path().unlink(missing_ok=True)
