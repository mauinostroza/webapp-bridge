import sys
import types

import pytest

from bridge.actions import (
    ActionValidationError,
    PartialApplyError,
    SectionExistsError,
    _call_get_coord_cartesian,
    apply_loads,
    assign_section,
    create_combinations,
    create_response_spectrum,
    create_section,
    existing_loads,
    groups_from_model,
    load_cases_and_combos,
    load_patterns,
    log_profile_diagnostics_if_no_materials,
    model_is_locked,
    preview_loads,
    profile_catalog,
    profile_diagnostics,
    selected_frame_properties,
    selected_frames,
    selected_objects,
)


class Frame:
    def __init__(self):
        self.calls = []

    def GetSection(self, name):
        return ("W310", "", 0)

    def SetSection(self, frame, section):
        self.calls.append((frame, section))
        return 0


class Props:
    def GetNameList(self):
        return (0, 2, ("W310", "HSS200"))


class Material:
    def GetNameList(self):
        return (0, 1, ("A36",))


class Select:
    def GetSelected(self):
        return (3, (1, 2, 2), ("P1", "F1", "F2"), 0)


class Combos:
    def __init__(self):
        self.calls = []
        self.deleted = []
        self.fail_case = False

    def GetNameList(self):
        return (0, 0, ())

    def Add(self, name, kind):
        self.calls.append(("add", name, kind))
        return 0

    def SetCaseList(self, name, kind, case, factor):
        self.calls.append(("term", name, kind, case, factor))
        return 4 if self.fail_case else 0

    def Delete(self, name):
        self.deleted.append(name)
        return 0


class Functions:
    def __init__(self):
        self.calls = []
        self.deleted = []

    def GetNameList(self, kind, names, function_type):
        assert (kind, names, function_type) == (0, [], 1)
        return (0, 0, ())

    def SetUser(self, *args):
        self.calls.append(args)
        return 0

    def Delete(self, name):
        self.deleted.append(name)
        return 0


class Model:
    def __init__(self):
        self.PropMaterial = Material()
        self.PropFrame = Props()
        self.SelectObj = Select()
        self.FrameObj = Frame()
        self.RespCombo = Combos()
        self.Func = type(
            "Func",
            (),
            {
                "FuncRS": Functions(),
                "GetNameList": lambda _self, kind, names, function_type: _self.FuncRS.GetNameList(
                    kind, names, function_type
                ),
            },
        )()


def test_reads_selected_frames_and_catalog_without_mutation():
    model = Model()
    assert profile_catalog(model) == {"materials": ["A36"], "sections": ["W310", "HSS200"]}
    assert selected_frames(model) == [{"frame": "F1", "section": "W310"}, {"frame": "F2", "section": "W310"}]
    assert model.FrameObj.calls == []


def test_assign_rejects_unknown_section_before_writing():
    model = Model()
    with pytest.raises(ActionValidationError):
        assign_section(model, ["F1"], "NOT_CATALOG")
    assert model.FrameObj.calls == []


def test_combinations_do_not_overwrite_and_roll_back_created_on_failure():
    model = Model()
    model.RespCombo.fail_case = True
    with pytest.raises(RuntimeError):
        create_combinations(
            model, [{"name": "ULS1", "combo_type": 0, "items": [{"kind": 0, "name": "D", "factor": 1.4}]}]
        )
    assert model.RespCombo.deleted == ["ULS1"]


def test_spectrum_uses_original_set_user_call_without_existing_overwrite():
    model = Model()
    result = create_response_spectrum(
        model, "RS_X", [{"period": 0.0, "value": 0.4}, {"period": 1.0, "value": 0.2}], 0.05
    )
    assert result["created"] == "RS_X"
    assert model.Func.FuncRS.calls == [("RS_X", 2, [0.0, 1.0], [0.4, 0.2], 0.05)]


