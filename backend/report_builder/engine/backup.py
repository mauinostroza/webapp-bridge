"""
Backups automaticos y versionados del .docx antes de cualquier escritura.
Se crea siempre, incluso si al final solo se toca un modulo.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

_FORMATO_TIMESTAMP = "%Y%m%d_%H%M%S_%f"
#: Sufijo que `create_backup` agrega al stem del documento: "_20260101_120000_000000".
_SUFIJO_BACKUP = re.compile(r"_\d{8}_\d{6}_\d{6}$")


def create_backup(docx_path: str | Path, backups_dir: str | Path, keep_last: int = 5) -> Path:
    docx_path = Path(docx_path)
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(_FORMATO_TIMESTAMP)
    dest = backups_dir / f"{docx_path.stem}_{timestamp}{docx_path.suffix}"
    shutil.copy2(docx_path, dest)

    _rotate(docx_path, backups_dir, keep_last)
    return dest


def _es_backup_de(candidato: Path, docx_path: Path) -> bool:
    """True si `candidato` es un backup del documento `docx_path`.

    El glob `f"{stem}_*"` que se usaba antes tambien matchea los backups de
    OTRO documento cuyo nombre empieza con ese stem: en una carpeta con
    "Memoria.docx" y "Memoria_rev B.docx" -y `report_backups/` es por carpeta,
    compartido por todos los .docx, con dos documentos por carpeta como caso
    soportado a proposito (ver manifest_ops.manifest_path_for)-, respaldar
    "Memoria.docx" iba borrando los backups de "Memoria_rev B". Justo esos
    backups son la unica red cuando una escritura sale mal, asi que se exige
    que lo que sigue al stem sea el sufijo de timestamp y que la extension
    coincida."""
    if candidato.suffix != docx_path.suffix:
        return False
    resto = candidato.stem[len(docx_path.stem) :]
    return candidato.stem.startswith(docx_path.stem) and bool(_SUFIJO_BACKUP.fullmatch(resto))


def _rotate(docx_path: Path, backups_dir: Path, keep_last: int) -> None:
    matches = sorted(
        (p for p in backups_dir.glob(f"{docx_path.stem}_*") if _es_backup_de(p, docx_path)),
        key=lambda p: p.stat().st_mtime,
    )
    excess = len(matches) - keep_last
    for old in matches[: max(excess, 0)]:
        old.unlink(missing_ok=True)
