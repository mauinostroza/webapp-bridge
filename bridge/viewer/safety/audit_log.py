"""JSONL audit logging for validated SAP2000 actions."""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class AuditLogWriter:
    """Append technical audit events as one JSON object per line."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write_event(self, event: Mapping[str, Any]) -> None:
        """Append an event to the JSONL audit file."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dict(event),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
            file.write("\n")

    def write(self, event: Mapping[str, Any]) -> None:
        self.write_event(event)

    def record_action(
        self,
        *,
        user_instruction: str,
        command: Mapping[str, Any],
        validation: Any,
        confirmed: bool | None = None,
        execution_result: Mapping[str, Any] | None = None,
        errors: list[str] | None = None,
        exported_files: list[str] | None = None,
    ) -> None:
        """Record the audit fields described by the project specification."""

        self.write_event(
            {
                "user_instruction": user_instruction,
                "command": command,
                "validation": validation,
                "confirmed": confirmed,
                "execution_result": execution_result,
                "errors": errors or [],
                "exported_files": exported_files or [],
            }
        )


AuditLog = AuditLogWriter


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