def test_selected_frame_properties_route_is_installed(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.get(
        "/v1/sap/actions/profiles/selected-frame-properties", headers={"Authorization": "Bearer test-token"}
    )
    # No SAP2000/COM available in CI: same 409/503 contract as every other
    # action route (see profiles/catalog above), proving the route reaches
    # the actor instead of 404ing.
    assert response.status_code in (200, 409, 503)
    assert response.status_code != 404


def test_preview_routes_are_installed_and_apply_requires_confirmation(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer test-token"}
    payload = {"frames": ["F1"], "section": "W310"}
    preview = client.post("/v1/sap/actions/profiles/section/preview", json=payload, headers=headers)
    apply = client.post("/v1/sap/actions/profiles/section/apply", json=payload, headers=headers)
    assert preview.status_code == 200
    assert preview.json()["will_mutate"] is False
    assert apply.status_code == 422
    assert "confirmed" in apply.json()["detail"]


def test_assign_reports_partial_com_state():
    model = Model()

    def set_section(frame, section):
        model.FrameObj.calls.append((frame, section))
        return 5 if frame == "F2" else 0

    model.FrameObj.SetSection = set_section
    with pytest.raises(PartialApplyError) as caught:
        assign_section(model, ["F1", "F2"], "W310")
    assert caught.value.result == {
        "section": "W310",
        "assigned_frames": ["F1"],
        "failed_frame": "F2",
        "error": "SAP2000 FrameObj.SetSection('F2') returned 5",
        "partial": True,
    }


def _units_model():
    """Attach a mutable GetPresentUnits/SetPresentUnits pair to a fresh Model."""
    model = Model()
    model.units = 6
    model.unit_calls = []
    model.GetPresentUnits = lambda: model.units

    def set_units(unit):
        model.unit_calls.append(unit)
        model.units = unit
        return 0

    model.SetPresentUnits = set_units
    return model


def test_create_section_uses_original_mm_signature_and_restores_units():
    model = _units_model()
    model.PropFrame.SetISection = lambda *args: setattr(model, "i_args", args) or 0
    result = create_section(model, "I-300", "I", "A36", 300, 150, 8, 12)
    assert model.i_args == ("I-300", "A36", 300, 150, 12, 8, 150, 12, -1, "", "")
    assert model.unit_calls == [9, 6]
    assert result == {
        "name": "I-300",
        "section_type": "I",
        "ret": 0,
        "message": "[I-300] Sección enviada correctamente (I).",
        "overwritten": False,
    }


@pytest.mark.parametrize(
    ("section_type", "method", "expected_args"),
    [
        ("I", "SetISection", ("Sec1", "A36", 300, 150, 12, 8, 150, 12, -1, "", "")),
        ("Channel", "SetChannel", ("Sec1", "A36", 300, 150, 12, 8, -1, "", "")),
        ("Angle", "SetAngle", ("Sec1", "A36", 300, 150, 12, 8, -1, "", "")),
        ("Tube", "SetTube", ("Sec1", "A36", 300, 150, 12, 8, -1, "", "")),
    ],
)
def test_create_section_argument_order_matches_original_per_family(section_type, method, expected_args):
    model = _units_model()
    setattr(model.PropFrame, method, lambda *args: setattr(model, "recorded", args) or 0)
    create_section(model, "Sec1", section_type, "A36", 300, 150, 8, 12)
    assert model.recorded == expected_args


def test_create_section_pipe_uses_setpipe_with_diameter_and_thickness():
    model = _units_model()
    model.PropFrame.SetPipe = lambda *args: setattr(model, "recorded", args) or 0
    result = create_section(model, "CHS6x23.6", "Pipe", "A36", 165.2, 165.2, 6, 6)
    assert model.recorded == ("CHS6x23.6", "A36", 165.2, 6, -1, "", "")
    assert result["section_type"] == "Pipe"
    assert result["message"] == "[CHS6x23.6] Sección enviada correctamente (Pipe)."


def test_create_section_nonzero_ret_reports_warning_without_raising():
    model = _units_model()
    model.PropFrame.SetISection = lambda *args: 3
    result = create_section(model, "I-300", "I", "A36", 300, 150, 8, 12)
    assert result["ret"] == 3
    assert result["message"] == "[I-300] SAP2000 devolvió código 3 (puede ser warning)."
    assert result["overwritten"] is False


def test_create_section_refuses_existing_name_without_overwrite():
    model = _units_model()
    with pytest.raises(SectionExistsError) as excinfo:
        create_section(model, "W310", "Tube", "A36", 300, 200, 8, 8)
    assert excinfo.value.detail == {
        "code": "section_exists",
        "name": "W310",
        "message": "La sección W310 ya existe en el modelo.",
    }
    # units were never forced/restored because the call is refused up front
    assert model.unit_calls == []


def test_create_section_overwrite_calls_setter_once_and_reports_overwritten():
    model = _units_model()
    model.PropFrame.SetTube = lambda *args: (
        setattr(model, "tube_calls", getattr(model, "tube_calls", 0) + 1) or 0
    )
    result = create_section(model, "W310", "Tube", "A36", 300, 200, 8, 8, overwrite=True)
    assert model.tube_calls == 1
    assert result["overwritten"] is True
    assert result["message"] == "[W310] Sección enviada correctamente (Tube)."


def test_create_section_material_error_message_is_spanish():
    model = _units_model()
    with pytest.raises(ActionValidationError, match="no existe en el modelo de SAP2000"):
        create_section(model, "New-1", "I", "NOPE", 300, 150, 8, 12)


def test_create_section_material_error_literal_lists_available_materials():
    model = _units_model()
    model.PropMaterial.GetNameList = lambda *args: (0, 2, ("A36", "A572"))
    with pytest.raises(ActionValidationError) as err:
        create_section(model, "New-1", "I", "NOPE", 300, 150, 8, 12)
    assert str(err.value) == (
        "El material 'NOPE' no existe en el modelo de SAP2000. Materiales disponibles: A36, A572"
    )
    assert model.unit_calls == []  # nada se envía ni se tocan las unidades


def test_create_section_material_unreadable_or_empty_is_free_text():
    # Sin materiales (o sin poder leerlos) el material se escribe libre.
    model = _units_model()
    model.PropMaterial.GetNameList = lambda *args: (0, 0, ())
    model.PropFrame.SetISection = lambda *args: 0
    assert create_section(model, "New-1", "I", "A36Acero", 300, 150, 8, 12)["ret"] == 0

    def boom(*_args):
        raise RuntimeError("COM failure")

    model = _units_model()
    model.PropMaterial.GetNameList = boom
    model.PropFrame.SetISection = lambda *args: 0
    assert create_section(model, "New-1", "I", "A36Acero", 300, 150, 8, 12)["ret"] == 0


def test_create_section_null_reference_error_gets_hint():
    model = _units_model()

    def boom(*_args):
        raise RuntimeError("Referencia a objeto no establecida como instancia de un objeto.")

    model.PropFrame.SetISection = boom
    with pytest.raises(RuntimeError) as err:
        create_section(model, "H414x405x232.8*", "I", "A36", 414, 405, 18, 28)
    text = str(err.value)
    assert text.startswith(
        "Error enviando sección H414x405x232.8*: Referencia a objeto no establecida como instancia"
    )
    assert text.endswith(
        "\nPosibles causas: el material 'A36' no está definido en el "
        "modelo (si no hay materiales, créalo en SAP2000: Define > Materials y "
        "vuelve a conectar), o el nombre de la sección contiene un carácter "
        "que SAP2000 no acepta (p. ej. '*')."
    )
    assert model.unit_calls == [9, 6]  # unidades restauradas igualmente


def test_create_section_other_errors_have_no_hint():
    model = _units_model()

    def boom(*_args):
        raise RuntimeError("COM failure")

    model.PropFrame.SetISection = boom
    with pytest.raises(RuntimeError) as err:
        create_section(model, "I-1", "I", "A36", 300, 150, 8, 12)
    assert str(err.value) == "Error enviando sección I-1: COM failure"


def test_create_section_accepts_star_marker_of_commercial_h_variations():
    model = _units_model()
    model.PropFrame.SetISection = lambda *args: setattr(model, "i_args", args) or 0
    result = create_section(model, "H414x405x232.8*", "I", "A36", 414, 405, 18, 28)
    assert model.i_args[0] == "H414x405x232.8*"
    assert result["name"] == "H414x405x232.8*"
    with pytest.raises(ActionValidationError):  # el resto de caracteres sigue prohibido
        create_section(model, "H/1*", "I", "A36", 414, 405, 18, 28)


def test_profile_diagnostics_reports_raw_com_values_and_exceptions():
    model = _units_model()
    model.GetModelFilename = lambda: "C:/m.sdb"
    model.GetModelIsLocked = lambda: False

    def boom(*_args):
        raise RuntimeError("no va")

    model.PropFrame.GetNameList = boom
    lines = profile_diagnostics(model)
    assert lines[0] == "SapModel.GetModelFilename: 'C:/m.sdb'"
    assert lines[1] == "SapModel.GetPresentUnits: 6"
    assert lines[2] == "SapModel.GetModelIsLocked: False"
    assert lines[-1] == "PropFrame.GetNameList(0, []): EXCEPCIÓN no va"
    assert len(lines) == 6


def test_diagnostics_logged_only_when_no_materials(caplog):
    model = _units_model()
    model.GetModelFilename = lambda: "m.sdb"
    model.GetModelIsLocked = lambda: False
    with caplog.at_level("WARNING", logger="bridge.actions"):
        log_profile_diagnostics_if_no_materials(model, ["A36"])
        assert caplog.records == []
        log_profile_diagnostics_if_no_materials(model, [])
    messages = [r.getMessage() for r in caplog.records]
    assert "[DIAG] SapModel.GetModelFilename: 'm.sdb'" in messages
    assert len(messages) == 6


def test_catalog_route_logs_diagnostics_when_sap_returns_no_materials(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bridge.actions import install_routes

    model = _units_model()
    model.GetModelFilename = lambda: "m.sdb"
    model.GetModelIsLocked = lambda: False
    model.PropMaterial.GetNameList = lambda *args: (0, 0, ())

    class FakeActor:
        def call(self, fn):
            return fn()

    class FakeSession:
        actor = FakeActor()

        def _model(self):
            return model

    app = FastAPI()
    install_routes(app, FakeSession(), RuntimeError)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    with caplog.at_level("WARNING", logger="bridge.actions"):
        response = client.get("/v1/sap/actions/profiles/catalog")
    assert response.status_code == 200
    assert response.json()["materials"] == []
    assert any(r.getMessage().startswith("[DIAG] SapModel.GetModelFilename") for r in caplog.records)


def test_create_section_route_returns_409_section_exists_shape():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bridge.actions import install_routes

    model = _units_model()

    class FakeActor:
        def call(self, fn):
            return fn()

    class FakeSession:
        actor = FakeActor()

        def _model(self):
            return model

    app = FastAPI()
    install_routes(app, FakeSession(), RuntimeError)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    payload = {
        "name": "W310",
        "section_type": "Tube",
        "material": "A36",
        "depth": 300,
        "width": 200,
        "tw": 8,
        "tf": 8,
        "confirmed": True,
    }
    response = client.post("/v1/sap/actions/profiles/create/apply", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "section_exists",
        "name": "W310",
        "message": "La sección W310 ya existe en el modelo.",
    }


def test_create_section_route_overwrite_true_creates_and_returns_shape():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bridge.actions import install_routes

    model = _units_model()
    model.PropFrame.SetTube = lambda *args: (
        setattr(model, "tube_calls", getattr(model, "tube_calls", 0) + 1) or 0
    )

    class FakeActor:
        def call(self, fn):
            return fn()

    class FakeSession:
        actor = FakeActor()

        def _model(self):
            return model

    app = FastAPI()
    install_routes(app, FakeSession(), RuntimeError)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    payload = {
        "name": "W310",
        "section_type": "Tube",
        "material": "A36",
        "depth": 300,
        "width": 200,
        "tw": 8,
        "tf": 8,
        "confirmed": True,
        "overwrite": True,
    }
    response = client.post("/v1/sap/actions/profiles/create/apply", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "name": "W310",
        "section_type": "Tube",
        "ret": 0,
        "message": "[W310] Sección enviada correctamente (Tube).",
        "overwritten": True,
    }
    assert model.tube_calls == 1


def test_create_section_restores_units_even_on_exception():
    model = _units_model()

    def boom(*_args):
        raise RuntimeError("COM failure")

    model.PropFrame.SetISection = boom
    with pytest.raises(RuntimeError, match="Error enviando sección I-300: COM failure"):
        create_section(model, "I-300", "I", "A36", 300, 150, 8, 12)
    assert model.unit_calls == [9, 6]


def _properties_model(sections, points, coords, *, units=9):
    """Build a double for ``selected_frame_properties``.

    ``sections`` maps frame name -> (section_name, section_type_or_None,
    dims_or_None). ``section_type`` mirrors the doubled PropFrame.Get*
    signature this module derives from create_section's Set* calls: for the
    matching type it returns (material, depth, width, tf, tw, ..., 0); for
    every other type it returns a nonzero return code, exactly as SAP2000
    would refuse a mismatched getter.
    """
    dims_by_section: dict[str, tuple[str | None, dict[str, float] | None]] = {
        section: (section_type, dims) for section, section_type, dims in sections.values()
    }

    class _Frame:
        def GetSection(self, name):
            return (sections[name][0], "", 0)

        def GetPoints(self, name):
            i, j = points[name]
            return (i, j, 0)

    class _Point:
        def GetCoordCartesian(self, name):
            x, y, z = coords[name]
            return (x, y, z, 0)

    class _PropFrame:
        def _get(self, kind, name):
            section_type, dims = dims_by_section.get(name, (None, None))
            if section_type == kind:
                return ("A36", dims["h_mm"], dims["b_mm"], dims["tf_mm"], dims["tw_mm"], -1, "", "", 0)
            return (0, 0, 0, 0, 0, 0, "", "", 7)

        def GetISection(self, name):
            return self._get("I", name)

        def GetChannel(self, name):
            return self._get("Channel", name)

        def GetAngle(self, name):
            return self._get("Angle", name)

        def GetTube(self, name):
            return self._get("Tube", name)

    class _Select:
        def GetSelected(self):
            names = list(sections.keys())
            return (len(names), tuple([2] * len(names)), tuple(names), 0)

    class _PropertiesModel:
        def __init__(self):
            self.SelectObj = _Select()
            self.FrameObj = _Frame()
            self.PointObj = _Point()
            self.PropFrame = _PropFrame()
            self.units = units
            self.unit_calls: list[int] = []

        def GetPresentUnits(self):
            return self.units

        def SetPresentUnits(self, unit):
            self.unit_calls.append(unit)
            self.units = unit
            return 0

    return _PropertiesModel()


def test_selected_frame_properties_reads_i_channel_angle_and_tube_with_length():
    model = _properties_model(
        sections={
            "F1": ("I-SEC", "I", {"h_mm": 300.0, "b_mm": 150.0, "tf_mm": 12.0, "tw_mm": 8.0}),
            "F2": ("C-SEC", "Channel", {"h_mm": 250.0, "b_mm": 90.0, "tf_mm": 15.0, "tw_mm": 9.0}),
            "F3": ("L-SEC", "Angle", {"h_mm": 150.0, "b_mm": 100.0, "tf_mm": 12.0, "tw_mm": 12.0}),
            "F4": ("TB-SEC", "Tube", {"h_mm": 200.0, "b_mm": 200.0, "tf_mm": 8.0, "tw_mm": 8.0}),
        },
        points={"F1": ("P1", "P2"), "F2": ("P2", "P3"), "F3": ("P3", "P4"), "F4": ("P4", "P5")},
        coords={
            "P1": (0.0, 0.0, 0.0),
            "P2": (3000.0, 4000.0, 0.0),
            "P3": (3000.0, 4000.0, 1200.0),
            "P4": (3000.0, 4000.0, 1200.0),
            "P5": (3000.0, 4000.0, 2200.0),
        },
        units=9,
    )
    by_frame = {row["frame"]: row for row in selected_frame_properties(model)}
    assert by_frame["F1"]["section_type"] == "I"
    assert by_frame["F1"]["h_mm"] == 300.0
    assert by_frame["F1"]["b_mm"] == 150.0
    assert by_frame["F1"]["length_mm"] == pytest.approx(5000.0)
    assert by_frame["F2"]["section_type"] == "Channel"
    assert by_frame["F2"]["length_mm"] == pytest.approx(1200.0)
    assert by_frame["F3"]["section_type"] == "Angle"
    assert by_frame["F4"]["section_type"] == "Tube"
    assert all(row["fy_mpa"] is None for row in by_frame.values())
    assert all(row["note"] is None for row in by_frame.values())
    assert model.unit_calls == []  # already N_mm_C, no switch needed


def test_units_are_restored_even_when_the_read_fails_partway():
    model = _properties_model(
        sections={
            "F1": ("I-SEC", "I", {"h_mm": 300.0, "b_mm": 150.0, "tf_mm": 12.0, "tw_mm": 8.0}),
            "F2": ("C-SEC", "Channel", {"h_mm": 250.0, "b_mm": 90.0, "tf_mm": 15.0, "tw_mm": 9.0}),
        },
        points={"F1": ("P1", "P2"), "F2": ("P2", "P3")},
        coords={"P1": (0.0, 0.0, 0.0), "P2": (3000.0, 4000.0, 0.0), "P3": (3000.0, 4000.0, 1200.0)},
        units=6,
    )

    def failing_get_points(name):
        if name == "F2":
            raise RuntimeError("simulated COM failure")
        return ("P1", "P2", 0)

    model.FrameObj.GetPoints = failing_get_points
    with pytest.raises(RuntimeError, match="simulated COM failure"):
        selected_frame_properties(model)
    assert model.unit_calls == [9, 6]
    assert model.units == 6


def test_unsupported_section_type_is_marked_without_failing_the_request():
    model = _properties_model(
        sections={"F1": ("PLATE-GIRDER", None, None)},
        points={"F1": ("P1", "P2")},
        coords={"P1": (0.0, 0.0, 0.0), "P2": (1000.0, 0.0, 0.0)},
    )
    result = selected_frame_properties(model)
    assert len(result) == 1
    assert result[0]["section_type"] is None
    assert result[0]["h_mm"] is None
    assert result[0]["length_mm"] == pytest.approx(1000.0)
    assert "no reconocido" in result[0]["note"]


def test_nonzero_return_code_raises_instead_of_silently_wrong_length():
    model = _properties_model(
        sections={"F1": ("I-SEC", "I", {"h_mm": 300.0, "b_mm": 150.0, "tf_mm": 12.0, "tw_mm": 8.0})},
        points={"F1": ("P1", "P2")},
        coords={"P1": (0.0, 0.0, 0.0), "P2": (1000.0, 0.0, 0.0)},
    )

    def bad_return_code(name):
        return ("P1", "P2", 5)

    model.FrameObj.GetPoints = bad_return_code
    with pytest.raises(RuntimeError, match=r"FrameObj\.GetPoints\('F1'\) returned 5"):
        selected_frame_properties(model)


def test_no_selection_returns_an_empty_list_without_touching_units():
    model = _properties_model(sections={}, points={}, coords={})
    assert selected_frame_properties(model) == []
    assert model.unit_calls == []


# ---------------------------------------------------------------------------
# Foundations "SAP2000" panel data operations.
# ---------------------------------------------------------------------------


class _CasesModel:
    class LoadCases:
        def GetNameList(self):
            return (0, 2, ("DEAD", "LIVE"))

    class RespCombo:
        def GetNameList(self):
            return (0, 1, ("COMB1",))

    def __init__(self):
        self.LoadCases = self.LoadCases()
        self.RespCombo = self.RespCombo()


def test_foundations_cases_lists_load_cases_and_combos():
    assert load_cases_and_combos(_CasesModel()) == {"load_cases": ["DEAD", "LIVE"], "combos": ["COMB1"]}


class _LegacyCasesModel:
    """Only exposes GetNameList_1, the legacy signature reacciones.py's
    ``_get_name_list`` also falls back to (older SAP2000 builds)."""

    class LoadCases:
        def GetNameList(self):
            raise TypeError("not supported on this build")

        def GetNameList_1(self, *args):
            return (0, 1, ("DEAD",))

    class RespCombo:
        def GetNameList(self):
            raise TypeError("not supported on this build")

        def GetNameList_1(self, *args):
            return (0, 0, ())

    def __init__(self):
        self.LoadCases = self.LoadCases()
        self.RespCombo = self.RespCombo()


def test_foundations_cases_falls_back_to_get_name_list_1():
    assert load_cases_and_combos(_LegacyCasesModel()) == {"load_cases": ["DEAD"], "combos": []}


class _SelectionModel:
    class SelectObj:
        def GetSelected(self):
            return (4, (1, 1, 5, 5), ("J1", "J2", "S1", "S2"), 0)

    def __init__(self):
        self.SelectObj = self.SelectObj()


def test_foundations_selection_filters_by_object_type():
    model = _SelectionModel()
    assert selected_objects(model, "joints") == ["J1", "J2"]
    assert selected_objects(model, "shells") == ["S1", "S2"]


def test_foundations_selection_frames_uses_object_type_2():
    class Model:
        class SelectObj:
            def GetSelected(self):
                return (4, (1, 2, 2, 5), ("J1", "F1", "F2", "S1"), 0)

        def __init__(self):
            self.SelectObj = self.SelectObj()

    assert selected_objects(Model(), "frames") == ["F1", "F2"]


class _SelectionModelByRefOnly:
    """SelectObj.GetSelected() only supported with ByRef placeholders — the
    "Intento 2" fallback in coordenadas.py/presion_contacto.py, which
    selected_objects must also try before giving up."""

    class SelectObj:
        def GetSelected(self, *args):
            if not args:
                raise TypeError("this build requires (count, types, names)")
            return (2, (1, 5), ("J1", "S1"), 0)

    def __init__(self):
        self.SelectObj = self.SelectObj()


def test_foundations_selection_falls_back_to_byref_signature():
    model = _SelectionModelByRefOnly()
    assert selected_objects(model, "joints") == ["J1"]
    assert selected_objects(model, "shells") == ["S1"]


class _GroupsModel:
    class GroupDef:
        def GetNameList(self):
            return (0, 1, ("G1",))

        def GetAssignments(self, name, *args):
            return (2, (1, 5), ("J1", "S1"), 0)

    def __init__(self):
        self.GroupDef = self.GroupDef()


def test_foundations_groups_from_model_splits_joints_and_shells():
    assert groups_from_model(_GroupsModel()) == [
        {"id": "G1", "name": "G1", "nodes": ["J1"], "shells": ["S1"]}
    ]


class _LoadPatternsModel:
    class LoadPatterns:
        def GetNameList(self):
            return (0, 2, ("DEAD", "LIVE"))

    def __init__(self):
        self.LoadPatterns = self.LoadPatterns()


def test_foundations_load_patterns_lists_names():
    assert load_patterns(_LoadPatternsModel()) == ["DEAD", "LIVE"]


class _PointLoads:
    """PointObj double: GetLoadForce/SetLoadForce with the OAPI ByRef shape."""

    def __init__(self, loads: dict[str, list[tuple]], joint_names: list[str] | None = None):
        self.loads = loads  # {joint: [(load_pat, csys, f1, f2, f3, m1, m2, m3), ...]}
        self.set_calls: list[tuple] = []
        # apply_loads validates joints against GetNameList before writing.
        # ``None`` (the default) simulates a build/fake that doesn't support
        # it — apply_loads tolerates that by skipping pre-validation — which
        # keeps every test written before that validation existed passing
        # unchanged; pass an explicit list to exercise ``missing_joints``.
        self._joint_names = joint_names

    def GetLoadForce(self, joint, *_args):
        rows = self.loads.get(joint, [])
        columns = list(zip(*rows)) if rows else [()] * 8
        load_pat, csys, f1, f2, f3, m1, m2, m3 = columns
        point_name = tuple(joint for _ in rows)
        lc_step = tuple(0.0 for _ in rows)
        return (len(rows), point_name, load_pat, lc_step, csys, f1, f2, f3, m1, m2, m3, 0)

    def GetNameList(self, *_args):
        if self._joint_names is None:
            raise RuntimeError("GetNameList not supported by this fake")
        return (0, len(self._joint_names), tuple(self._joint_names))

    def SetLoadForce(self, joint, load_pat, forces, replace, csys):
        self.set_calls.append((joint, load_pat, tuple(forces), replace, csys))
        return 0


class _LoadsModel:
    def __init__(self, loads: dict[str, list[tuple]], joint_names: list[str] | None = None):
        self.PointObj = _PointLoads(loads, joint_names)
        self.unit_calls: list[int] = []
        self._units = 6  # arbitrary ambient units the SAP2000 user had set (kN_m_C)

    def GetPresentUnits(self):
        return self._units

    def SetPresentUnits(self, value):
        self.unit_calls.append(value)
        self._units = value
        return 0


def test_existing_loads_sums_multiple_entries_per_pattern():
    model = _LoadsModel(
        {
            "J1": [
                ("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                ("DEAD", "Global", 2.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                ("LIVE", "Global", 5.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            ]
        }
    )
    by_pattern = {row["load_pat"]: row for row in existing_loads(model, ["J1"])}
    assert by_pattern["DEAD"]["f1"] == 3.0
    assert by_pattern["LIVE"]["f1"] == 5.0
    # Units are forced to Ton_m_C (12) and restored, same as
    # cargas_nodales._obtener_cargas_existentes_impl.
    assert model.unit_calls == [12, 6]


def test_existing_loads_empty_joint_returns_no_rows():
    assert existing_loads(_LoadsModel({}), ["J9"]) == []


def test_preview_loads_replace_and_add_modes_do_not_mutate():
    model = _LoadsModel({"J1": [("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)]})
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 10.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    replaced = preview_loads(model, [row], "replace")
    assert replaced["changes"][0]["before"]["f1"] == 1.0
    assert replaced["changes"][0]["after"]["f1"] == 10.0
    added = preview_loads(model, [row], "add")
    assert added["changes"][0]["after"]["f1"] == 11.0
    new_row = {**row, "load_pat": "WIND"}
    created = preview_loads(model, [new_row], "add")
    assert created["changes"][0]["before"] is None
    assert created["changes"][0]["after"]["f1"] == 10.0
    assert model.PointObj.set_calls == []
    # Units forced to Ton_m_C (12) for each preview call and restored after.
    assert model.unit_calls == [12, 6, 12, 6, 12, 6]


def test_apply_loads_uses_replace_flag_from_mode():
    model = _LoadsModel({})
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 10.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    assert apply_loads(model, [row], "replace") == {"applied": 1, "failed": [], "missing_joints": []}
    assert model.PointObj.set_calls == [("J1", "DEAD", (10.0, 0.0, 0.0, 0.0, 0.0, 0.0), True, "Global")]
    apply_loads(model, [row], "add")
    assert model.PointObj.set_calls[-1][3] is False
    # Units forced to Ton_m_C (12) for each write and restored after, same as
    # cargas_nodales._aplicar_cargas_en_nodos_impl.
    assert model.unit_calls == [12, 6, 12, 6]


class _PointLoadsSetFailsThenSucceeds(_PointLoads):
    """First SetLoadForce (replace=True) always fails; the replace=False
    retry — SAP2000 has no separate AddLoadForce method — is what's under
    test."""

    def SetLoadForce(self, joint, load_pat, forces, replace, csys):
        self.set_calls.append((joint, load_pat, tuple(forces), replace, csys))
        if replace:
            raise RuntimeError("SetLoadForce(replace=True) not supported by this SAP2000 build")
        return 0


def test_apply_loads_retries_with_replace_false_when_first_set_load_force_raises():
    """Fidelity: the current cargas_nodales._aplicar_cargas_en_nodos_impl has
    no ``AddLoadForce`` fallback (SAP2000's OAPI has no such method) — it
    retries the same ``SetLoadForce`` with ``replace=False`` instead."""
    model = _LoadsModel({})
    model.PointObj = _PointLoadsSetFailsThenSucceeds({}, joint_names=["J1"])
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 10.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    assert apply_loads(model, [row], "replace") == {"applied": 1, "failed": [], "missing_joints": []}
    assert model.PointObj.set_calls == [
        ("J1", "DEAD", (10.0, 0.0, 0.0, 0.0, 0.0, 0.0), True, "Global"),
        ("J1", "DEAD", (10.0, 0.0, 0.0, 0.0, 0.0, 0.0), False, "Global"),
    ]


class _PointLoadsNonzeroReturnCode(_PointLoads):
    """SetLoadForce(replace=True) returns a nonzero code (SAP2000 rejects
    the write without raising); the replace=False retry succeeds."""

    def SetLoadForce(self, joint, load_pat, forces, replace, csys):
        self.set_calls.append((joint, load_pat, tuple(forces), replace, csys))
        return 7 if replace else 0


def test_apply_loads_retries_with_replace_false_on_nonzero_return_code():
    """Fidelity: a silent nonzero return code (no exception) is treated the
    same as a raised exception — retried with replace=False — per
    cargas_nodales._codigo_retorno/_aplicar_cargas_en_nodos_impl."""
    model = _LoadsModel({})
    model.PointObj = _PointLoadsNonzeroReturnCode({}, joint_names=["J1"])
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 10.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    assert apply_loads(model, [row], "replace") == {"applied": 1, "failed": [], "missing_joints": []}
    assert [call[3] for call in model.PointObj.set_calls] == [True, False]


def test_apply_loads_collects_failure_and_continues_when_both_attempts_fail():
    """Fidelity: cargas_nodales._aplicar_cargas_en_nodos_impl never raises for a
    single row — when both the replace=True and replace=False SetLoadForce
    attempts fail it appends ``f"{nodo} / {patron}"`` to ``fallidas`` and
    moves on to the next row."""

    class _PointLoadsBothFail(_PointLoads):
        def SetLoadForce(self, joint, load_pat, forces, replace, csys):
            raise RuntimeError("SetLoadForce unsupported")

    model = _LoadsModel({})
    model.PointObj = _PointLoadsBothFail({}, joint_names=["J1", "J2"])
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 1.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    other_row = {**row, "joint": "J2"}
    assert apply_loads(model, [row, other_row], "replace") == {
        "applied": 0,
        "failed": ["J1 / DEAD", "J2 / DEAD"],
        "missing_joints": [],
    }


def test_apply_loads_skips_missing_joints_without_calling_set_load_force():
    """Fidelity: cargas_nodales._aplicar_cargas_en_nodos_impl validates every
    ``Joint`` against ``PointObj.GetNameList`` before writing anything — a
    joint absent from the connected model gets the literal failure entry
    ``f"{nodo} / {patron} (nodo no existe en el modelo)"`` and is reported in
    ``nodos_inexistentes`` (``missing_joints`` here), without ever calling
    ``SetLoadForce`` for that row."""
    model = _LoadsModel({}, joint_names=["J1"])
    row_ok = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 1.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    row_missing = {**row_ok, "joint": "J9"}
    assert apply_loads(model, [row_ok, row_missing], "replace") == {
        "applied": 1,
        "failed": ["J9 / DEAD (nodo no existe en el modelo)"],
        "missing_joints": ["J9"],
    }
    assert model.PointObj.set_calls == [("J1", "DEAD", (1.0, 0.0, 0.0, 0.0, 0.0, 0.0), True, "Global")]


def test_apply_loads_skips_pre_validation_when_get_name_list_is_unsupported():
    """When ``PointObj.GetNameList`` itself fails, apply_loads tolerates it
    (same as the original's ``try/except`` around
    ``obtener_nombres_nodos_del_modelo``) and applies loads without
    filtering by missing joints."""
    model = _LoadsModel({})  # joint_names=None -> GetNameList raises
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 1.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    assert apply_loads(model, [row], "replace") == {"applied": 1, "failed": [], "missing_joints": []}


def test_foundations_read_routes_are_installed(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer test-token"}
    for path in (
        "/v1/sap/actions/foundations/cases",
        "/v1/sap/actions/foundations/groups",
        "/v1/sap/actions/foundations/load-patterns",
        "/v1/sap/actions/foundations/loads/existing?joints=J1,J2",
        "/v1/sap/actions/foundations/selection?kind=joints",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code in (200, 409, 503), path
        assert response.status_code != 404, path


def test_foundations_selection_route_rejects_unknown_kind(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.get(
        "/v1/sap/actions/foundations/selection?kind=bogus", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 422


def test_foundations_loads_apply_requires_confirmation(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer test-token"}
    payload = {"rows": [{"joint": "J1", "load_pat": "DEAD", "f1": 1.0}], "mode": "replace"}
    preview = client.post("/v1/sap/actions/foundations/loads/preview", json=payload, headers=headers)
    apply_response = client.post("/v1/sap/actions/foundations/loads/apply", json=payload, headers=headers)
    assert preview.status_code in (200, 409, 503)
    assert apply_response.status_code == 422
    assert "confirmed" in apply_response.json()["detail"]


def test_snapshot_scoping_sets_output_selection_and_filters_joints_and_shells(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Setup:
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

    class Results:
        def __init__(self):
            self.Setup = Setup()

        def JointReact(self, name, item_type):
            assert item_type == 0
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (1.0,),
                (2.0,),
                (-3.0,),
                (4.0,),
                (5.0,),
                (6.0,),
                0,
            )

        def JointDispl(self, name, item_type):
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (0.1,),
                (0.2,),
                (-0.3,),
                (0.0,),
                (0.0,),
                (0.0,),
                0,
            )

    class Point:
        def __init__(self):
            self.calls = []

        def GetNameList(self):
            return (0, 3, ("1", "2", "3"))

        def GetCoordCartesian(self, name):
            self.calls.append(name)
            return (0, float(name), 0.0, 0.0)

    class DatabaseTables:
        @staticmethod
        def GetTableForDisplayArray(*args):
            assert args[0] == "Element Soil Pressure - Area Shells"
            return (
                0,
                (),
                0,
                ("Area", "OutputCase", "Pressure"),
                2,
                ("A1", "DEAD", "-2.5", "A2", "DEAD", "-3.5"),
            )

    class Model:
        def __init__(self):
            self.PointObj = Point()
            self.Results = Results()
            self.DatabaseTables = DatabaseTables()
            self.unit_calls = []

        def GetPresentUnits(self):
            return 99

        def SetPresentUnits(self, value):
            self.unit_calls.append(value)
            return 0

        def GetModelFilename(self):
            return "fake.sdb"

    model = Model()
    data = _snapshot_from_model(
        model, groups=[], joints=["1", "2"], shells=["A1"], cases=["DEAD"], combos=["COMB1"]
    )
    assert model.Results.Setup.calls == [("deselect",), ("case", "DEAD", True), ("combo", "COMB1", True)]
    assert model.PointObj.calls == ["1", "2"]
    assert [row["joint"] for row in data["coordinates"]] == ["1", "2"]
    assert [row["shell"] for row in data["soil_pressures"]] == ["A1"]
    assert model.unit_calls == [12, 99]


def test_snapshot_without_cases_or_combos_selects_all_load_cases(monkeypatch):
    """Fidelity: reacciones.py::_seleccionar_casos_output's no-``casos`` branch
    deselects everything and selects every load case (not combos) even when
    the snapshot is unscoped — every original caller runs it unconditionally
    before reading reactions/displacements, so the bridge must too."""
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    calls = []

    class Setup:
        def DeselectAllCasesAndCombosForOutput(self):
            calls.append("deselect")
            return 0

        def SetCaseSelectedForOutput(self, name, value):
            calls.append(("case", name, value))
            return 0

    class LoadCases:
        def GetNameList(self):
            return (0, 2, ("DEAD", "LIVE"))

    class Results:
        def __init__(self):
            self.Setup = Setup()

        def JointReact(self, name, item_type):
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (1.0,),
                (2.0,),
                (-3.0,),
                (4.0,),
                (5.0,),
                (6.0,),
                0,
            )

        def JointDispl(self, name, item_type):
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (0.1,),
                (0.2,),
                (-0.3,),
                (0.0,),
                (0.0,),
                (0.0,),
                0,
            )

    class Point:
        def GetNameList(self):
            return (0, 2, ("1", "2"))

        def GetCoordCartesian(self, name):
            return (0, float(name), 0.0, 0.0)

    class DatabaseTables:
        @staticmethod
        def GetTableForDisplayArray(*args):
            return (0, (), 0, ("Area", "OutputCase", "Pressure"), 1, ("A1", "DEAD", "-2.5"))

    class Model:
        def __init__(self):
            self.PointObj = Point()
            self.Results = Results()
            self.DatabaseTables = DatabaseTables()
            self.LoadCases = LoadCases()

        def GetPresentUnits(self):
            return 99

        def SetPresentUnits(self, value):
            return 0

        def GetModelFilename(self):
            return "fake.sdb"

    data = _snapshot_from_model(Model(), [])
    assert calls == [
        "deselect",
        ("case", "DEAD", True),
        ("case", "LIVE", True),
    ]
    assert [row["joint"] for row in data["coordinates"]] == ["1", "2"]
    assert [row["shell"] for row in data["soil_pressures"]] == ["A1"]


# ── foundations shell-diagram (Presión de contacto) ─────────────────────────


class _ShellAreaObj:
    def GetElm(self, area):
        data = {"S1": (0, 2, ("E1", "E2"))}
        return data[area]


class _ShellAreaElm:
    def GetPoints(self, elm):
        data = {
            "E1": (0, 4, ("J1", "J2", "J3", "J4")),
            "E2": (0, 3, ("J2", "J5", "J3")),
        }
        return data[elm]


class _ShellPointObj:
    _coords = {
        "J1": (0.0, 0.0),
        "J2": (1.0, 0.0),
        "J3": (1.0, 1.0),
        "J4": (0.0, 1.0),
        "J5": (2.0, 0.0),
    }

    def GetCoordCartesian(self, name):
        x, y = self._coords[name]
        return (0, x, y, 0.0)


class _ShellModel:
    def __init__(self, table_response):
        self.AreaObj = _ShellAreaObj()
        self.AreaElm = _ShellAreaElm()
        self.PointObj = _ShellPointObj()
        self.unit_calls: list[int] = []
        self._table_response = table_response

    class DatabaseTables:
        def __init__(self, outer):
            self._outer = outer

        def GetTableForDisplayArray(self, *args):
            return self._outer._table_response

    def __getattr__(self, item):
        raise AttributeError(item)

    def GetPresentUnits(self):
        return 99

    def SetPresentUnits(self, value):
        self.unit_calls.append(value)
        return 0


def _install_database_tables(model, table_response):
    model.DatabaseTables = _ShellModel.DatabaseTables(model)
    model._table_response = table_response


def test_shell_geometry_ports_area_get_elm_and_area_elm_get_points():
    from bridge.actions import shell_geometry

    model = _ShellModel(table_response=None)
    elementos, joints, elementos_por_area = shell_geometry(model, ["S1"])

    assert elementos == {
        "E1": ["J1", "J2", "J3", "J4"],
        # Elemento triangular (3 puntos): p4 = p3, igual que el original.
        "E2": ["J2", "J5", "J3", "J3"],
    }
    assert joints == {
        "J1": [0.0, 0.0],
        "J2": [1.0, 0.0],
        "J3": [1.0, 1.0],
        "J4": [0.0, 1.0],
        "J5": [2.0, 0.0],
    }
    assert elementos_por_area == {"S1": ["E1", "E2"]}


def test_shell_geometry_skips_area_when_get_elm_fails():
    from bridge.actions import shell_geometry

    class FailingAreaObj:
        def GetElm(self, area):
            raise RuntimeError("no elements")

    model = _ShellModel(table_response=None)
    model.AreaObj = FailingAreaObj()
    elementos, joints, elementos_por_area = shell_geometry(model, ["S1"])
    assert elementos == {}
    assert joints == {}
    assert elementos_por_area == {}


# ── model_is_locked: literal port of estado_modelo._modelo_bloqueado ───────


def test_model_is_locked_bare_bool():
    model = types.SimpleNamespace(GetModelIsLocked=lambda: True)
    assert model_is_locked(model) is True
    model = types.SimpleNamespace(GetModelIsLocked=lambda: False)
    assert model_is_locked(model) is False


def test_model_is_locked_byref_tuple_shape():
    model = types.SimpleNamespace(GetModelIsLocked=lambda: (0, True))
    assert model_is_locked(model) is True


def test_model_is_locked_no_bool_in_tuple_falls_back_to_first_item():
    model = types.SimpleNamespace(GetModelIsLocked=lambda: (1,))
    assert model_is_locked(model) is True
    model = types.SimpleNamespace(GetModelIsLocked=lambda: (0,))
    assert model_is_locked(model) is False


def test_model_is_locked_treats_com_failure_as_not_locked():
    def raises():
        raise RuntimeError("no active model")

    model = types.SimpleNamespace(GetModelIsLocked=raises)
    assert model_is_locked(model) is False


# ── _call_get_coord_cartesian: literal port of
# coordenadas.py::_call_get_coord_cartesian's three COM signatures ─────────


def test_call_get_coord_cartesian_direct_signature():
    class PointObj:
        def GetCoordCartesian(self, name):
            return (0, 1.0, 2.0, 3.0)

    model = types.SimpleNamespace(PointObj=PointObj())
    assert _call_get_coord_cartesian(model, "N1") == (1.0, 2.0, 3.0)


def test_call_get_coord_cartesian_variant_byref_fallback(monkeypatch):
    """The direct signature raises TypeError (this SAP2000 build wants ByRef
    VARIANT out-parameters); the VARIANT branch imports pythoncom/win32com
    lazily, so a fake of each is enough to exercise it without Windows."""

    class FakeVariant:
        def __init__(self, vt, value):
            self.value = value

    fake_pythoncom = types.SimpleNamespace(VT_R8=5)
    fake_win32com_client = types.SimpleNamespace(VARIANT=FakeVariant)
    fake_win32com = types.SimpleNamespace(client=fake_win32com_client)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com_client)

    class PointObj:
        def GetCoordCartesian(self, name, x=None, y=None, z=None):
            if x is None:
                raise TypeError("this build requires ByRef VARIANT arguments")
            x.value, y.value, z.value = 4.0, 5.0, 6.0
            return 0

    model = types.SimpleNamespace(PointObj=PointObj())
    assert _call_get_coord_cartesian(model, "N1") == (4.0, 5.0, 6.0)


def test_call_get_coord_cartesian_retless_three_tuple_fallback():
    """Neither the direct nor the VARIANT signature works; an old build
    returns a bare (x, y, z) with no leading return code."""

    class PointObj:
        def __init__(self):
            self.calls = 0

        def GetCoordCartesian(self, name, *args):
            self.calls += 1
            if args:
                raise TypeError("ByRef signature not supported either")
            return (7.0, 8.0, 9.0)

    model = types.SimpleNamespace(PointObj=PointObj())
    assert _call_get_coord_cartesian(model, "N1") == (7.0, 8.0, 9.0)
    # Called twice: once for the direct-signature attempt (rejected because
    # a 3-tuple isn't the >=4-length (ret, x, y, z) shape) and once again for
    # the ret-less fallback, exactly like the original's Intento 1 / Intento 3.
    assert model.PointObj.calls == 2


def test_call_get_coord_cartesian_raises_when_no_signature_matches():
    class PointObj:
        def GetCoordCartesian(self, name, *args):
            raise RuntimeError("SAP2000 has no idea what you're asking")

    model = types.SimpleNamespace(PointObj=PointObj())
    with pytest.raises(RuntimeError):
        _call_get_coord_cartesian(model, "N1")


def test_shell_soil_pressure_joins_by_global_element_id():
    from bridge.actions import shell_geometry, shell_soil_pressure

    model = _ShellModel(table_response=None)
    elementos, _joints, elementos_por_area = shell_geometry(model, ["S1"])
    _install_database_tables(
        model,
        (
            0,
            (),
            0,
            ("Area", "AreaElem", "Joint", "OutputCase", "Pressure"),
            8,
            (
                "S1",
                "E1",
                "J1",
                "COMB1",
                "-1.0",
                "S1",
                "E1",
                "J2",
                "COMB1",
                "-1.5",
                "S1",
                "E1",
                "J3",
                "COMB1",
                "-2.0",
                "S1",
                "E1",
                "J4",
                "COMB1",
                "-1.2",
                "S1",
                "E2",
                "J2",
                "COMB1",
                "-1.5",
                "S1",
                "E2",
                "J5",
                "COMB1",
                "-2.5",
                "S1",
                "E2",
                "J3",
                "COMB1",
                "-2.0",
                "S1",
                "E2",
                "J3",
                "COMB1",
                "-2.0",
            ),
        ),
    )
    resultados = shell_soil_pressure(model, elementos, elementos_por_area)
    assert resultados["E1"] == {"J1": -1.0, "J2": -1.5, "J3": -2.0, "J4": -1.2}
    assert resultados["E2"]["J5"] == -2.5
    # Se fuerza y se restaura Ton_m_C (eUnits=12) alrededor de la lectura.
    assert model.unit_calls == [12, 99]


def test_shell_soil_pressure_falls_back_to_area_and_local_index():
    """La columna `AreaElem` puede ser un índice 1-based LOCAL a cada Area
    en vez del ID global de elemento (confirmado con un log real de
    producción, ver docstring de `shell_soil_pressure`): cuando el join
    directo no matchea ningún elemento pedido, se reconstruye vía `Area` +
    `elementos_por_area` (mismo orden que `AreaObj.GetElm`)."""
    from bridge.actions import shell_geometry, shell_soil_pressure

    model = _ShellModel(table_response=None)
    elementos, _joints, elementos_por_area = shell_geometry(model, ["S1"])
    _install_database_tables(
        model,
        (
            0,
            (),
            0,
            ("Area", "AreaElem", "Joint", "OutputCase", "Pressure"),
            8,
            (
                "S1",
                "1",
                "J1",
                "COMB1",
                "-1.0",
                "S1",
                "1",
                "J2",
                "COMB1",
                "-1.5",
                "S1",
                "1",
                "J3",
                "COMB1",
                "-2.0",
                "S1",
                "1",
                "J4",
                "COMB1",
                "-1.2",
                "S1",
                "2",
                "J2",
                "COMB1",
                "-1.5",
                "S1",
                "2",
                "J5",
                "COMB1",
                "-2.5",
                "S1",
                "2",
                "J3",
                "COMB1",
                "-2.0",
                "S1",
                "2",
                "J3",
                "COMB1",
                "-2.0",
            ),
        ),
    )
    resultados = shell_soil_pressure(model, elementos, elementos_por_area)
    assert resultados["E1"]["J1"] == -1.0
    assert resultados["E2"]["J5"] == -2.5


def test_shell_soil_pressure_returns_empty_without_pressure_column():
    from bridge.actions import shell_geometry, shell_soil_pressure

    model = _ShellModel(table_response=None)
    elementos, _joints, elementos_por_area = shell_geometry(model, ["S1"])
    _install_database_tables(model, (0, (), 0, ("Area", "Joint"), 0, ()))
    assert shell_soil_pressure(model, elementos, elementos_por_area) == {}


def test_call_display_table_tries_signature_variants_in_order():
    """Port of ``presion_contacto._llamar_tabla_presion_suelo``: a variant
    that succeeds without error but returns zero columns is not good enough
    — the next variant is tried, in the same order as the original, until
    one brings back columns."""
    from bridge.actions import _call_display_table

    calls: list[tuple] = []

    class _DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            calls.append(args)
            if len(calls) < 3:
                return (0, (), 0, (), 0, ())  # success, but no columns
            return (0, (), 0, ("Area", "Pressure"), 2, ("S1", "-1.0"))

    class Model:
        DatabaseTables = _DatabaseTables()

    raw = _call_display_table(Model(), "Element Soil Pressure - Area Shells")
    assert raw == (0, (), 0, ("Area", "Pressure"), 2, ("S1", "-1.0"))
    assert calls == [
        ("Element Soil Pressure - Area Shells", [], "", 0, [], 0, []),
        ("Element Soil Pressure - Area Shells", [], "ALL", 0, [], 0, []),
        ("Element Soil Pressure - Area Shells", [], "", "", [], 0, []),
    ]


def test_call_display_table_falls_back_to_first_success_without_columns():
    from bridge.actions import _call_display_table

    class _DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            return (0, (), 0, (), 0, ())

    class Model:
        DatabaseTables = _DatabaseTables()

    assert _call_display_table(Model(), "X") == (0, (), 0, (), 0, ())


def test_call_display_table_raises_when_every_variant_errors():
    from bridge.actions import _call_display_table

    class _DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            raise RuntimeError("nope")

    class Model:
        DatabaseTables = _DatabaseTables()

    with pytest.raises(RuntimeError, match="every known signature"):
        _call_display_table(Model(), "X")


class _SelectCaseModel:
    class LoadCases:
        def GetNameList(self):
            return (0, 1, ("DEAD",))

    class RespCombo:
        def GetNameList(self):
            return (0, 1, ("COMB1",))

    class Setup:
        def __init__(self):
            self.calls: list[tuple] = []

        def DeselectAllCasesAndCombosForOutput(self):
            self.calls.append(("deselect",))

        def SetComboSelectedForOutput(self, name, flag):
            self.calls.append(("combo", name, flag))

        def SetCaseSelectedForOutput(self, name, flag):
            self.calls.append(("case", name, flag))

    class Results:
        def __init__(self):
            self.Setup = _SelectCaseModel.Setup()

    def __init__(self):
        self.LoadCases = _SelectCaseModel.LoadCases()
        self.RespCombo = _SelectCaseModel.RespCombo()
        self.Results = _SelectCaseModel.Results()


def test_select_case_for_output_classifies_combo_vs_load_case():
    from bridge.actions import _select_case_for_output

    model = _SelectCaseModel()
    _select_case_for_output(model, "COMB1")
    assert model.Results.Setup.calls == [("deselect",), ("combo", "COMB1", True)]

    model = _SelectCaseModel()
    _select_case_for_output(model, "DEAD")
    assert model.Results.Setup.calls == [("deselect",), ("case", "DEAD", True)]


def test_select_case_for_output_tries_both_for_unknown_name():
    from bridge.actions import _select_case_for_output

    model = _SelectCaseModel()
    _select_case_for_output(model, "UNKNOWN")
    assert model.Results.Setup.calls == [
        ("deselect",),
        ("combo", "UNKNOWN", True),
        ("case", "UNKNOWN", True),
    ]


def test_foundations_shell_diagram_route_is_installed(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.post(
        "/v1/sap/actions/foundations/shell-diagram",
        json={"shells": ["S1"], "combinacion": "COMB1"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code in (200, 409, 503)
    assert response.status_code != 404

    empty = client.post(
        "/v1/sap/actions/foundations/shell-diagram",
        json={"shells": [], "combinacion": "COMB1"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert empty.status_code == 422


def test_foundations_groups_route_returns_the_shape_the_web_reads(monkeypatch):
    """La web (FoundationGroup) lee ``nodes``; el puente devolvía ``joints``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import bridge.actions as actions

    monkeypatch.setattr(
        actions,
        "groups_from_model",
        lambda model: [{"id": "G1", "name": "G1", "nodes": ["1", "2"], "shells": ["S1"]}],
    )

    class _Actor:
        def call(self, fn):
            return fn()

    class _Session:
        actor = _Actor()

        def _model(self):
            return object()

    app = FastAPI()
    actions.install_routes(app, _Session(), RuntimeError)
    response = TestClient(app, base_url="http://127.0.0.1:8765").get("/v1/sap/actions/foundations/groups")
    assert response.status_code == 200
    assert response.json() == {"groups": [{"id": "G1", "name": "G1", "nodes": ["1", "2"], "shells": ["S1"]}]}


def test_loads_body_acepta_mas_de_500_filas_y_rechaza_el_exceso():
    from pydantic import ValidationError

    from bridge.actions import _MAX_LOAD_ROWS, LoadsBody

    fila = {"joint": "46", "load_pat": "DEAD", "coord_sys": "GLOBAL"}
    assert len(LoadsBody(rows=[fila] * 1200, mode="add").rows) == 1200
    with pytest.raises(ValidationError):
        LoadsBody(rows=[fila] * (_MAX_LOAD_ROWS + 1), mode="add")
