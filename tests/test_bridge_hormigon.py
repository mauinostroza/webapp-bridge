"""Tests de bridge/hormigon.py: lectura en vivo de SAP2000 para Hormigón simple
(armado de viga); devuelve filas crudas, la demanda la calcula el servidor.

COM falso con el mismo patrón de sesión/modelo que tests/test_bridge_diagrama.py.
"""

from __future__ import annotations

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge import hormigon
from bridge.actions import install_routes
from bridge.hormigon import (
    SapHormigonError,
    get_frame_forces,
    get_frames,
    get_selected_frames,
    list_combos,
)

# Estaciones (m) y valores por combinación: (V2, M3, P).
STATIONS = (0.0, 1.0, 2.0, 3.0)
DATA = {
    "C1": {s: (10.0 - 4.0 * s, 5.0 * s - 2.0, 1.0) for s in STATIONS},
    "C2": {s: (-8.0 + 3.0 * s, -6.0 * s + 1.0, 2.0) for s in STATIONS},
}


class _Setup:
    def __init__(self):
        self.selected: list[str] = []
        self.calls: list[tuple] = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.calls.append(("deselect",))
        self.selected = []
        return 0

    def SetComboSelectedForOutput(self, name, *rest):
        self.calls.append(("combo", name, *rest))
        self.selected.append(name)
        return 0

    def SetCaseSelectedForOutput(self, name, *rest):
        self.calls.append(("case", name, *rest))
        self.selected.append(name)
        return 0


class _Results:
    def __init__(self, data, fail=False):
        self.Setup = _Setup()
        self.data = data
        self.fail = fail
        self.args: list[tuple] = []

    def FrameForce(self, *args):
        self.args.append(args)
        if len(args) > 16:  # pywin32 con SAP2000 23.3.1: «takes from 1 to 17 positional arguments»
            raise TypeError("FrameForce() takes from 1 to 17 positional arguments but 18 were given")
        if self.fail:
            raise RuntimeError("boom")
        rows = [
            (c, s, *vals) for c in self.Setup.selected for s, vals in sorted(self.data.get(c, {}).items())
        ]
        n = len(rows)
        z = (0.0,) * n
        return (
            n,
            ("B1",) * n,
            tuple(r[1] for r in rows),
            ("",) * n,
            z,
            tuple(r[0] for r in rows),
            ("Max",) * n,
            z,
            tuple(r[4] for r in rows),  # P
            tuple(r[2] for r in rows),  # V2
            z,
            z,
            z,
            tuple(r[3] for r in rows),  # M3
            0,
        )


class FakeModel:
    def __init__(self, data=None, fail=False, fail_set_units=False, ret=0):
        self.Results = _Results(DATA if data is None else data, fail)
        self.units = 4  # kip_ft
        self.unit_history: list[int] = []
        self.fail_set_units = fail_set_units
        self.ret = ret
        outer = self

        class FrameObj:
            def GetNameList(self, *a):
                return (2, ("B1", "B2"), outer.ret)

            def GetPoints(self, name, *a):
                return ("P1" if name == "B1" else "P3", "P2" if name == "B1" else "P4", 0)

            def GetSection(self, name, *a):
                if name == "B2":
                    return (1, 0, 5)  # código de retorno != 0
                return ("V30x60", "", 0)

        class PointObj:
            coords = {
                "P1": (0.0, 0.0, 0.0),
                "P2": (3.0, 4.0, 0.0),
                "P3": (0.0, 0.0, 0.0),
                "P4": (1.0, 0.0, 0.0),
            }

            def GetCoordCartesian(self, p, *a):
                return (*self.coords[p], 0)

        class RespCombo:
            def GetNameList(self, *a):
                return (2, ("C1", "C2"), outer.ret)

        class LoadCases:
            def GetNameList(self, *a):
                return (3, ("DEAD", "C1", "LIVE"), 0)

        self.FrameObj, self.PointObj, self.RespCombo, self.LoadCases = (
            FrameObj(),
            PointObj(),
            RespCombo(),
            LoadCases(),
        )

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, value):
        if self.fail_set_units:
            raise RuntimeError("no units")
        self.unit_history.append(value)
        self.units = value
        return 0


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------


