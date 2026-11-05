"""Ruta del icono de la bandeja y validez del .ico empaquetado (Linux-runnable)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from bridge import tray

REPO = Path(__file__).resolve().parent.parent


def test_icon_path_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    path = tray.tray_icon_path()
    assert path == REPO / "assets" / "sap2000_bridge.ico"
    assert path.is_file()


def test_icon_path_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert tray.tray_icon_path() == tmp_path / "assets" / "sap2000_bridge.ico"


def test_ico_is_valid_multi_size():
    data = (REPO / "assets" / "sap2000_bridge.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    assert count >= 4
