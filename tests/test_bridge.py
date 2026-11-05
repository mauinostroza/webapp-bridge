import sys

import pytest

from bridge.sap_client import SapAgentClient, SapUnavailable


def test_client_never_claims_a_connection_without_agent(monkeypatch):
    monkeypatch.delenv("SAP2000_AGENT_URL", raising=False)
    monkeypatch.delenv("SAP2000_AGENT_TOKEN", raising=False)
    client = SapAgentClient()
    assert client.status()["connected"] is False
    with pytest.raises(SapUnavailable):
        client.model()


def test_client_rejects_non_loopback_agent_urls(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_URL", "https://sap.example.test")
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "secret")
    client = SapAgentClient()
    assert client.status()["connected"] is False
    with pytest.raises(SapUnavailable, match="loopback"):
        client.model()


def test_name_list_supports_real_pywin32_return_shape(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _name_list

    assert _name_list((0, 2, ("DEAD", "LIVE"))) == ["DEAD", "LIVE"]
    with pytest.raises(Exception, match="returned 1"):
        _name_list((1, 0, ()))


def test_windows_agent_rejects_non_windows(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("Linux/macOS guard")
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapComError, SapSession

    with pytest.raises(SapComError, match="requires Windows"):
        SapSession().status()


def test_connected_version_prefers_getversion_str_tuple(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetVersion(self):
            return (0, "24.2.0")

    session = SapSession()
    session.model = Model()
    session.sap_object = object()
    assert session._connected_version() == "24.2.0"


def test_connected_version_falls_back_to_oapi_float(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetVersion(self):
            raise Exception("not supported")

    class SapObject:
        def GetOAPIVersionNumber(self):
            return (0, 1.0)

    session = SapSession()
    session.model = Model()
    session.sap_object = SapObject()
    assert session._connected_version() == "1.0"


def test_connected_version_none_when_both_getters_fail(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetVersion(self):
            raise Exception("boom")

    class SapObject:
        def GetOAPIVersionNumber(self):
            raise Exception("boom too")

    session = SapSession()
    session.model = Model()
    session.sap_object = SapObject()
    assert session._connected_version() is None


def test_agent_status_returns_explicit_503_on_linux(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("Linux/macOS guard")
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    response = TestClient(app, base_url="http://127.0.0.1:8765").get("/v1/sap/status", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    assert "requires Windows" in response.json()["detail"]


def test_status_action_includes_version_when_connected(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetModelFilename(self):
            return "fake.sdb"

        def GetVersion(self):
            return (0, "24.2.0")

    session = SapSession()
    session.model = Model()
    session.sap_object = object()
    result = session._status_action()
    assert result["connected"] is True
    assert result["version"] == "24.2.0"


def test_status_action_omits_version_when_unavailable(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    class Model:
        def GetModelFilename(self):
            return "fake.sdb"

        def GetVersion(self):
            raise Exception("boom")

    class SapObject:
        def GetOAPIVersionNumber(self):
            raise Exception("boom too")

    session = SapSession()
    session.model = Model()
    session.sap_object = SapObject()
    result = session._status_action()
    assert result["connected"] is True
    assert "version" not in result


def test_agent_options_preflight_does_not_require_bearer(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    response = TestClient(app, base_url="http://127.0.0.1:8765").options(
        "/v1/sap/actions/profiles/catalog",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_snapshot_normalizes_and_restores_units(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Point:
        def __init__(self):
            self.calls = []

        def GetNameList(self):
            return (0, 2, ("1", "2"))

        def GetCoordCartesian(self, name):
            self.calls.append(name)
            return (0, float(name), 0.0, 0.0)

    class Results:
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

    class LoadCases:
        def GetNameList(self):
            return (0, 0, ())

    class Model:
        def __init__(self):
            self.PointObj = Point()
            self.Results = Results()
            self.LoadCases = LoadCases()
            self.unit_calls = []

        def GetPresentUnits(self):
            return 99

        def SetPresentUnits(self, value):
            self.unit_calls.append(value)
            return 0

        def GetModelFilename(self):
            return "fake.sdb"

        def GetModelIsLocked(self):
            return True

        class DatabaseTables:
            @staticmethod
            def GetTableForDisplayArray(*args):
                assert args[0] == "Element Soil Pressure - Area Shells"
                return (0, (), 0, ("Area", "OutputCase", "Pressure"), 1, ("A1", "DEAD", "-2.5"))

    model = Model()
    model.DatabaseTables = Model.DatabaseTables()
    data = _snapshot_from_model(model, [])
    assert data["model"]["units"] == "Ton_m_C"
    assert model.unit_calls == [12, 99]
    assert model.PointObj.calls == ["1", "2"]
    assert data["reactions"][0]["step_num"] == 0.0
    assert data["displacements"][1]["u3"] == -0.3
    assert data["soil_pressures"] == [{"shell": "A1", "output_case": "DEAD", "pressure": -2.5}]
    assert data["model_locked"] is True


def test_snapshot_model_locked_reflects_get_model_is_locked(monkeypatch):
    """Fidelity: the snapshot exposes SAP2000's model-locked state (port of
    estado_modelo._modelo_bloqueado) so the web can show the desktop's
    literal 'modelo no corrido' message instead of a generic empty result."""
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Point:
        def GetNameList(self):
            return (0, 0, ())

        def GetCoordCartesian(self, name):
            return (0, 0.0, 0.0, 0.0)

    class Setup:
        def DeselectAllCasesAndCombosForOutput(self):
            return 0

        def SetCaseSelectedForOutput(self, name, value):
            return 0

    class Results:
        def __init__(self):
            self.Setup = Setup()

        def JointReact(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

        def JointDispl(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

    class DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            return (0, (), 0, (), 0, ())

    class LoadCases:
        def GetNameList(self):
            return (0, 0, ())

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

        def GetModelIsLocked(self):
            return False

    data = _snapshot_from_model(Model(), [])
    assert data["model_locked"] is False


def test_snapshot_soil_pressure_falls_back_across_table_signatures(monkeypatch):
    """The unscoped snapshot's soil-pressure read goes through
    ``_call_display_table`` (port of
    ``presion_contacto._llamar_tabla_presion_suelo``): a first signature
    that succeeds without error but returns no columns must not be taken as
    the final answer."""
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Point:
        def GetNameList(self):
            return (0, 0, ())

        def GetCoordCartesian(self, name):
            return (0, 0.0, 0.0, 0.0)

    class Results:
        def JointReact(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

        def JointDispl(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

    class DatabaseTables:
        def __init__(self):
            self.calls: list[tuple] = []

        def GetTableForDisplayArray(self, *args):
            self.calls.append(args)
            if len(self.calls) < 2:
                return (0, (), 0, (), 0, ())  # success, but no columns
            return (0, (), 0, ("Area", "OutputCase", "Pressure"), 1, ("A1", "DEAD", "-2.5"))

    class LoadCases:
        def GetNameList(self):
            return (0, 0, ())

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

    model = Model()
    data = _snapshot_from_model(model, [])
    assert data["soil_pressures"] == [{"shell": "A1", "output_case": "DEAD", "pressure": -2.5}]
    assert len(model.DatabaseTables.calls) == 2


def test_snapshot_joints_falls_back_to_byref_get_name_list(monkeypatch):
    """The unscoped snapshot's joint listing goes through ``_get_name_list``
    (port of ``coordenadas._obtener_lista_nodos``'s tolerant retries): a
    build whose bare ``PointObj.GetNameList()`` raises ``TypeError`` must
    still resolve every joint via the ByRef-style call."""
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Point:
        def GetNameList(self, *args):
            if not args:
                raise TypeError("this build requires (count, names)")
            return (0, 2, ("1", "2"))

        def GetCoordCartesian(self, name):
            return (0, float(name), 0.0, 0.0)

    class Results:
        def JointReact(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

        def JointDispl(self, name, item_type):
            return (0, (), (), (), (), (), (), (), (), (), (), (), 0)

    class DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            return (0, (), 0, (), 0, ())

    class LoadCases:
        def GetNameList(self):
            return (0, 0, ())

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
    assert [row["joint"] for row in data["coordinates"]] == ["1", "2"]


def test_group_assignments_uses_group_def_get_assignments_with_output_params(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _group_assignments

    class GroupDef:
        def __init__(self):
            self.calls = []

        def GetAssignments(self, name, *args):
            self.calls.append((name, args))
            return (3, (1, 2, 5), ("N1", "F1", "A1"), 0)

    class Model:
        def __init__(self):
            self.GroupDef = GroupDef()

    model = Model()
    assert _group_assignments(model, "G1", 1) == ["N1"]
    assert _group_assignments(model, "G1", 5) == ["A1"]
    # The output-parameter signature is tried first, per section_cuts.py.
    assert model.GroupDef.calls[0] == ("G1", (0, [], []))


def test_group_assignments_falls_back_to_short_signature(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _group_assignments

    class GroupDef:
        def GetAssignments(self, *args):
            if len(args) > 1:
                raise Exception("output-parameter signature not supported")
            return (2, (1, 5), ("N1", "A1"), 0)

    class Model:
        pass

    model = Model()
    model.GroupDef = GroupDef()
    assert _group_assignments(model, "G1", 1) == ["N1"]
    assert _group_assignments(model, "G1", 5) == ["A1"]


def test_group_assignments_finds_the_parallel_pair_at_any_position(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _group_assignments

    class GroupDef:
        def GetAssignments(self, *args):
            # The type/name pair sits after an unrelated, differently-sized
            # array; a fixed-position parse would miss it.
            return (0, ("only_one",), (1, 2, 5), ("N1", "F1", "A1"), 99)

    class Model:
        pass

    model = Model()
    model.GroupDef = GroupDef()
    assert _group_assignments(model, "G1", 1) == ["N1"]
    assert _group_assignments(model, "G1", 5) == ["A1"]


def test_group_assignments_empty_group_returns_empty_list(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _group_assignments

    class GroupDef:
        def GetAssignments(self, *args):
            return (0, (), (), 0)

    class Model:
        pass

    model = Model()
    model.GroupDef = GroupDef()
    assert _group_assignments(model, "EMPTY", 1) == []
    assert _group_assignments(model, "EMPTY", 5) == []


def _react_row(name):
    return {
        "joint": name,
        "output_case": "DEAD",
        "step_type": "Max",
        "step_num": 0.0,
        "f1": 1.0,
        "f2": 2.0,
        "f3": -3.0,
        "m1": 4.0,
        "m2": 5.0,
        "m3": 6.0,
    }


def test_joint_result_rows_short_signature(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _joint_result_rows

    class Results:
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

    class Model:
        pass

    model = Model()
    model.Results = Results()
    assert _joint_result_rows(model, "JointReact", ["N1"]) == [_react_row("N1")]


def test_joint_result_rows_falls_back_to_long_signature(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _joint_result_rows

    class Results:
        def JointReact(self, *args):
            if len(args) == 2:
                raise Exception("short signature not supported by this SAP2000 build")
            name = args[0]
            # Format B: (ret, NumberResults, Obj, Elm, LoadCase, StepType,
            # StepNum, F1, F2, F3, M1, M2, M3) -- ret leads, per reacciones.py.
            return (
                0,
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
            )

    class Model:
        pass

    model = Model()
    model.Results = Results()
    assert _joint_result_rows(model, "JointReact", ["N1"]) == [_react_row("N1")]


def test_joint_result_rows_long_signature_for_joint_displ():
    from bridge.sap2000_agent import _joint_result_rows

    class Results:
        def JointDispl(self, *args):
            if len(args) == 2:
                raise Exception("short signature not supported")
            name = args[0]
            # Format B, 11 data arrays: Obj, Elm, LoadCase, StepType, StepNum,
            # U1, U2, U3, R1, R2, R3 -- rotations are read but unused below.
            return (
                0,
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
            )

    class Model:
        pass

    model = Model()
    model.Results = Results()
    rows = _joint_result_rows(model, "JointDispl", ["N1"])
    assert rows == [
        {
            "joint": "N1",
            "output_case": "DEAD",
            "step_type": "Max",
            "step_num": 0.0,
            "u1": 0.1,
            "u2": 0.2,
            "u3": -0.3,
        }
    ]


def test_joint_result_rows_nonzero_return_code_raises():
    from bridge.sap2000_agent import SapComError, _joint_result_rows

    class Results:
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
                7,
            )

    class Model:
        pass

    model = Model()
    model.Results = Results()
    with pytest.raises(SapComError, match="returned 7"):
        _joint_result_rows(model, "JointReact", ["N1"])


def test_joint_result_rows_rejects_inconsistent_vector_lengths():
    from bridge.sap2000_agent import SapComError, _joint_result_rows

    class Results:
        def JointReact(self, name, item_type):
            return (
                2,
                (name, "N2"), (name, "N2"), ("DEAD", "DEAD"), ("Step", "Step"), (0.0, 1.0),
                (1.0, 2.0), (2.0, 3.0), (-3.0,), (4.0, 5.0), (5.0, 6.0), (6.0, 7.0), 0,
            )

    class Model:
        pass

    model = Model()
    model.Results = Results()
    with pytest.raises(SapComError, match="inconsistent result arrays.*declared 2"):
        _joint_result_rows(model, "JointReact", ["N1"])


def test_soil_pressures_preserves_step_identity(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _soil_pressures

    class DatabaseTables:
        def GetTableForDisplayArray(self, *args):
            return (
                0, (), 0,
                ("Area", "OutputCase", "StepType", "StepNum", "Pressure"), 2,
                ("A1", "TH", "Step", "0", "-1.5", "A1", "TH", "Step", "1", "-2.5"),
            )

    class Model:
        pass

    model = Model()
    model.DatabaseTables = DatabaseTables()
    assert _soil_pressures(model) == [
        {"shell": "A1", "output_case": "TH", "step_type": "Step", "step_num": 0.0, "pressure": -1.5},
        {"shell": "A1", "output_case": "TH", "step_type": "Step", "step_num": 1.0, "pressure": -2.5},
    ]


def test_soil_pressures_rejects_table_return_code_but_accepts_partial_step_columns(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapComError, _soil_pressures

    class FailedTables:
        def GetTableForDisplayArray(self, *args):
            return (7, (), 0, ("Area", "OutputCase", "Pressure"), 1, ("A1", "DEAD", "-1"))

    class PartialStepTables:
        def GetTableForDisplayArray(self, *args):
            return (0, (), 0, ("Area", "OutputCase", "StepType", "Pressure"), 1, ("A1", "TH", "Step", "-1"))

    class Model:
        def __init__(self, tables):
            self.DatabaseTables = tables

    with pytest.raises(SapComError, match="returned 7"):
        _soil_pressures(Model(FailedTables()))
    assert _soil_pressures(Model(PartialStepTables())) == [
        {"shell": "A1", "output_case": "TH", "step_type": "Step", "pressure": -1.0}
    ]


def test_snapshot_skips_soil_pressures_only_for_joints_only_request(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    import bridge.sap2000_agent as agent

    reads = []
    monkeypatch.setattr(agent, "_configured_units", lambda: ("Ton_m_C", 12))
    monkeypatch.setattr(agent, "_select_all_cases_for_output", lambda m: None)
    monkeypatch.setattr(agent, "_set_output_selection", lambda m, c, b: None)
    monkeypatch.setattr(agent, "_get_name_list", lambda o, label: [])
    monkeypatch.setattr(agent, "_coordinates", lambda m, n: [])
    monkeypatch.setattr(agent, "_joint_result_rows", lambda m, k, n: [])
    monkeypatch.setattr(agent, "_model_is_locked", lambda m: True)
    monkeypatch.setattr(
        agent,
        "_soil_pressures",
        lambda m: reads.append(1) or [{"shell": "A1", "output_case": "D", "pressure": -1.0}],
    )

    class Model:
        def GetPresentUnits(self):
            return 5

        def SetPresentUnits(self, u):
            return 0

        def GetModelFilename(self):
            return ""

        PointObj = None

    assert agent._snapshot_from_model(Model(), [], joints=["1"])["soil_pressures"] == []
    assert reads == []
    assert len(agent._snapshot_from_model(Model(), [], joints=["1"], shells=["A1"])["soil_pressures"]) == 1
    assert len(agent._snapshot_from_model(Model(), [])["soil_pressures"]) == 1
    assert len(reads) == 2
