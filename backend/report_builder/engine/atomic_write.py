"""Escritura atómica de un archivo existente (el .docx del usuario, sobre todo).

Se escribe en un temporal junto al destino y luego se sustituye con os.replace:
el archivo queda con el contenido viejo o con el nuevo, nunca a medias. Si el
documento está abierto en Word (Windows lo bloquea), el reemplazo falla con
PermissionError y el original no se toca; el llamador lo traduce a un aviso.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path


def replace_with(path: str | Path, writer: Callable[[Path], None]) -> None:
    """Llama a ``writer(temporal)`` y sustituye ``path`` por el temporal al terminar."""
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    temp = Path(name)
    try:
        if path.exists():
            # mkstemp crea el archivo con permisos 0600; el documento conserva los suyos.
            shutil.copymode(path, temp)
        writer(temp)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def save_document_atomic(document, path: str | Path) -> None:
    """Guarda un python-docx ``Document`` sobre ``path`` de forma atómica."""
    replace_with(path, lambda temp: document.save(str(temp)))
