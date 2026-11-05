"""Geometría pura de las ventanas de la bandeja (sin Win32)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bridge import tray

SCALES = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
CONTROLS = ["title", "origin", "code_label", "code", "instruction", "accept", "reject"]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _inside(rect, layout):
    x, y, w, h = rect
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= layout["width"] and y + h <= layout["height"]


def _check_no_overlap_inside(layout, names):
    for name in names:
        assert _inside(layout[name], layout), name
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not _overlap(layout[a], layout[b]), (a, b)


@pytest.mark.parametrize("scale", SCALES)
def test_pairing_layout_no_overlap_and_inside(scale):
    _check_no_overlap_inside(tray.pairing_layout(scale), CONTROLS)


@pytest.mark.parametrize("scale", SCALES)
def test_pairing_buttons_min_size_and_side_by_side(scale):
    layout = tray.pairing_layout(scale)
    for name in ("accept", "reject"):
        _, _, w, h = layout[name]
        assert w >= 120 * scale - 1
        assert h >= 36 * scale - 1
    assert layout["accept"][1] == layout["reject"][1]
    assert layout["accept"][0] < layout["reject"][0]


@pytest.mark.parametrize("scale", SCALES)
def test_pairing_code_is_tall_enough_for_big_font(scale):
    assert tray.pairing_layout(scale)["code"][3] >= 70 * scale - 1


def test_pairing_layout_monotonic_in_scale():
    sizes = [(tray.pairing_layout(s)["width"], tray.pairing_layout(s)["height"]) for s in SCALES]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)


def test_pairing_layout_values_are_ints():
    layout = tray.pairing_layout(1.25)
    assert isinstance(layout["width"], int) and isinstance(layout["height"], int)
    assert all(isinstance(v, int) for name in CONTROLS for v in layout[name])


@pytest.mark.parametrize("scale", SCALES)
def test_status_layout(scale):
    # La ventana de estado se rediseñó (ver bridge/tray.py): cabecera, panel con
    # el ícono grande y columna de datos a la derecha.
    names = ["icon_small", "title", "divider", "panel", "label_column", "value_column", "footer"]
    _check_no_overlap_inside(tray.status_layout(scale), names)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("has_pairings", [True, False])
def test_pairings_layout(scale, has_pairings):
    layout = tray.pairings_layout(scale, has_pairings)
    names = ["list", "revoke"] + ([] if has_pairings else ["hint"])
    _check_no_overlap_inside(layout, names)


def test_dpi_scale():
    assert tray.dpi_scale(96) == 1.0
    assert tray.dpi_scale(144) == 1.5
    assert tray.dpi_scale(192) == 2.0
    assert tray.dpi_scale(0) == 1.0


def test_font_pixel_height():
    assert tray.font_pixel_height(10, 96) == -13
    assert tray.font_pixel_height(40, 144) == -80


def test_pairing_dialog_fields_and_text_intact():
    request = SimpleNamespace(origin="https://x.example", confirm_code="123456")
    assert tray.pairing_dialog_fields(request) == {"origin": "https://x.example", "code": "123456"}
    assert "123456" in tray.pairing_dialog_text(request)


def test_dpi_helpers_are_safe_off_windows():
    assert tray.get_system_dpi() >= 1
    assert tray.enable_dpi_awareness() in (True, False)
