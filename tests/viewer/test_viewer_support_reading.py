# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_geometry import _joint_restraint_data


def test_joint_restraint_data_fixed() -> None:
    fake = FakeSapObject().SapModel
    assert _joint_restraint_data(fake.PointObj, "J1") == [True, True, True, True, True, True]


def test_joint_restraint_data_pinned() -> None:
    fake = FakeSapObject().SapModel
    assert _joint_restraint_data(fake.PointObj, "J2") == [True, True, True, False, False, False]


def test_joint_restraint_data_free() -> None:
    fake = FakeSapObject().SapModel
    assert _joint_restraint_data(fake.PointObj, "J3") == [False, False, False, False, False, False]


def test_joint_restraints_wrapper_omits_free_joints() -> None:
    """Fase 3 del roadmap: joint_restraints() (antes inexistente) solo lista apoyos reales."""
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.joint_restraints()
    assert table["name"] == "joint_restraints"
    assert table["columns"] == ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"]
    joints = [row[0] for row in table["rows"]]
    assert joints == ["J1", "J2"]  # J3/J4 are free, omitted


def test_joint_restraints_filters_by_joint_name() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.joint_restraints(joint_name="J1")
    assert len(table["rows"]) == 1
    assert table["rows"][0] == ["J1", True, True, True, True, True, True]
    assert table["summary_message"] == "El joint J1 tiene restriccion de apoyo asignada."

    table_free = reader.joint_restraints(joint_name="J3")
    assert table_free["rows"] == []
    assert table_free["summary_message"] == "El joint J3 no tiene restriccion de apoyo asignada."


def test_frame_releases_still_works_with_new_fake_hinge() -> None:
    """Sanity check: frame_releases() picks up the new fake GetReleases on F3."""
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.frame_releases()
    f3_rows = [row for row in table["rows"] if row[0] == "F3"]
    assert len(f3_rows) == 2
    i_row = next(r for r in f3_rows if r[1] == "I")
    j_row = next(r for r in f3_rows if r[1] == "J")
    assert i_row[2:] == [False, False, False, False, False, False]
    assert j_row[2:] == [False, False, False, False, True, True]
