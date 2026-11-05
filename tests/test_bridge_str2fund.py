import os
import sys
import types

import pytest

from bridge.str2fund import (
    Str2FundError,
    _add_area,
    _add_frame,
    _add_point,
    apply_target,
    extract_source,
    preview_target,
    update_target_loads,
)


def test_csi_add_calls_put_requested_names_in_username_argument():
    calls = {}

    class Point:
        def AddCartesian(inner, x, y, z, name, username, csys, merge_off, merge_number):
            calls["point"] = (name, username, csys, merge_off, merge_number)
            assert name == ""
            return (username, 0)

    class Frame:
        def AddByPoint(inner, point1, point2, name, prop_name, username):
            calls["frame"] = (name, prop_name, username)
            assert name == ""
            return (username, 0)

    class Area:
        def AddByPoint(inner, count, points, name, prop_name, username):
            calls["area"] = (count, list(points), name, prop_name, username)
            assert name == ""
            return (username, 0)

    _add_point(types.SimpleNamespace(PointObj=Point()), {"name": "12", "x": 1, "y": 2, "z": 3})
    _add_frame(types.SimpleNamespace(FrameObj=Frame()), {"name": "PED_12", "i": "12", "j": "13", "section": "S2F_PED_A"})
    _add_area(types.SimpleNamespace(AreaObj=Area()), {"name": "FTG_A", "points": ["12", "13", "14", "15"], "section": "S2F_FTG_A"})
    assert calls["point"] == ("", "12", "Global", True, 0)
    assert calls["frame"] == ("", "S2F_PED_A", "PED_12")
    assert calls["area"] == (4, ["12", "13", "14", "15"], "", "S2F_FTG_A", "FTG_A")


class SourceModel:
    def __init__(self):
        self.units = 6
        self.case_selected = {"DEAD": True}
        self.combo_selected = {"COMBO": True}

        class Point:
            def GetNameList(inner):
                return (0, 2, ("10", "20"))

            def GetCoordCartesian(inner, name):
                return (0, 0.0, 0.0, 0.0) if name == "10" else (0, 0.0, 0.0, 4.0)

        class Frame:
            def GetNameList(inner):
                return (0, 1, ("C1",))

            def GetPoints(inner, name):
                return ("10", "20", 0)

            def GetSection(inner, name):
                return ("RECT300x400", "", 0)

            def GetLocalAxes(inner, name):
                return (0.0, False, 0)

        class PropFrame:
            def GetRectangle(inner, name):
                return ("CONC", 0.4, 0.3, -1, "", "", 0)

        class LoadCases:
            def GetNameList(inner):
                return (0, 1, ("DEAD",))

            def GetTypeOAPI(inner, name):
                return (1, 0)

        class RespCombo:
            def GetNameList(inner):
                return (0, 1, ("COMBO",))

        class Setup:
            def GetCaseSelectedForOutput(inner, name):
                return (self.case_selected[name], 0)

            def GetComboSelectedForOutput(inner, name):
                return (self.combo_selected[name], 0)

            def DeselectAllCasesAndCombosForOutput(inner):
                self.case_selected = {"DEAD": False}
                self.combo_selected = {"COMBO": False}
                return 0

            def SetCaseSelectedForOutput(inner, name, enabled):
                self.case_selected[name] = enabled
                return 0

            def SetComboSelectedForOutput(inner, name, enabled):
                self.combo_selected[name] = enabled
                return 0

        class Results:
            def JointReact(inner, joint, item_type):
                return (
                    1,
                    (joint,),
                    (joint,),
                    ("DEAD",),
                    ("",),
                    (0.0,),
                    (10.0,),
                    (20.0,),
                    (30.0,),
                    (1.0,),
                    (2.0,),
                    (3.0,),
                    0,
                )

        self.PointObj = Point()
        self.FrameObj = Frame()
        self.PropFrame = PropFrame()
        self.LoadCases = LoadCases()
        self.RespCombo = RespCombo()
        self.Results = Results()
        self.Results.Setup = Setup()

    def GetModelFilename(self):
        return (r"C:\models\source.sdb", 0)

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, value):
        self.units = value
        return 0

    def GetVersion(self):
        return (0, "26.0.0")


def test_source_extracts_vertical_rectangular_supports_and_restores_sap_state():
    model = SourceModel()
    result = extract_source(model, ["10"], ["DEAD"])["source_snapshot"]

    assert result["model"]["units"] == "Ton_m_C"
    assert result["model"]["path"].endswith("source.sdb")
    assert result["supports"][0]["joint"] == "10"
    assert result["supports"][0]["column"]["width_x"] == pytest.approx(0.3)
    assert result["supports"][0]["column"]["width_y"] == pytest.approx(0.4)
    assert result["reactions"][0] == {
        "joint": "10",
        "case": "DEAD",
        "step_type": "",
        "step_num": 0.0,
        "f1": 10.0,
        "f2": 20.0,
        "f3": 30.0,
        "m1": 1.0,
        "m2": 2.0,
        "m3": 3.0,
    }
    assert model.units == 6
    assert model.case_selected == {"DEAD": True}
    assert model.combo_selected == {"COMBO": True}
    assert len(result["model"]["hash"]) == 64


