"""Los nombres de combinación admiten «:» (nombres por defecto «ASD1: D + L»)."""

import pytest

from bridge.actions import ActionValidationError, _valid_combo_name, _valid_name
from bridge.combinaciones import DeleteBody, MatrixCombination


def test_combo_name_acepta_dos_puntos():
    assert _valid_combo_name("ASD1: SCM + SCO + D + 0.75SCU") == "ASD1: SCM + SCO + D + 0.75SCU"
    assert MatrixCombination(name="ASD6: SCM + Ex -Ev").name == "ASD6: SCM + Ex -Ev"
    assert DeleteBody(names=["ASD1: D + L"]).names == ["ASD1: D + L"]


@pytest.mark.parametrize("nombre", ["a/b", "a\\b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "", "x" * 129])
def test_combo_name_rechaza_lo_demas(nombre):
    with pytest.raises(ActionValidationError):
        _valid_combo_name(nombre)


def test_el_resto_de_nombres_no_cambia():
    with pytest.raises(ActionValidationError):
        _valid_name("FRAME:1")
