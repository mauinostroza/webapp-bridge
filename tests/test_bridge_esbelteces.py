"""Tests for bridge/esbelteces.py: extraction, groups, sections, K factors.

Fakes follow the same minimal-COM-double pattern as tests/test_bridge_actions.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.esbelteces import (
    DesignCodeError,
    apply_k_factors,
    apply_section,
    extract_frame_data,
    group_frame_names,
    group_names,
    highlight_frames,
    import_by_groups,
    section_names,
    selected_members,
)

# ---------------------------------------------------------------------------
# Fake SAP2000 COM model
# ---------------------------------------------------------------------------


class _FrameObj:
    def __init__(self, sections: dict[str, str], points: dict[str, tuple[str, str]]):
        self.sections = sections
        self.points = points
        self.set_section_calls: list[tuple[str, str]] = []
        self.selected: list[tuple[str, bool]] = []

    def GetSection(self, frame_name, *_args):
        return (self.sections[frame_name], "", 0)

    def GetPoints(self, frame_name, *_args):
        i, j = self.points[frame_name]
        return (i, j, 0)

    def SetSection(self, frame_name, section_name):
        self.set_section_calls.append((frame_name, section_name))
        self.sections[frame_name] = section_name
        return 0

    def SetSelected(self, name, value):
        self.selected.append((name, value))
        return 0


class _PointObj:
    def __init__(self, coords: dict[str, tuple[float, float, float]]):
        self.coords = coords

    def GetCoordCartesian(self, point_name, *_args):
        x, y, z = self.coords[point_name]
        return (x, y, z, 0)


class _PropFrame:
    """Maps section name -> (prop_type, dims dict); dims per prop_type family."""

    def __init__(self, sections: dict[str, tuple[int, dict]]):
        self.sections = sections
        self.name_list = list(sections.keys())

    def GetNameList(self):
        return (0, len(self.name_list), tuple(self.name_list))

    def GetTypeOAPI(self, name, _flag):
        if name not in self.sections:
            return (0, 1)
        return (self.sections[name][0], 0)

    def _dims(self, name, expected_type):
        entry = self.sections.get(name)
        if entry is None or entry[0] != expected_type:
            return None
        return entry[1]

    def GetISection(self, name, *_args):
        d = self._dims(name, 1)
        if d is None:
            return (None,) * 11 + (3,)
        return (name, d["mat"], d["h"], d["b"], d["tf"], d["tw"], d["b"], d["tf"], -1, "", "")[:11] + (0,)

    def GetChannel(self, name, *_args):
        d = self._dims(name, 2)
        if d is None:
            return (None,) * 9 + (3,)
        return (name, d["mat"], d["h"], d["b"], d["tf"], d["tw"], -1, "", "")[:9] + (0,)

    def GetTee(self, name, *_args):
        d = self._dims(name, 3)
        if d is None:
            return (None,) * 9 + (3,)
        return (name, d["mat"], d["h"], d["b"], d["tf"], d["tw"], -1, "", "")[:9] + (0,)

    def GetAngle(self, name, *_args):
        d = self._dims(name, 4)
        if d is None:
            return (None,) * 9 + (3,)
        return (name, d["mat"], d["h"], d["b"], d["tf"], d["tw"], -1, "", "")[:9] + (0,)

    def GetTube(self, name, *_args):
        d = self._dims(name, 6)
        if d is None:
            return (None,) * 9 + (3,)
        return (name, d["mat"], d["h"], d["b"], d["tf"], d["tw"], -1, "", "")[:9] + (0,)

    def GetPipe(self, name, *_args):
        d = self._dims(name, 7)
        if d is None:
            return (None,) * 7 + (3,)
        return (name, d["mat"], d["h"], d["tw"], -1, "", "")[:7] + (0,)

    def GetRectangle(self, name, *_args):
        d = self._dims(name, 8)
        if d is None:
            return (None,) * 6 + (3,)
        return (name, d["mat"], d["h"], d["b"], -1, "", "")[:6] + (0,)

    def GetGeneral(self, name, *_args):
        d = self._dims(name, 10)
        if d is None:
            return (None,) * 18 + (3,)
        return (name, d["mat"], d["h"], d["b"]) + (0,) * 14 + (0,)

    def GetSectProps(self, name, *_args):
        d = self.sections[name][1]
        area = d.get("area", 100.0)
        i22 = d.get("i22", 400.0)
        i33 = d.get("i33", 900.0)
        return (area, 0, 0, 0, i22, i33, 0, 0, 0, 0, 0, 0, 0)


class _PropMaterial:
    def __init__(self, fy_by_material: dict[str, float]):
        self.fy_by_material = fy_by_material

    def GetOSteel_1(self, mat_prop, *_args):
        fy = self.fy_by_material.get(mat_prop)
        if fy is None:
            return (0.0, 3)
        return (fy, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0)


class _SelectObj:
    def __init__(self, selected: list[tuple[int, str]]):
        self.selected = selected
        self.cleared = False

    def GetSelected(self, *_args):
        types = tuple(t for t, _ in self.selected)
        names = tuple(n for _, n in self.selected)
        return (len(self.selected), types, names, 0)

    def ClearSelection(self):
        self.cleared = True
        return 0


class _GroupDef:
    def __init__(self, groups: dict[str, list[tuple[int, str]]]):
        self.groups = groups

    def GetNameList(self, *_args):
        names = tuple(self.groups.keys())
        return (0, len(names), names)

    def GetAssignments(self, name, *_args):
        members = self.groups.get(name, [])
        types = tuple(t for t, _ in members)
        names = tuple(n for _, n in members)
        return (len(members), types, names, 0)


class _AISC360_16:
    def __init__(self):
        self.calls: list[tuple[str, int, float, int]] = []
        self.fail_frames: set[str] = set()

    def SetOverwrite(self, frame_name, item, value, flag):
        if frame_name in self.fail_frames:
            return 7
        self.calls.append((frame_name, item, value, flag))
        return 0


class _DesignSteel:
    def __init__(self, code: str):
        self.code = code
        self.AISC360_16 = _AISC360_16()

    def GetCode(self, _name):
        return (self.code, 0)


class FakeModel:
    def __init__(
        self,
        *,
        sections=None,
        points=None,
        coords=None,
        prop_sections=None,
        fy_by_material=None,
        selected=None,
        groups=None,
        design_code="AISC 360-16",
    ):
        self.units = 6
        self.unit_calls: list[int] = []
        self.FrameObj = _FrameObj(sections or {}, points or {})
        self.PointObj = _PointObj(coords or {})
        self.PropFrame = _PropFrame(prop_sections or {})
        self.PropMaterial = _PropMaterial(fy_by_material or {})
        self.SelectObj = _SelectObj(selected or [])
        self.GroupDef = _GroupDef(groups or {})
        self.DesignSteel = _DesignSteel(design_code)
        self.locked_calls: list[bool] = []

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, value):
        self.unit_calls.append(value)
        self.units = value
        return 0

    def SetModelIsLocked(self, value):
        self.locked_calls.append(value)
        return 0


def _standard_model(**overrides) -> FakeModel:
    defaults = dict(
        sections={"F1": "W1", "F2": "CH1", "F3": "L1", "F4": "HSS1", "F5": "GEN1"},
        points={
            "F1": ("P1", "P2"),
            "F2": ("P1", "P3"),
            "F3": ("P1", "P4"),
            "F4": ("P1", "P5"),
            "F5": ("P1", "P6"),
        },
        coords={
            "P1": (0.0, 0.0, 0.0),
            "P2": (0.0, 0.0, 5000.0),
            "P3": (0.0, 0.0, 3000.0),
            "P4": (0.0, 0.0, 4000.0),
            "P5": (0.0, 0.0, 6000.0),
            "P6": (0.0, 0.0, 2000.0),
        },
        prop_sections={
            "W1": (
                1,
                {
                    "mat": "A992",
                    "h": 400.0,
                    "b": 200.0,
                    "tf": 15.0,
                    "tw": 10.0,
                    "area": 100.0,
                    "i22": 400.0,
                    "i33": 900.0,
                },
            ),
            "CH1": (
                2,
                {
                    "mat": "A36",
                    "h": 250.0,
                    "b": 90.0,
                    "tf": 12.0,
                    "tw": 8.0,
                    "area": 80.0,
                    "i22": 300.0,
                    "i33": 700.0,
                },
            ),
            "L1": (
                4,
                {
                    "mat": "A36",
                    "h": 100.0,
                    "b": 75.0,
                    "tf": 8.0,
                    "tw": 8.0,
                    "area": 13.4,
                    "i22": 47.0,
                    "i33": 120.0,
                },
            ),
            "HSS1": (
                6,
                {
                    "mat": "A500",
                    "h": 200.0,
                    "b": 200.0,
                    "tf": 8.0,
                    "tw": 8.0,
                    "area": 60.0,
                    "i22": 350.0,
                    "i33": 350.0,
                },
            ),
            "GEN1": (10, {"mat": "USER", "h": 300.0, "b": 150.0, "area": 90.0, "i22": 200.0, "i33": 800.0}),
        },
        fy_by_material={"A992": 345.0, "A36": 248.0, "A500": 315.0, "USER": 250.0},
    )
    defaults.update(overrides)
    return FakeModel(**defaults)


# ---------------------------------------------------------------------------
# extract_frame_data
# ---------------------------------------------------------------------------


def test_extract_i_section():
    model = _standard_model()
    data = extract_frame_data(model, "F1")
    assert data is not None
    assert data.prop_type_name == "I_H"
    assert data.section_name == "W1"
    assert data.material_name == "A992"
    assert data.fy_mpa == 345.0
    assert data.h_mm == 400.0 and data.b_mm == 200.0 and data.tf_mm == 15.0 and data.tw_mm == 10.0
    assert data.length_mm == pytest.approx(5000.0)
    assert data.area_mm2 == 100.0
    assert data.rx_mm == pytest.approx((900.0 / 100.0) ** 0.5)
    assert data.ry_mm == pytest.approx((400.0 / 100.0) ** 0.5)
    assert data.is_user_defined is False


def test_extract_channel_section():
    model = _standard_model()
    data = extract_frame_data(model, "F2")
    assert data.prop_type_name == "Channel"
    assert data.h_mm == 250.0 and data.b_mm == 90.0
    assert data.length_mm == pytest.approx(3000.0)


def test_extract_angle_returns_raw_data_without_rz():
    """El puente entrega el dato CRUDO del ángulo; rz, el reemplazo de ry y las notas los pone el servidor."""
    model = _standard_model()
    data = extract_frame_data(model, "F3")
    assert data.prop_type_name == "Angle"
    assert data.is_rz_calculated is False
    assert data.rz_mm is None
    # ry es el eje principal menor de SAP2000, sin reemplazar.
    assert data.ry_mm == pytest.approx((47.0 / 13.4) ** 0.5)
    assert data.notes == ""
    assert data.h_mm and data.b_mm and data.tf_mm and data.tw_mm


def test_extract_tube_section():
    model = _standard_model()
    data = extract_frame_data(model, "F4")
    assert data.prop_type_name == "Box_HSS"
    assert data.h_mm == 200.0 and data.b_mm == 200.0


def test_extract_general_section_marks_user_defined():
    model = _standard_model()
    data = extract_frame_data(model, "F5")
    assert data.prop_type_name == "General"
    assert data.is_user_defined is True
    assert data.h_mm == 300.0 and data.b_mm == 150.0


def test_extract_restores_units_even_on_failure():
    model = _standard_model()
    model.units = 6

    def boom(*_args, **_kwargs):
        raise RuntimeError("COM failure")

    model.PropFrame.GetTypeOAPI = boom
    data = extract_frame_data(model, "F1")
    assert data is None
    # units were forced to 9 (N_mm_C) then restored to the original 6.
    assert model.unit_calls[0] == 9
    assert model.units == 6


def test_extract_unknown_frame_returns_none():
    model = _standard_model()
    assert extract_frame_data(model, "NOPE") is None


# ---------------------------------------------------------------------------
# Selection / groups / sections
# ---------------------------------------------------------------------------


def test_selected_members_skips_failed_extractions():
    model = _standard_model(selected=[(2, "F1"), (2, "NOPE"), (1, "P1"), (2, "F2")])
    members = selected_members(model)
    names = [m["frame_name"] for m in members]
    assert names == ["F1", "F2"]


def test_group_names_lists_all_groups():
    model = _standard_model(groups={"GrpA": [(2, "F1")], "GrpB": []})
    assert set(group_names(model)) == {"GrpA", "GrpB"}


def test_group_frame_names_filters_frame_type():
    model = _standard_model(groups={"GrpA": [(2, "F1"), (1, "P1")]})
    assert group_frame_names(model, "GrpA") == ["F1"]


def test_import_by_groups_dedups_first_category_wins():
    model = _standard_model(
        groups={
            "GrpCols": [(2, "F1"), (2, "F2")],
            "GrpBeams": [(2, "F2"), (2, "F3")],
        }
    )
    category_map = {"Columna_SCBF": "GrpCols", "Viga_SCBF": "GrpBeams"}
    members = import_by_groups(model, category_map)
    by_frame = {m["frame_name"]: m["group_category"] for m in members}
    assert by_frame == {"F1": "Columna_SCBF", "F2": "Columna_SCBF", "F3": "Viga_SCBF"}


def test_import_by_groups_skips_blank_group_names():
    model = _standard_model(groups={"GrpCols": [(2, "F1")]})
    members = import_by_groups(model, {"Columna_SCBF": "GrpCols", "Viga_SCBF": ""})
    assert [m["frame_name"] for m in members] == ["F1"]


def test_section_names_lists_frame_properties():
    model = _standard_model()
    assert set(section_names(model)) == {"W1", "CH1", "L1", "HSS1", "GEN1"}


# ---------------------------------------------------------------------------
# apply_section
# ---------------------------------------------------------------------------


def test_apply_section_assigns_and_reextracts():
    model = _standard_model()
    result = apply_section(model, "F1", "CH1")
    assert model.FrameObj.set_section_calls == [("F1", "CH1")]
    assert model.locked_calls == [False]
    assert result["member"]["section_name"] == "CH1"
    assert result["member"]["prop_type_name"] == "Channel"


def test_apply_section_raises_literal_message_when_reextract_fails():
    model = _standard_model()

    def get_section(*_args):
        raise RuntimeError("boom")

    model.FrameObj.GetSection = get_section
    with pytest.raises(RuntimeError) as excinfo:
        apply_section(model, "F1", "CH1")
    assert str(excinfo.value) == "No se pudo re-extraer datos de F1 tras el cambio de sección."
    assert model.FrameObj.set_section_calls == [("F1", "CH1")]


# ---------------------------------------------------------------------------
# highlight
# ---------------------------------------------------------------------------


def test_highlight_clears_then_selects_each_frame():
    model = _standard_model()
    count = highlight_frames(model, ["F1", "F2"])
    assert count == 2
    assert model.SelectObj.cleared is True
    assert model.FrameObj.selected == [("F1", True), ("F2", True)]


# ---------------------------------------------------------------------------
# K factors
# ---------------------------------------------------------------------------


def test_apply_k_factors_writes_overwrites_for_both_axes():
    model = _standard_model(design_code="AISC 360-16")
    result = apply_k_factors(model, [{"frame": "F1", "kx": 1.0, "ky": 2.0}])
    assert result == {"applied": 1, "failures": []}
    assert model.DesignSteel.AISC360_16.calls == [("F1", 23, 1.0, 0), ("F1", 24, 2.0, 0)]


def test_apply_k_factors_skips_zero_values():
    model = _standard_model(design_code="AISC 360-16")
    apply_k_factors(model, [{"frame": "F1", "kx": 1.5, "ky": 0.0}])
    assert model.DesignSteel.AISC360_16.calls == [("F1", 23, 1.5, 0)]


def test_apply_k_factors_wrong_code_raises_design_code_error_writes_nothing():
    model = _standard_model(design_code="AISC 360-10")
    with pytest.raises(DesignCodeError) as excinfo:
        apply_k_factors(model, [{"frame": "F1", "kx": 1.0, "ky": 1.0}])
    assert excinfo.value.detail["code"] == "design_code"
    assert "AISC 360-10" in excinfo.value.detail["message"]
    assert "AISC 360-16" in excinfo.value.detail["message"]
    assert model.DesignSteel.AISC360_16.calls == []


def test_apply_k_factors_collects_per_frame_failures_without_aborting():
    model = _standard_model(design_code="AISC 360-16")
    model.DesignSteel.AISC360_16.fail_frames = {"F2"}
    result = apply_k_factors(
        model,
        [
            {"frame": "F1", "kx": 1.0, "ky": 1.0},
            {"frame": "F2", "kx": 1.0, "ky": 1.0},
            {"frame": "F3", "kx": 1.0, "ky": 1.0},
        ],
    )
    assert result["applied"] == 2
    assert len(result["failures"]) == 1
    assert result["failures"][0]["frame"] == "F2"


# ---------------------------------------------------------------------------
# Routes (installed via bridge.actions.install_routes, same fake session
# pattern as test_bridge_actions.py's create-section route tests)
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


def test_route_selected_returns_members():
    model = _standard_model(selected=[(2, "F1")])
    response = _client(model).get("/v1/sap/actions/esbelteces/selected-raw")
    assert response.status_code == 200
    assert response.json()["members"][0]["frame_name"] == "F1"


def test_old_routes_selected_and_import_groups_are_gone():
    model = _standard_model(selected=[(2, "F1")], groups={"GrpCols": [(2, "F1")]})
    cliente = _client(model)
    assert cliente.get("/v1/sap/actions/esbelteces/selected").status_code == 404
    assert (
        cliente.post("/v1/sap/actions/esbelteces/import-groups", json={"map": {"C": "GrpCols"}}).status_code
        == 404
    )


def test_route_groups_lists_names():
    model = _standard_model(groups={"GrpA": []})
    response = _client(model).get("/v1/sap/actions/esbelteces/groups")
    assert response.status_code == 200
    assert response.json() == {"groups": ["GrpA"]}


def test_route_sections_lists_names():
    model = _standard_model()
    response = _client(model).get("/v1/sap/actions/esbelteces/sections")
    assert response.status_code == 200
    assert set(response.json()["sections"]) == {"W1", "CH1", "L1", "HSS1", "GEN1"}


def test_route_section_apply_requires_confirmed():
    model = _standard_model()
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/section/apply", json={"frame": "F1", "section": "CH1"}
    )
    assert response.status_code == 422
    assert model.FrameObj.set_section_calls == []


def test_route_section_apply_confirmed_assigns():
    model = _standard_model()
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/section/apply",
        json={"frame": "F1", "section": "CH1", "confirmed": True},
    )
    assert response.status_code == 200
    assert response.json()["member"]["section_name"] == "CH1"


def test_route_highlight():
    model = _standard_model()
    response = _client(model).post("/v1/sap/actions/esbelteces/highlight", json={"frames": ["F1", "F2"]})
    assert response.status_code == 200
    assert response.json() == {"count": 2}


def test_route_import_groups():
    model = _standard_model(groups={"GrpCols": [(2, "F1")]})
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/import-groups-raw", json={"map": {"Columna_SCBF": "GrpCols"}}
    )
    assert response.status_code == 200
    members = response.json()["members"]
    assert members[0]["frame_name"] == "F1"
    assert members[0]["group_category"] == "Columna_SCBF"


def test_route_k_factors_requires_confirmed():
    model = _standard_model(design_code="AISC 360-16")
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/k-factors/apply",
        json={"items": [{"frame": "F1", "kx": 1.0, "ky": 1.0}]},
    )
    assert response.status_code == 422
    assert model.DesignSteel.AISC360_16.calls == []


def test_route_k_factors_wrong_design_code_returns_409_and_writes_nothing():
    model = _standard_model(design_code="AISC 360-10")
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/k-factors/apply",
        json={"items": [{"frame": "F1", "kx": 1.0, "ky": 1.0}], "confirmed": True},
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "design_code"
    assert model.DesignSteel.AISC360_16.calls == []


def test_route_k_factors_confirmed_applies():
    model = _standard_model(design_code="AISC 360-16")
    response = _client(model).post(
        "/v1/sap/actions/esbelteces/k-factors/apply",
        json={"items": [{"frame": "F1", "kx": 1.0, "ky": 1.0}], "confirmed": True},
    )
    assert response.status_code == 200
    assert response.json() == {"applied": 1, "failures": []}


@pytest.mark.parametrize(
    "forma",
    [
        lambda n, t, nm: (n, t, nm, 0),  # comtypes: (n, tipos, nombres, ret)
        lambda n, t, nm: (0, n, t, nm),  # pywin32: (ret, n, tipos, nombres)
    ],
)
def test_selected_frame_names_accepts_return_code_first_or_last(forma):
    from bridge import esbelteces as bridge

    class _Sel:
        def GetSelected(self, *_args):
            return forma(3, (2, 1, 2), ("F1", "J1", "F2"))

    class _Model:
        SelectObj = _Sel()

    assert bridge._selected_frame_names(_Model()) == ["F1", "F2"]


class _RetFirst:
    """Proxy que mueve el código de retorno al inicio de toda tupla (forma pywin32)."""

    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return _RetFirst(attr)

        def call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if isinstance(result, tuple) and result and isinstance(result[-1], int):
                return (result[-1], *result[:-1])
            return result

        return call


def test_extract_frame_data_return_code_first_matches_return_code_last():
    """Regresión: con el código al inicio se leía prop_name='0' y fy/rx/ry quedaban en 0."""
    model = _standard_model()
    esperado = extract_frame_data(model, "F1")
    obtenido = extract_frame_data(_RetFirst(_standard_model()), "F1")
    assert obtenido is not None and esperado is not None
    assert obtenido == esperado
    assert obtenido.section_name == "W1"
    assert obtenido.fy_mpa == 345.0 and obtenido.rx_mm and obtenido.ry_mm
    assert obtenido.length_mm == pytest.approx(5000.0)


def test_extract_pipe_section():
    """Pipe (tipo 7): h = diámetro exterior, tw = espesor, b y tf quedan vacíos.
    Con el código de retorno al inicio (proxy _RetFirst) el resultado es idéntico."""

    def _pipe_model() -> FakeModel:
        model = _standard_model()
        model.FrameObj.sections["F6"] = "PIPE1"
        model.FrameObj.points["F6"] = ("P1", "P7")
        model.PointObj.coords["P7"] = (0.0, 0.0, 4000.0)
        model.PropFrame.sections["PIPE1"] = (
            7,
            {"mat": "A500", "h": 219.1, "tw": 6.0, "area": 6000.0, "i22": 2.0e7, "i33": 2.0e7},
        )
        return model

    data = extract_frame_data(_pipe_model(), "F6")
    assert data is not None
    assert data.prop_type_name == "Pipe_HSS_Round"
    assert data.section_name == "PIPE1"
    assert data.h_mm == pytest.approx(219.1)
    assert data.tw_mm == pytest.approx(6.0)
    assert data.b_mm is None and data.tf_mm is None
    assert data.material_name == "A500"
    assert data.fy_mpa == 315.0
    assert data.length_mm == pytest.approx(4000.0)
    assert data.rx_mm == pytest.approx((2.0e7 / 6000.0) ** 0.5)
    assert data.ry_mm == pytest.approx((2.0e7 / 6000.0) ** 0.5)

    obtenido = extract_frame_data(_RetFirst(_pipe_model()), "F6")
    assert obtenido == data
