"""Regla de separación entre estaciones para el Cb (bridge/acero_aisc360_fuerzas.py).

Viga simple de 6 m con carga uniforme w = 10 kN/m: M(x) = w·x·(L−x)/2 = 5x(6−x) (kN·m). Los valores
esperados salen de M(x) real y de la ecuación F1-1, no de números fijos. Si el Cb se interpola con
una separación entre estaciones mayor que L/10, el Cb del paso pasa a 1,0 y se avisa. Con estaciones en
L/4, L/2 y 3L/4 (o interpolación con separación ≤ L/10) no cambia nada salvo el aviso de interpolado.
"""

from __future__ import annotations

import pytest

import bridge.acero_aisc360_fuerzas as fuerzas
from bridge.acero_aisc360_fuerzas import (
    FRACCION_MAX_SEPARACION_CB,
    _demanda,
    cb_para_demanda,
    leer_fuerzas,
)
from tests.test_bridge_acero_fuerzas import DATA, ENDPOINTS, SECCION, FakeAcero

L = 6.0
W = 10.0  # kN/m


def m_real(x: float) -> float:
    """Momento de la viga uniforme: M(x) = w·x·(L−x)/2."""
    return W * x * (L - x) / 2


def v_real(x: float) -> float:
    """Cortante de la viga uniforme: V(x) = w·(L/2 − x)."""
    return W * (L / 2 - x)


def _xs(n: int) -> list[float]:
    """n estaciones equiespaciadas de 0 a L (n >= 2)."""
    return [L * i / (n - 1) for i in range(n)]


def _cb_tres_estaciones() -> float:
    """Cb con estaciones en 0, L/2 y L: MA y MC son la media de los extremos (interpolación lineal)."""
    mmax = m_real(L / 2)
    ma = mc = (m_real(0.0) + mmax) / 2
    return 12.5 * mmax / (2.5 * mmax + 3 * ma + 4 * mmax + 3 * mc)


def _cb_once_estaciones() -> float:
    """Cb con estaciones cada 0,6 m: L/4 y 3L/4 caen entre dos estaciones y se interpolan; L/2 es estación."""
    mb = m_real(L / 2)
    ma = (m_real(1.2) + m_real(1.8)) / 2
    mc = (m_real(4.2) + m_real(4.8)) / 2
    return 12.5 * mb / (2.5 * mb + 3 * ma + 4 * mb + 3 * mc)


def _filas(xs: list[float], combo: str = "U1", step: str = "") -> list[dict]:
    return [
        {
            "combo": combo,
            "step": step,
            "x": x,
            "P": 0.0,
            "V2": v_real(x),
            "V3": 0.0,
            "T": 0.0,
            "M2": 0.0,
            "M3": m_real(x),
        }
        for x in xs
    ]


def _filas_fake(xs: list[float]) -> list[tuple]:
    """Filas del doble COM de tests/test_bridge_acero_fuerzas.py: (paso, x, P, V2, V3, T, M2, M3)."""
    return [("", x, 0.0, v_real(x), 0.0, 0.0, 0.0, m_real(x)) for x in xs]


@pytest.fixture
def barra_v3(monkeypatch):
    """Registra una barra 'V3' en el doble COM; cada test define sus combinaciones y estaciones."""

    def registrar(datos: dict[str, list[tuple]]) -> None:
        monkeypatch.setitem(DATA, "V3", datos)
        monkeypatch.setitem(ENDPOINTS, "V3", ((0.0, 0.0, 0.0), (L, 0.0, 0.0)))
        monkeypatch.setitem(SECCION, "V3", "W1")

    return registrar


def _avisos(res: dict, fragmento: str) -> list[str]:
    return [w for w in res["warnings"] if fragmento in w]


# ---------------------------------------------------------------------------
# Función pura
# ---------------------------------------------------------------------------


def test_constante_de_la_fraccion_es_un_decimo():
    assert FRACCION_MAX_SEPARACION_CB == pytest.approx(0.1)