def test_source_extracts_response_spectrum_magnitude_and_preserves_max_step_type():
    model = SourceModel()
    model.LoadCases.GetNameList = lambda: (0, 2, ("DEAD", "SPEC_X"))
    model.case_selected["SPEC_X"] = False
    model.LoadCases.GetTypeOAPI = lambda name: (4 if name == "SPEC_X" else 1, 0)

    def joint_react(joint, item_type):
        return (
            2,
            (joint, joint),
            (joint, joint),
            ("DEAD", "SPEC_X"),
            ("", "Max"),
            (0.0, 0.0),
            (10.0, 101.0),
            (20.0, 202.0),
            (30.0, 303.0),
            (1.0, 10.1),
            (2.0, 20.2),
            (3.0, 30.3),
            0,
        )

    model.Results.JointReact = joint_react
    snapshot = extract_source(model, ["10"], ["SPEC_X"])["source_snapshot"]

    assert snapshot["cases"] == [{
        "name": "SPEC_X",
        "kind": "response_spectrum",
        "eligible": True,
        "eligible_for_sign": False,
        "eligible_for_magnitude": True,
        "reason": None,
    }]
    assert snapshot["reactions"] == [{
        "joint": "10", "case": "SPEC_X", "step_type": "Max", "step_num": 0.0,
        "f1": 101.0, "f2": 202.0, "f3": 303.0,
        "m1": 10.1, "m2": 20.2, "m3": 30.3,
    }]


def test_source_rejects_unsaved_model_and_restores_units():
    model = SourceModel()
    model.GetModelFilename = lambda: ""
    with pytest.raises(Str2FundError, match="Save the source"):
        extract_source(model, [], [])
    assert model.units == 6


def _model_with_ex():
    """SourceModel con un segundo caso «EX» cuya selección se puede falsear."""
    model = SourceModel()
    model.LoadCases.GetNameList = lambda: (0, 2, ("DEAD", "EX"))
    model.case_selected["EX"] = True
    original = model.Results.Setup.GetCaseSelectedForOutput
    model.get_overrides = {}

    def get_case(name):
        if name in model.get_overrides:
            override = model.get_overrides[name]
            if isinstance(override, Exception):
                raise override
            return override
        return original(name)

    model.Results.Setup.GetCaseSelectedForOutput = get_case
    return model


def test_source_unreadable_selection_warns_and_restores_only_what_was_read():
    model = _model_with_ex()
    model.get_overrides["EX"] = (False, 1)
    result = extract_source(model, ["10"], ["DEAD"])

    assert result["warnings"] == [
        "No se pudo leer la selección de salida de: EX. Tras la lectura se restauró lo que sí se pudo "
        "leer; revise la selección de salida en SAP2000."
    ]
    assert "warnings" not in result["source_snapshot"]
    assert result["source_snapshot"]["reactions"][0]["case"] == "DEAD"
    # DEAD se leyó y se restaura; EX era desconocido y no se vuelve a marcar.
    assert model.case_selected == {"DEAD": True}
    assert model.combo_selected == {"COMBO": True}
    assert model.units == 6


def test_source_selection_warning_does_not_change_stable_hash():
    clean = extract_source(SourceModel(), ["10"], ["DEAD"])["source_snapshot"]
    model = _model_with_ex()
    model.LoadCases.GetNameList = lambda: (0, 1, ("DEAD",))
    model.Results.Setup.GetCaseSelectedForOutput = lambda name: (True, 1)
    result = extract_source(model, ["10"], ["DEAD"])
    assert result["warnings"]
    assert result["source_snapshot"]["model"]["hash"] == clean["model"]["hash"]


@pytest.mark.parametrize(
    "response, readable",
    [
        ((True, 0), True),
        ((False, 0), True),
        ((0, True), True),
        ((True,), True),
        (True, True),
        (False, True),
        ((1, 0), True),
        ((False, 1), False),
        ((1, False), False),
        ((1,), False),
        (1, False),
    ],
)
def test_source_accepts_varied_selection_return_shapes(response, readable):
    model = _model_with_ex()
    model.get_overrides["EX"] = response
    result = extract_source(model, ["10"], ["DEAD"])
    assert ("warnings" in result) is (not readable)


def test_source_com_error_on_one_selection_is_unknown_not_fatal():
    model = _model_with_ex()
    model.get_overrides["EX"] = RuntimeError("COM error")
    result = extract_source(model, ["10"], ["DEAD"])
    assert "EX" in result["warnings"][0]
    assert model.case_selected == {"DEAD": True}


def test_source_selection_warning_truncates_and_deduplicates_names():
    from bridge.str2fund import _selection_warning

    names = [f"C{i}" for i in range(13)]
    text = _selection_warning(names)
    assert "C9 y 3 más." in text and "C10" not in text
    model = _model_with_ex()
    model.RespCombo.GetNameList = lambda: (0, 1, ("EX",))
    model.Results.Setup.GetComboSelectedForOutput = lambda name: (True, 1)
    model.get_overrides["EX"] = (True, 1)
    warning = extract_source(model, ["10"], ["DEAD"])["warnings"][0]
    assert warning.count("EX") == 1


@pytest.mark.parametrize("bad", ["texto", None, 1.5, ("a", 0), (), (2, 0)])
def test_source_malformed_selection_response_still_fails_closed(bad):
    model = _model_with_ex()
    model.get_overrides["EX"] = bad
    with pytest.raises(Str2FundError):
        extract_source(model, ["10"], ["DEAD"])
    assert model.units == 6


