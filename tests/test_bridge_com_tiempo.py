"""Tiempo límite configurable de las llamadas al hilo COM (bridge/com_tiempo.py)."""

from __future__ import annotations

from bridge.com_tiempo import LIMITE_PREDETERMINADO, con_limite, limite


def test_limite_predeterminado_y_restauracion():
    assert limite.get() == LIMITE_PREDETERMINADO == 30.0
    with con_limite(60):
        assert limite.get() == 60.0
    assert limite.get() == 30.0


def test_se_restaura_aunque_falle():
    try:
        with con_limite(120):
            raise RuntimeError("falló")
    except RuntimeError:
        pass
    assert limite.get() == 30.0
