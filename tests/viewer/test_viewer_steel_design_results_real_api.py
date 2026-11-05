"""
Regression tests for steel_design_results() calling the REAL SAP2000 API
(DesignSteel.GetSummaryResults) instead of a `.summary_results` Python
attribute that only ever existed on the test double — confirmed against
real SAP2000 always silently returned an empty table before this fix.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader


def test_steel_design_results_calls_real_getsummaryresults_method() -> None:
    """Proves the fix: the reader must invoke DesignSteel.GetSummaryResults
    (the actual OAPI method), not read a `.summary_results` attribute that
    doesn't exist on a real SAP2000 COM object."""
    model = FakeSapObject().SapModel
    calls = []
    original = model.DesignSteel.GetSummaryResults

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    model.DesignSteel.GetSummaryResults = spy

    reader = SapModelReader(model)
    reader.steel_design_results()

    assert len(calls) == 1


def test_steel_design_results_does_not_depend_on_summary_results_attribute() -> None:
    """Removing the fake-only `.summary_results` attribute must NOT break
    the reader — if it did, that would mean the old (broken) attribute-based
    path was still being used somewhere."""
    model = FakeSapObject().SapModel
    del model.DesignSteel.summary_results

    reader = SapModelReader(model)
    table = reader.steel_design_results()

    assert len(table["rows"]) == 3
    assert table["rows"][0][0] == "F1"


def test_steel_design_results_returns_empty_table_when_com_call_fails() -> None:
    """If GetSummaryResults itself raises (as it would on real SAP2000 if
    e.g. design hasn't been run), fall back to an empty/diagnostic table
    instead of crashing."""
    model = FakeSapObject().SapModel

    def broken(*args, **kwargs):
        raise RuntimeError("design not run")

    model.DesignSteel.GetSummaryResults = broken

    reader = SapModelReader(model)
    table = reader.steel_design_results()

    assert table["rows"]
    assert "Sin datos" in str(table["rows"][0][-1]) or table["columns"][-1] == "Aviso"


def test_steel_design_results_maps_ratio_type_codes_to_labels() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.steel_design_results()

    types = {row[0]: row[2] for row in table["rows"]}
    assert types["F1"] == "PMM"
    assert types["F3"] == "Corte mayor"


def test_steel_design_results_status_reflects_ratio_threshold() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.steel_design_results()

    for row in table["rows"]:
        ratio = float(row[1])
        status = row[4]
        assert status == ("OK" if ratio <= 1.0 else "FAIL")


def test_steel_design_results_filters_by_frame_name_and_sets_summary() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.steel_design_results(frame_name="F3")

    assert len(table["rows"]) == 1
    assert table["rows"][0][0] == "F3"
    assert "F3" in table["summary_message"]
    assert table["rows"][0][1] in table["summary_message"]


def test_build_design_ratios_picks_up_real_steel_results() -> None:
    """End-to-end: build_design_ratios (feeds the 3D viewer's
    "D/C Diseño" color mode) must see the real steel ratios, not an empty
    dict caused by the old broken attribute access."""
    from bridge.viewer.sap2000.results_views import build_design_ratios

    reader = SapModelReader(FakeSapObject().SapModel)
    ratios = build_design_ratios(reader)

    assert ratios["F1"] == 0.85
    assert ratios["F2"] == 0.62
    assert ratios["F3"] == 0.43
