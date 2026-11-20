"""Mecánica de scripts/sonda_shells_malla_auto.py con un SAP simulado (las pruebas físicas las hace el usuario en SAP2000)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bridge.viewer import shell_forces as sf
from tests.viewer.test_viewer_shell_forces import FakeShell, matriz_columnas
from tests.viewer.test_viewer_shell_joint_forces import FakeJoint

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "sonda_shells_malla_auto.py"
spec = importlib.util.spec_from_file_location("sonda_shells_malla_auto", RUTA)
sonda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonda)


@pytest.fixture(autouse=True)
def _limpia():
    sf.limpiar_cache_ejes()
    yield
    sf.limpiar_cache_ejes()


def _identidad(_e):
    return matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1))


def test_rango_expande_rangos_y_listas():
    assert sonda._rango("1-3") == ["1", "2", "3"]
    assert sonda._rango("L2,L3") == ["L2", "L3"]
    assert sonda._rango("1-2, X") == ["1", "2", "X"]


def test_prueba_a_detecta_malla_automatica_y_herencia_de_ejes():
    inf = sonda.Informe()
    m = FakeShell(matriz_elemento=_identidad, matriz_objeto=_identidad(None))
    sonda.prueba_a(m, inf, ["A1"])
    por_id = {p["id"]: p for p in inf.pruebas}
    assert por_id["A0"]["veredicto"] == sonda.PASS  # A1 tiene 2 elementos
    assert por_id["A1"]["veredicto"] == sonda.PASS
    assert por_id["A2"]["veredicto"] == sonda.PASS  # el nudo interno ~1
    assert por_id["A3"]["veredicto"] == sonda.PASS
    assert por_id["A5"]["veredicto"] == sonda.PASS


def test_prueba_a_marca_fail_si_los_elementos_no_heredan_el_angulo():
    inf = sonda.Informe()
    c, s = 0.8660254037844387, 0.5
    m = FakeShell(matriz_elemento=_identidad, matriz_objeto=matriz_columnas((c, s, 0), (-s, c, 0), (0, 0, 1)))
    sonda.prueba_a(m, inf, ["A1"])
    assert {p["id"]: p["veredicto"] for p in inf.pruebas}["A3"] == sonda.FAIL


def test_prueba_a_sin_el_area_es_inconcluso():
    inf = sonda.Informe()
    sonda.prueba_a(FakeShell(), inf, ["NOEXISTE"])
    assert inf.pruebas[0]["veredicto"] == sonda.INCONCLUSO


def test_prueba_c_api_y_tabla_iguales():
    inf = sonda.Informe()
    sonda.prueba_c(FakeShell(), inf, ["A1", "A2"], "DEAD")
    assert inf.pruebas[0]["veredicto"] == sonda.PASS
    assert sf._llamar_area_force.__name__ == "_llamar_area_force"  # la sonda restaura la función


def test_prueba_b_corre_con_el_modelo_de_la_sonda_y_no_falla_por_mecanica():
    from tests.viewer.test_viewer_shell_forces import _malla_sonda

    malla, coord = _malla_sonda()
    inf = sonda.Informe()
    m = FakeJoint(malla=malla, coord=coord, matriz_elemento=_identidad)
    l1, l3 = [str(i) for i in range(1, 97)], [str(2000 + i) for i in range(1, 97)]
    sonda.prueba_b(m, inf, l1, [str(1000 + i) for i in range(1, 97)], l3, "DEAD", 2000)
    ids = {p["id"] for p in inf.pruebas}
    assert {"B1V13", "B1V23", "B1M11", "B2"} <= ids
    assert not [p for p in inf.pruebas if p["veredicto"] == sonda.ERROR]
    b1 = next(p for p in inf.pruebas if p["id"] == "B1V13")
    assert "pares" in b1["nota"] and b1["datos"]["conteo"]["igual"] + b1["datos"]["conteo"]["opuesto"] > 0


def test_informe_markdown_lista_las_pruebas():
    inf = sonda.Informe()
    inf.agregar("X1", "titulo", sonda.FAIL, "nota de prueba")
    assert "nota de prueba" in inf.a_markdown() and "FAIL" in inf.a_markdown()