def test_tres_estaciones_fuerzan_cb_1_0():
    xs = _xs(3)
    cb, interpolado, forzado, gap = cb_para_demanda(xs, [m_real(x) for x in xs], L)
    assert interpolado and forzado
    assert cb == 1.0
    assert gap == pytest.approx(L / 2)
    assert _cb_tres_estaciones() == pytest.approx(1.3158, abs=1e-4)  # el Cb interpolado que se descarta


def test_once_estaciones_interpolan_con_separacion_justo_en_el_limite():
    xs = _xs(11)  # separación L/10: no supera el límite
    cb, interpolado, forzado, gap = cb_para_demanda(xs, [m_real(x) for x in xs], L)
    assert interpolado and not forzado
    assert gap == pytest.approx(L / 10)
    assert cb == pytest.approx(_cb_once_estaciones(), rel=1e-12)
    assert cb == pytest.approx(1.143, abs=1e-3)


def test_trece_estaciones_exactas_sin_interpolar():
    xs = _xs(13)  # hay estaciones en L/4, L/2 y 3L/4
    cb, interpolado, forzado, gap = cb_para_demanda(xs, [m_real(x) for x in xs], L)
    assert not interpolado and not forzado
    assert cb == pytest.approx(12.5 / 11, rel=1e-12)  # 1,1364
    assert gap == pytest.approx(0.5)


def test_cinco_estaciones_en_cuartos_son_exactas():
    xs = [0.0, L / 4, L / 2, 3 * L / 4, L]
    cb, interpolado, forzado, _ = cb_para_demanda(xs, [m_real(x) for x in xs], L)
    assert not interpolado and not forzado
    assert cb == pytest.approx(12.5 / 11, rel=1e-12)


def test_momento_nulo_no_da_cb_ni_error():
    cb, interpolado, forzado, gap = cb_para_demanda([0.0, 1.0, 3.0], [0.0, 0.0, 0.0], L)
    assert cb is None and not forzado
    assert gap == pytest.approx(2.0)


def test_regla_desactivada_mantiene_el_cb_interpolado():
    xs = _xs(3)
    cb, interpolado, forzado, _ = cb_para_demanda(xs, [m_real(x) for x in xs], L, regla=False)
    assert interpolado and not forzado
    assert cb == pytest.approx(_cb_tres_estaciones(), rel=1e-12)


def test_con_una_sola_estacion_no_hay_separacion():
    cb, _, forzado, gap = cb_para_demanda([0.0], [1.0], L)
    assert cb is None and not forzado and gap is None


# ---------------------------------------------------------------------------
# Demanda por barra, paso y combinación
# ---------------------------------------------------------------------------


def test_demanda_tres_estaciones_avisa_con_el_cb_que_se_descarta():
    avisos: list[str] = []
    demanda = _demanda("V3", "U1", _filas(_xs(3)), L, avisos)
    assert demanda["Cb"] == 1.0
    assert len(avisos) == 1
    assert avisos[0] == (
        "Barra 'V3', combinación 'U1': solo 3 estaciones de salida en SAP2000 (separación máxima 3,00 m > L/10): "
        "Cb = 1,0 en lugar del Cb interpolado (1,316). Asigne más estaciones (Assign > Frame > Output Stations, "
        "p. ej. 11 secciones o segmento máximo 0,5 m) y vuelva a traer fuerzas."
    )


def test_demanda_once_estaciones_conserva_cb_interpolado_con_su_aviso():
    avisos: list[str] = []
    demanda = _demanda("V3", "U1", _filas(_xs(11)), L, avisos)
    assert demanda["Cb"] == pytest.approx(_cb_once_estaciones(), rel=1e-12)
    assert any("Cb interpolado entre estaciones" in w for w in avisos)
    assert not any("solo " in w for w in avisos)


def test_demanda_trece_estaciones_sin_avisos_de_cb():
    avisos: list[str] = []
    demanda = _demanda("V3", "U1", _filas(_xs(13)), L, avisos)
    assert demanda["Cb"] == pytest.approx(12.5 / 11, rel=1e-12)
    assert avisos == []


