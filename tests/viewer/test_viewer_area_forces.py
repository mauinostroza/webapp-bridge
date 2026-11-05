# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader


def test_area_forces_reads_via_results_areaforceshell() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.area_forces()

    assert "M11" in table["columns"]
    assert "V13" in table["columns"]
    assert len(table["rows"]) == 4  # 4 corner points for the fake model's single area


def test_area_forces_filters_by_area_name() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.area_forces(area_name="A1")

    assert all(row[0] == "A1" for row in table["rows"])


def test_area_forces_registered_in_table_for_action() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.table_for_action("area_forces", {"area": "A1"})

    assert table["rows"]
