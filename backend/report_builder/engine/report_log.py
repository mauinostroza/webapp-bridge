"""
Log JSON estructurado de cada corrida del motor de sincronizacion: que
modulos se tocaron, con que resultado, y donde quedo el backup.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def write_log(
    logs_dir: str | Path,
    docx_path,
    backup_path,
    results: list,
    started_at: datetime,
    finished_at: datetime,
    dry_run: bool = False,
    keep_last: int = 200,
    save_error: str | None = None,
) -> Path:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Prefijo por docx_path.stem - igual criterio que engine.backup para los
    # backups: sin esto, los logs de sync de dos documentos distintos en la
    # misma carpeta de logs se entreveraban con nombres puramente por
    # timestamp, y "el ultimo log de la carpeta" (ver latest_log/_rotate mas
    # abajo) dejaba de identificar a que documento pertenecia.
    stem = Path(docx_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = logs_dir / f"sync_log_{stem}_{timestamp}.json"

    payload = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "dry_run": dry_run,
        # Sin rutas del PC: el nombre del documento y el respaldo relativo a su carpeta.
        "docx_path": Path(docx_path).name,
        "backup_path": f"report_backups/{Path(backup_path).name}" if backup_path else None,
        # Si save_error esta seteado, ninguno de los resultados "applied" de
        # abajo llego a escribirse realmente en el .docx - el guardado fallo
        # despues de generar el contenido en memoria.
        "save_error": save_error,
        "results": [
            {"module_id": r.module_id, "tag": r.tag, "status": r.status, "detail": r.detail} for r in results
        ],
    }

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    _rotate(logs_dir, stem, keep_last)
    return log_path


def _rotate(logs_dir: Path, stem: str, keep_last: int) -> None:
    # Los nombres de archivo (sync_log_<stem>_%Y%m%d_%H%M%S_%f.json) ya son
    # ordenables lexicograficamente por fecha dentro de un mismo stem, igual
    # criterio que latest_log(). Solo rota los logs de ESTE documento -  los
    # de otro documento en la misma carpeta no se tocan.
    logs = sorted(logs_dir.glob(f"sync_log_{stem}_*.json"))
    excess = len(logs) - keep_last
    for old in logs[: max(excess, 0)]:
        old.unlink(missing_ok=True)


def latest_log(logs_dir: str | Path, docx_path: str | Path) -> Path | None:
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return None
    stem = Path(docx_path).stem
    logs = sorted(logs_dir.glob(f"sync_log_{stem}_*.json"))
    return logs[-1] if logs else None