def test_source_model_without_results_gives_clear_message():
    expected = "El modelo no tiene resultados para los casos pedidos; ejecute el análisis en SAP2000 e intente de nuevo."
    model = SourceModel()
    model.Results.JointReact = lambda joint, item_type: (0, (), (), (), (), (), (), (), (), (), (), (), 1)
    with pytest.raises(Str2FundError) as failed:
        extract_source(model, ["10"], ["DEAD"])
    assert str(failed.value) == expected

    model = SourceModel()
    model.Results.JointReact = lambda joint, item_type: (0, (), (), (), (), (), (), (), (), (), (), (), 0)
    with pytest.raises(Str2FundError) as failed:
        extract_source(model, ["10"], ["DEAD"])
    assert str(failed.value) == expected
    assert model.case_selected == {"DEAD": True}


class TargetModel:
    def __init__(self):
        self.path = os.path.abspath("target.sdb")
        self.units = 6
        self.points = {}
        self.frames = {}
        self.areas = {}
        self.patterns = set()
        self.cases = set()
        self.loads = {}
        self.groups = {"ALL": {"points": set(), "frames": set(), "areas": set()}}

        class Point:
            def GetNameList(inner):
                return (0, len(self.points), tuple(self.points))

            def GetCoordCartesian(inner, name):
                return (0, *self.points[name])

            def AddCartesian(inner, x, y, z, name, user_name, csys, merge_off, merge_number):
                assert name == ""
                self.points[user_name] = (x, y, z)
                return (user_name, 0)

            def SetLoadForce(inner, joint, pattern, forces, replace, csys):
                self.loads.setdefault(joint, {})[pattern] = list(forces)
                return 0

            def SetGroupAssign(inner, name, group, *rest):
                self.groups[group]["points"].add(name)
                return 0

            def GetLoadForce(inner, joint):
                mapping = self.loads.get(joint, {})
                patterns = list(mapping)
                vectors = [[mapping[p][i] for p in patterns] for i in range(6)]
                return (len(patterns), tuple(patterns), tuple("Global" for _ in patterns), *[tuple(v) for v in vectors], 0)

        class Frame:
            def GetNameList(inner):
                return (0, len(self.frames), tuple(self.frames))

            def SetGroupAssign(inner, name, group, *rest):
                self.groups[group]["frames"].add(name)
                return 0

        class Area:
            def GetNameList(inner):
                return (0, len(self.areas), tuple(self.areas))

            def AddByPoint(inner, count, points, name, prop_name, user_name):
                assert count == 4 and name == ""
                self.areas[user_name] = {"points": list(points), "property": prop_name}
                return (user_name, 0)

            def GetPoints(inner, name):
                return (4, tuple(self.areas[name]["points"]), 0)

            def GetProperty(inner, name):
                return (self.areas[name]["property"], 0)

            def SetGroupAssign(inner, name, group, *rest):
                self.groups[group]["areas"].add(name)
                return 0

        class LoadPatterns:
            def GetNameList(inner):
                return (0, len(self.patterns), tuple(self.patterns))

            def Add(inner, name, pattern_type, selfwt, add_case):
                self.patterns.add(name)
                self.cases.add(name)
                return 0

            def GetLoadType(inner, name):
                return (0, 1)

            def GetSelfWTMultiplier(inner, name):
                return (0, 1.0)

            def ChangeName(inner, old, new):
                self.patterns.discard(old)
                self.patterns.add(new)
                return 0

        class RespCombo:
            def GetNameList(inner):
                return (0, 0, None)

        class GroupDef:
            def GetNameList(inner):
                return (0, len(self.groups), tuple(self.groups))

            def SetGroup(inner, name, *rest):
                self.groups.setdefault(name, {"points": set(), "frames": set(), "areas": set()})
                return 0

            def GetAssignments(inner, name):
                group = self.groups[name]
                types, objects = [], []
                for code, key in ((1, "points"), (2, "frames"), (5, "areas")):
                    for obj in sorted(group[key]):
                        types.append(code)
                        objects.append(obj)
                return (0, len(objects), tuple(types), tuple(objects))

        class LoadCases:
            def GetNameList(inner):
                return (0, len(self.cases), tuple(self.cases))

            def GetTypeOAPI(inner, name):
                return (1, 0)

            def ChangeName(inner, old, new):
                self.cases.discard(old)
                self.cases.add(new)
                return 0

        class Names:
            def GetNameList(inner):
                return (0, 1, ("CONC",))

        class AreaProperties:
            def __init__(inner):
                inner.sections = {}
                inner.types = {}

            def GetNameList(inner):
                names = tuple(inner.sections)
                return (0, len(names), names)

            def SetShell_1(
                inner, section, shell_type, drilling, material, angle, thickness, bending, color=-1, notes="", guid=""
            ):
                inner.sections[section] = (material, thickness, bending)
                inner.types[section] = shell_type
                return 0

            def GetShell_1(inner, name):
                material, thickness, bending = inner.sections[name]
                return (0, inner.types[name], True, material, 0.0, thickness, bending, 0, "", "")

        self.PointObj = Point()
        self.FrameObj = Frame()
        self.AreaObj = Area()
        self.LoadPatterns = LoadPatterns()
        self.LoadCases = LoadCases()
        self.RespCombo = RespCombo()
        self.GroupDef = GroupDef()
        self.PropFrame = Names()
        self.PropArea = AreaProperties()
        self.PropMaterial = Names()

    def GetModelFilename(self):
        return self.path

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, value):
        self.units = value
        return 0


