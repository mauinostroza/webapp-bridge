"""Los nombres de combinación admiten «:» (p. ej. «ASD1: D + L», el nombre por defecto de SAP2000).

Los casos de carga y los demás nombres siguen sin admitirlo; los caracteres realmente prohibidos
(``\\ / * ? " < > |`` y de control) se rechazan también en las combinaciones.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bridge import acero_aisc360_fuerzas as acero
from bridge import diagrama, equilibrio, foundations_cuts, hormigon, placa_base, zapata_biaxial
from bridge.actions import Combination, ComboItem

ASD = "ASD1: D + L"


def test_zapata_reacciones_acepta_dos_puntos_en_combinaciones_pero_no_en_casos():
    b = zapata_biaxial.ReaccionesBody(nodos=["1"], casos=["DEAD"], combinaciones=[ASD])
    assert b.combinaciones == [ASD]
    with pytest.raises(ValidationError, match="caso no es un nombre válido"):
        zapata_biaxial.ReaccionesBody(nodos=["1"], casos=["CASO: x"], combinaciones=[ASD])


@pytest.mark.parametrize("malo", ["A/B", "A\\B", "A*B", "A?B", 'A"B', "A<B", "A>B", "A|B", "A\x01B", ""])
def test_zapata_reacciones_rechaza_caracteres_prohibidos_en_combinaciones(malo):
    with pytest.raises(ValidationError, match="combinación no es un nombre válido"):
        zapata_biaxial.ReaccionesBody(nodos=["1"], combinaciones=[malo])


def test_placa_base_acepta_dos_puntos_solo_en_combinaciones():
    assert placa_base.ReaccionesBody(nodos=["1"], combinaciones=[ASD]).combinaciones == [ASD]
    assert placa_base.SeleccionarCargasBody(combinaciones=[ASD]).combinaciones == [ASD]
    with pytest.raises(ValidationError):
        placa_base.SeleccionarCargasBody(casos=[ASD])


def test_equilibrio_acepta_dos_puntos_en_las_combinaciones():
    assert equilibrio.FrameDataBody(frame="F1", combo_sismo=ASD, combo_grav=ASD).combo_sismo == ASD
    assert equilibrio.GroupDemandsBody(group="G", combo_sismo=ASD).combo_sismo == ASD
    with pytest.raises(ValidationError):
        equilibrio.FrameDataBody(frame="F1", combo_sismo="A|B")


def test_cortes_de_fundaciones_acepta_dos_puntos_en_combos():
    assert foundations_cuts.ForcesBody(cuts=["C1"], combos=[ASD]).combos == [ASD]


def test_crear_combinaciones_acepta_dos_puntos_en_el_nombre_y_en_sus_items():
    c = Combination(name=ASD, combo_type=0, items=[ComboItem(name="ASD2: D", factor=1.0, kind=1)])
    assert c.name == ASD and c.items[0].name == "ASD2: D"


def test_acero_diagrama_y_hormigon_ya_aceptaban_dos_puntos():
    assert acero.FuerzasBody(frames=["F1"], combos=[ASD]).combos == [ASD]
    assert diagrama.GroupDemandsBody(group="G", combos=[ASD]).combos == [ASD]
    assert hormigon.FrameForcesBody(frame="F1", combos=[ASD]).combos == [ASD]
