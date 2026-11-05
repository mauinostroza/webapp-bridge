"""Tests de bridge/equilibrio.py: puerto de la parte en vivo de
``sap2000_import.py`` y del diálogo de importación (Equilibrio plástico).

COM falso con el mismo patrón de sesión/modelo que tests/test_bridge_hormigon.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.equilibrio import SapEquilibrioError, get_frame_data, get_group_demands

P_VALUES = {
    ("D1", "SISMO"): (-12.0, 30.0, -45.5),
    ("D1", "GRAV"): (2.0, -7.5),
    ("D2", "SISMO"): (5.0, -9.0),
}
COORDS = {
    "P1": (0.0, 0.0, 0.0),
    "P2": (3.0, 4.0, 5.0),
    "P3": (1.0, 1.0, 1.0),
    "P4": (1.0, 1.0, 4.0),
}
POINTS = {"D1": ("P1", "P2"), "D2": ("P3", "P4")}
PREFIX = "/v1/sap/actions/equilibrio"


class _Setup:
    def __init__(self, fail_combos=()):
        self.selected: list[str] = []
        self.fail_combos = set(fail_combos)

    def DeselectAllCasesAndCombosForOutput(self):
        self.selected = []
        return 0

    def SetComboSelectedForOutput(self, name, *rest):
        if name in self.fail_combos:
            return 1
        self.selected.append(name)
        return 0


class _Results:
    def __init__(self, long_signature, no_data):
        self.Setup = _Setup(fail_combos=("MALO",))
        self.long_signature = long_signature
        self.no_data = set(no_data)
        self.args: list[tuple] = []

    def FrameForce(self, *args):
        self.args.append(args)
        if self.long_signature and len(args) == 2:
            raise TypeError("firma corta no soportada")
        frame = args[0]
        if frame not in POINTS:
            raise RuntimeError("objeto inexistente")
        combo = self.Setup.selected[-1]
        p = () if frame in self.no_data else P_VALUES.get((frame, combo), ())
        n = len(p)
        z = (0.0,) * n
        return (n, (frame,) * n, z, ("",) * n, z, (combo,) * n, ("Max",) * n, z, tuple(p), z, z, z, z, z, 0)


class FakeModel:
    def __init__(self, long_signature=False, no_data=(), bad_points=()):
        self.Results = _Results(long_signature, no_data)
        self.units = 4
        self.unit_calls: list = []

        class FrameObj:
            def GetPoints(self, name, *a):
                if name in bad_points or name not in POINTS:
                    raise RuntimeError("sin puntos")
                if len(a) == 0:
                    if long_signature:
                        raise TypeError("firma corta no soportada")
                    return (0, *POINTS[name])
                return (*POINTS[name], 0)

        class PointObj:
            def GetCoordCartesian(self, p, *a):
                if len(a) == 0:
                    if long_signature:
                        raise TypeError("firma corta no soportada")
                    return (0, *COORDS[p])
                return (*COORDS[p], 0)

        class RespCombo:
            def GetNameList(self, *a):
                return (3, ("SISMO", "GRAV", "MALO"), 0)

        class GroupDef:
            def GetNameList(self, *a):
                return (2, ("GRUPO1", "GRUPO2"), 0)

            def GetAssignments(self, name, *a):
                if name != "GRUPO1":
                    return (0, 0, [], [])
                return (0, 4, [2, 1, 2, 2], ["D1", "J1", "D2", "D3"])

        self.FrameObj, self.PointObj, self.RespCombo, self.GroupDef = (
            FrameObj(),
            PointObj(),
            RespCombo(),
            GroupDef(),
        )

    def GetPresentUnits(self):
        self.unit_calls.append("get")
        return self.units

    def SetPresentUnits(self, value):
        self.unit_calls.append(("set", value))
        self.units = value
        return 0


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


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------


def test_combos_and_groups():
    client = _client(FakeModel())
    assert client.get(f"{PREFIX}/combos").json() == {"combos": ["SISMO", "GRAV", "MALO"]}
    assert client.get(f"{PREFIX}/groups").json() == {"groups": ["GRUPO1", "GRUPO2"]}


def test_group_members_filters_frames_only():
    response = _client(FakeModel()).get(f"{PREFIX}/group-members", params={"group": "GRUPO1"})
    assert response.status_code == 200
    assert response.json() == {"group": "GRUPO1", "members": ["D1", "D2", "D3"]}
    empty = _client(FakeModel()).get(f"{PREFIX}/group-members", params={"group": "GRUPO2"})
    assert empty.json() == {"group": "GRUPO2", "members": []}


# ---------------------------------------------------------------------------
# frame-data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("long_signature", [False, True])
def test_frame_data_axial_and_geometry(long_signature):
    model = FakeModel(long_signature=long_signature)
    response = _client(model).post(
        f"{PREFIX}/frame-data", json={"frame": "D1", "combo_sismo": "SISMO", "combo_grav": "GRAV"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["axial_sismo"] == 45.5  # máxima |P| entre estaciones
    assert body["axial_grav"] == 7.5
    assert body["H"] == pytest.approx(5.0)
    assert body["L"] == pytest.approx(5.0)  # sqrt(3^2 + 4^2)
    assert body["avisos"] == []
    assert body["frame"] == "D1"
    assert model.unit_calls == []  # el original no toca las unidades


def test_frame_data_without_combos():
    body = _client(FakeModel()).post(f"{PREFIX}/frame-data", json={"frame": "D2"}).json()
    assert body["axial_sismo"] is None and body["axial_grav"] is None
    assert body["H"] == pytest.approx(3.0) and body["L"] == pytest.approx(0.0)


def test_frame_data_unknown_frame_returns_409_literal():
    response = _client(FakeModel()).post(f"{PREFIX}/frame-data", json={"frame": "NOEXISTE"})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "sap_error", "message": "sin puntos"}


def test_frame_data_axial_failures_become_avisos():
    model = FakeModel(no_data={"D1"})
    body = get_frame_data(model, "D1", "MALO", "SISMO")
    assert body["axial_sismo"] is None and body["axial_grav"] is None
    assert body["avisos"] == [
        "No se pudo activar el combo 'MALO'.",
        "Sin resultados para 'D1' / 'SISMO'.",
    ]


# ---------------------------------------------------------------------------
# group-demands
# ---------------------------------------------------------------------------


def test_group_demands_tn_and_pn_are_max_abs_axial():
    model = FakeModel(bad_points=("D2",))
    response = _client(model).post(
        f"{PREFIX}/group-demands", json={"group": "GRUPO1", "combo_sismo": "SISMO"}
    )
    assert response.status_code == 200
    body = response.json()
    d1, d2 = body["members"]
    assert d1 == {"label": "D1", "Tn_req": 45.5, "Pn_req": 45.5, "H": 5.0, "L": 5.0, "avisos": []}
    # Geometría fallida: H = L = 5.0 y aviso; D3 (sin resultados) se omite.
    assert d2["label"] == "D2" and d2["Tn_req"] == d2["Pn_req"] == 9.0
    assert d2["H"] == d2["L"] == 5.0
    assert d2["avisos"] == ["Grupo — D2: sin puntos"]
    assert body["avisos"] == ["Grupo — D3: objeto inexistente"]
    assert body["group"] == "GRUPO1"


def test_group_demands_without_combo_skips_members():
    body = get_group_demands(FakeModel(), "GRUPO1")
    assert body == {"group": "GRUPO1", "members": [], "avisos": []}


def test_too_many_members_returns_409():
    model = FakeModel()
    model.GroupDef.GetAssignments = lambda name, *a: (0, 201, [2] * 201, [f"F{i}" for i in range(201)])
    response = _client(model).get(f"{PREFIX}/group-members", params={"group": "GRUPO1"})
    assert response.status_code == 409
    assert "más de 200" in response.json()["detail"]["message"]
    with pytest.raises(SapEquilibrioError):
        get_group_demands(model, "GRUPO1", "SISMO")


def test_units_are_never_changed_even_on_failure():
    model = FakeModel()
    _client(model).post(f"{PREFIX}/frame-data", json={"frame": "NOEXISTE", "combo_sismo": "SISMO"})
    _client(model).post(f"{PREFIX}/group-demands", json={"group": "GRUPO1", "combo_sismo": "SISMO"})
    assert model.units == 4 and model.unit_calls == []


# ---------------------------------------------------------------------------
# Validación y token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/frame-data", {"frame": "x" * 101}),
        ("post", "/frame-data", {"frame": "D1", "combo_sismo": "x" * 101}),
        ("post", "/frame-data", {"frame": "D1", "combo_grav": "bad\nname"}),
        ("post", "/frame-data", {"frame": ""}),
        ("post", "/group-demands", {"group": "x" * 101}),
        ("post", "/group-demands", {"group": "G", "combo_sismo": "x" * 101}),
        ("get", "/group-members?group=" + "x" * 101, None),
        ("get", "/group-members", None),
    ],
)
def test_validation_422(method, path, body):
    model = FakeModel()
    kwargs = {} if body is None else {"json": body}
    response = getattr(_client(model), method)(PREFIX + path, **kwargs)
    assert response.status_code == 422
    assert model.Results.args == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/combos", None),
        ("get", "/groups", None),
        ("get", "/group-members?group=G", None),
        ("post", "/frame-data", {"frame": "D1"}),
        ("post", "/group-demands", {"group": "G"}),
    ],
)
def test_routes_require_agent_token(monkeypatch, method, path, body):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    kwargs = {} if body is None else {"json": body}
    denied = getattr(client, method)(PREFIX + path, **kwargs)
    assert denied.status_code in (401, 403)
    allowed = getattr(client, method)(PREFIX + path, headers={"Authorization": "Bearer test-token"}, **kwargs)
    assert allowed.status_code not in (401, 403, 404)