def _plan():
    return {
        "schema_version": "str2fund_plan_v1",
        "source": {"path": os.path.abspath("source.sdb"), "hash": "source-hash", "units": "Ton_m_C"},
        "points": [
            {"name": "10", "x": 0.0, "y": 0.0, "z": 0.0},
            {"name": "11", "x": 1.0, "y": 0.0, "z": 0.0},
            {"name": "12", "x": 1.0, "y": 1.0, "z": 0.0},
            {"name": "13", "x": 0.0, "y": 1.0, "z": 0.0},
        ],
        "frames": [],
        "areas": [{"name": "FTG_A", "points": ["10", "11", "12", "13"], "section": "S2F_FTG_A", "thickness": 0.5, "material": "CONC", "group": "A"}],
        "patterns": [{"name": "DEAD", "source_case": "DEAD", "self_weight": 0.0}],
        "loads": [{"joint": "10", "pattern": "DEAD", "f1": -10, "f2": -20, "f3": -30, "m1": -1, "m2": -2, "m3": -3, "coord_sys": "Global"}],
        "manifest": {"id": "test-plan"},
    }


def test_target_requires_preview_identity_and_returns_verified_manifest():
    model = TargetModel()
    plan = _plan()
    preview = preview_target(model, plan)
    assert preview["will_mutate"] is False
    assert preview["conflicts"] == []

    result = apply_target(model, plan, True, preview["target"]["path"], preview["fingerprint"])
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["failures"] == []
    assert result["manifest"]["status"] == "verified"
    assert result["created"]["loads"] == 1
    assert model.loads["10"]["DEAD"] == [-10, -20, -30, -1, -2, -3]
    assert model.units == 6


def test_update_loads_changes_only_managed_reactions_and_refreshes_manifest():
    model = TargetModel()
    plan = _plan()
    preview = preview_target(model, plan)
    applied = apply_target(model, plan, True, preview["target"]["path"], preview["fingerprint"])
    new_plan = _plan()
    new_plan["source"]["hash"] = "new-source-hash"
    new_plan["loads"][0]["f3"] = -45
    result = update_target_loads(model, applied["manifest"], new_plan, True, applied["target"]["path"], applied["fingerprint"])

    assert result["ok"] is True
    assert result["verified"] is True
    assert model.loads["10"]["DEAD"] == [-10, -20, -45, -1, -2, -3]
    assert result["manifest"]["source_hash"] == "new-source-hash"
    assert result["manifest"]["status"] == "verified"


def test_target_refuses_stale_preview_and_duplicate_point_names():
    model = TargetModel()
    plan = _plan()
    preview = preview_target(model, plan)
    model.points["other"] = (1.0, 0.0, 0.0)
    with pytest.raises(Str2FundError, match="changed after preview"):
        apply_target(model, plan, True, preview["target"]["path"], preview["fingerprint"])


def test_target_refuses_same_source_and_destination_path():
    model = TargetModel()
    plan = _plan()
    plan["source"]["path"] = model.path
    with pytest.raises(Str2FundError, match="source model is currently active"):
        preview_target(model, plan)


def _install_fake_com(monkeypatch, model, helper_result=None):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.IID_IDispatch = "IDispatch"

    class SapObject:
        SapModel = model

        def QueryInterface(self, iid):
            return self

    sap_obj = SapObject()
    pythoncom.GetActiveObject = lambda clsid: sap_obj
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)

    winreg = types.ModuleType("winreg")
    winreg.HKEY_CLASSES_ROOT = object()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    winreg.OpenKey = lambda root, path: Key()
    winreg.QueryValueEx = lambda key, value: ("{01234567-89AB-CDEF-0123-456789ABCDEF}", 1)
    monkeypatch.setitem(sys.modules, "winreg", winreg)

    win32com = types.ModuleType("win32com")
    win32com.__path__ = []
    client = types.ModuleType("win32com.client")
    client.__path__ = []
    dynamic = types.ModuleType("win32com.client.dynamic")

    def dispatch(value):
        if isinstance(value, str):
            class Helper:
                def GetObject(self, prog_id):
                    return helper_result if helper_result is not None else sap_obj

            if "Helper" in value:
                return Helper()
            raise RuntimeError("unexpected Dispatch ProgID")
        return value

    dynamic.Dispatch = dispatch
    client.dynamic = dynamic
    win32com.client = client
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setitem(sys.modules, "win32com.client.dynamic", dynamic)


