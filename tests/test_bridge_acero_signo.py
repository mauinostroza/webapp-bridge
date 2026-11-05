"""Signo de la carga axial en bridge/acero_aisc360_fuerzas.py (convención SAP2000: P de tracción positiva).

Barra ficticia «SIGNO» (L = 4 m) sobre el COM falso de tests/test_bridge_acero_fuerzas.py. Se
inyecta con monkeypatch en sus diccionarios, sin tocar el fichero de tests existente. Cubre:
compresión pura, tracción pura, mixta (P cambia de signo entre estaciones y entre pasos), que
ningún valor negativo de P se pierde, que Mux, Muy y Vux son magnitudes, y la envolvente.
"""

from __future__ import annotations

import pytest

import tests.test_bridge_acero_fuerzas as base
from bridge.acero_aisc360_fuerzas import _axiales, leer_fuerzas

XS = (0.0, 1.0, 2.0, 3.0, 4.0)


def fila(x: float, P: float, *, paso: str = "", V2: float = 0.0, M2: float = 0.0, M3: float = 0.0):
    """Estación (paso, x, P, V2, V3, T, M2, M3) con P de tracción positiva."""
    return (paso, float(x), float(P), V2, 0.0, 0.0, M2, M3)


@pytest.fixture
def barra(monkeypatch):
    def poner(datos):
        monkeypatch.setitem(base.DATA, "SIGNO", datos)
        monkeypatch.setitem(base.ENDPOINTS, "SIGNO", ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0)))
        monkeypatch.setitem(base.SECCION, "SIGNO", "W1")

    return poner


def demanda(res, combo):
    return next(d for d in res["demandas"] if d["combo"] == combo)


# --- Reparto de signo (funciones puras) -----------------------------------------------


def test_axiales_compresion_pura_va_a_pu():
    assert _axiales([-100.0, -50.0, -80.0]) == (100.0, 0.0)


def test_axiales_traccion_pura_va_a_tu():
    assert _axiales([30.0, 80.0, 0.0]) == (0.0, 80.0)


def test_axiales_mixta_conserva_ambas_magnitudes():
    assert _axiales([-100.0, 40.0, 300.0]) == (100.0, 300.0)


def test_axiales_compresion_pequena_no_se_pierde_ni_se_redondea():
    assert _axiales([-0.5]) == (0.5, 0.0)


def test_axiales_sin_carga_da_ceros():
    assert _axiales([0.0, 0.0]) == (0.0, 0.0)


# --- Lectura de SAP2000 de extremo a extremo ----------------------------------------


def test_compresion_pura_da_pu_y_tu_cero(barra):
    barra({"U1": [fila(x, -100.0) for x in XS]})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    d = demanda(res, "U1")
    assert d["Pu_kN"] == pytest.approx(100.0)
    assert d["Tu_kN"] == 0.0
    assert not any("cambia de signo" in w for w in res["warnings"])


def test_traccion_pura_da_tu_y_pu_cero(barra):
    barra({"U1": [fila(x, 80.0) for x in XS]})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    d = demanda(res, "U1")
    assert d["Tu_kN"] == pytest.approx(80.0)
    assert d["Pu_kN"] == 0.0


def test_mixta_entre_estaciones_conserva_compresion_y_traccion(barra):
    # Compresión 100 en el extremo izquierdo y tracción 300 en el derecho: ambas deben salir.
    barra({"U1": [fila(0.0, -100.0), fila(1.0, 0.0), fila(2.0, 40.0), fila(3.0, 150.0), fila(4.0, 300.0)]})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    d = demanda(res, "U1")
    assert d["Pu_kN"] == pytest.approx(100.0)
    assert d["Tu_kN"] == pytest.approx(300.0)
    assert any("cambia de signo" in w and "'U1'" in w for w in res["warnings"])


def test_mixta_entre_pasos_de_una_combinacion(barra):
    # Dos pasos de la misma combinación: P = -100 en el primero y +300 en el segundo.
    datos = [fila(x, -100.0, paso="PASO1") for x in XS] + [fila(x, 300.0, paso="PASO2") for x in XS]
    barra({"U1": datos})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    d = demanda(res, "U1")
    assert d["Pu_kN"] == pytest.approx(100.0)
    assert d["Tu_kN"] == pytest.approx(300.0)


def test_envolvente_toma_cada_componente_de_su_combinacion(barra):
    barra(
        {
            "U1": [fila(x, -100.0) for x in XS],
            "U2": [fila(x, 300.0) for x in XS],
        }
    )
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1", "U2"])
    env = res["envolvente"][0]
    assert env["Pu_kN"] == pytest.approx(100.0) and env["gobierna"]["Pu_kN"] == "U1"
    assert env["Tu_kN"] == pytest.approx(300.0) and env["gobierna"]["Tu_kN"] == "U2"


# --- Momentos y cortantes siempre en magnitud ---------------------------------------


def test_momentos_y_cortantes_negativos_salen_en_magnitud(barra):
    # M3 y M2 negativos en todas las estaciones y V2 negativo: sin abs saldrían negativos.
    barra({"U1": [fila(x, -10.0, V2=-20.0, M2=-7.0, M3=-50.0) for x in XS]})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    d = demanda(res, "U1")
    assert d["Mux_kNm"] == pytest.approx(50.0)
    assert d["Muy_kNm"] == pytest.approx(7.0)
    assert d["Vux_kN"] == pytest.approx(20.0)
    assert d["Mux_kNm"] >= 0 and d["Muy_kNm"] >= 0 and d["Vux_kN"] >= 0 and d["Vuy_kN"] >= 0


def test_momento_de_signo_contrario_al_maximo_se_toma_en_magnitud(barra):
    # El máximo |M3| es el negativo (-80); el positivo (+60) no debe ganarle.
    barra({"U1": [fila(0.0, 0.0, M3=60.0), fila(2.0, 0.0, M3=-80.0), fila(4.0, 0.0, M3=10.0)]})
    res = leer_fuerzas(base.FakeAcero(), ["SIGNO"], ["U1"])
    assert demanda(res, "U1")["Mux_kNm"] == pytest.approx(80.0)
