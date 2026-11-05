"""Tests de bridge/modificacion_fundaciones.py con un doble de SAP2000 (sin COM real)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_bridge_str2fund import TargetModel

from bridge import modificacion_fundaciones as mf
from bridge.str2fund import Str2FundError

CONTRACTS = json.loads(
    (Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json").read_text("utf-8")
)["routes"]


class ModifModel(TargetModel):
    """TargetModel + frames, restricciones, secciones de frame y cargas uniformes de área."""

    def __init__(self):
        super().__init__()
        self.locked = False
        self.restraints: dict[str, list[bool]] = {}
        self.uniform: dict[str, list[tuple[str, int, float]]] = {}
        self.frame_pts: dict[str, tuple[str, str]] = {}
        self.frame_section: dict[str, str] = {}
        self.frame_angle: dict[str, float] = {}
        self.frame_props = {"C40x60": ("rect", "CONC", 0.6, 0.4), "CIRC50": ("circ", "CONC", 0.5, 0.5)}
        self.selected: list[tuple[int, str]] = []
        self.reject_uniform_long = False
        model = self

        class Point(type(self.PointObj)):
            def SetRestraint(inner, name, value, *rest):
                model.restraints[name] = list(value)
                return 0

            def GetRestraint(inner, name):
                return (tuple(model.restraints.get(name, [False] * 6)), 0)

        class Frame(type(self.FrameObj)):
            def GetNameList(inner):
                return (0, len(model.frame_pts), tuple(model.frame_pts))

            def GetPoints(inner, name):
                return (*model.frame_pts[name], 0)

            def GetSection(inner, name):
                return (model.frame_section[name], "", 0)

            def GetLocalAxes(inner, name):
                return (model.frame_angle.get(name, 0.0), False, 0)

        class Area(type(self.AreaObj)):
            def SetLoadUniform(inner, name, pattern, value, direction, replace, csys="Global", item=0):
                if (
                    model.reject_uniform_long
                    and len((name, pattern, value, direction, replace, csys, item)) == 7
                ):
                    raise TypeError("signature")
                rows = (
                    [r for r in model.uniform.get(name, []) if r[0] != pattern]
                    if replace
                    else model.uniform.get(name, [])
                )
                model.uniform[name] = [*rows, (pattern, direction, value)]
                return 0

            def GetLoadUniform(inner, name, *_a):
                rows = model.uniform.get(name, [])
                n = len(rows)
                return (
                    n,
                    tuple(name for _ in rows),
                    tuple(r[0] for r in rows),
                    tuple("Global" for _ in rows),
                    tuple(r[1] for r in rows),
                    tuple(r[2] for r in rows),
                    0,
                )

        class PropFrame:
            def GetNameList(inner):
                return (0, len(model.frame_props), tuple(model.frame_props))

            def GetRectangle(inner, name, *_a):
                kind, mat, t3, t2 = model.frame_props[name]
                if kind != "rect":
                    return (name, mat, 0.0, 0.0, 0, "", "", 1)
                return (name, mat, t3, t2, -1, "", "", 0)

            def GetCircle(inner, name, *_a):
                kind, mat, t3, _t2 = model.frame_props[name]
                if kind != "circ":
                    return (name, mat, 0.0, -1, "", "", 1)
                return (name, mat, t3, -1, "", "", 0)

        class Select:
            def GetSelected(inner, *_a):
                return (
                    len(model.selected),
                    tuple(t for t, _ in model.selected),
                    tuple(n for _, n in model.selected),
                    0,
                )

        for attr, cls in (("PointObj", Point), ("FrameObj", Frame), ("AreaObj", Area)):
            old = getattr(self, attr)
            new = cls()
            new.__dict__.update(old.__dict__)
            setattr(self, attr, new)
        self.PropFrame = PropFrame()
        self.SelectObj = Select()

    def GetModelIsLocked(self):
        return self.locked

    # utilidades de prueba
    def add_column(self, name, base, top, section="C40x60", angle=0.0, i=None, j=None):
        i = i or f"B{name}"
        j = j or f"T{name}"
        self.points[i] = base
        self.points[j] = top
        self.frame_pts[name] = (i, j)
        self.frame_section[name] = section
        self.frame_angle[name] = angle


def _model():
    m = ModifModel()
    m.add_column("12", (5.0, 3.0, 0.0), (5.0, 3.0, 3.0))
    m.patterns.add("D")
    m.cases.add("D")
    return m


def _plan(**over):
    plan = {
        "schema": "modif_plan_v1",
        "naming_version": 1,
        "name": "F1",
        "level_z": 0.0,
        "centre": {"x": 5.0, "y": 3.0},
        "bounds": {"x_min": 4.0, "x_max": 6.0, "y_min": 2.0, "y_max": 4.0},
        "warnings": [],
        "errors": [],
        "points": [
            {
                "ref": "p0",
                "name": "101",
                "x": 4.0,
                "y": 2.0,
                "z": 0.0,
                "existing": None,
                "restraint": {"u1": True, "u2": True},
            },
            {
                "ref": "p1",
                "name": "102",
                "x": 6.0,
                "y": 2.0,
                "z": 0.0,
                "existing": None,
                "restraint": {"u1": True, "u2": True},
            },
            {
                "ref": "p2",
                "name": "103",
                "x": 6.0,
                "y": 4.0,
                "z": 0.0,
                "existing": None,
                "restraint": {"u1": True, "u2": True},
            },
            {
                "ref": "p3",
                "name": "104",
                "x": 4.0,
                "y": 4.0,
                "z": 0.0,
                "existing": None,
                "restraint": {"u1": True, "u2": True},
            },
            {"ref": "p4", "name": "B12", "x": 5.0, "y": 3.0, "z": 0.0, "existing": "B12"},
        ],
        "areas": [
            {"name": "1", "section": "Losa e=600", "points": ["101", "102", "B12", "104"]},
            {"name": "2", "section": "Losa e=600", "points": ["102", "103", "104", "B12"]},
        ],
        "area_sections": [
            {
                "name": "Losa e=600",
                "thickness": 0.6,
                "material": "CONC",
                "shell_type": "thick",
                "create": True,
            }
        ],
        "group": {"name": "F1", "create": True, "points": ["101", "102"], "areas": ["1", "2"]},
        "load": {"pattern": "D", "uniform": -2.0, "areas": ["1", "2"]},
        "object_names": {"points": ["101", "102", "103", "104"], "areas": ["1", "2"]},
        "hash": "h1",
    }
    plan.update(over)
    return plan


# --- lectura -----------------------------------------------------------------


def test_read_frames_lower_node_rect_footprint_and_existing():
    m = _model()
    out = mf.read_frames(m, ["12"])
    assert out["schema"] == "modif_source_v1" and out["units"] == "Ton_m_C"
    frame = out["frames"][0]
    assert frame["lower_joint"] == "B12" and frame["upper_joint"] == "T12"
    assert frame["lower"] == {"x": 5.0, "y": 3.0, "z": 0.0}
    assert frame["section"] == "C40x60" and frame["vertical"] is True
    assert frame["footprint"] == {"shape": "rect", "lx": 0.6, "ly": 0.4}
    assert set(out["existing"]["points"]) == {"B12", "T12"} and out["existing"]["frames"] == ["12"]
    assert m.units == 6  # unidades restauradas


def test_read_frames_lower_when_frame_defined_top_first_and_rotation():
    m = ModifModel()
    m.add_column("7", (1.0, 1.0, -2.0), (1.0, 1.0, 1.0), angle=90.0, i="TOP", j="BOT")
    m.points["TOP"], m.points["BOT"] = (1.0, 1.0, 1.0), (1.0, 1.0, -2.0)
    frame = mf.read_frames(m, ["7"])["frames"][0]
    assert frame["lower_joint"] == "BOT" and frame["lower"]["z"] == -2.0
    assert frame["footprint"]["lx"] == pytest.approx(0.4) and frame["footprint"]["ly"] == pytest.approx(0.6)
    assert any("giro" in n for n in frame["notes"])


def test_read_frames_circular_and_unreadable_section():
    m = _model()
    m.add_column("13", (8.0, 3.0, 0.0), (8.0, 3.0, 3.0), section="CIRC50")
    m.add_column("14", (9.0, 3.0, 0.0), (9.0, 3.0, 3.0), section="DESCONOCIDA")
    m.frame_props["DESCONOCIDA"] = ("other", "CONC", 0.0, 0.0)
    frames = {f["name"]: f for f in mf.read_frames(m, ["13", "14"])["frames"]}
    assert frames["13"]["footprint"] == {"shape": "circ", "d": 0.5}
    assert frames["14"]["footprint"] is None and frames["14"]["notes"]


def test_read_frames_uses_selection_and_rejects_unknown():
    m = _model()
    m.selected = [(1, "B12"), (2, "12")]
    assert [f["name"] for f in mf.read_frames(m)["frames"]] == ["12"]
    with pytest.raises(mf.ModifError, match="no existe"):
        mf.read_frames(m, ["99"])
    m.selected = []
    with pytest.raises(mf.ModifError, match="seleccionados"):
        mf.read_frames(m, [])


def test_read_catalog():
    m = _model()
    m.PropArea.SetShell_1("Losa e=500", 2, True, "CONC", 0.0, 0.5, 0.5)
    m.PropArea.SetShell_1("Fina", 1, True, "CONC", 0.0, 0.2, 0.2)
    m.groups["F1"] = {"points": set(), "frames": set(), "areas": set()}
    cat = mf.read_catalog(m)
    assert cat["area_sections"] == [
        {"name": "Losa e=500", "thickness": 0.5, "material": "CONC", "shell_type": "thick"},
        {"name": "Fina", "thickness": 0.2, "material": "CONC", "shell_type": "thin"},
    ]
    assert cat["materials"] == ["CONC"] and cat["load_patterns"] == ["D"]
    assert set(cat["groups"]) == {"ALL", "F1"}


# --- vista previa --------------------------------------------------------------


def test_preview_ok_counts():
    result = mf.preview(_model(), _plan())
    assert result["conflicts"] == [] and result["ok"] is True and result["model_locked"] is False
    assert result["will_create"] == {"points": 4, "areas": 2, "area_sections": 1, "groups": 1}


def test_preview_conflicts():
    m = _model()
    m.points["101"] = (0.0, 0.0, 0.0)
    m.areas["1"] = {"points": [], "property": "x"}
    m.PropArea.SetShell_1("Losa e=600", 2, True, "CONC", 0.0, 0.5, 0.5)
    m.groups["F1"] = {"points": set(), "frames": set(), "areas": set()}
    m.locked = True
    plan = _plan(load={"pattern": "NOEXISTE", "uniform": -1.0, "areas": ["1"]})
    plan["points"][4]["x"] = 5.5  # el nodo existente ya no está donde se espera
    result = mf.preview(m, plan)
    text = " | ".join(result["conflicts"])
    assert result["ok"] is False and result["model_locked"] is True
    for fragment in ("punto 101", "área 1", "Losa e=600", "grupo nuevo F1", "NOEXISTE", "B12", "bloqueado"):
        assert fragment in text, fragment


def test_preview_existing_section_and_group_ok_and_case_insensitive_names():
    m = _model()
    m.PropArea.SetShell_1("Losa e=600", 2, True, "CONC", 0.0, 0.6, 0.6)
    m.groups["F1"] = {"points": set(), "frames": set(), "areas": set()}
    plan = _plan(
        area_sections=[
            {
                "name": "LOSA E=600",
                "thickness": 0.6,
                "material": "conc",
                "shell_type": "thick",
                "create": False,
            }
        ],
        group={"name": "f1", "create": False, "points": ["101"], "areas": ["1"]},
    )
    result = mf.preview(m, plan)
    assert result["conflicts"] == [] and result["will_create"]["area_sections"] == 0
    m.points["102"] = (0, 0, 0)
    assert any("102" in c for c in mf.preview(m, plan)["conflicts"])


def test_preview_missing_material_and_missing_group_and_plan_errors():
    m = _model()
    plan = _plan(
        area_sections=[{"name": "N", "thickness": 0.6, "material": "H99", "create": True}],
        group={"name": "NOGRP", "create": False, "points": [], "areas": []},
        errors=["algo mal"],
    )
    text = " | ".join(mf.preview(m, plan)["conflicts"])
    assert "H99" in text and "NOGRP" in text and "algo mal" in text


# --- apply ---------------------------------------------------------------------


def test_apply_happy_path_with_verification():
    m = _model()
    result = mf.apply(m, _plan(), "h1")
    assert result["ok"] is True, result
    assert result["verification"] == {"ok": True, "issues": []}
    assert result["created"] == {
        "area_sections": 1,
        "points": 4,
        "areas": 2,
        "restraints": 4,
        "groups": 1,
        "loads": 2,
    }
    assert m.points["103"] == (6.0, 4.0, 0.0) and set(m.points) >= {"101", "102", "103", "104"}
    assert m.areas["1"]["points"] == ["101", "102", "B12", "104"] and m.areas["1"]["property"] == "Losa e=600"
    assert m.PropArea.sections["Losa e=600"] == ("CONC", 0.6, 0.6) and m.PropArea.types["Losa e=600"] == 2
    assert m.restraints["101"] == [True, True, False, False, False, False]
    assert "B12" not in m.restraints
    assert m.groups["F1"]["points"] == {"101", "102"} and m.groups["F1"]["areas"] == {"1", "2"}
    assert m.uniform["1"] == [("D", 6, -2.0)] and m.uniform["2"] == [("D", 6, -2.0)]
    assert m.units == 6


def test_apply_reuses_existing_node_and_keeps_its_restraints():
    m = _model()
    m.restraints["B12"] = [False, False, True, False, False, False]
    plan = _plan()
    plan["points"][4]["restraint"] = {"u1": True, "u2": True}
    result = mf.apply(m, plan, "h1")
    assert result["ok"] is True and result["created"]["points"] == 4
    assert m.restraints["B12"] == [True, True, True, False, False, False]
    assert list(m.points).count("B12") == 1


def test_apply_existing_group_and_section_without_load_or_restraints():
    m = _model()
    m.PropArea.SetShell_1("Losa e=600", 2, True, "CONC", 0.0, 0.6, 0.6)
    m.groups["G"] = {"points": {"X"}, "frames": set(), "areas": set()}
    plan = _plan(
        area_sections=[
            {
                "name": "Losa e=600",
                "thickness": 0.6,
                "material": "CONC",
                "shell_type": "thick",
                "create": False,
            }
        ],
        group={"name": "G", "create": False, "points": ["101"], "areas": ["1"]},
        load=None,
    )
    for p in plan["points"]:
        p.pop("restraint", None)
    result = mf.apply(m, plan, "h1")
    assert result["ok"] is True
    assert result["created"] == {
        "area_sections": 0,
        "points": 4,
        "areas": 2,
        "restraints": 0,
        "groups": 0,
        "loads": 0,
    }
    assert m.groups["G"]["points"] == {"X", "101"} and m.groups["G"]["areas"] == {"1"}
    assert m.restraints == {} and m.uniform == {}


def test_apply_rejects_hash_mismatch_locked_and_conflicts():
    m = _model()
    with pytest.raises(mf.ModifError, match="hash"):
        mf.apply(m, _plan(), "otro")
    with pytest.raises(mf.ModifError, match="hash"):
        mf.apply(m, _plan(), "")
    assert "101" not in m.points
    m.locked = True
    with pytest.raises(mf.ModifError, match="bloqueado"):
        mf.apply(m, _plan(), "h1")
    m.locked = False
    m.points["101"] = (0.0, 0.0, 0.0)
    with pytest.raises(mf.ModifError, match="conflictos"):
        mf.apply(m, _plan(), "h1")
    with pytest.raises(mf.ModifError, match="errores"):
        mf.apply(_model(), _plan(errors=["x"]), "h1")
    assert isinstance(mf.ModifError("x"), Str2FundError)


def test_apply_load_falls_back_to_shorter_signature():
    m = _model()
    original = m.AreaObj.SetLoadUniform

    def strict(name, pattern, value, direction, replace, *rest):
        if rest:
            raise TypeError("too many")
        return original(name, pattern, value, direction, replace)

    m.AreaObj.SetLoadUniform = strict
    assert mf.apply(m, _plan(), "h1")["ok"] is True


def test_apply_reports_verification_issues():
    m = _model()
    original = m.AreaObj.SetLoadUniform
    m.AreaObj.SetLoadUniform = lambda name, pat, v, d, r, *a: original(name, pat, v * 2, d, r)
    result = mf.apply(m, _plan(), "h1")
    assert result["ok"] is False and result["verification"]["ok"] is False
    assert any("carga" in i and "-4.0" in i for i in result["verification"]["issues"])


def test_apply_partial_error_has_detail():
    m = _model()
    m.AreaObj.SetLoadUniform = lambda *a: 5
    with pytest.raises(mf.ModifPartialError) as info:
        mf.apply(m, _plan(), "h1")
    detail = info.value.detail
    assert detail["partial"] is True and detail["created"]["areas"] == 2


def test_point_name_returned_by_sap_is_verified():
    m = _model()
    original = m.PointObj.AddCartesian
    m.PointObj.AddCartesian = lambda x, y, z, name, user, *a: (
        original(x, y, z, name, "auto" + user, *a)[0],
        0,
    )
    with pytest.raises(mf.ModifPartialError, match="instead of requested"):
        mf.apply(m, _plan(), "h1")


# --- rutas ---------------------------------------------------------------------


class _Actor:
    def __init__(self, model):
        self.model = model

    def call(self, fn):
        return fn()


def _client(model):
    from fastapi import HTTPException

    app = FastAPI()

    def call(fn):
        try:
            return fn(model)
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            raise HTTPException(409, detail if detail is not None else str(exc)) from exc

    mf.install_routes(app, call)
    return TestClient(app)


def _keys(route, body):
    assert set(body) == set(CONTRACTS[f"POST /v1/sap/actions/foundations/modif/{route}"]["keys"])


def test_routes_match_contract():
    m = _model()
    client = _client(m)
    base = "/v1/sap/actions/foundations/modif"
    body = client.post(f"{base}/frames", json={"frames": ["12"]}).json()
    _keys("frames", body)
    assert set(body["frames"][0]) == set(CONTRACTS[f"POST {base}/frames"]["items"]["frames"])
    body = client.post(f"{base}/catalog", json={}).json()
    _keys("catalog", body)
    body = client.post(f"{base}/preview", json={"plan": _plan()}).json()
    _keys("preview", body)
    assert body["ok"] is True
    body = client.post(f"{base}/apply", json={"plan": _plan(), "expected_hash": "h1"}).json()
    _keys("apply", body)
    assert body["ok"] is True
    bad = client.post(f"{base}/apply", json={"plan": _plan(), "expected_hash": "zzz"})
    assert bad.status_code == 409 and "hash" in bad.json()["detail"]


def test_routes_are_installed_by_bridge_actions():
    from bridge.actions import install_routes as install_all

    app = FastAPI()
    install_all(app, type("S", (), {"actor": _Actor(None), "_model": lambda self: None})(), RuntimeError)
    paths = set(app.openapi()["paths"])
    for name in ("frames", "catalog", "preview", "apply"):
        assert f"/v1/sap/actions/foundations/modif/{name}" in paths


def test_footprint_math_helper_is_consistent():
    # giro de 45°: la caja envolvente de 0.6 x 0.4 mide (0.6 + 0.4) / sqrt(2) en cada eje
    m = _model()
    m.frame_angle["12"] = 45.0
    fp = mf.read_frames(m, ["12"])["frames"][0]["footprint"]
    assert fp["lx"] == pytest.approx(1.0 / math.sqrt(2)) and fp["ly"] == pytest.approx(1.0 / math.sqrt(2))
