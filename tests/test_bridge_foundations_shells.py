"""Tests de bridge/foundations_shells.py (Diagramas de shells, port del
escritorio) con COM falso, mismo patrón que tests/test_bridge_espectro.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge import foundations_shells as fs
from bridge.actions import install_routes, shell_geometry

CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"

# ---------------------------------------------------------------------------
# Modelo falso: malla de 2 elementos (E1 y E2 de 1x1 m, lado a lado en X).
#   J4(0,1) J3(1,1) J6(2,1)
#   J1(0,0) J2(1,0) J5(2,0)      z = 5 en J1, 0 en el resto
# ---------------------------------------------------------------------------

_COORDS = {
    "J1": (0.0, 0.0, 5.0),
    "J2": (1.0, 0.0, 0.0),
    "J3": (1.0, 1.0, 0.0),
    "J4": (0.0, 1.0, 0.0),
    "J5": (2.0, 0.0, 0.0),
    "J6": (2.0, 1.0, 0.0),
    # Frames
    "T1": (1.0, 0.0, 3.0),  # cabeza de la columna sobre J2
    "FX": (9.0, 9.0, 0.0),
}
_ELEMS = {"E1": ("J1", "J2", "J3", "J4"), "E2": ("J2", "J5", "J6", "J3")}
_PUNTOS_POR_ELM = {elm: pts for elm, pts in _ELEMS.items()}


class _AreaObj:
    def GetElm(self, area):
        return (0, 2, ("E1", "E2"))


class _AreaElm:
    def GetPoints(self, elm):
        return (0, 4, _ELEMS[elm])


class _PointObj:
    def GetCoordCartesian(self, name):
        x, y, z = _COORDS[name]
        return (0, x, y, z)


class _Setup:
    def __init__(self):
        self.calls = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.calls.append(("deselect",))
        return 0

    def SetCaseSelectedForOutput(self, name, flag):
        self.calls.append(("case", name, flag))
        return 0

    def SetComboSelectedForOutput(self, name, flag):
        self.calls.append(("combo", name, flag))
        return 0


class _LoadCases:
    def GetNameList(self, *args):
        return (0, 2, ("DEAD", "LIVE"))


class _RespCombo:
    def __init__(self, types=None):
        self.types = types or {}

    def GetNameList(self, *args):
        return (0, 2, ("C1", "ENV"))

    def ComboType(self, name, *args):
        if name not in self.types:
            raise RuntimeError("sin firma")
        return (self.types[name], 0)  # (ComboType, ret): el escritorio toma el primer numérico


def _filas_forces(valores_max, valores_min):
    """Filas de la tabla Element Forces: (AreaElem, Joint, StepType, F11, M11)."""
    rows = []
    for elm, pts in _ELEMS.items():
        for pt in pts:
            rows.append((elm, pt, "Max", valores_max[elm], valores_max[elm] * 10))
            rows.append((elm, pt, "Min", valores_min[elm], valores_min[elm] * 10))
    return rows


def _tabla(columnas, filas):
    plano = tuple(str(v) for fila in filas for v in fila)
    return (0, tuple(columnas), len(filas), plano)


class _DatabaseTables:
    def __init__(self, raw=None, error=False):
        self.raw = raw
        self.error = error
        self.tables = []

    def GetTableForDisplayArray(self, table, *args):
        self.tables.append(table)
        if self.error:
            raise RuntimeError("no table")
        return self.raw


class _Results:
    def __init__(self, area_force=None):
        self.Setup = _Setup()
        self._area_force = area_force

    def AreaForceShell(self, elm, *args):
        if self._area_force is None:
            raise RuntimeError("sin resultados")
        return self._area_force(elm)

    AreaStressShell = AreaForceShell


class FakeModel:
    def __init__(self, table=None, table_error=False, area_force=None, locked=True, combo_types=None):
        self.AreaObj = _AreaObj()
        self.AreaElm = _AreaElm()
        self.PointObj = _PointObj()
        self.LoadCases = _LoadCases()
        self.RespCombo = _RespCombo(combo_types)
        self.Results = _Results(area_force)
        self.DatabaseTables = _DatabaseTables(table, table_error)
        self.locked = locked
        self.unit_calls: list[int] = []

    def GetModelIsLocked(self):
        return self.locked

    def GetPresentUnits(self):
        return 6

    def SetPresentUnits(self, value):
        self.unit_calls.append(value)
        return 0


class _Actor:
    def call(self, fn):
        return fn()


class _Session:
    actor = _Actor()

    def __init__(self, model):
        self.model = model

    def _model(self):
        return self.model


def _client(model) -> TestClient:
    app = FastAPI()
    install_routes(app, _Session(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


COLS_FORCES = ["AreaElem", "Joint", "StepType", "F11", "M11"]


def _modelo_forces(**kw):
    filas = _filas_forces({"E1": 1.0, "E2": 3.0}, {"E1": -2.0, "E2": -6.0})
    return FakeModel(table=_tabla(COLS_FORCES, filas), **kw)


# ---------------------------------------------------------------------------
# shell_geometry: z como campo aparte
# ---------------------------------------------------------------------------


def test_shell_geometry_fills_joints_z_without_changing_joints():
    model = FakeModel()
    joints_z: dict[str, float] = {}
    elementos, joints, _epa = shell_geometry(model, ["S1"], joints_z)
    assert joints["J1"] == [0.0, 0.0]  # contrato de shell-diagram intacto: solo X/Y
    assert joints_z["J1"] == 5.0 and joints_z["J2"] == 0.0
    assert elementos["E2"] == ["J2", "J5", "J6", "J3"]
    # Sin joints_z: misma firma/forma de antes.
    assert shell_geometry(model, ["S1"])[1] == joints


# ---------------------------------------------------------------------------
# shell-results
# ---------------------------------------------------------------------------


def test_shell_results_forces_table_path_with_paso_and_units():
    model = _modelo_forces()
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results",
        json={"shells": ["S1"], "combinacion": "C1", "tipo": "forces", "componente": "M11", "paso": "max"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"elementos", "joints", "joints_z", "resultados", "mensaje_sin_resultados"}
    assert body["joints_z"]["J1"] == 5.0
    assert body["resultados"]["E1"] == {"J1": 10.0, "J2": 10.0, "J3": 10.0, "J4": 10.0}
    assert body["resultados"]["E2"]["J5"] == 30.0
    assert body["mensaje_sin_resultados"] == ""
    assert model.DatabaseTables.tables == ["Element Forces - Area Shells"]
    # Ton_m_C forzado y restaurado
    assert model.unit_calls == [12, 6]
    # Caso seleccionado para output
    assert ("combo", "C1", True) in model.Results.Setup.calls


def test_shell_results_paso_min_and_sin_paso():
    model = _modelo_forces()
    body = {"shells": ["S1"], "combinacion": "C1", "tipo": "forces", "componente": "F11"}
    r = _client(model).post("/v1/sap/actions/foundations/shell-results", json={**body, "paso": "MIN"})
    assert r.json()["resultados"]["E1"]["J1"] == -2.0
    # Sin paso: última fila gana (comportamiento previo del escritorio)
    r = _client(model).post("/v1/sap/actions/foundations/shell-results", json=body)
    assert r.json()["resultados"]["E1"]["J1"] == -2.0


def test_shell_results_sin_geometria_keeps_keys_but_empty_geometry():
    model = _modelo_forces()
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results",
        json={
            "shells": ["S1"],
            "combinacion": "C1",
            "tipo": "forces",
            "componente": "F11",
            "sin_geometria": True,
        },
    )
    body = r.json()
    assert body["elementos"] == {} and body["joints"] == {} and body["joints_z"] == {}
    assert body["resultados"]["E1"]["J1"] == -2.0


def test_shell_results_falls_back_to_per_element_when_table_fails():
    orden = fs._ORDEN_COMPONENTES_STRESSES

    def area_stress(elm):
        pts = list(_ELEMS[elm])
        n = len(pts)
        vacio = [""] * n
        arrays = [vacio, [elm] * n, pts, vacio, ["Max", "Min", "Max", "Min"], vacio]
        for i, _comp in enumerate(orden):
            arrays.append([float(i * 100 + (1 if elm == "E1" else 2)) + k for k in range(n)])
        return (n, *arrays, 0)

    model = FakeModel(table_error=True, area_force=area_stress)
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results",
        json={
            "shells": ["S1"],
            "combinacion": "DEAD",
            "tipo": "stresses",
            "componente": "SVM",
            "paso": "max",
        },
    )
    assert r.status_code == 200, r.text
    # SVM es el índice 6 de _ORDEN_COMPONENTES_STRESSES: valores 601+k; paso max = índices 0 y 2
    assert r.json()["resultados"]["E1"] == {"J1": 601.0, "J3": 603.0}
    assert set(model.DatabaseTables.tables) == {"Element Stresses - Area Shells"}
    assert ("case", "DEAD", True) in model.Results.Setup.calls


def test_shell_results_soil_pressure_ignores_paso_and_requires_no_step():
    cols = ["Area", "AreaElem", "Joint", "Pressure"]
    filas = [("S1", "1", "J1", "-1.5"), ("S1", "1", "J2", "-2.5"), ("S1", "2", "J5", "-3.5")]
    model = FakeModel(table=_tabla(cols, filas))
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results",
        json={
            "shells": ["S1"],
            "combinacion": "C1",
            "tipo": "soil_pressure",
            "componente": "Pressure",
            "paso": "max",
        },
    )
    assert r.status_code == 200, r.text
    # AreaElem local 1-based -> E1/E2 vía Area + orden de GetElm
    assert r.json()["resultados"] == {"E1": {"J1": -1.5, "J2": -2.5}, "E2": {"J5": -3.5}}
    assert model.DatabaseTables.tables == ["Element Soil Pressure - Area Shells"]


def test_shell_results_empty_gives_literal_message_by_lock_state():
    model = FakeModel(table_error=True, locked=False)
    body = {"shells": ["S1"], "combinacion": "C1", "tipo": "forces", "componente": "F11"}
    r = _client(model).post("/v1/sap/actions/foundations/shell-results", json=body)
    assert r.status_code == 200
    assert r.json()["resultados"] == {}
    assert r.json()["mensaje_sin_resultados"] == fs.MSG_MODELO_NO_CORRIDO
    model.locked = True
    r = _client(model).post("/v1/sap/actions/foundations/shell-results", json=body)
    assert r.json()["mensaje_sin_resultados"] == fs.MSG_CORRIDO_SIN_RESULTADOS


@pytest.mark.parametrize(
    "extra",
    [
        {"tipo": "forces", "componente": "S11"},
        {"tipo": "otro", "componente": "F11"},
        {"tipo": "forces", "componente": "F11", "paso": "mid"},
        {"shells": []},
    ],
)
def test_shell_results_validation_422(extra):
    body = {"shells": ["S1"], "combinacion": "C1", "tipo": "forces", "componente": "F11", **extra}
    r = _client(FakeModel()).post("/v1/sap/actions/foundations/shell-results", json=body)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# shell-results-multi
# ---------------------------------------------------------------------------

FRANJA_X = {"centro_x": 1.0, "centro_y": 0.5, "largo": 2.0, "ancho": 1.0, "largo_en_x": True}


def test_shell_results_multi_governing_extreme_per_franja_and_combo():
    model = _modelo_forces()
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results-multi",
        json={
            "shells": ["S1"],
            "combos": [{"combinacion": "C1", "paso": "max"}, {"combinacion": "C1", "paso": "min"}],
            "tipo": "forces",
            "componentes": ["F11", "M11"],
            "franjas": [FRANJA_X],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errores"] == []
    assert body["filas"] == [
        # max: E1=1, E2=3 -> estaciones (0.5,1),(1.5,3): gobierna 3; M11 30
        {"franja": 0, "combinacion": "C1", "paso": "max", "valores": {"F11": 3.0, "M11": 30.0}},
        # min: E1=-2, E2=-6 -> gobierna -6
        {"franja": 0, "combinacion": "C1", "paso": "min", "valores": {"F11": -6.0, "M11": -60.0}},
    ]
    # La tabla se pidió una sola vez por combo (no una por componente)
    assert model.DatabaseTables.tables == ["Element Forces - Area Shells"] * 2


def test_shell_results_multi_franja_outside_mesh_has_empty_valores_and_errors_reported():
    model = _modelo_forces()
    fuera = {"centro_x": 50.0, "centro_y": 50.0, "largo": 1.0, "ancho": 1.0, "largo_en_x": False}
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results-multi",
        json={
            "shells": ["S1"],
            "combos": [{"combinacion": "C1"}],
            "tipo": "forces",
            "componentes": ["F11"],
            "franjas": [FRANJA_X, fuera],
        },
    )
    filas = r.json()["filas"]
    assert filas[0]["valores"] == {"F11": -6.0}
    assert filas[1] == {"franja": 1, "combinacion": "C1", "paso": None, "valores": {}}


def test_shell_results_multi_default_componentes_and_validation():
    model = _modelo_forces()
    r = _client(model).post(
        "/v1/sap/actions/foundations/shell-results-multi",
        json={
            "shells": ["S1"],
            "combos": [{"combinacion": "C1", "paso": "max"}],
            "tipo": "forces",
            "franjas": [FRANJA_X],
        },
    )
    # Tabla solo con F11/M11: el resto cae al loop por elemento y no da datos
    assert r.json()["filas"][0]["valores"] == {"F11": 3.0, "M11": 30.0}
    for bad in ({"franjas": []}, {"combos": []}, {"componentes": ["S11"]}):
        body = {
            "shells": ["S1"],
            "combos": [{"combinacion": "C1"}],
            "tipo": "forces",
            "franjas": [FRANJA_X],
            **bad,
        }
        assert (
            _client(model).post("/v1/sap/actions/foundations/shell-results-multi", json=body).status_code
            == 422
        )


def test_calcular_estaciones_franja_buckets_use_python_round():
    # Elementos de tamaño 1: s = 0.5 y 1.5 -> buckets round(0.5)=0 y round(1.5)=2 (bancario)
    elementos = {k: list(v) for k, v in _ELEMS.items()}
    joints = {k: [v[0], v[1]] for k, v in _COORDS.items() if k.startswith("J")}
    est = fs.calcular_estaciones_franja(elementos, joints, {"E1": {"J1": 2.0}, "E2": {"J2": 4.0}}, FRANJA_X)
    assert est == [(0.5, 2.0), (1.5, 4.0)]
    assert fs.extremo_gobernante(est) == 4.0
    assert fs.extremo_gobernante([(0, -5.0), (1, 4.0)]) == -5.0
    assert fs.extremo_gobernante([]) is None


# ---------------------------------------------------------------------------
# combo types / cases
# ---------------------------------------------------------------------------


def test_foundations_cases_adds_envolventes_backward_compatible():
    r = _client(FakeModel(combo_types={"ENV": 1, "C1": 0})).get("/v1/sap/actions/foundations/cases")
    assert r.status_code == 200
    assert r.json() == {"load_cases": ["DEAD", "LIVE"], "combos": ["C1", "ENV"], "envolventes": ["ENV"]}


def test_combo_type_three_tolerant_signatures_and_failure():
    class RC:
        def __init__(self, mode):
            self.mode = mode

        def ComboType(self, name, *args):
            if self.mode == "scalar":
                return 1
            if self.mode == "tuple2" and len(args) == 1 and not isinstance(args[0], list):
                return (1, 0)
            if self.mode == "byref" and args and isinstance(args[0], list):
                args[0][0] = 1
                return 0
            raise TypeError("firma")

    import types

    for mode in ("scalar", "tuple2", "byref"):
        assert fs.combo_type(types.SimpleNamespace(RespCombo=RC(mode)), "X") == 1, mode
    assert fs.combo_type(types.SimpleNamespace(RespCombo=RC("none")), "X") is None
    assert fs.envelope_combos(types.SimpleNamespace(RespCombo=RC("none")), ["X"]) == []


# ---------------------------------------------------------------------------
# pedestal-candidates
# ---------------------------------------------------------------------------


class _FrameObj:
    def __init__(self, frames, axes=None):
        self.frames = frames
        self.axes = axes or {}

    def GetNameList(self, *args):
        return (0, len(self.frames), tuple(self.frames))

    def GetPoints(self, name):
        return self.frames[name][0] + (0,)

    def GetSection(self, name):
        return (self.frames[name][1], "", 0)

    def GetLocalAxes(self, name):
        if name not in self.axes:
            raise RuntimeError("sin ejes")
        return (0, self.axes[name], False)  # (ret, Ang, Advanced)


class _PropFrame:
    def __init__(self, rects, circles):
        self.rects = rects
        self.circles = circles

    def GetRectangle(self, sec):
        # (FileName, MatProp, T3, T2, Color, Notes, GUID, ret)
        t3, t2 = self.rects[sec]
        return ("", "CONC", t3, t2, 255, "", "", 0)

    def GetCircle(self, sec):
        return ("", "CONC", self.circles[sec], 255, "", "", 0)


def _pedestal_model():
    m = FakeModel()
    m.PointObj = _PointObjFrames()
    m.FrameObj = _FrameObj(
        {
            "COL1": (("T1", "J2"), "R40x60"),  # vertical, pie en J2 (z=0 < 3)
            "COL2": (("J3", "TJ3"), "C50"),  # circular
            "COL3": (("J6", "TJ6"), "RARA"),  # sección desconocida
            "VIGA": (("J1", "J2"), "R40x60"),  # no vertical
            "COL4": (("FX", "TFX"), "R40x60"),  # vertical pero pie fuera de la malla
        },
        axes={"COL1": 90.0, "COL2": 200.0},
    )
    m.PropFrame = _PropFrame({"R40x60": (0.6, 0.4)}, {"C50": 0.5})
    return m


class _PointObjFrames(_PointObj):
    extra = {"TJ3": (1.0, 1.0, 3.0), "TJ6": (2.0, 1.0, 3.0), "TFX": (9.0, 9.0, 3.0)}

    def GetCoordCartesian(self, name):
        if name in self.extra:
            x, y, z = self.extra[name]
            return (0, x, y, z)
        return super().GetCoordCartesian(name)


def test_pedestal_candidates_literal_port():
    model = _pedestal_model()
    r = _client(model).post(
        "/v1/sap/actions/foundations/pedestal-candidates",
        json={"joints": ["J1", "J2", "J3", "J4", "J5", "J6"]},
    )
    assert r.status_code == 200, r.text
    cands = {c["frame"]: c for c in r.json()["candidatos"]}
    contrato = json.loads(CONTRACTS_PATH.read_text("utf-8"))["routes"]
    spec = contrato["POST /v1/sap/actions/foundations/pedestal-candidates"]
    assert set(r.json()["candidatos"][0]) == set(spec["items"]["candidatos"])
    assert list(cands) == ["COL1", "COL2", "COL3"]
    assert cands["COL1"] == {
        "frame": "COL1", "largo": 0.6, "ancho": 0.4, "largo_en_x": False,  # 90 grados
        "centro_x": 1.0, "centro_y": 0.0, "aproximado": False, "motivo_aviso": "",
    }  # fmt: skip
    assert cands["COL2"]["largo"] == cands["COL2"]["ancho"] == 0.5
    assert cands["COL2"]["aproximado"] is True
    assert cands["COL2"]["motivo_aviso"] == "Sección circular, aproximada como cuadrado"
    assert cands["COL2"]["largo_en_x"] is True  # 200 % 180 = 20 < 45
    assert cands["COL3"]["largo"] == cands["COL3"]["ancho"] == 0.30
    assert (
        cands["COL3"]["motivo_aviso"] == "Sección no reconocida, dimensión de relleno — revisar manualmente"
    )
    assert cands["COL3"]["largo_en_x"] is True  # sin GetLocalAxes legible: True


def test_pedestal_candidates_empty_when_no_frames_or_no_shared_joint():
    model = _pedestal_model()
    r = _client(model).post("/v1/sap/actions/foundations/pedestal-candidates", json={"joints": ["J4"]})
    assert r.json() == {"candidatos": []}
    model.FrameObj = None  # GetNameList falla -> []
    r = _client(model).post("/v1/sap/actions/foundations/pedestal-candidates", json={"joints": ["J2"]})
    assert r.json() == {"candidatos": []}
    assert (
        _client(model)
        .post("/v1/sap/actions/foundations/pedestal-candidates", json={"joints": []})
        .status_code
        == 422
    )