def test_varios_pasos_el_mas_bajo_gobierna_y_avisa_solo_el_forzado():
    filas = _filas(_xs(3), step="A") + _filas(_xs(13), step="B")
    avisos: list[str] = []
    demanda = _demanda("V3", "U1", filas, L, avisos)
    assert demanda["Cb"] == 1.0  # mínimo entre el paso A (forzado) y el B (exacto, 1,136)
    assert len([w for w in avisos if "solo 3 estaciones" in w]) == 1
    assert not any("solo 13" in w for w in avisos)

    solo_b: list[str] = []
    assert _demanda("V3", "U1", _filas(_xs(13), step="B"), L, solo_b)["Cb"] == pytest.approx(12.5 / 11)
    assert solo_b == []


def test_longitud_no_medida_desactiva_la_regla():
    avisos: list[str] = []
    demanda = _demanda("V3", "U1", _filas(_xs(3)), L, avisos, regla_separacion=False)
    assert demanda["Cb"] == pytest.approx(_cb_tres_estaciones(), rel=1e-12)
    assert any("Cb interpolado entre estaciones" in w for w in avisos)
    assert not any("solo 3" in w for w in avisos)


# ---------------------------------------------------------------------------
# Lectura completa con el COM falso: varias combinaciones y envolvente
# ---------------------------------------------------------------------------


def test_leer_fuerzas_tres_estaciones_da_cb_1_0_y_envolvente_lo_refleja(barra_v3):
    barra_v3({"U1": _filas_fake(_xs(3))})
    res = leer_fuerzas(FakeAcero(), ["V3"], ["U1"])
    assert res["demandas"][0]["Cb"] == 1.0
    assert res["envolvente"][0]["Cb"] == 1.0
    assert res["envolvente"][0]["gobierna"]["Cb"] == "U1"
    assert len(_avisos(res, "solo 3 estaciones")) == 1


def test_leer_fuerzas_envolvente_toma_el_1_0_forzado_aunque_otra_combinacion_sea_exacta(barra_v3):
    barra_v3({"U1": _filas_fake(_xs(3)), "U2": _filas_fake(_xs(13))})
    res = leer_fuerzas(FakeAcero(), ["V3"], ["U1", "U2"])
    por_combo = {d["combo"]: d["Cb"] for d in res["demandas"]}
    assert por_combo["U1"] == 1.0
    assert por_combo["U2"] == pytest.approx(12.5 / 11, rel=1e-12)
    env = res["envolvente"][0]
    assert env["Cb"] == 1.0 and env["gobierna"]["Cb"] == "U1"
    assert len(_avisos(res, "solo 3 estaciones")) == 1
    assert not _avisos(res, "solo 13")


def test_leer_fuerzas_once_estaciones_cb_interpolado_sin_forzar(barra_v3):
    barra_v3({"U1": _filas_fake(_xs(11))})
    res = leer_fuerzas(FakeAcero(), ["V3"], ["U1"])
    assert res["demandas"][0]["Cb"] == pytest.approx(_cb_once_estaciones(), rel=1e-12)
    assert _avisos(res, "Cb interpolado entre estaciones")
    assert not _avisos(res, "solo 11")


def test_leer_fuerzas_momento_nulo_no_da_cb_ni_error(barra_v3):
    barra_v3({"U1": [("", x, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0) for x in _xs(3)]})
    res = leer_fuerzas(FakeAcero(), ["V3"], ["U1"])
    assert res["demandas"][0]["Cb"] is None
    assert res["envolvente"][0]["Cb"] is None
    assert not _avisos(res, "solo ")


def test_leer_fuerzas_sin_longitud_medida_mantiene_el_comportamiento_anterior(barra_v3, monkeypatch):
    """Si SAP2000 no da la longitud, Cb usa la distancia entre estaciones y la regla no se aplica."""
    barra_v3({"U1": _filas_fake(_xs(3))})
    monkeypatch.setattr(fuerzas, "_longitud_m", lambda *a, **k: None)
    res = leer_fuerzas(FakeAcero(), ["V3"], ["U1"])
    assert res["demandas"][0]["Cb"] == pytest.approx(_cb_tres_estaciones(), rel=1e-12)
    assert _avisos(res, "Cb interpolado entre estaciones")
    assert _avisos(res, "longitud no disponible")
    assert not _avisos(res, "solo 3")
