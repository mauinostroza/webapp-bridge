"""Reacciones de apoyo en ejes globales (``bridge/reacciones_globales.py``) y las rutas que las usan."""

from __future__ import annotations

import math

import pytest

from bridge import reacciones_globales as rg

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


class _Puntos:
    def __init__(self, ejes):
        self.ejes = ejes
        self.llamadas: list[str] = []

    def GetLocalAxes(self, nodo, *_a):
        self.llamadas.append(nodo)
        valor = self.ejes[nodo]
        if isinstance(valor, Exception):
            raise valor
        return valor


class _Modelo:
    def __init__(self, ejes):
        self.PointObj = _Puntos(ejes)


def _fila(nodo="1", paso="", f=(10.0, 0.0, 5.0), m=(0.0, 0.0, 2.0)):
    return {
        "joint": nodo,
        "step_type": paso,
        "f1": f[0],
        "f2": f[1],
        "f3": f[2],
        "m1": m[0],
        "m2": m[1],
        "m3": m[2],
    }


# --- la matriz (convención aislada) -------------------------------------------------------------------------------------


def test_matriz_sin_giro_es_la_identidad():
    m = rg.matriz_local_a_global(0, 0, 0)
    assert m == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def test_giro_a_alrededor_de_z():
    m = rg.matriz_local_a_global(30, 0, 0)
    assert rg._rotar(m, (1.0, 0.0, 0.0)) == pytest.approx((C30, S30, 0.0))  # eje local 1 en globales
    assert rg._rotar(m, (0.0, 0.0, 1.0)) == pytest.approx((0.0, 0.0, 1.0))


def test_giro_c_alrededor_del_eje_1():
    m = rg.matriz_local_a_global(0, 0, 40)
    c, s = math.cos(math.radians(40)), math.sin(math.radians(40))
    assert rg._rotar(m, (1.0, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 0.0))
    assert rg._rotar(m, (0.0, 1.0, 0.0)) == pytest.approx((0.0, c, s))


def test_giros_sucesivos_son_una_rotacion_ortonormal():
    m = rg.matriz_local_a_global(30, 20, 10)
    for i in range(3):
        for j in range(3):
            punto = sum(m[i][k] * m[j][k] for k in range(3))
            assert punto == pytest.approx(1.0 if i == j else 0.0, abs=1e-12)
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    assert det == pytest.approx(1.0)


# --- la conversión -------------------------------------------------------------------------------------------------------


def test_nodo_sin_giro_no_cambia_nada():
    filas = [_fila("1")]
    antes = [dict(f) for f in filas]
    res = rg.reacciones_a_globales(_Modelo({"1": (0.0, 0.0, 0.0, False, 0)}), filas)
    assert filas == antes
    assert res.campos() == {"reactions_frame": "global", "joints_rotated": [], "joints_axes_unresolved": []}
    assert res.warnings == []


def test_a_30_grados_convierte_fuerzas_y_momentos_a_globales():
    filas = [_fila("1", f=(10.0, 0.0, 5.0), m=(0.0, 4.0, 2.0))]
    res = rg.reacciones_a_globales(_Modelo({"1": (30.0, 0.0, 0.0, False, 0)}), filas)
    f = filas[0]
    assert (f["f1"], f["f2"], f["f3"]) == pytest.approx((10 * C30, 10 * S30, 5.0))
    assert (f["m1"], f["m2"], f["m3"]) == pytest.approx((-4 * S30, 4 * C30, 2.0))
    assert res.joints_rotated == ["1"] and res.joints_axes_unresolved == []
    assert any("convertidas a ejes globales" in w and "pendiente de verificar" in w for w in res.warnings)


def test_c_distinto_de_cero_gira_alrededor_del_eje_1_local():
    filas = [_fila("1", f=(0.0, 10.0, 0.0), m=(0.0, 0.0, 0.0))]
    rg.reacciones_a_globales(_Modelo({"1": (0.0, 0.0, 40.0, False, 0)}), filas)
    c, s = math.cos(math.radians(40)), math.sin(math.radians(40))
    assert (filas[0]["f1"], filas[0]["f2"], filas[0]["f3"]) == pytest.approx((0.0, 10 * c, 10 * s))


