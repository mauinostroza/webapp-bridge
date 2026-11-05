"""sap2000/results_views.py: datos de resultados sin interfaz (antes en el visor)."""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.results_views import (
    AREA_STRESS_COMPONENTS,
    area_stress_unit,
    build_design_ratios,
    build_design_ratios_report,
)


def test_componentes_de_shell():
    assert AREA_STRESS_COMPONENTS == {"M11", "M22", "M12", "V13", "V23", "F11", "F22", "F12"}


def test_unidad_de_momento_es_fuerza_y_la_de_fuerza_por_longitud():
    ru = {"force": "tonf", "length": "m", "label": "tonf, m"}
    assert area_stress_unit("M11", ru) == "tonf"
    assert area_stress_unit("V13", ru) == "tonf/m"
    assert area_stress_unit("F22", ru) == "tonf/m"


def test_sin_unidades_no_se_inventa_ninguna():
    assert area_stress_unit("M11", None) == ""
    assert area_stress_unit("V13", {}) == ""
    assert area_stress_unit("V13", {"length": "m"}) == ""


class _Lector:
    """Lector de pega con las dos tablas de D/C."""

    def __init__(self, concreto=None, acero=None, falla_acero=False):
        self._c, self._a, self._falla = concreto or [], acero or [], falla_acero

    def concrete_design_results(self, limit=None):
        return {"rows": self._c}

    def steel_design_results(self, limit=None):
        if self._falla:
            raise RuntimeError("sin diseño ejecutado")
        return {"rows": self._a}


def test_el_acero_sobrescribe_al_hormigon_en_una_misma_barra():
    r = _Lector(concreto=[["F1", "0.4"], ["F2", "0.6"]], acero=[["F1", "0.9"]])
    assert build_design_ratios(r) == {"F1": 0.9, "F2": 0.6}


def test_filas_no_numericas_se_cuentan_y_avisan_en_vez_de_perderse():
    rep = build_design_ratios_report(_Lector(acero=[["F1", "0.5"], ["F2", "N/A"], ["F3", ""]]))
    assert rep.ratios == {"F1": 0.5}
    assert rep.bad_rows == {"steel_design_results": 2}
    assert any("2 fila(s)" in w for w in rep.warnings)


def test_una_lectura_que_falla_queda_en_los_avisos_y_no_impide_la_otra():
    rep = build_design_ratios_report(_Lector(concreto=[["F1", "0.3"]], falla_acero=True))
    assert rep.ratios == {"F1": 0.3}
    assert any("steel_design_results" in w and "sin diseño ejecutado" in w for w in rep.warnings)


def test_sin_lector_devuelve_vacio():
    assert build_design_ratios(None) == {}
    assert build_design_ratios_report(None).warnings == ()


def test_con_el_simulador_completo_no_revienta():
    ratios = build_design_ratios(SapModelReader(FakeSapObject().SapModel))
    assert isinstance(ratios, dict)
