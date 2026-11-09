"""Reacciones, coordenadas y sonda de zapatas biaxiales con SAP2000 simulado."""

from __future__ import annotations

import pytest

from bridge import zapata_biaxial as zb


class _Setup:
    def __init__(self, outer):
        self.o = outer

    def DeselectAllCasesAndCombosForOutput(self):
        self.o.sel_c, self.o.sel_k = set(), set()
        return 0

    def SetCaseSelectedForOutput(self, n):
        self.o.sel_c.add(n)
        return 0

    def SetComboSelectedForOutput(self, n):
        self.o.sel_k.add(n)
        return 0

    def GetCaseSelectedForOutput(self, n):
        return (n in self.o.sel_c, 0)

    def GetComboSelectedForOutput(self, n):
        return (n in self.o.sel_k, 0)


class _Results:
    def __init__(self, outer):
        self.o = outer
        self.Setup = _Setup(outer)

    def JointReact(self, nodo, _tipo):
        filas = [(c, "", 1.0) for c in sorted(self.o.sel_c)] + [(k, "Max", 2.0) for k in sorted(self.o.sel_k)]
        n = len(filas)
        col = lambda f: tuple(f(x) for x in filas)  # noqa: E731
        return (
            n, (nodo,) * n, ("",) * n, col(lambda x: x[0]), col(lambda x: x[1]), col(lambda x: x[2]),
            (1.0,) * n, (2.0,) * n, (3.0,) * n, (4.0,) * n, (5.0,) * n, (6.0,) * n, 0,
        )  # fmt: skip


class _Lista:
    def __init__(self, nombres):
        self.nombres = tuple(nombres)

    def GetNameList(self, *a):
        return (len(self.nombres), self.nombres, 0)


class _Casos(_Lista):
    def GetTypeOAPI_1(self, nombre):
        return (4 if nombre == "SX" else 1, 0, 0)


class _Puntos(_Lista):
    def __init__(self, giro):
        super().__init__(("1", "2"))
        self.giro = giro

    def GetCoordCartesian(self, n, *a):
        return (float(n), 0.0, 10.0 + 0.5 * int(n), 0)

    def GetRestraint(self, n):
        return ((True,) * 6 if n == "2" else (False,) * 6, 0)

    def GetLocalAxes(self, n):
        return (30.0 if n in self.giro else 0.0, 0.0, 0.0, False, 0)


class _Seleccion:
    def __init__(self, outer):
        self.o = outer

    def GetSelected(self):
        n = self.o.seleccion
        return (len(n), (1,) * len(n), tuple(n), 0)


class FakeZapata:
    def __init__(self, giro=(), seleccion=()):
        self.seleccion = tuple(seleccion)
        self.SelectObj = _Seleccion(self)
        self.sel_c, self.sel_k = {"DEAD"}, {"U9"}
        self.units = 6
        self.Results = _Results(self)
        self.LoadCases = _Casos(("DEAD", "SX"))
        self.RespCombo = _Lista(("U1", "U9", "ASD1: D + L"))
        self.PointObj = _Puntos(giro)

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, v):
        self.units = v
        return 0

    def GetModelFilename(self):
        return "m.sdb"


def test_reacciones_agrega_stepnum_al_final_tipo_real_y_restaura_seleccion():
    model = FakeZapata()
    res = zb.reacciones(model, ["1"], ["DEAD", "SX"], ["U1"])
    assert res["columns"][-2:] == ["StepNum", "CaseTypeSap"]
    assert res["columns"][:10] == [
        "Joint",
        "OutputCase",
        "CaseType",
        "StepType",
        "F1",
        "F2",
        "F3",
        "M1",
        "M2",
        "M3",
    ]
    assert {f[1]: f[2] for f in res["data"]} == {"DEAD": "LoadCase", "SX": "LoadCase", "U1": "Combination"}
    tipos = {f[1]: f[11] for f in res["data"]}
    assert tipos == {"DEAD": "LinStatic", "SX": "LinRespSpec", "U1": "Combination"}
    assert {f[1]: f[10] for f in res["data"]} == {"DEAD": 1.0, "SX": 1.0, "U1": 2.0}
    assert all(len(f) == 12 for f in res["data"])
    assert (model.sel_c, model.sel_k) == ({"DEAD"}, {"U9"})  # selección del usuario restaurada
    assert "avisos" not in res and "nodos_ejes_locales" not in res  # claves opcionales


def test_reacciones_avisa_de_nodos_con_ejes_locales_girados():
    res = zb.reacciones(FakeZapata(giro=("2",)), ["1", "2"], ["DEAD"], [])
    assert res["nodos_ejes_locales"] == ["2"]
    assert any("ejes locales girados" in a for a in res["avisos"])


def test_coordenadas_de_nodos_concretos_y_dispersion_de_cotas():
    res = zb.coordenadas(FakeZapata(), ["1", "2"])
    assert res["data"] == [["1", 1.0, 0.0, 10.5], ["2", 2.0, 0.0, 11.0]]
    assert res["dispersion_z"] == pytest.approx(0.5)
    with pytest.raises(zb.ZapataBiaxialError, match="no existen"):
        zb.coordenadas(FakeZapata(), ["99"])