def test_list_combos_includes_cases_without_duplicates():
    assert list_combos(FakeModel()) == ["C1", "C2", "DEAD", "LIVE"]
    assert list_combos(FakeModel(), include_cases=False) == ["C1", "C2"]


def test_get_frames_length_and_section():
    frames = get_frames(FakeModel())
    # Unidades presentes kip-ft: 5 ft -> 1.524 m.
    assert frames[0]["nombre"] == "B1" and frames[0]["seccion"] == "V30x60"
    assert frames[0]["longitud_m"] == pytest.approx(5.0 * 0.3048)
    assert frames[1]["nombre"] == "B2" and frames[1]["seccion"] is None
    assert frames[1]["longitud_m"] == pytest.approx(0.3048)


def test_list_error_literal_message():
    with pytest.raises(SapHormigonError) as excinfo:
        list_combos(FakeModel(ret=3))
    assert excinfo.value.detail == {
        "code": "sap_error",
        "message": "SAP2000 API: RespCombo.GetNameList falló (código 3).",
    }


# ---------------------------------------------------------------------------
# Demanda
# ---------------------------------------------------------------------------


def _esperadas(combos, frame_ok=True):
    """Filas crudas esperadas para ``combos`` (V2, M3, P de DATA) en columnas paralelas."""
    filas = {k: [] for k in ("estacion_m", "caso", "paso", "P", "V2", "M3")}
    for c in combos:
        for s, (v2, m3, p) in sorted(DATA[c].items()):
            for k, v in zip(filas, (s, c, "Max", p, v2, m3)):
                filas[k].append(v)
    return filas


def test_frame_forces_devuelve_filas_crudas():
    model = FakeModel()
    result = get_frame_forces(model, "B1", ["C1", "C2"])
    assert set(result) == {"frame", "longitud_m", "unidades", "combos", "filas"}
    assert result["frame"] == "B1"
    assert result["longitud_m"] == 3.0
    assert result["unidades"] == {"fuerza": "tonf", "longitud": "m"}
    assert result["combos"] == ["C1", "C2"]
    assert result["filas"] == _esperadas(["C1", "C2"])
    # M3 sin invertir y columnas paralelas.
    assert result["filas"]["M3"][:4] == [-2.0, 3.0, 8.0, 13.0]
    assert len({len(v) for v in result["filas"].values()}) == 1
    # Mismos argumentos COM que el original.
    assert model.Results.args == [("B1", 0)]  # firma corta (la larga de 17 argumentos falla con pywin32)
    assert model.Results.Setup.calls == [("deselect",), ("combo", "C1"), ("combo", "C2")]


def test_frame_forces_combos_sin_duplicados_en_orden_pedido():
    result = get_frame_forces(FakeModel(), "B1", ["C2", "C1", "C2"])
    assert result["combos"] == ["C2", "C1"]
    assert result["filas"] == _esperadas(["C2", "C1"])


def test_non_combo_case_uses_case_selection():
    model = FakeModel(data={"C1": DATA["C1"], "DEAD": DATA["C1"]})
    get_frame_forces(model, "B1", ["C1", "DEAD"])
    assert model.Results.Setup.calls == [("deselect",), ("combo", "C1"), ("case", "DEAD")]


def test_frame_forces_convierte_unidades_presentes():
    # Con kip-ft como unidades "de origen" el puente igualmente fuerza tonf-m antes de leer;
    # el factor se aplica sobre las unidades presentes (aquí tonf-m, factores 1).
    model = FakeModel()
    get_frame_forces(model, "B1", ["C1"])
    assert model.unit_history[0] == 12


def test_units_forced_to_tonf_m_and_restored():
    model = FakeModel()
    get_frame_forces(model, "B1", ["C1"])
    assert model.unit_history == [12, 4]
    assert model.units == 4


