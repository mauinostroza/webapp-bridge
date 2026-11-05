# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_geometry import _frame_point_loads, _parse_point_load_result


def test_frame_point_loads_parses_fake_model_data() -> None:
    """Fase 2 del roadmap: FrameObj.GetLoadPoint (antes ausente) ahora se lee."""
    fake = FakeSapObject().SapModel
    entries = _frame_point_loads(fake.FrameObj, "F2")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["frame"] == "F2"
    assert entry["pattern"] == "Live"
    assert entry["type"] == "Force"
    assert entry["dist"] == 0.5
    assert entry["val"] == 15.0


def test_frame_point_loads_empty_for_frame_without_loads() -> None:
    fake = FakeSapObject().SapModel
    assert _frame_point_loads(fake.FrameObj, "F1") == []


def test_parse_point_load_result_ignores_non_list_result() -> None:
    assert _parse_point_load_result("F1", None) == []
    assert _parse_point_load_result("F1", "not-a-tuple") == []


def test_frame_point_loads_list_wrapper() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.frame_point_loads_list()
    assert table["name"] == "frame_point_loads_list"
    assert table["columns"] == ["Frame", "Pattern", "Type", "Dir", "Dist", "Val"]
    # Fase 17: batch read (DatabaseTables "Frame Loads - Point") includes an
    # extra F3/Dead row beyond the per-object GetLoadPoint fixture (F2/Live
    # only), to exercise a real multi-row batch table.
    assert len(table["rows"]) == 2
    row = next(r for r in table["rows"] if r[0] == "F2")
    assert row[1] == "Live"


def test_frame_point_loads_list_filters_by_frame_name() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.frame_point_loads_list(frame_name="F1")
    assert table["rows"] == []
    assert table["summary_message"] == "El frame F1 no tiene cargas puntuales asignadas."

    table_f2 = reader.frame_point_loads_list(frame_name="F2")
    assert len(table_f2["rows"]) == 1
    assert table_f2["summary_message"] == "El frame F2 tiene 1 carga(s) puntual(es)."


def test_frame_point_loads_list_filters_by_pattern() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.frame_point_loads_list(pattern="Dead")
    assert len(table["rows"]) == 1
    assert table["rows"][0][0] == "F3"

    table_live = reader.frame_point_loads_list(pattern="live")  # case-insensitive
    assert len(table_live["rows"]) == 1
    assert table_live["rows"][0][0] == "F2"


def test_frame_distributed_and_joint_loads_still_work() -> None:
    """Sanity check: existing readers keep working after adding fake load methods."""
    reader = SapModelReader(FakeSapObject().SapModel)
    dist = reader.frame_loads_list()
    # Fase 17: batch read (DatabaseTables "Frame Loads - Distributed")
    # includes an extra F2/Live row beyond the per-object
    # GetLoadDistributed fixture (F1/Dead only).
    assert len(dist["rows"]) == 2
    assert {row[0] for row in dist["rows"]} == {"F1", "F2"}

    joints = reader.joint_loads_list()
    # Fase 17: batch read (DatabaseTables "Joint Loads - Force") includes an
    # extra J2/Live row beyond the per-object GetLoadForce fixture (J3 only).
    assert len(joints["rows"]) == 2
    assert {row[0] for row in joints["rows"]} == {"J2", "J3"}