def _install_fake_fastapi(monkeypatch):
    """Keep attach-only tests independent of web-server dependencies."""
    fastapi = types.ModuleType("fastapi")

    class App:
        def __init__(self, **_kwargs): pass
        def add_middleware(self, *_args, **_kwargs): pass
        def middleware(self, *_args, **_kwargs): return lambda fn: fn
        def get(self, *_args, **_kwargs): return lambda fn: fn
        def post(self, *_args, **_kwargs): return lambda fn: fn

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail
            super().__init__(str(detail))

    fastapi.FastAPI = App
    fastapi.HTTPException = HTTPException
    fastapi.Request = object
    middleware = types.ModuleType("fastapi.middleware")
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = object
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "fastapi.middleware", middleware)
    monkeypatch.setitem(sys.modules, "fastapi.middleware.cors", cors)
    import bridge.actions as actions
    import bridge.pairing as pairing
    monkeypatch.setattr(actions, "install_routes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pairing, "install_routes", lambda *_args, **_kwargs: None)


def test_attach_falls_back_to_registry_when_pythoncom_lacks_clsidfromprogid(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    _install_fake_fastapi(monkeypatch)
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetModelFilename(self):
            return (r"C:\models\target.sdb", 0)

        def GetPresentUnits(self):
            return 12

    model = Model()
    _install_fake_com(monkeypatch, model, helper_result=object())
    session = SapSession()
    session.actor.call = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    result = session.attach(r"C:\models\target.sdb")
    assert result["connected"] is True
    assert result["model_path"].endswith("target.sdb")
    assert result["method"].startswith("GetActiveObject")


def test_attach_keeps_diagnostic_when_helper_returns_no_sapmodel(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    _install_fake_fastapi(monkeypatch)
    from bridge.sap2000_agent import SapComError, SapSession

    class Model:
        def GetModelFilename(self):
            return (r"C:\models\target.sdb", 0)

        def GetPresentUnits(self):
            return 12

    class NoModel:
        pass

    _install_fake_com(monkeypatch, Model(), helper_result=NoModel())
    # Disable active-object attach so the helper failure is the diagnostic.
    import pythoncom

    pythoncom.GetActiveObject = lambda clsid: (_ for _ in ()).throw(RuntimeError("not running"))
    session = SapSession()
    session.actor.call = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    with pytest.raises(SapComError, match="returned an object without SapModel"):
        session.attach()
    assert session.model is None
    assert session.sap_object is None


# --- Detección robusta de apoyos -------------------------------------------


def _model_with(nodes, frames, sections, restraints=None):
    """Modelo fake: nodes {n:(x,y,z)}, frames {f:(i,j,section)}, sections {s:(kind,values)}."""
    model = SourceModel()

    class Point:
        def GetNameList(inner):
            return (0, len(nodes), tuple(nodes))

        def GetCoordCartesian(inner, name):
            return (0, *nodes[name])

    class Frame:
        def GetNameList(inner):
            return (0, len(frames), tuple(frames))

        def GetPoints(inner, name):
            return (frames[name][0], frames[name][1], 0)

        def GetSection(inner, name):
            return (frames[name][2], "", 0)

        def GetLocalAxes(inner, name):
            return (0.0, False, 0)

    class PropFrame:
        pass

    for kind in ("Rectangle", "Circle"):
        def getter(inner, name, kind=kind):
            k, vals = sections[name]
            if k != kind:
                raise RuntimeError("not this type")
            return vals

        setattr(PropFrame, f"Get{kind}", getter)

    if restraints is not None:
        def get_restraint(inner, name):
            return (tuple(restraints[name]), 0)

        Point.GetRestraint = get_restraint
    model.PointObj = Point()
    model.FrameObj = Frame()
    model.PropFrame = PropFrame()
    return model


RESTRAINED = (True, True, True, False, False, False)
FREE = (False,) * 6


def test_support_circular_column_uses_diameter():
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3)},
        {"C": ("1", "2", "CIR")},
        {"CIR": ("Circle", ("", "CONC", 0.5, -1, "", "", 0))},
        {"1": RESTRAINED, "2": FREE},
    )
    snap = extract_source(model, [], [])["source_snapshot"]
    col = snap["available_supports"][0]["column"]
    assert col["width_x"] == pytest.approx(0.5) and col["width_y"] == pytest.approx(0.5)
    assert col["material"] == "CONC"


def test_support_coordinate_tolerance_accepts_small_noise():
    model = _model_with(
        {"1": (0, 0, 0), "2": (5e-5, -5e-5, 3)},
        {"C": ("1", "2", "R")},
        {"R": ("Rectangle", ("", "CONC", 0.4, 0.3, -1, "", "", 0))},
        {"1": RESTRAINED, "2": FREE},
    )
    snap = extract_source(model, [], [])["source_snapshot"]
    assert [s["joint"] for s in snap["available_supports"]] == ["1"]


def test_intermediate_floor_unrestrained_is_not_support_and_is_reported():
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3), "3": (0, 0, 6), "9": (5, 5, 0)},
        {"C1": ("1", "2", "R"), "C2": ("2", "3", "R")},
        {"R": ("Rectangle", ("", "CONC", 0.4, 0.3, -1, "", "", 0))},
        {"1": RESTRAINED, "2": FREE, "3": FREE, "9": RESTRAINED},
    )
    result = extract_source(model, [], [])
    snap = result["source_snapshot"]
    assert [s["joint"] for s in snap["available_supports"]] == ["1"]
    assert "2" in snap["excluded_supports"]
    diag = result["support_diagnostics"]
    assert diag["mode"] == "restraint"
    assert diag["excluded"] == [{"joint": "9", "reason": "no_vertical_frame"}]
    assert any("9" in w for w in result["warnings"])


def test_unsupported_section_is_reported_not_silent():
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3)},
        {"C": ("1", "2", "WF")},
        {"WF": ("Other", ())},
        {"1": RESTRAINED, "2": FREE},
    )
    result = extract_source(model, [], [])
    assert result["source_snapshot"]["available_supports"] == []
    exc = result["support_diagnostics"]["excluded"][0]
    assert exc["reason"] == "unsupported_section" and "WF" in exc["detail"]


