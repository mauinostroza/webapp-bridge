# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.reader_geometry import (
    _frame_endpoints_from_api,
    _frame_geometry,
    _joint_coordinates_from_api,
    _joint_coords_and_groups,
)


def test_joint_coords_prefer_database_table_over_per_joint_com_calls() -> None:
    """Fase 6: GetTableForDisplayArray es ahora el camino primario, no el loop."""
    fake = FakeSapObject().SapModel
    call_count = {"n": 0}
    original = fake.PointObj.GetCoordCartesian

    def counting_get_coord(name, *args):
        call_count["n"] += 1
        return original(name, *args)

    fake.PointObj.GetCoordCartesian = counting_get_coord

    coords, _groups, errors = _joint_coords_and_groups(fake)

    assert call_count["n"] == 0, "no debe llamar GetCoordCartesian por joint si la tabla batch trae todo"
    assert not errors
    assert coords == {
        "J1": (0.0, 0.0, 0.0),
        "J2": (6.0, 0.0, 0.0),
        "J3": (6.0, 4.0, 3.0),
        "J4": (0.0, 4.0, 3.0),
    }


def test_frame_endpoints_prefer_database_table_over_per_frame_com_calls() -> None:
    fake = FakeSapObject().SapModel
    call_count = {"n": 0}
    original = fake.FrameObj.GetPoints

    def counting_get_points(name, *args):
        call_count["n"] += 1
        return original(name, *args)

    fake.FrameObj.GetPoints = counting_get_points

    frames, errors = _frame_geometry(fake)

    assert call_count["n"] == 0, "no debe llamar GetPoints por frame si la tabla batch trae todo"
    assert not errors
    assert {f["name"]: (f["i_end"], f["j_end"]) for f in frames} == {
        "F1": ("J1", "J2"),
        "F2": ("J2", "J3"),
        "F3": ("J3", "J4"),
    }


def test_joint_coords_batch_path_matches_per_object_loop_result() -> None:
    """Equivalencia: la tabla batch y el loop por-objeto deben devolver los mismos datos."""
    fake = FakeSapObject().SapModel
    coords_from_table, _groups, _errors = _joint_coords_and_groups(fake)
    coords_from_loop = _joint_coordinates_from_api(fake, [])
    assert coords_from_table == coords_from_loop


def test_frame_endpoints_batch_path_matches_per_object_loop_result() -> None:
    fake = FakeSapObject().SapModel
    frames, _errors = _frame_geometry(fake)
    endpoints_from_table = {f["name"]: (f["i_end"], f["j_end"]) for f in frames}
    endpoints_from_loop = _frame_endpoints_from_api(fake, [])
    assert endpoints_from_table == endpoints_from_loop
