"""Tests de bridge/acero_aisc360_fuerzas.py: fuerzas de barras de SAP2000 y demandas AISC 360-22.

COM falso con el mismo patrón que tests/test_bridge_hormigon.py. Las unidades cambian de
verdad (las coordenadas y las estaciones salen en pies si el modelo está en kip-ft y en m
si está en kN-m), así que la restauración y la conversión se comprueban de extremo a extremo.
El orden del código de retorno se prueba con el doble de tests/test_bridge_esbelteces.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.acero_aisc360_fuerzas import (
    EJE_FUERTE,
    VALIDADO_CON,
    VALIDADO_FECHA,
    SapAceroFuerzasError,
    _avisar_filas,
    _valor_unico,
    cb_f1,
    leer_fuerzas,
    seleccion_acero,
)
from bridge.actions import ActionValidationError, install_routes
from tests.test_bridge_contracts import assert_matches_contract
from tests.test_bridge_esbelteces import _RetFirst

PIE = 0.3048
KN_M = 6
KIP_FT = 4

# Estaciones (m): (paso, x, P, V2, V3, T, M2, M3) con P de tracción positiva.


def _viga(P: float, escala: float = 1.0):
    """Viga simple L=6 m, carga uniforme w=10 kN/m: M3 = 5x(6-x), V2 = 10(3-x)."""
    xs = (0.0, 1.5, 3.0, 4.5, 6.0)
    return [("", x, P, 10.0 * (3.0 - x) * escala, 0.0, 0.0, 0.0, 5.0 * x * (6.0 - x) * escala) for x in xs]


DATA = {
    "V1": {"U1": _viga(-50.0), "U2": _viga(30.0, 2.0)},
    # Voladizo L=3 m con carga puntual 10 kN en el extremo libre: M3 = 10(3-x), V2 = -10.
    "CANT": {
        "U1": [("", x, 0.0, -10.0, 0.0, 0.0, 0.0, 10.0 * (3.0 - x)) for x in (0.0, 0.75, 1.5, 2.25, 3.0)]
    },
    # Momento constante 10 kN·m.
    "MOM": {"U1": [("", x, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0) for x in (0.0, 1.5, 3.0, 4.5, 6.0)]},
}
ENDPOINTS = {"V1": ((0.0, 0.0, 0.0), (6.0, 0.0, 0.0)), "CANT": ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0))}
ENDPOINTS["MOM"] = ((0.0, 1.0, 0.0), (6.0, 1.0, 0.0))
SECCION = {"V1": "W1", "CANT": "W2", "MOM": "W1"}


class _Setup:
    def __init__(self, outer):
        self.outer = outer

    def DeselectAllCasesAndCombosForOutput(self):
        self.outer.selected_combos = []
        return 0

    def SetComboSelectedForOutput(self, name, *rest):
        self.outer.selected_combos.append(name)
        return 0

    def SetCaseSelectedForOutput(self, name, *rest):
        self.outer.selected_combos.append(name)
        return 0


class _Results:
    def __init__(self, outer):
        self.outer = outer
        self.Setup = _Setup(outer)

    def FrameForce(self, barra, *args):
        outer = self.outer
        outer.ff_firmas.append(len(args))
        if barra == outer.fail_frame:
            raise RuntimeError("boom")
        # Firma pywin32 de SAP2000 23.3.1: la larga (17 argumentos + nombre) no existe; la corta sí.
        if len(args) > 1 and not outer.firma_larga:
            raise TypeError("FrameForce() takes from 1 to 17 positional arguments but 18 were given")
        if len(args) == 1 and outer.corta_falla:
            raise RuntimeError("corta no disponible")
        if barra not in DATA:
            return (0, (), (), (), (), (), (), (), (), (), (), (), (), (), 0)
        combos = ("U1", "U2") if outer.ignora_seleccion else outer.selected_combos
        filas = [(combo, fila) for combo in combos for fila in DATA[barra].get(combo, [])]
        escala = outer.escala_longitud()
        n = len(filas)
        z = (0.0,) * n
        return (
            n,
            (barra,) * n,
            tuple(f[1] * escala for _, f in filas),
            ("",) * n,
            z,
            tuple(c for c, _ in filas),
            tuple(f[0] for _, f in filas),
            z,
            tuple(f[2] for _, f in filas),
            tuple(f[3] for _, f in filas),
            tuple(f[4] for _, f in filas),
            tuple(f[5] for _, f in filas),
            tuple(f[6] for _, f in filas),
            tuple(f[7] for _, f in filas),
            0,
        )


class _FrameObj:
    def __init__(self, outer):
        self.outer = outer

    def GetNameList(self, *a):
        return (len(DATA), tuple(ENDPOINTS), 0)

    def GetPoints(self, name, *a):
        return (f"{name}_I", f"{name}_J", 0)

    def GetSection(self, name, *a):
        if name in self.outer.sin_seccion:
            return ("", "", 0)
        return (SECCION[name], "", 0)

    def SetSelected(self, *a):
        return 0


class _PointObj:
    def __init__(self, outer):
        self.outer = outer

    def GetCoordCartesian(self, point, *a):
        nombre, extremo = point.rsplit("_", 1)
        punto = ENDPOINTS[nombre][0 if extremo == "I" else 1]
        escala = self.outer.escala_longitud()
        x, y, z = punto
        return (x * escala, y * escala, z * escala, 0)


class _SelectObj:
    def __init__(self, outer):
        self.outer = outer

    def GetSelected(self, *a):
        nombres = self.outer.seleccion
        return (len(nombres), (2,) * len(nombres), tuple(nombres), 0)


class FakeAcero:
    def __init__(
        self,
        *,
        fail_restore: bool = False,
        fail_frame: str | None = None,
        seleccion: tuple[str, ...] = (),
        sin_seccion: tuple[str, ...] = (),
        unidades: int = KIP_FT,
        tupla_unidades: bool = False,
        firma_larga: bool = False,
        corta_falla: bool = False,
        ignora_seleccion: bool = False,
    ):
        # Por defecto kip-ft (distinto de kN-m) para probar la restauración; 12 = Ton_m_C.
        self.units = unidades
        self.tupla_unidades = tupla_unidades
        self.firma_larga = firma_larga  # ¿acepta la firma larga? (comtypes)
        self.corta_falla = corta_falla  # ¿falla la firma corta? (obliga al respaldo)
        self.ignora_seleccion = ignora_seleccion  # SAP2000 devuelve también casos no seleccionados
        self.ff_firmas: list[int] = []  # nº de argumentos de cada FrameForce
        self.unit_history: list[int] = []
        self.fail_restore = fail_restore
        self.fail_frame = fail_frame
        self.seleccion = list(seleccion)
        self.sin_seccion = sin_seccion
        self.selected_combos: list[str] = []
        self.Results = _Results(self)
        self.FrameObj = _FrameObj(self)
        self.PointObj = _PointObj(self)
        self.SelectObj = _SelectObj(self)
        self.RespCombo = _Lista(("U1", "U2"))
        self.LoadCases = _Lista(("DEAD",))
        self.LoadPatterns = _Lista(("DEAD", "U1"))

    def escala_longitud(self) -> float:
        return 1.0 / PIE if self.units == KIP_FT else 1.0

    def GetPresentUnits(self):
        return (0, self.units) if self.tupla_unidades else self.units

    def SetPresentUnits(self, value):
        self.unit_history.append(value)
        if self.fail_restore and value == KIP_FT:
            raise RuntimeError("sin permiso para cambiar unidades")
        self.units = value
        return 0


class _Lista:
    """Doble de ``GetNameList`` con la forma comtypes (count, nombres, ret)."""

    def __init__(self, nombres):
        self.nombres = tuple(nombres)

    def GetNameList(self, *a):
        return (len(self.nombres), self.nombres, 0)


def _traduce(fake):
    """Doble que devuelve el código de retorno al inicio (forma pywin32)."""
    return _RetFirst(fake)


# ---------------------------------------------------------------------------
# Cb (AISC 360-22 Ec. F1-1) sin COM
# ---------------------------------------------------------------------------


def test_cb_viga_uniforme_teorico():
    xs = [0.0, 1.5, 3.0, 4.5, 6.0]
    ms = [5.0 * x * (6.0 - x) for x in xs]
    cb, interpolado = cb_f1(xs, ms, 6.0)
    assert cb == pytest.approx(50.0 / 44.0)  # 1.136
    assert not interpolado


def test_cb_voladizo_y_momento_uniforme():
    xs = [0.0, 0.75, 1.5, 2.25, 3.0]
    cb, _ = cb_f1(xs, [10.0 * (3.0 - x) for x in xs], 3.0)
    assert cb == pytest.approx(5.0 / 3.0)  # 12.5·30 / (75 + 67.5 + 60 + 22.5)
    cb_constante, _ = cb_f1(xs, [10.0] * 5, 3.0)
    assert cb_constante == pytest.approx(1.0)


def test_cb_sin_estaciones_en_cuartos_interpola_y_avisa():
    xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    cb, interpolado = cb_f1(xs, [5.0 * x * (6.0 - x) for x in xs], 6.0)
    assert interpolado
    assert cb == pytest.approx(562.5 / 487.5)  # MA y MC interpolados: 32.5 en lugar de 33.75


def test_cb_momento_nulo_no_se_calcula():
    cb, _ = cb_f1([0.0, 1.0], [0.0, 0.0], 1.0)
    assert cb is None


# ---------------------------------------------------------------------------
# Lectura y demandas
# ---------------------------------------------------------------------------


def test_viga_uniforme_demandas_teoricas_y_unidades_restauradas():
    model = FakeAcero()
    res = leer_fuerzas(model, ["V1"], ["U1"])
    assert res["unidades"] == "kN-m" and res["ejes"] == "locales 1-2-3"
    frame = res["frames"][0]
    assert frame["frame"] == "V1" and frame["seccion"] == "W1"
    assert frame["longitud_m"] == pytest.approx(6.0)
    demanda = res["demandas"][0]
    assert demanda["combo"] == "U1"
    assert demanda["Pu_kN"] == pytest.approx(50.0)
    assert demanda["Tu_kN"] == 0.0
    assert demanda["Mux_kNm"] == pytest.approx(45.0)  # wL²/8
    assert demanda["Vux_kN"] == pytest.approx(30.0)  # wL/2
    assert demanda["Muy_kNm"] == 0.0 and demanda["Vuy_kN"] == 0.0
    assert demanda["x_Mmax_m"] == pytest.approx(3.0)
    assert demanda["Cb"] == pytest.approx(50.0 / 44.0)
    # Restauración: forzó kN-m y volvió a kip-ft.
    assert model.unit_history == [KN_M, KIP_FT]
    assert model.units == KIP_FT
    assert not any("restaurar" in w for w in res["warnings"])


def test_eje_fuerte_validado_con_sap_y_sin_hipotesis():
    assert EJE_FUERTE == "M3"
    res = leer_fuerzas(FakeAcero(), ["V1"], ["U1"])
    assert res["validado_con_sap"] is True
    assert res["validado_con"] == VALIDADO_CON == "SAP2000 23.3.1 (pywin32), sonda v3"
    assert res["validado_fecha"] == VALIDADO_FECHA == "2026-10-14"
    assert not any("HIPÓTESIS" in w or "sin validar" in w for w in res["warnings"])
    assert any("Lb" in w for w in res["warnings"])
    assert any("conservadora" in w for w in res["warnings"])


def test_voladizo_y_momento_constante_en_la_respuesta():
    res = leer_fuerzas(FakeAcero(), ["CANT", "MOM"], ["U1"])
    por_barra = {d["frame"]: d for d in res["demandas"]}
    assert por_barra["CANT"]["Cb"] == pytest.approx(5.0 / 3.0)
    assert por_barra["CANT"]["Mux_kNm"] == pytest.approx(30.0)
    assert por_barra["CANT"]["Vux_kN"] == pytest.approx(10.0)
    assert por_barra["CANT"]["Pu_kN"] == 0.0
    assert por_barra["MOM"]["Cb"] == pytest.approx(1.0)
    assert por_barra["MOM"]["Mux_kNm"] == pytest.approx(10.0)


def test_envolvente_por_componente_indica_combinacion_que_gobierna():
    res = leer_fuerzas(FakeAcero(), ["V1"], ["U1", "U2"])
    env = res["envolvente"][0]
    assert env["Pu_kN"] == pytest.approx(50.0) and env["gobierna"]["Pu_kN"] == "U1"
    assert env["Tu_kN"] == pytest.approx(30.0) and env["gobierna"]["Tu_kN"] == "U2"
    assert env["Mux_kNm"] == pytest.approx(90.0) and env["gobierna"]["Mux_kNm"] == "U2"
    assert env["Vux_kN"] == pytest.approx(60.0) and env["gobierna"]["Vux_kN"] == "U2"
    assert env["Cb"] == pytest.approx(50.0 / 44.0, rel=1e-9)
    assert len(res["demandas"]) == 2  # una demanda por barra y combinación
    assert {c["combo"] for c in res["frames"][0]["combinaciones"]} == {"U1", "U2"}


def test_estaciones_filtran_la_salida_sin_cambiar_las_demandas():
    res = leer_fuerzas(FakeAcero(), ["V1"], ["U1"], estaciones=[0.0, 0.5])
    estaciones = res["frames"][0]["combinaciones"][0]["estaciones"]
    assert [e["x_m"] for e in estaciones] == pytest.approx([0.0, 3.0])
    assert res["demandas"][0]["Mux_kNm"] == pytest.approx(45.0)


def test_error_de_lectura_restaura_las_unidades():
    model = FakeAcero(fail_frame="V1")
    with pytest.raises(RuntimeError, match="boom"):
        leer_fuerzas(model, ["V1"], ["U1"])
    assert model.units == KIP_FT


def test_fallo_al_restaurar_unidades_se_avisa():
    model = FakeAcero(fail_restore=True)
    res = leer_fuerzas(model, ["V1"], ["U1"])
    assert any("restaurar las unidades" in w for w in res["warnings"])
    assert model.units == KN_M  # la restauración no ocurrió: se avisa, no se oculta


def test_barra_inexistente_se_omite_con_aviso():
    res = leer_fuerzas(FakeAcero(), ["V1", "NOEXISTE"], ["U1"])
    assert [f["frame"] for f in res["frames"]] == ["V1"]
    assert any("NOEXISTE" in w for w in res["warnings"])


def test_ninguna_barra_existe_es_error():
    with pytest.raises(SapAceroFuerzasError):
        leer_fuerzas(FakeAcero(), ["NOEXISTE"], ["U1"])


def test_combinacion_inexistente_es_error():
    with pytest.raises(SapAceroFuerzasError, match="NOPE"):
        leer_fuerzas(FakeAcero(), ["V1"], ["NOPE"])


def test_codigo_de_retorno_al_inicio_da_el_mismo_resultado():
    normal = leer_fuerzas(FakeAcero(), ["V1", "CANT"], ["U1", "U2"])
    invertido = leer_fuerzas(_traduce(FakeAcero()), ["V1", "CANT"], ["U1", "U2"])
    assert invertido == normal


def test_seleccion_acero_lee_seccion_y_longitud():
    model = FakeAcero(seleccion=("V1", "MOM"))
    barras = seleccion_acero(model)
    assert barras[0] == {"frame": "V1", "seccion": "W1", "longitud_m": pytest.approx(6.0)}
    assert barras[1]["frame"] == "MOM" and barras[1]["longitud_m"] == pytest.approx(6.0)
    assert model.units == KIP_FT  # la selección no cambia las unidades


# ---------------------------------------------------------------------------
# Rutas (mismo envoltorio ``call`` que bridge.actions)
# ---------------------------------------------------------------------------


class _FakeActor:
    def call(self, fn):
        return fn()


class _FakeSession:
    def __init__(self, model):
        self.actor = _FakeActor()
        self.model = model

    def _model(self):
        return self.model


@pytest.fixture
def client_factory():
    def make(model):
        app = FastAPI()
        install_routes(app, _FakeSession(model), RuntimeError)
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


@pytest.fixture(scope="module")
def contracts():
    import json
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    return json.loads((raiz / "web" / "src" / "bridge_contracts.json").read_text("utf-8"))["routes"]


def test_ruta_combos_y_contrato(client_factory, contracts):
    response = client_factory(FakeAcero()).get("/v1/sap/actions/acero-aisc360/combos")
    assert response.status_code == 200
    body = response.json()
    assert body == {"combinaciones": ["U1", "U2"], "casos": ["DEAD"]}
    assert_matches_contract(contracts, "GET /v1/sap/actions/acero-aisc360/combos", body)


def test_ruta_selected_y_contrato(client_factory, contracts):
    response = client_factory(FakeAcero(seleccion=("V1",))).get("/v1/sap/actions/acero-aisc360/selected")
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "GET /v1/sap/actions/acero-aisc360/selected", body)
    assert body["frames"][0]["frame"] == "V1"


def test_ruta_selected_sin_seleccion_es_409(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": "selected", "combos": ["U1"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "sap_error"


def test_ruta_fuerzas_y_contrato(client_factory, contracts):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": ["V1"], "combos": ["U1"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "POST /v1/sap/actions/acero-aisc360/fuerzas", body)
    assert body["demandas"][0]["Mux_kNm"] == pytest.approx(45.0)


def test_ruta_fuerzas_seleccion_del_modelo(client_factory):
    response = client_factory(FakeAcero(seleccion=("MOM",))).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": "selected", "combos": ["U1"]}
    )
    assert response.status_code == 200
    assert [f["frame"] for f in response.json()["frames"]] == ["MOM"]


def test_ruta_501_barras_es_422(client_factory):
    nombres = [f"B{i}" for i in range(501)]
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": nombres, "combos": ["U1"]}
    )
    assert response.status_code == 422


def test_ruta_500_barras_no_es_error_de_limite(client_factory):
    nombres = [f"B{i}" for i in range(500)]
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": nombres, "combos": ["U1"]}
    )
    # Ninguna existe en el modelo falso: el límite no rechaza, la lectura sí.
    assert response.status_code == 409


def test_ruta_combos_vacios_es_422(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": ["V1"], "combos": []}
    )
    assert response.status_code == 422


def test_ruta_mas_de_300_combos_es_422(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas",
        json={"frames": ["V1"], "combos": [f"C{i}" for i in range(301)]},
    )
    assert response.status_code == 422


def test_ruta_estaciones_fuera_de_rango_es_422(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas",
        json={"frames": ["V1"], "combos": ["U1"], "estaciones": [1.5]},
    )
    assert response.status_code == 422


def test_ruta_barra_inexistente_responde_con_aviso(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": ["V1", "NOEXISTE"], "combos": ["U1"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert [f["frame"] for f in body["frames"]] == ["V1"]
    assert any("NOEXISTE" in w for w in body["warnings"])


def test_ruta_combinacion_inexistente_es_409(client_factory):
    response = client_factory(FakeAcero()).post(
        "/v1/sap/actions/acero-aisc360/fuerzas", json={"frames": ["V1"], "combos": ["NOPE"]}
    )
    assert response.status_code == 409


def test_validacion_directa_de_combos_vacios():
    with pytest.raises(ActionValidationError):
        leer_fuerzas(FakeAcero(), ["V1"], [])


# ---------------------------------------------------------------------------
# Firma de FrameForce, retornos escalares y unidades originales (pywin32, SAP2000 23.3.1)
# ---------------------------------------------------------------------------


def test_frame_force_usa_la_firma_corta_y_no_intenta_la_larga():
    model = FakeAcero()  # pywin32: la larga lanza TypeError; la corta funciona
    res = leer_fuerzas(model, ["V1"], ["U1"])
    assert model.ff_firmas == [1]  # una sola llamada, firma corta (nombre + ItemTypeElm)
    assert res["demandas"][0]["Mux_kNm"] == pytest.approx(45.0)


def test_firma_larga_solo_si_la_corta_falla():
    normal = leer_fuerzas(FakeAcero(), ["V1"], ["U1"])
    model = FakeAcero(corta_falla=True, firma_larga=True)  # comtypes: la corta falla, la larga sí
    assert leer_fuerzas(model, ["V1"], ["U1"]) == normal
    assert model.ff_firmas == [1, 15]


def test_corta_y_larga_fallan_da_error_con_ambas_causas():
    with pytest.raises(SapAceroFuerzasError, match="firma corta"):
        leer_fuerzas(FakeAcero(corta_falla=True), ["V1"], ["U1"])


@pytest.mark.parametrize("tupla", [False, True])
def test_unidades_originales_12_se_restauran_con_retorno_escalar_o_tupla(tupla):
    model = FakeAcero(unidades=12, tupla_unidades=tupla)  # 12 = Ton_m_C, el modelo del usuario
    res = leer_fuerzas(model, ["V1"], ["U1"])
    assert model.unit_history == [6, 12]
    assert model.units == 12
    assert res["demandas"][0]["Cb"] == pytest.approx(12.5 / 11)  # viga uniforme (1.136), en metros
    assert not any("restaurar" in w for w in res["warnings"])


def test_seleccion_acero_acepta_retorno_de_unidades_en_tupla():
    model = FakeAcero(unidades=12, tupla_unidades=True, seleccion=("V1",))
    assert seleccion_acero(model)[0]["longitud_m"] == pytest.approx(6.0)
    assert model.units == 12


def test_filas_de_casos_no_pedidos_se_descartan_con_aviso():
    normal = leer_fuerzas(FakeAcero(), ["V1"], ["U1"])
    model = FakeAcero(ignora_seleccion=True)  # SAP2000 devuelve también U2, que no se pidió
    res = leer_fuerzas(model, ["V1"], ["U1"])
    assert res["demandas"] == normal["demandas"]
    assert [f["combo"] for f in res["frames"][0]["combinaciones"]] == ["U1"]
    assert any("5 filas de casos o combinaciones no pedidos" in w for w in res["warnings"])


def test_filas_por_combinacion_distintas_se_avisan():
    filas = [
        {"combo": "U1", "step": "", "x": x, "P": 0.0, "V2": 0.0, "V3": 0.0, "T": 0.0, "M2": 0.0, "M3": 1.0}
        for x in (0.0, 1.0, 2.0)
    ]
    filas += [dict(f, combo="U2") for f in filas[:2]]
    avisos: list[str] = []
    _avisar_filas("V1", filas, 0, ["U1", "U2"], avisos)
    assert any("número de filas distinto" in w for w in avisos)
    sin_aviso: list[str] = []
    _avisar_filas("V1", filas[:3] + [dict(f, combo="U2") for f in filas[:3]], 0, ["U1", "U2"], sin_aviso)
    assert sin_aviso == []


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(12, 12), ((0, 12), 12), ((12, 0), 12), ((True, 0), True), ((0, False), False)],
)
def test_valor_unico_acepta_escalar_o_tupla_con_codigo(valor, esperado):
    assert _valor_unico(valor, "GetX") == esperado


def test_valor_unico_con_codigo_de_error_o_varios_datos_falla():
    with pytest.raises(SapAceroFuerzasError):
        _valor_unico((5, 12), "GetX")  # código distinto de cero
    with pytest.raises(SapAceroFuerzasError):
        _valor_unico((0, 1, 2), "GetX")  # varios datos donde se esperaba uno
