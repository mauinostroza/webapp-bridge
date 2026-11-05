"""Cada símbolo win32* que usa la bandeja existe en pywin32 (solo en Windows).

Protege contra fallos como ``win32gui.SetTimer`` (no existe en pywin32), que
solo aparecían al abrir SAP2000Bridge.exe. En Linux se salta; el build del
portable la corre en el runner Windows antes de empaquetar.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

SOURCES = [Path(__file__).resolve().parent.parent / "bridge" / "tray.py"]


def _used_symbols() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for source in SOURCES:
        for module, name in re.findall(
            r"\b(win32gui|win32api|win32con)\.([A-Za-z_][A-Za-z0-9_]*)", source.read_text(encoding="utf-8")
        ):
            found.add((module, name))
    return found


def test_scanner_finds_the_tray_calls():
    symbols = _used_symbols()
    assert ("win32gui", "Shell_NotifyIcon") in symbols
    assert ("win32gui", "SetTimer") not in symbols


@pytest.mark.skipif(sys.platform != "win32", reason="pywin32 solo existe en Windows")
def test_every_win32_symbol_exists():
    missing = [
        f"{module}.{name}"
        for module, name in sorted(_used_symbols())
        if not hasattr(importlib.import_module(module), name)
    ]
    assert not missing, f"Símbolos inexistentes en pywin32: {missing}"