def test_coincident_vertical_frames_pick_largest_section():
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3)},
        {"A": ("1", "2", "SMALL"), "B": ("2", "1", "BIG")},
        {
            "SMALL": ("Rectangle", ("", "CONC", 0.3, 0.3, -1, "", "", 0)),
            "BIG": ("Rectangle", ("", "CONC", 0.6, 0.5, -1, "", "", 0)),
        },
        {"1": RESTRAINED, "2": FREE},
    )
    sup = extract_source(model, [], [])["source_snapshot"]["available_supports"]
    assert len(sup) == 1 and sup[0]["column"]["section"] == "BIG"
    assert sup[0]["column"]["width_x"] == pytest.approx(0.5)


@pytest.mark.parametrize("file_name", ["", "C:\\sections\\lib.pro"])
def test_getrectangle_real_order_with_empty_or_filled_filename(file_name):
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3)},
        {"C": ("1", "2", "R")},
        {"R": ("Rectangle", (file_name, "CONC", 0.4, 0.3, -1, "note", "guid", 0))},
        {"1": RESTRAINED, "2": FREE},
    )
    col = extract_source(model, [], [])["source_snapshot"]["available_supports"][0]["column"]
    assert col["material"] == "CONC"
    assert col["width_x"] == pytest.approx(0.3) and col["width_y"] == pytest.approx(0.4)


def test_missing_getrestraint_falls_back_to_geometry_with_warning():
    model = _model_with(
        {"1": (0, 0, 0), "2": (0, 0, 3)},
        {"C": ("1", "2", "R")},
        {"R": ("Rectangle", ("", "CONC", 0.4, 0.3, -1, "", "", 0))},
    )
    result = extract_source(model, [], [])
    assert result["support_diagnostics"]["mode"] == "geometric"
    assert [s["joint"] for s in result["source_snapshot"]["available_supports"]] == ["1"]
    assert "warnings" not in result


@pytest.mark.parametrize(
    "result, kind",
    [((1, 0, 0), "linear_static"), ((0, 4, 0), "response_spectrum"), ((4, 0, 0), "response_spectrum")],
)
def test_case_type_uses_get_type_oapi_1_with_either_return_order(result, kind):
    from bridge.str2fund import _case_type

    class LoadCases:
        def GetTypeOAPI_1(self, name, *outs):
            return result

    class Model:
        pass

    model = Model()
    model.LoadCases = LoadCases()
    info = _case_type(model, "X")
    assert info["kind"] == kind and info["eligible"] is True


def test_com_retries_with_placeholders_for_late_bound_calls():
    from bridge.str2fund import _com

    def getter(name, *outs):
        if len(outs) != 2:
            raise TypeError("parameter not optional")
        return (0, "A", "B")

    assert _com(getter, "F1", outs=("", "")) == (0, "A", "B")


def test_case_type_falls_back_to_probing_case_objects():
    from bridge.str2fund import _case_type

    class RS:
        def GetLoads(self, name, *outs):
            return (0, 1, ("EQ",), ("F",), (1.0,), ("Global",), (0.0,)) if name == "SX" else (0, 1, 1)

    class SL:
        def GetLoads(self, name, *outs):
            return (1,) if name == "SX" else (1, ("Load",), ("DEAD",), (1.0,), 0)

    class LoadCases:
        ResponseSpectrum = RS()
        StaticLinear = SL()

    class Model:
        pass

    model = Model()
    model.LoadCases = LoadCases()
    Model = lambda: model  # noqa: E731

    assert _case_type(Model(), "SX")["kind"] == "response_spectrum"
    info = _case_type(Model(), "DEAD")
    assert info["kind"] == "linear_static" and info["eligible_for_sign"] is True


def test_section_dims_supports_i_sections():
    from bridge.str2fund import _section_dims

    class PropFrame:
        def GetISection(self, name, *outs):
            return ("", "A992", 0.3, 0.2, 0.02, 0.01, 0.2, 0.02, 0, "", "", 0)

    class Model:
        pass

    model = Model()
    model.PropFrame = PropFrame()
    assert _section_dims(model, "W12") == ("A992", 0.3, 0.2)


class _FakeMaterials:
    def __init__(self, existing=()):
        self.mats = {n: {"type": 2, "E": 2.5e6, "U": 0.2, "A": 1e-5, "W": 2.4, "design": [300.0, False, 1.0, 2, 4, 0.002, 0.005, -0.1, 0.0, 0.0, 0.0]} for n in existing}

    def GetNameList(self, *a):
        return (0, len(self.mats), tuple(self.mats))

    def GetMaterial(self, name, *outs):
        return (0, self.mats[name]["type"], 0, "", "")

    def GetMPIsotropic(self, name, *outs):
        m = self.mats[name]
        return (0, m["E"], m["U"], m["A"], 1.0)

    def GetWeightAndMass(self, name, *outs):
        return (0, self.mats[name]["W"], 0.2)

    def GetOConcrete_1(self, name, *outs):
        return (0, *self.mats[name]["design"])

    def SetMaterial(self, name, kind):
        self.mats[name] = {"type": kind}
        return 0

    def SetMPIsotropic(self, name, e, u, a):
        self.mats[name].update(E=e, U=u, A=a)
        return 0

    def SetWeightAndMass(self, name, option, w):
        self.mats[name]["W"] = w
        return 0

    def SetOConcrete_1(self, name, *design):
        self.mats[name]["design"] = list(design)
        return 0


