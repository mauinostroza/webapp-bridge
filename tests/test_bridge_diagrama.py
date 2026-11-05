"""Tests de bridge/diagrama.py: puerto de sap2000_bridge.py del proyecto
Diagrama de interacción (grupos, combinaciones, demandas de columnas).

COM falso con el mismo patrón de sesión/modelo que tests/test_bridge_espectro.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.diagrama import (
    SapDiagramaError,
    get_frames_demands,
    get_frames_info,
    get_group_demands,
    get_group_frames,
    get_selected_demands,
    get_selected_frames_list,
    list_groups,
    list_load_combinations,
)

ROW_KEYS = {"frame", "combo", "station", "n_kn", "mx_knm", "my_knm", "vu_x_kn", "vu_y_kn"}


class _Setup:
    def __init__(self):
        self.deselect_calls = 0
        self.selected: list[str] = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.deselect_calls += 1
        self.selected = []
        return 0

    def SetComboSelectedForOutput(self, combo, flag):
        assert flag is True
        self.selected.append(combo)
        return 0


class _Results:
    def __init__(self, forces, fail_frames=()):
        self.Setup = _Setup()
        self.forces = forces  # frame -> lista de (combo, station, p, m2, m3)
        self.fail_frames = set(fail_frames)
        self.calls: list[tuple] = []

    def FrameForce(self, name, item_type):
        self.calls.append((name, item_type))
        if name in self.fail_frames:
            raise RuntimeError("boom")
        rows = [r for r in self.forces.get(name, []) if r[0] in self.Setup.selected]
        n = len(rows)
        col = lambda i: tuple(r[i] for r in rows)  # noqa: E731
        zeros = (0.0,) * n
        v2 = tuple(r[5] if len(r) > 5 else 0.0 for r in rows)
        v3 = tuple(r[6] if len(r) > 6 else 0.0 for r in rows)
        return (
            n,
            (name,) * n,  # Obj
            col(1),  # ObjSta
            ("",) * n,  # Elm
            zeros,  # ElmSta
            col(0),  # LoadCase
            ("Max",) * n,  # StepType
            zeros,  # StepNum
            col(2),  # P
            v2,  # V2
            v3,  # V3
            zeros,  # T
            col(3),  # M2
            col(4),  # M3
            0,
        )


class _GroupDef:
    def __init__(self, groups, assignments):
        self.groups = groups
        self.assignments = assignments

    def GetNameList(self):
        return (len(self.groups), tuple(self.groups), 0)

    def GetAssignments(self, name, *_args):
        types, names = self.assignments.get(name, ((), ()))
        return (len(types), tuple(types), tuple(names), 0)


class _SelectObj:
    def __init__(self, selected):
        self.selected = selected

    def GetSelected(self, *_args):
        types = tuple(t for t, _ in self.selected)
        names = tuple(n for _, n in self.selected)
        return (len(types), types, names, 0)


class _RespCombo:
    def __init__(self, combos):
        self.combos = combos

    def GetNameList(self):
        return (len(self.combos), tuple(self.combos), 0)


class _FrameObj:
    def __init__(self, secciones):
        self.secciones = secciones

    def GetSection(self, name, *_args):
        if name not in self.secciones:
            raise RuntimeError("sin frame")
        return (self.secciones[name], "", 0)


class _PropFrame:
    """Rectangulares (t3, t2) y circulares (t3) en las unidades presentes; ``ret`` al inicio."""

    def __init__(self, rect, circ):
        self.rect = rect
        self.circ = circ

    def GetRectangle(self, name, *_args):
        if name not in self.rect:
            return (1, "", "", 0.0, 0.0, 0, "", "")
        t3, t2 = self.rect[name]
        return (0, "", "C25", t3, t2, -1, "", "")

    def GetCircle(self, name, *_args):
        if name not in self.circ:
            return (1, "", "", 0.0, -1, "", "")
        return (0, "", "C25", self.circ[name], -1, "", "")


class FakeModel:
    def __init__(
        self,
        *,
        groups=("G1",),
        assignments=None,
        combos=("C1", "C2"),
        selected=(),
        forces=None,
        fail_frames=(),
        fail_set_units=False,
        frame_sections=None,
        rect_sections=None,
        circ_sections=None,
    ):
        self.GroupDef = _GroupDef(list(groups), assignments or {})
        self.RespCombo = _RespCombo(list(combos))
        self.SelectObj = _SelectObj(list(selected))
        self.Results = _Results(forces or {}, fail_frames)
        self.units = 4  # kip_ft_F del usuario
        self.unit_history: list[int] = []
        self.fail_set_units = fail_set_units
        self.FrameObj = _FrameObj(frame_sections or {})
        self.PropFrame = _PropFrame(rect_sections or {}, circ_sections or {})

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, value):
        if self.fail_set_units:
            raise RuntimeError("no units")
        self.unit_history.append(value)
        self.units = value
        return 0


def _forces():
    """2 columnas x 2 combinaciones x 3 estaciones (más una combinación C3 no pedida)."""
    out = {}
    for k, frame in enumerate(("COL1", "COL2"), start=1):
        rows = []
        for combo in ("C1", "C2", "C3"):
            for s, station in enumerate((0.0, 1.5, 3.0)):
                base = 100.0 * k + 10.0 * s
                rows.append((combo, station, base, base + 1, base + 2))
        out[frame] = rows
    return out


def _model_group(**kw):
    assignments = {"G1": ((2, 2, 1, 5), ("COL1", "COL2", "J1", "A1"))}
    return FakeModel(assignments=assignments, forces=_forces(), **kw)


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------


def test_list_groups_and_combos():
    model = FakeModel(groups=["ALL", "G1"], combos=["C1", "C2", "ENV"])
    assert list_groups(model) == ["ALL", "G1"]
    assert list_load_combinations(model) == ["C1", "C2", "ENV"]


def test_list_groups_com_error():
    model = FakeModel()
    model.GroupDef.GetNameList = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    with pytest.raises(SapDiagramaError) as excinfo:
        list_groups(model)
    assert excinfo.value.detail["code"] == "sap_error"
    assert "GroupDef.GetNameList()" in excinfo.value.detail["message"]


# ---------------------------------------------------------------------------
# Demandas por grupo
# ---------------------------------------------------------------------------


def test_group_demands_order_stations_and_sign():
    model = _model_group()
    result = get_group_demands(model, "G1", ["C1", "C2"])
    assert result["n_frames"] == 2  # sólo Frames (tipo 2), no Joint ni Area
    assert result["n_rows"] == len(result["rows"]) == 12
    rows = result["rows"]
    # Orden: columna, luego combinación (orden de SAP), luego estación.
    assert [(r["frame"], r["combo"], r["station"]) for r in rows[:6]] == [
        ("COL1", "C1", 0.0),
        ("COL1", "C1", 1.5),
        ("COL1", "C1", 3.0),
        ("COL1", "C2", 0.0),
        ("COL1", "C2", 1.5),
        ("COL1", "C2", 3.0),
    ]
    assert {r["frame"] for r in rows[6:]} == {"COL2"}
    assert all(set(r) == ROW_KEYS for r in rows)
    assert all(r["combo"] != "C3" for r in rows)
    first = rows[0]
    # P: tracción positiva en SAP -> compresión positiva (se invierte el signo).
    assert first["n_kn"] == -100.0
    # Ejes del motor: Mx = M3 (en torno a x, ancho = eje local 3) y My = M2 (eje local 2).
    assert first["mx_knm"] == 102.0  # M3
    assert first["my_knm"] == 101.0  # M2
    assert first["vu_x_kn"] == 0.0 and first["vu_y_kn"] == 0.0
    assert rows[1]["n_kn"] == -110.0
    assert rows[6]["n_kn"] == -200.0
    assert model.Results.calls == [("COL1", 0), ("COL2", 0)]


def test_units_forced_to_kn_m_and_restored():
    model = _model_group()
    get_group_demands(model, "G1", ["C1"])
    assert model.unit_history == [6, 4]
    assert model.units == 4


def test_units_restored_when_reading_fails():
    model = _model_group(fail_frames={"COL2"})
    with pytest.raises(SapDiagramaError) as excinfo:
        get_group_demands(model, "G1", ["C1"])
    assert "Results.FrameForce('COL2') falló" in str(excinfo.value)
    assert model.unit_history == [6, 4]
    assert model.units == 4


def test_units_failure_is_reported_and_nothing_read():
    model = _model_group(fail_set_units=True)
    with pytest.raises(SapDiagramaError, match="unidades"):
        get_group_demands(model, "G1", ["C1"])
    assert model.Results.calls == []


def test_group_without_frames_raises():
    model = FakeModel(assignments={"G1": ((1,), ("J1",))})
    with pytest.raises(SapDiagramaError) as excinfo:
        get_group_demands(model, "G1", ["C1"])
    assert "no tiene ningún Frame object" in str(excinfo.value)
    assert model.unit_history == []


def test_group_with_missing_results_returns_empty_rows():
    # El original no distingue "sin análisis": devuelve cero filas.
    model = FakeModel(assignments={"G1": ((2,), ("COL1",))}, forces={})
    result = get_group_demands(model, "G1", ["C1"])
    assert result == {"rows": [], "n_frames": 1, "n_rows": 0}


# ---------------------------------------------------------------------------
# Demandas por selección
# ---------------------------------------------------------------------------


def test_selected_demands_only_frames():
    model = FakeModel(selected=[(2, "COL1"), (1, "J1"), (2, "COL2")], forces=_forces())
    result = get_selected_demands(model, ["C2"])
    assert result["n_frames"] == 2
    assert result["n_rows"] == 6
    assert {r["combo"] for r in result["rows"]} == {"C2"}
    assert model.unit_history == [6, 4]


def test_selected_demands_empty_selection_raises():
    model = FakeModel(selected=[(1, "J1")], forces=_forces())
    with pytest.raises(SapDiagramaError) as excinfo:
        get_selected_demands(model, ["C1"])
    assert "No hay ningún Frame object (columna) seleccionado en SAP2000." in str(excinfo.value)


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


def test_route_groups_and_combos():
    client = _client(FakeModel(groups=["G1", "G2"], combos=["C1"]))
    assert client.get("/v1/sap/actions/diagrama/groups").json() == {"groups": ["G1", "G2"]}
    assert client.get("/v1/sap/actions/diagrama/combos").json() == {"combos": ["C1"]}


def test_route_group_demands():
    model = _model_group()
    response = _client(model).post(
        "/v1/sap/actions/diagrama/group-demands", json={"group": "G1", "combos": ["C1", "C2"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"rows", "n_frames", "n_rows"}
    assert body["n_frames"] == 2 and body["n_rows"] == 12
    assert set(body["rows"][0]) == ROW_KEYS


def test_route_selected_demands():
    model = FakeModel(selected=[(2, "COL1")], forces=_forces())
    response = _client(model).post("/v1/sap/actions/diagrama/selected-demands", json={"combos": ["C1"]})
    assert response.status_code == 200
    body = response.json()
    assert body["n_frames"] == 1 and body["n_rows"] == 3


@pytest.mark.parametrize(
    "body",
    [
        {"group": "", "combos": ["C1"]},
        {"group": "G1", "combos": []},
        {"group": "G1", "combos": [""]},
        {"group": "G1", "combos": ["C\x00"]},
        {"group": "G1", "combos": [f"C{i}" for i in range(501)]},
        {"combos": ["C1"]},
    ],
)
def test_route_group_demands_validation(body):
    model = _model_group()
    response = _client(model).post("/v1/sap/actions/diagrama/group-demands", json=body)
    assert response.status_code == 422
    assert model.Results.calls == []


@pytest.mark.parametrize("combos", [[], [""], ["C1", " "], [f"C{i}" for i in range(501)]])
def test_route_selected_demands_validation(combos):
    model = FakeModel(selected=[(2, "COL1")], forces=_forces())
    response = _client(model).post("/v1/sap/actions/diagrama/selected-demands", json={"combos": combos})
    assert response.status_code == 422
    assert model.Results.calls == []


def test_route_group_without_frames_returns_409():
    model = FakeModel(assignments={"G1": ((1,), ("J1",))})
    response = _client(model).post(
        "/v1/sap/actions/diagrama/group-demands", json={"group": "G1", "combos": ["C1"]}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "sap_error"
    assert "no tiene ningún Frame object" in detail["message"]


def test_route_read_failure_returns_409_and_restores_units():
    model = _model_group(fail_frames={"COL1"})
    response = _client(model).post(
        "/v1/sap/actions/diagrama/group-demands", json={"group": "G1", "combos": ["C1"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "sap_error"
    assert model.units == 4


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/v1/sap/actions/diagrama/groups", None),
        ("get", "/v1/sap/actions/diagrama/combos", None),
        ("post", "/v1/sap/actions/diagrama/group-demands", {"group": "G1", "combos": ["C1"]}),
        ("post", "/v1/sap/actions/diagrama/selected-demands", {"combos": ["C1"]}),
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
    # Sin SAP2000 en CI: llega al actor (409/503), no 401/403/404.
    assert allowed.status_code not in (401, 403, 404)


# ---------------------------------------------------------------------------
# Cortantes, frames explícitos y secciones
# ---------------------------------------------------------------------------


def test_cortantes_se_leen_en_modulo_con_ejes_del_motor():
    fuerzas = {"COL1": [("C1", 0.0, 100.0, 11.0, 12.0, -30.0, 45.0)]}  # V2 = -30, V3 = 45
    model = FakeModel(assignments={"G1": ((2,), ("COL1",))}, forces=fuerzas)
    fila = get_group_demands(model, "G1", ["C1"])["rows"][0]
    assert fila["vu_x_kn"] == 45.0  # V3, a lo largo del eje local 3 (x)
    assert fila["vu_y_kn"] == 30.0  # V2, a lo largo del eje local 2 (y), en módulo


def test_demandas_de_frames_explicitos():
    model = FakeModel(forces=_forces())
    resultado = get_frames_demands(model, ["COL2"], ["C1"])
    assert resultado["n_frames"] == 1 and resultado["n_rows"] == 3
    assert {r["frame"] for r in resultado["rows"]} == {"COL2"}
    with pytest.raises(SapDiagramaError, match="vacía"):
        get_frames_demands(model, [], ["C1"])


def test_frames_del_grupo_y_seleccion_sin_leer_resultados():
    model = FakeModel(
        assignments={"G1": ((2, 2, 1), ("COL1", "COL2", "J1"))}, selected=[(2, "COL2"), (1, "J1")]
    )
    assert get_group_frames(model, "G1") == {"frames": ["COL1", "COL2"]}
    assert get_selected_frames_list(model) == {"frames": ["COL2"]}
    assert model.Results.calls == []


def test_info_de_secciones_rectangular_y_circular_en_mm():
    model = FakeModel(
        frame_sections={"C1": "C50x60", "C2": "C50x60", "C3": "CIRC40", "C4": "PERFIL"},
        rect_sections={"C50x60": (0.6, 0.5)},  # t3 = 0,6 (canto, y) y t2 = 0,5 (ancho, x), en m
        circ_sections={"CIRC40": 0.4},
    )
    model.units = 6  # kN_m_C: longitudes en m
    info = get_frames_info(model, ["C1", "C2", "C3", "C4"])["frames"]
    c1 = info[0]
    assert c1["frame"] == "C1" and c1["seccion"] == "C50x60" and c1["tipo"] == "rect"
    assert c1["alto_mm"] == pytest.approx(600) and c1["ancho_mm"] == pytest.approx(500)
    assert info[1]["seccion"] == "C50x60"
    assert info[2]["tipo"] == "circ" and info[2]["diametro_mm"] == pytest.approx(400)
    assert info[3]["tipo"] == "otra" and info[3]["alto_mm"] is None


def test_info_de_secciones_convierte_las_unidades_presentes():
    model = FakeModel(frame_sections={"C1": "S"}, rect_sections={"S": (60.0, 50.0)})
    model.units = 14  # kgf_cm_C: longitudes en cm
    c = get_frames_info(model, ["C1"])["frames"][0]
    assert c["alto_mm"] == pytest.approx(600) and c["ancho_mm"] == pytest.approx(500)


def test_info_de_secciones_frame_inexistente():
    with pytest.raises(SapDiagramaError, match="FrameObj.GetSection"):
        get_frames_info(FakeModel(), ["X"])


def test_rutas_nuevas():
    model = FakeModel(
        assignments={"G1": ((2,), ("COL1",))},
        selected=[(2, "COL1")],
        forces=_forces(),
        frame_sections={"COL1": "S"},
        rect_sections={"S": (0.5, 0.4)},
    )
    model.units = 6
    client = _client(model)
    base = "/v1/sap/actions/diagrama"
    assert client.get(f"{base}/selected-frames").json() == {"frames": ["COL1"]}
    assert client.post(f"{base}/group-frames", json={"group": "G1"}).json() == {"frames": ["COL1"]}
    info = client.post(f"{base}/frames-info", json={"frames": ["COL1"]}).json()
    assert info["frames"][0]["ancho_mm"] == pytest.approx(400)
    dem = client.post(f"{base}/frames-demands", json={"frames": ["COL1"], "combos": ["C1"]}).json()
    assert set(dem) == {"rows", "n_frames", "n_rows"} and dem["n_rows"] == 3
    assert client.post(f"{base}/frames-demands", json={"frames": [], "combos": ["C1"]}).status_code == 422


def test_combinaciones_con_nombres_por_defecto_de_sap_son_validas():
    """SAP2000 nombra las combinaciones «ASD1: DEAD + 0.7EX -0.21EY»: con «:», «+», «-» y espacios."""
    from bridge.diagrama import FramesDemandsBody, GroupDemandsBody

    nombres = ["ASD1: DEAD + Df + Dsuelo + 0.75LIVE + 0.7EX + 0.21EY + 0.21EV", "ASD2: DEAD + Df -0.21EV"]
    assert FramesDemandsBody(frames=["F1"], combos=nombres).combos == nombres
    assert GroupDemandsBody(group="G1", combos=nombres).combos == nombres
    for malo in ("A/B", "A*B", "A\\B", ""):
        try:
            FramesDemandsBody(frames=["F1"], combos=[malo])
        except ValueError:
            continue
        raise AssertionError(f"debía rechazar {malo!r}")
