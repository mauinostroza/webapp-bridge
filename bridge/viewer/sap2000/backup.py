# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


class BackupManager:
    def __init__(self, backup_dir: Path) -> None:
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self, model: Any, action: str) -> Path:
        try:
            model.File.Save()
        except Exception:
            pass
        model_path = Path(str(model.GetModelFilename()))
        if not model_path.exists():
            raise FileNotFoundError(f"No se encontró el archivo del modelo: {model_path}")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_action = "".join(ch if ch.isalnum() else "_" for ch in action)[:40]
        target_dir = self.backup_dir / f"{model_path.stem}_{stamp}_pre_{safe_action}"
        target_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in _related_model_files(model_path):
            target = target_dir / source.name
            shutil.copy2(source, target)
            copied.append(target)
        if not copied:
            raise FileNotFoundError(f"No se encontraron archivos para respaldar: {model_path}")
        return target_dir

    def restore(self, backup_path: Path) -> Path:
        if not backup_path.exists() or not backup_path.is_dir():
            raise FileNotFoundError(f"No se encontro el backup: {backup_path}")
        files = list(backup_path.iterdir())
        if not files:
            raise FileNotFoundError(f"El backup {backup_path} esta vacio.")
        restored: list[Path] = []
        for source in files:
            if not source.is_file():
                continue
            target = backup_path.parent.parent / source.name
            shutil.copy2(source, target)
            restored.append(target)
        if not restored:
            raise FileNotFoundError(f"No se pudieron restaurar archivos desde {backup_path}")
        return backup_path

    def list_backups(self) -> list[dict[str, Any]]:
        backups: list[dict[str, Any]] = []
        if not self.backup_dir.exists():
            return backups
        entries = sorted(self.backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in entries:
            if not entry.is_dir():
                continue
            stamp_str = entry.stem.split("_pre_")[0] if "_pre_" in entry.stem else ""
            timestamp = ""
            if stamp_str:
                try:
                    dt = datetime.strptime(stamp_str, "%Y%m%d_%H%M%S")
                    timestamp = dt.isoformat(timespec="seconds")
                except ValueError:
                    timestamp = stamp_str
            action = entry.stem.split("_pre_", 1)[1] if "_pre_" in entry.stem else ""
            backups.append({
                "path": str(entry),
                "name": entry.stem,
                "timestamp": timestamp,
                "action": action,
            })
        return backups

    def latest_backup(self) -> Path | None:
        backups = self.list_backups()
        if not backups:
            return None
        return Path(backups[0]["path"])


def _related_model_files(model_path: Path) -> list[Path]:
    if not model_path.exists():
        return []
    candidates = [path for path in model_path.parent.glob(f"{model_path.stem}.*") if path.is_file()]
    if model_path not in candidates:
        candidates.insert(0, model_path)
    return candidates
