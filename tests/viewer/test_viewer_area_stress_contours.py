# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.results_views import reduce_area_forces_to_scalar


def _area_forces_table() -> dict:
    return {
        "columns": ["Obj", "Elm", "PointElm", "LoadCase", "StepType", "StepNum", "M11", "M22"],
        "rows": [
            ["A1", "A1", "1", "DEAD", "Max", 0.0, 10.0, -4.0],
            ["A1", "A1", "2", "DEAD", "Max", 0.0, 20.0, -6.0],
            ["A2", "A2", "1", "DEAD", "Max", 0.0, 5.0, 1.0],
        ],
    }


def test_reduce_area_forces_averages_corner_points_per_area() -> None:
    values, vmax = reduce_area_forces_to_scalar(_area_forces_table(), "M11")

    assert values == {"A1": 15.0, "A2": 5.0}
    assert vmax == 15.0


def test_reduce_area_forces_selects_requested_component() -> None:
    values, vmax = reduce_area_forces_to_scalar(_area_forces_table(), "M22")

    assert values == {"A1": -5.0, "A2": 1.0}
    assert vmax == 5.0


def test_reduce_area_forces_returns_empty_when_component_unresolvable() -> None:
    values, vmax = reduce_area_forces_to_scalar(_area_forces_table(), "V13")

    assert values == {}
    assert vmax == 0.0


def test_reduce_area_forces_handles_empty_table() -> None:
    values, vmax = reduce_area_forces_to_scalar({"columns": [], "rows": []}, "M11")

    assert values == {}
    assert vmax == 0.0


