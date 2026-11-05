# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.reader_sections import area_shell_thickness
from bridge.viewer.sap2000.units import length_factor_to_meters


def test_length_factor_to_meters_for_common_systems() -> None:
    assert length_factor_to_meters("kN_m_C") == 1.0
    assert length_factor_to_meters("tonf_m_C") == 1.0
    assert length_factor_to_meters("kN_mm_C") == 0.001
    assert length_factor_to_meters("kN_cm_C") == 0.01
    assert length_factor_to_meters("kip_ft_F") == 0.3048
    assert length_factor_to_meters("kip_in_F") == 0.0254


def test_length_factor_to_meters_accepts_numeric_code() -> None:
    # 5 = kN_mm_C per sap2000/units.py::UNIT_CODES
    assert length_factor_to_meters(5) == 0.001
    # 12 = tonf_m_C
    assert length_factor_to_meters(12) == 1.0


def test_length_factor_to_meters_unknown_falls_back_to_one() -> None:
    assert length_factor_to_meters("bogus_unit_C") == 1.0
    assert length_factor_to_meters(9999) == 1.0


def test_area_shell_thickness_converts_millimeters_to_meters() -> None:
    """Regression: PropArea.GetShell() returns the thickness in SAP2000's
    active present-units, not necessarily meters — a 200mm slab must be
    reported as 0.2m, not 200.0 (the bug found in the Fase 10 bug-hunt)."""
    model = FakeSapObject().SapModel
    model.PropArea._thickness["LOSA_15CM"] = 200.0  # 200mm
    model.GetPresentUnits = lambda: 5  # kN_mm_C

    table = area_shell_thickness(model)

    assert table["thicknesses"]["LOSA_15CM"] == 0.2
    assert table["rows"][0][1] == 0.2


def test_area_shell_thickness_leaves_meters_unconverted() -> None:
    model = FakeSapObject().SapModel
    model.PropArea._thickness["LOSA_15CM"] = 0.25  # already meters
    model.GetPresentUnits = lambda: 12  # tonf_m_C

    table = area_shell_thickness(model)

    assert table["thicknesses"]["LOSA_15CM"] == 0.25


def test_area_shell_thickness_default_fallback_unaffected_by_units() -> None:
    """When the real thickness can't be read, the 0.15m default is already
    in meters and must NOT be multiplied by the unit factor again."""

    class _BrokenPropArea:
        def GetNameList(self, *a):
            return (1, ["SEC1"], 0)

        def GetShell(self, *a):
            raise RuntimeError("boom")

    class _Model:
        PropArea = _BrokenPropArea()

        def GetPresentUnits(self):
            return 5  # kN_mm_C — factor 0.001, must not apply to the default

    table = area_shell_thickness(_Model())

    assert table["thicknesses"]["SEC1"] == 0.15
    assert table["metadata"]["errors"]