def test_source_materials_reads_definitions_and_create_replays_them(tmp_path):
    from bridge.str2fund import _material_definition, _source_materials, create_materials

    class Model:
        def __init__(self, path, mats):
            self.PropMaterial = _FakeMaterials(mats)
            self.path = path

        def GetModelFilename(self):
            return (self.path, 0)

        def GetPresentUnits(self):
            return 12

    source = Model("C:/src.sdb", ["G30"])
    defs = _source_materials(source)
    assert defs["G30"]["kind"] == "concrete" and defs["G30"]["E"] == 2.5e6
    assert defs["G30"]["design"][0] == 300.0 and defs["G30"]["weight_per_volume"] == 2.4

    target = Model("C:/dst.sdb", [])
    out = create_materials(target, [defs["G30"]], True, "C:/dst.sdb", "C:/src.sdb")
    assert out["created"] == ["G30"] and out["verified"] is True
    assert target.PropMaterial.mats["G30"]["design"][0] == 300.0
    assert _material_definition(target, "G30")["E"] == 2.5e6


def test_create_materials_refuses_source_model_and_missing_confirmation():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, create_materials

    class Model:
        PropMaterial = _FakeMaterials()

        def GetModelFilename(self):
            return ("C:/src.sdb", 0)

        def GetPresentUnits(self):
            return 12

    with _pytest.raises(Str2FundError):
        create_materials(Model(), [{"name": "G30"}], False, "C:/src.sdb")
    with _pytest.raises(Str2FundError):
        create_materials(Model(), [{"name": "G30"}], True, "C:/src.sdb", "C:/src.sdb")


def test_call_set_prefers_full_signature_and_rejects_non_numeric_result():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, _call_set

    seen = []

    def full_only(*args):
        seen.append(len(args))
        if len(args) != 7:
            raise TypeError("wrong arity")
        return 0

    _call_set(full_only, "op", (1, 2, 3, 4, -1, "", ""), (1, 2, 3, 4))
    assert seen == [7]

    with _pytest.raises(Str2FundError):
        _call_set(lambda *a: "G30", "op", (1,))


def test_add_point_retries_variants_and_reports_locked_model():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, _add_point

    calls = []

    class PointObj:
        def AddCartesian(self, *args):
            calls.append(len(args))
            if len(args) == 8 and args[6] is True:
                return ("", 1)
            return ("1", 0)

    class Model:
        pass

    model = Model()
    model.PointObj = PointObj()
    _add_point(model, {"name": "1", "x": 0, "y": 0, "z": 0})
    assert calls == [8, 8]

    class Locked:
        class PointObj:
            @staticmethod
            def AddCartesian(*args):
                return ("", 1)

        @staticmethod
        def GetModelIsLocked():
            return True

    with _pytest.raises(Str2FundError, match="bloqueado"):
        _add_point(Locked(), {"name": "1", "x": 0, "y": 0, "z": 0})


def test_set_load_force_retries_signatures_and_explains_failure():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, _set_load_force

    seen = []

    class PointObj:
        def SetLoadForce(self, *args):
            seen.append(len(args))
            return 0 if len(args) == 6 and args[3] is True else 1

    class Model:
        pass

    model = Model()
    model.PointObj = PointObj()
    load = {"joint": "1", "pattern": "P", "coord_sys": "Global", "f1": 1.0}
    _set_load_force(model, load)
    assert seen == [5, 6]

    class Bad:
        class PointObj:
            @staticmethod
            def SetLoadForce(*args):
                return 1

        class LoadPatterns:
            @staticmethod
            def GetNameList():
                return (0, 1, ("OTRO",))

    with _pytest.raises(Str2FundError, match="no existe"):
        _set_load_force(Bad(), load)


def test_set_load_force_skips_all_zero_loads_without_calling_sap():
    from bridge.str2fund import _set_load_force

    calls = []

    class PointObj:
        def SetLoadForce(self, *args):
            calls.append(args)
            return 1

        def DeleteLoadForce(self, joint, pattern):
            calls.append(("delete", joint, pattern))
            return 0

    class Model:
        pass

    model = Model()
    model.PointObj = PointObj()
    _set_load_force(model, {"joint": "1", "pattern": "P", "f1": 0.0, "m3": -0.0})
    assert calls == [("delete", "1", "P")]


@pytest.mark.parametrize("ret_first", [True, False])
def test_read_joint_loads_handles_both_return_orders(ret_first):
    from bridge.str2fund import _read_joint_loads

    body = (
        2,
        ("1", "1"),
        ("P1", "P2"),
        ("", ""),
        ("Global", "Global"),
        (1.0, 4.0),
        (2.0, 5.0),
        (3.0, 6.0),
        (0.0, 0.0),
        (0.0, 0.0),
        (0.0, 0.0),
    )
    raw = (0, *body) if ret_first else (*body, 0)
    loads = _read_joint_loads(lambda joint: raw, "1", {"P1", "P2"})
    assert loads["P1"] == [1.0, 2.0, 3.0, 0.0, 0.0, 0.0]
    assert loads["P2"] == [4.0, 5.0, 6.0, 0.0, 0.0, 0.0]


def _dead_model():
    model = TargetModel()
    model.patterns.add("DEAD")
    model.cases.add("DEAD")
    return model


