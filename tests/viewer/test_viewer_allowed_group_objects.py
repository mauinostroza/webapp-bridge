# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.reader_geometry import _allowed_group_objects


def test_allowed_group_objects_resolves_joint_names_from_frame_endpoints() -> None:
    """Regression: previously routed through _frame_geometry() (which also
    re-reads joint coordinates and group memberships the caller already had).
    Now reads frame endpoints directly — same result, fewer COM calls."""
    fake = FakeSapObject().SapModel
    group_members = {"F1", "F2"}

    allowed = _allowed_group_objects(fake, "VIGAS_N2", "joint_coordinates", group_members)

    assert allowed == {"J1", "J2", "J3"}


def test_allowed_group_objects_passthrough_for_frame_actions() -> None:
    fake = FakeSapObject().SapModel
    group_members = {"F1", "F2"}

    allowed = _allowed_group_objects(fake, "VIGAS_N2", "frame_geometry", group_members)

    assert allowed == group_members