def test_la_conversion_conserva_la_magnitud_de_la_resultante():
    filas = [_fila("1", f=(3.0, 4.0, 12.0), m=(1.0, 2.0, 2.0))]
    rg.reacciones_a_globales(_Modelo({"1": (30.0, 20.0, 10.0, False, 0)}), filas)
    f = filas[0]
    assert math.hypot(f["f1"], f["f2"], f["f3"]) == pytest.approx(13.0)
    assert math.hypot(f["m1"], f["m2"], f["m3"]) == pytest.approx(3.0)


def test_nodo_avanzado_queda_igual_y_se_avisa():
    filas = [_fila("1")]
    antes = dict(filas[0])
    res = rg.reacciones_a_globales(_Modelo({"1": (30.0, 0.0, 0.0, True, 0)}), filas)
    assert filas[0] == antes
    assert res.joints_axes_unresolved == ["1"] and res.joints_rotated == []
    assert any("No se pudieron convertir" in w and "1" in w for w in res.warnings)


@pytest.mark.parametrize("ejes", [RuntimeError("sin ejes"), (1, 0), None])
def test_lectura_de_ejes_fallida_no_adivina(ejes):
    filas = [_fila("1")]
    antes = dict(filas[0])
    res = rg.reacciones_a_globales(_Modelo({"1": ejes}), filas)
    assert filas[0] == antes
    assert res.joints_axes_unresolved == ["1"]


def test_espectral_max_min_se_convierte_y_avisa():
    filas = [_fila("1", paso="Max"), _fila("1", paso="Min"), _fila("1", paso="")]
    res = rg.reacciones_a_globales(_Modelo({"1": (30.0, 0.0, 0.0, False, 0)}), filas)
    assert filas[0]["f1"] == pytest.approx(10 * C30)  # se convierte igual
    assert any("Max/Min" in w and "2 filas" in w for w in res.warnings)


def test_solo_se_leen_los_ejes_de_los_nodos_con_filas_y_una_vez():
    m = _Modelo({"1": (0.0, 0.0, 0.0, False, 0), "2": (0.0, 0.0, 0.0, False, 0)})
    rg.reacciones_a_globales(m, [_fila("1"), _fila("1", paso="Max")])
    assert m.PointObj.llamadas == ["1"]


def test_claves_propias_de_cada_ruta():
    filas = [{"node": "1", "step_type": "", "Vx": 10.0, "Vy": 0.0, "P": 5.0, "Mx": 0.0, "My": 0.0, "Mz": 2.0}]
    rg.reacciones_a_globales(
        _Modelo({"1": (30.0, 0.0, 0.0, False, 0)}),
        filas,
        clave_nodo="node",
        claves_fuerza=("Vx", "Vy", "P"),
        claves_momento=("Mx", "My", "Mz"),
    )
    assert (filas[0]["Vx"], filas[0]["Vy"], filas[0]["P"]) == pytest.approx((10 * C30, 10 * S30, 5.0))


# --- cada ruta afectada devuelve los campos nuevos -------------------------------------------------------------------------


def test_zapata_biaxial_convierte_el_nodo_girado_y_devuelve_los_campos():
    from bridge import zapata_biaxial as zb
    from tests.test_bridge_zapata_biaxial import FakeZapata

    sin = zb.reacciones(FakeZapata(), ["1", "2"], ["DEAD"], [])
    con = zb.reacciones(FakeZapata(giro=("2",)), ["1", "2"], ["DEAD"], [])
    assert sin["reactions_frame"] == "global" and sin["joints_rotated"] == []
    assert con["joints_rotated"] == ["2"] and con["joints_axes_unresolved"] == []
    assert con["nodos_ejes_locales"] == ["2"]
    assert any("convertidas a ejes globales" in a for a in con["warnings"])
    # el nodo 1 no cambia y el 2 sí (Vx, Vy)
    fila1_sin, fila1_con = sin["data"][0], con["data"][0]
    assert fila1_sin == fila1_con
    vx, vy = sin["data"][1][4], sin["data"][1][5]
    assert con["data"][1][4] == pytest.approx(vx * C30 - vy * S30)
    assert con["data"][1][5] == pytest.approx(vx * S30 + vy * C30)