def test_sonda_devuelve_forma_cruda_y_restaura_seleccion():
    model = FakeZapata()
    res = zb.sonda_joint_react(model, "1", None, "U1")
    assert res["n_salidas"] == 13
    assert res["salidas"][4] == ["Max"]
    assert model.sel_k == {"U9"}


# ---------------------------------------------------------------------------
# Contrato HTTP con la web de fusion-visor (apiPuente.ts / flujoSap.ts)
# ---------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from bridge.actions import install_routes  # noqa: E402
from tests.test_bridge_contracts import assert_matches_contract  # noqa: E402

RAIZ = "/v1/sap/actions/zapata-biaxial"
CONTRATOS = json.loads(
    (Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json").read_text("utf-8")
)["routes"]


class _Actor:
    def call(self, fn):
        return fn()


class _Sesion:
    def __init__(self, model):
        self.actor = _Actor()
        self.model = model

    def _model(self):
        return self.model


def _cliente(model):
    app = FastAPI()
    install_routes(app, _Sesion(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


@pytest.mark.parametrize("enviar", ["sin_cuerpo", "vacio"])
def test_nodos_sin_cuerpo_o_con_vacio_devuelve_la_seleccion(enviar):
    c = _cliente(FakeZapata(seleccion=("1", "3")))
    r = c.post(f"{RAIZ}/nodos") if enviar == "sin_cuerpo" else c.post(f"{RAIZ}/nodos", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert_matches_contract(CONTRATOS, f"POST {RAIZ}/nodos", body)
    assert body["origen"] == "seleccion"
    assert body["columns"] == ["Joint", "X", "Y", "Z"]
    assert [f[0] for f in body["data"]] == ["1", "3"]


def test_nodos_sin_seleccion_devuelve_los_restringidos():
    r = _cliente(FakeZapata()).post(f"{RAIZ}/nodos", json={})
    assert r.status_code == 200, r.text
    assert r.json()["origen"] == "restringidos"
    assert [f[0] for f in r.json()["data"]] == ["2"]


def test_nodos_con_lista_devuelve_esos_nodos_con_las_mismas_claves():
    r = _cliente(FakeZapata(seleccion=("1",))).post(f"{RAIZ}/nodos", json={"nodos": ["1", "2"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= set(CONTRATOS[f"POST {RAIZ}/nodos"]["keys"])  # contrato de siempre, más claves nuevas
    assert body["columns"] == ["Joint", "X", "Y", "Z"]
    assert [f[0] for f in body["data"]] == ["1", "2"]
    assert body["dispersion_z"] == pytest.approx(0.5)


@pytest.mark.parametrize("cuerpo", [{"nodos": []}, {"nodos": [""]}, {"otro": 1}])
def test_nodos_rechaza_cuerpos_invalidos(cuerpo):
    assert _cliente(FakeZapata()).post(f"{RAIZ}/nodos", json=cuerpo).status_code == 422


def test_reacciones_http_cumple_el_contrato_y_solo_añade_columnas_al_final():
    r = _cliente(FakeZapata()).post(
        f"{RAIZ}/reacciones", json={"nodos": ["1"], "casos": ["DEAD"], "combinaciones": ["U1"]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert_matches_contract(CONTRATOS, f"POST {RAIZ}/reacciones", body)
    assert body["columns"][:10] == [
        "Joint",
        "OutputCase",
        "CaseType",
        "StepType",
        "F1",
        "F2",
        "F3",
        "M1",
        "M2",
        "M3",
    ]
    assert body["columns"][10:] == ["StepNum", "CaseTypeSap"]
    assert {f[2] for f in body["data"]} <= {"LoadCase", "Combination"}  # CaseType como siempre
    assert all(len(f) == len(body["columns"]) for f in body["data"])


def test_reacciones_sin_nodos_o_sin_casos_da_422():
    c = _cliente(FakeZapata())
    assert (
        c.post(f"{RAIZ}/reacciones", json={"nodos": [], "casos": ["DEAD"], "combinaciones": []}).status_code
        == 422
    )
    assert (
        c.post(f"{RAIZ}/reacciones", json={"nodos": ["1"], "casos": [], "combinaciones": []}).status_code
        == 422
    )


def test_estado_y_casos_http_cumplen_el_contrato():
    c = _cliente(FakeZapata())
    assert_matches_contract(CONTRATOS, f"GET {RAIZ}/estado", c.get(f"{RAIZ}/estado").json())


def test_reacciones_http_acepta_combinacion_con_dos_puntos():
    r = _cliente(FakeZapata()).post(
        f"{RAIZ}/reacciones", json={"nodos": ["1"], "casos": ["DEAD"], "combinaciones": ["ASD1: D + L"]}
    )
    assert r.status_code == 200, r.text
    assert {f[1] for f in r.json()["data"]} == {"DEAD", "ASD1: D + L"}
    assert {f[2] for f in r.json()["data"]} == {"LoadCase", "Combination"}
