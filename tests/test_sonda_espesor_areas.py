"""Mecánica de scripts/sonda_espesor_areas.py con un SAP simulado (los valores reales los mide el usuario en SAP2000)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bridge.viewer import shell_forces as sf
from tests.viewer.test_viewer_shell_forces import FakeShell

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "sonda_espesor_areas.py"
spec = importlib.util.spec_from_file_location("sonda_espesor_areas", RUTA)
sonda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonda)


@pytest.fixture(autouse=True)
def _limpia():
    sf.limpiar_cache_ejes()
    yield
    sf.limpiar_cache_ejes()


class ModeloConUnidades(FakeShell):
    """El espesor sale en las unidades presentes: ×1000 en N_mm_C (9) respecto de Ton_m_C (12)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        original = self.PropArea.GetShell_1

        def gs(nombre, *a):
            salida = list(original(nombre, *a))
            k = 1000.0 if self.units == 9 else 1.0
            salida[4], salida[5] = salida[4] * k, salida[5] * k
            return tuple(salida)

        self.PropArea.GetShell_1 = gs


def _correr(m, esperado=None):
    inf = sonda.Informe()
    sonda.sondear(m, inf, esperado, 20)
    return inf, {p["id"]: p for p in inf.pruebas}


def test_la_sonda_pasa_con_un_modelo_coherente_y_restaura_unidades():
    m = ModeloConUnidades()
    inf, por_id = _correr(m, esperado=0.2)
    assert por_id["E1"]["veredicto"] == sonda.PASS
    assert por_id["E3"]["veredicto"] == sonda.PASS
    assert por_id["E4"]["veredicto"] == sonda.PASS
    assert por_id["E5"]["veredicto"] == sonda.PASS
    assert m.units == 5  # restauradas
    assert por_id["E2"]["datos"]["formas"]  # salidas crudas registradas


def test_el_espesor_esperado_distinto_es_fail():
    _, por_id = _correr(ModeloConUnidades(), esperado=0.4)
    assert por_id["E5"]["veredicto"] == sonda.FAIL


def test_si_el_espesor_no_sigue_las_unidades_es_fail():
    _, por_id = _correr(FakeShell())  # el simulado base no escala
    assert por_id["E4"]["veredicto"] == sonda.FAIL


def test_sin_propiedad_de_area_la_prueba_e1_falla_o_es_inconclusa():
    _, por_id = _correr(ModeloConUnidades(sin_propiedad={"A1", "A2"}))
    assert por_id["E1"]["veredicto"] == sonda.INCONCLUSO


def test_modelo_sin_areas_es_inconcluso():
    inf = sonda.Informe()
    m = ModeloConUnidades(malla={})
    sonda.sondear(m, inf, None, 20)
    assert inf.pruebas[0]["veredicto"] == sonda.INCONCLUSO