def test_rename_default_dead_renames_pattern_and_case():
    from bridge.str2fund import _rename_default_dead

    model = _dead_model()
    assert _rename_default_dead(model, "Df") is True
    assert "Df" in model.patterns and "Df" in model.cases
    assert "DEAD" not in model.patterns and "DEAD" not in model.cases
    assert _rename_default_dead(model, "Df") is False  # idempotent


def test_preflight_lets_plan_pattern_named_dead_replace_the_default_one():
    from bridge.str2fund import _preflight

    model = _dead_model()
    plan = _plan()
    plan["patterns"] = [{"name": "DEAD", "source_case": "DEAD", "kind": "linear_static", "self_weight": 0}]
    plan["loads"] = [{**load, "pattern": "DEAD"} for load in plan["loads"]]
    plan["target_setup"] = {"rename_default_dead": "Df"}
    result = _preflight(model, plan)
    assert result["will_rename_default_dead"] is True
    assert not [c for c in result["conflict_details"] if c["kind"] == "patterns"]

    plan["target_setup"] = {}
    result = _preflight(model, plan)
    assert any(c["kind"] == "patterns" and c["name"] == "DEAD" for c in result["conflict_details"])


def test_preflight_reports_pattern_that_collides_with_an_existing_case():
    from bridge.str2fund import _preflight

    model = TargetModel()
    model.cases.add("MODAL")
    plan = _plan()
    plan["patterns"] = [{"name": "modal", "source_case": "MODAL", "kind": "linear_static", "self_weight": 0}]
    plan["loads"] = [{**load, "pattern": "modal"} for load in plan["loads"]]
    result = _preflight(model, plan)
    assert any("load case" in c["reason"] for c in result["conflict_details"])


def test_groups_are_created_and_read_back():
    from bridge.str2fund import _create_group, _group_members

    model = TargetModel()
    model.points["1"] = (0, 0, 0)
    model.frames["F1"] = {}
    model.areas["A1"] = {"points": [], "property": "S"}
    _create_group(model, {"name": "F-1", "points": ["1"], "frames": ["F1"], "areas": ["A1"]})
    assert _group_members(model, "F-1") == {"points": {"1"}, "frames": {"F1"}, "areas": {"A1"}}


def test_ensure_area_section_creates_thick_shell_and_detects_a_different_existing_one():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, _ensure_area_section

    model = TargetModel()
    section = {"name": "Losa e=0.50", "thickness": 0.5, "material": "CONC", "shell_type": "thick"}
    assert _ensure_area_section(model, section) is True
    assert model.PropArea.types["Losa e=0.50"] == 2
    assert _ensure_area_section(model, section) is False  # identical: reused
    with _pytest.raises(Str2FundError, match="thickness"):
        _ensure_area_section(model, {**section, "thickness": 0.6})


def test_circular_and_rectangular_pedestal_sections_are_created_and_compared():
    import pytest as _pytest

    from bridge.str2fund import Str2FundError, _ensure_frame_section

    class PropFrame:
        def __init__(inner):
            inner.items = {}

        def GetNameList(inner):
            return (0, len(inner.items), tuple(inner.items))

        def SetRectangle(inner, name, mat, t3, t2, *rest):
            inner.items[name] = ("rect", mat, t3, t2)
            return 0

        def SetCircle(inner, name, mat, d, *rest):
            inner.items[name] = ("circle", mat, d)
            return 0

        def GetRectangle(inner, name, *outs):
            kind, mat, *dims = inner.items[name]
            return (0, "", mat, dims[0], dims[1], 0, "", "") if kind == "rect" else (1, "", "", 0.0, 0.0, 0, "", "")

        def GetCircle(inner, name, *outs):
            kind, mat, *dims = inner.items[name]
            return (0, "", mat, dims[0], 0, "", "") if kind == "circle" else (1, "", "", 0.0, 0, "", "")

    class Model:
        pass

    model = Model()
    model.PropFrame = PropFrame()
    circle = {"name": "Ped. D0.50", "shape": "circle", "diameter": 0.5, "material": "CONC"}
    rect = {"name": "Ped. 0.80x0.50", "shape": "rect", "length_x": 0.8, "length_y": 0.5, "material": "CONC"}
    assert _ensure_frame_section(model, circle) is True
    assert _ensure_frame_section(model, rect) is True
    assert model.PropFrame.items["Ped. 0.80x0.50"] == ("rect", "CONC", 0.8, 0.5)  # T3 = long side, T2 = short side
    assert _ensure_frame_section(model, circle) is False and _ensure_frame_section(model, rect) is False
    with _pytest.raises(Str2FundError, match="shape"):
        _ensure_frame_section(model, {**circle, "name": "Ped. 0.80x0.50"})
    with _pytest.raises(Str2FundError, match="dimensions"):
        _ensure_frame_section(model, {**rect, "length_x": 0.9})


def test_add_frame_rotates_local_axes_only_when_the_plan_asks_for_it():
    from bridge.str2fund import _add_frame

    calls = []

    class FrameObj:
        def AddByPoint(self, i, j, name, section, user_name):
            return (user_name, 0)

        def SetLocalAxes(self, name, angle, *rest):
            calls.append((name, angle))
            return 0

    class Model:
        pass

    model = Model()
    model.FrameObj = FrameObj()
    base = {"i": "1", "j": "2", "section": "S"}
    _add_frame(model, {**base, "name": "A", "rotation_deg": 0})
    _add_frame(model, {**base, "name": "B", "rotation_deg": 90})
    assert calls == [("B", 90.0)]
