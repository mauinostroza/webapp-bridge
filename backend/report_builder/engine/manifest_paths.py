"""Rutas que el manifiesto persiste: `project.docx_path`, `modules[].source_json` y
`modules[].last_sync.docx_backup`.

Regla: el manifiesto (`<nombre>.report_manifest.json`) se guarda junto al .docx y sus
rutas son RELATIVAS a su propia carpeta, con separador `/`:

- documento: el nombre del archivo (`Memoria.docx`);
- fuentes: `<nombre>_reporte/<modulo>/<modulo>.json` (espejo local) o `<uuid>.json`
  (almacen del servidor, misma carpeta);
- respaldo: `report_backups/<archivo>.docx`.

Asi el manifiesto, su respaldo en el servidor y un proyecto .ewproj no llevan el
nombre de usuario de Windows ni las carpetas del PC, y la carpeta entera (Word +
manifiesto + `_reporte`) se puede mover sin reanclar nada.

Compatibilidad: un manifiesto antiguo con rutas absolutas sigue funcionando (se
resuelve tal cual) y se migra a relativas la proxima vez que se guarde, si la ruta
absoluta cae dentro de la carpeta del manifiesto. Una ruta absoluta AJENA se conserva
(no se pierde informacion) y se avisa. Solo se escribe `..` cuando el llamador lo
pide explicitamente (p. ej. reasignar el documento a otra carpeta).

Este modulo no importa nada del motor (ni ManifestError): los errores de ruta se
comunican con ValueError y el llamador los convierte.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

_UNIDAD = re.compile(r"^[A-Za-z]:")


def es_absoluta(ref: str) -> bool:
    """True si `ref` es una ruta absoluta de cualquier sistema (POSIX, Windows con unidad o UNC)."""
    return bool(ref) and (ref[0] in "/\\" or bool(_UNIDAD.match(ref)))


def _solo_windows(ref: str) -> bool:
    """Ruta con unidad o UNC que este sistema no puede resolver (p. ej. un manifiesto de Windows leido en Linux)."""
    return os.name != "nt" and (bool(_UNIDAD.match(ref)) or ref.startswith("\\\\"))


def _dentro(ref: str, carpeta: Path) -> bool:
    if _solo_windows(ref):
        return False
    return Path(ref).resolve().is_relative_to(Path(carpeta).resolve())


def ruta_relativa(destino: str | Path, carpeta: str | Path) -> str:
    """Ruta POSIX de `destino` relativa a `carpeta` (con `..` si sale de ella).

    Lanza ValueError si no existe ruta relativa posible (unidades distintas en Windows).
    """
    try:
        rel = os.path.relpath(Path(destino).resolve(), Path(carpeta).resolve())
    except ValueError as exc:
        raise ValueError("La ruta no puede expresarse relativa a la carpeta del manifiesto") from exc
    return PurePosixPath(*Path(rel).parts).as_posix()


def normalizar(ref: str, carpeta: str | Path) -> str:
    """Forma que se persiste: relativa si ya lo era o si cae dentro de `carpeta`.

    Una absoluta ajena se devuelve tal cual (ver `es_ajena`): se conserva y se avisa.
    """
    if not es_absoluta(ref):
        return ref.replace("\\", "/")
    if _dentro(ref, Path(carpeta)):
        return ruta_relativa(ref, carpeta)
    return ref


def es_ajena(ref: str, carpeta: str | Path) -> bool:
    """True si `ref` es absoluta y no cae dentro de `carpeta` (la ruta se conserva y se avisa)."""
    return es_absoluta(ref) and not _dentro(ref, Path(carpeta))


def resolver(ref: str, carpeta: str | Path) -> Path:
    """Path real de una ruta guardada: relativa a `carpeta`, o la absoluta legacy tal cual."""
    if es_absoluta(ref):
        return Path(ref).resolve()
    return (Path(carpeta) / PurePosixPath(ref.replace("\\", "/"))).resolve()


def buscar_por_cola(ref: str, carpetas: list[Path]) -> Path | None:
    """Localiza el archivo de una ruta absoluta de OTRA instalacion.

    Prueba primero la ruta tal cual (si sigue existiendo) y luego, en cada carpeta,
    las colas de la ruta de mas a menos componentes (`Memoria_reporte/x/x.json`,
    `x/x.json`, `x.json`). Devuelve el primer archivo existente o None.
    """
    if es_absoluta(ref) and Path(ref).is_file() and not _solo_windows(ref):
        return Path(ref)
    partes = [p for p in re.split(r"[\\/]+", ref) if p and not _UNIDAD.match(p)]
    for carpeta in carpetas:
        for k in range(len(partes), 0, -1):
            candidato = Path(carpeta).joinpath(*partes[-k:])
            if candidato.is_file():
                return candidato
    return None