def test_units_restored_when_reading_fails():
    model = FakeModel(fail=True)
    with pytest.raises(SapHormigonError):
        get_frame_forces(model, "B1", ["C1"])
    assert model.unit_history == [12, 4]
    assert model.units == 4


def test_units_failure_is_reported_and_nothing_read():
    model = FakeModel(fail_set_units=True)
    with pytest.raises(SapHormigonError, match="unidades"):
        get_frame_forces(model, "B1", ["C1"])
    assert model.Results.args == []


def test_no_results_literal_message():
    model = FakeModel(data={})
    with pytest.raises(SapHormigonError) as excinfo:
        get_frame_forces(model, "B1", ["C1"])
    assert excinfo.value.detail["message"] == "Casos inexistentes en B1: ['C1']"
    assert model.units == 4


def test_no_nan_in_output():
    result = get_frame_forces(FakeModel(), "B1", ["C1"])
    assert all(math.isfinite(v) for col in ("P", "V2", "M3") for v in result["filas"][col])


# ---------------------------------------------------------------------------
# Rutas
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


def _client(model) -> TestClient:
    app = FastAPI()
    install_routes(app, _FakeSession(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_route_frames_and_combos():
    client = _client(FakeModel())
    frames = client.get("/v1/sap/actions/hormigon/frames").json()["frames"]
    assert [f["nombre"] for f in frames] == ["B1", "B2"]
    assert set(frames[0]) == {"nombre", "longitud_m", "seccion"}
    assert client.get("/v1/sap/actions/hormigon/combos").json() == {"combos": ["C1", "C2", "DEAD", "LIVE"]}


def test_route_frame_forces():
    model = FakeModel()
    response = _client(model).post(
        "/v1/sap/actions/hormigon/frame-forces", json={"frame": "B1", "combos": ["C1", "C2"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"frame", "longitud_m", "unidades", "combos", "filas"}
    assert body["filas"] == _esperadas(["C1", "C2"])
    assert model.units == 4


@pytest.mark.parametrize(
    "body",
    [
        {"frame": "", "combos": ["C1"]},
        {"frame": "B" * 101, "combos": ["C1"]},
        {"frame": "B1", "combos": []},
        {"frame": "B1", "combos": [""]},
        {"frame": "B1", "combos": ["C\x00"]},
        {"frame": "B1", "combos": ["C" * 101]},
        {"frame": "B1", "combos": [f"C{i}" for i in range(51)]},
        {"combos": ["C1"]},
    ],
)
def test_route_frame_forces_validation(body):
    model = FakeModel()
    response = _client(model).post("/v1/sap/actions/hormigon/frame-forces", json=body)
    assert response.status_code == 422
    assert model.Results.args == [] and model.unit_history == []


def test_route_acepta_combinaciones_con_dos_puntos_y_espacios():
    nombre = "ASD1: DEAD + PIPING1"
    model = FakeModel(data={nombre: DATA["C1"]})
    response = _client(model).post(
        "/v1/sap/actions/hormigon/frame-forces", json={"frame": "B1", "combos": [nombre]}
    )
    assert response.status_code == 200


def test_route_accepts_50_combos():
    model = FakeModel(data={f"C{i}": DATA["C1"] for i in range(50)})
    response = _client(model).post(
        "/v1/sap/actions/hormigon/frame-forces", json={"frame": "B1", "combos": [f"C{i}" for i in range(50)]}
    )
    assert response.status_code == 200


def test_route_error_returns_409_with_literal_message():
    model = FakeModel(data={})
    response = _client(model).post(
        "/v1/sap/actions/hormigon/frame-forces", json={"frame": "B1", "combos": ["C1"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "sap_error", "message": "Casos inexistentes en B1: ['C1']"}
    assert model.units == 4


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/v1/sap/actions/hormigon/frames", None),
        ("get", "/v1/sap/actions/hormigon/combos", None),
        ("get", "/v1/sap/actions/hormigon/selected-frames", None),
        ("post", "/v1/sap/actions/hormigon/frame-forces", {"frame": "B1", "combos": ["C1"]}),
    ],
)
def test_routes_require_agent_token(monkeypatch, method, path, body):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    kwargs = {} if body is None else {"json": body}
    denied = getattr(client, method)(path, **kwargs)
    assert denied.status_code in (401, 403)
    allowed = getattr(client, method)(path, headers={"Authorization": "Bearer test-token"}, **kwargs)
    assert allowed.status_code not in (401, 403, 404)


# ---------------------------------------------------------------------------
# Selección de SAP2000
# ---------------------------------------------------------------------------


class _Seleccion:
    def __init__(self, raw):
        self.raw = raw

    def GetSelected(self, *args):
        return self.raw


def _con_seleccion(raw):
    model = FakeModel()
    model.SelectObj = _Seleccion(raw)
    return model


def test_selected_frames_devuelve_frames_con_nudos_y_omite_puntos():
    model = _con_seleccion((0, 3, [2, 1, 2], ["B1", "P1", "B2"]))
    frames = get_selected_frames(model)["frames"]
    assert [(f["nombre"], f["nudo_i"], f["nudo_j"]) for f in frames] == [
        ("B1", "P1", "P2"),
        ("B2", "P3", "P4"),
    ]
    assert set(frames[0]) == {"nombre", "longitud_m", "seccion", "nudo_i", "nudo_j"}
    assert frames[1]["seccion"] is None


def test_selected_frames_sin_frames_devuelve_lista_vacia():
    assert get_selected_frames(_con_seleccion((0, 0, [], [])))["frames"] == []
    assert get_selected_frames(_con_seleccion((0, 1, [1], ["J1"])))["frames"] == []


def test_selected_frames_error_de_sap_se_informa():
    with pytest.raises(SapHormigonError):
        get_selected_frames(_con_seleccion((5, 1, [2], ["B1"])))


def test_frame_forces_una_sola_lectura():
    model = FakeModel()
    lecturas = []
    lectura_original = model.Results.FrameForce

    def contar(*args):
        lecturas.append(args)
        return lectura_original(*args)

    model.Results.FrameForce = contar
    get_frame_forces(model, "B1", ["C1", "C2"])
    assert len(lecturas) == 1


def test_frame_forces_caso_inexistente_informa_el_error():
    with pytest.raises(SapHormigonError) as excinfo:
        get_frame_forces(FakeModel(data={"C1": DATA["C1"]}), "B1", ["C1", "C2"])
    assert excinfo.value.detail["message"] == "Casos inexistentes en B1: ['C2']"


def test_routes_selected_frames():
    client = _client(_con_seleccion((0, 2, [2, 2], ["B1", "B2"])))
    r = client.get("/v1/sap/actions/hormigon/selected-frames")
    assert r.status_code == 200, r.text
    assert [f["nombre"] for f in r.json()["frames"]] == ["B1", "B2"]


def test_rutas_antiguas_de_demanda_ya_no_existen():
    client = _client(FakeModel())
    for ruta in ("frame-demand", "frame-demand-multi"):
        r = client.post(f"/v1/sap/actions/hormigon/{ruta}", json={"frame": "B1", "combos": ["C1"]})
        assert r.status_code == 404


def test_frame_force_cae_a_la_firma_larga_si_la_corta_falla():
    """Con un enlace que no admite la firma corta se intenta la larga (16 argumentos como máximo)."""

    class Resultados:
        def __init__(self):
            self.llamadas = []

        def FrameForce(self, *args):
            self.llamadas.append(len(args))
            if len(args) == 2:
                raise TypeError("firma corta no admitida")
            return "ok"

    class Modelo:
        Results = Resultados()

    assert hormigon._frame_force(Modelo, "B1") == "ok"
    assert Modelo.Results.llamadas == [2, 16]