def test_snapshot_y_viewer_foundations_devuelven_los_campos_y_convierten(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model
    from bridge.viewer import foundations as fund
    from tests.test_bridge_viewer_foundations import FakeFund

    model = FakeFund()
    model.PointObj.GetLocalAxes = lambda n, *a: (30.0 if n == "1" else 0.0, 0.0, 0.0, False, 0)
    datos = _snapshot_from_model(
        model, groups=[{"id": "G", "name": "G", "nodes": ["1", "2"], "shells": []}], joints=["1", "2"]
    )
    assert datos["reactions_frame"] == "global" and datos["joints_rotated"] == ["1"]
    assert any("convertidas" in w for w in datos["warnings"])
    por_nodo = {r["joint"]: r for r in datos["reactions"]}
    assert por_nodo["1"]["f1"] == pytest.approx(
        1.0 * C30 - 1.0 * S30
    )  # el simulado da 1.0 en todas las componentes
    assert por_nodo["2"]["f1"] == pytest.approx(1.0)
    res = fund.resultados(model, [{"name": "F1", "joints": ["1", "2"], "shells": []}], ["DEAD"], None)
    assert (
        res["reactions_frame"] == "global"
        and res["joints_rotated"] == ["1"]
        and res["joints_axes_unresolved"] == []
    )


def test_str2fund_source_convierte_el_apoyo_girado_y_devuelve_los_campos():
    from bridge.str2fund import extract_source
    from tests.test_bridge_str2fund import SourceModel

    model = SourceModel()
    model.PointObj.GetLocalAxes = lambda n, *a: (30.0, 0.0, 0.0, False, 0)
    res = extract_source(model, ["10"], ["DEAD"])
    assert res["reactions_frame"] == "global" and res["joints_rotated"] == ["10"]
    fila = res["source_snapshot"]["reactions"][0]
    assert (fila["f1"], fila["f2"], fila["f3"]) == pytest.approx(
        (10 * C30 - 20 * S30, 10 * S30 + 20 * C30, 30.0)
    )
    assert (fila["m1"], fila["m2"], fila["m3"]) == pytest.approx((1 * C30 - 2 * S30, 1 * S30 + 2 * C30, 3.0))
    assert any("convertidas a ejes globales" in w for w in res["warnings"])


def test_str2fund_source_con_ejes_ilegibles_deja_las_filas_y_lista_el_nodo():
    from bridge.str2fund import extract_source
    from tests.test_bridge_str2fund import SourceModel

    model = SourceModel()

    def sin_ejes(n, *a):
        raise RuntimeError("no legible")

    model.PointObj.GetLocalAxes = sin_ejes
    res = extract_source(model, ["10"], ["DEAD"])
    assert res["joints_axes_unresolved"] == ["10"] and res["joints_rotated"] == []
    fila = res["source_snapshot"]["reactions"][0]
    assert (fila["f1"], fila["f2"], fila["f3"]) == (10.0, 20.0, 30.0)
    assert any("No se pudieron convertir" in w for w in res["warnings"])


def test_str2fund_sin_giro_no_cambia_el_hash_de_la_fuente():
    from bridge.str2fund import extract_source
    from tests.test_bridge_str2fund import SourceModel

    model = SourceModel()
    model.PointObj.GetLocalAxes = lambda n, *a: (0.0, 0.0, 0.0, False, 0)
    res = extract_source(model, ["10"], ["DEAD"])
    assert res["joints_rotated"] == [] and "warnings" not in res
    assert res["source_snapshot"]["reactions"][0]["f1"] == 10.0
