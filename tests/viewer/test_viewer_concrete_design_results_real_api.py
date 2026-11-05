"""
Regression tests for concrete_design_results() calling the REAL SAP2000 API
(DesignConcrete.GetSummaryResultsColumn) instead of a `.summary_results`
Python attribute that only ever existed on the test double — confirmed
against real SAP2000 always silently returned an empty table before this
fix (same bug class as steel_design_results, Fase 18).
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader


def test_concrete_design_results_calls_real_getsummaryresultscolumn_method() -> None:
    """Proves the fix: the reader must invoke
    DesignConcrete.GetSummaryResultsColumn (the actual OAPI method), not
    read a `.summary_results` attribute that doesn't exist on a real
    SAP2000 COM object."""
    model = FakeSapObject().SapModel
    calls = []
    original = model.DesignConcrete.GetSummaryResultsColumn

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    model.DesignConcrete.GetSummaryResultsColumn = spy

    reader = SapModelReader(model)
    reader.concrete_design_results()

    assert len(calls) == 1


def test_concrete_design_results_does_not_depend_on_summary_results_attribute() -> None:
    """Removing the fake-only `.summary_results` attribute must NOT break
    the reader — if it did, that would mean the old (broken) attribute-based
    path was still being used somewhere."""
    model = FakeSapObject().SapModel
    del model.DesignConcrete.summary_results

    reader = SapModelReader(model)
    table = reader.concrete_design_results()

    assert len(table["rows"]) == 2
    assert table["rows"][0][0] == "C1"


def test_concrete_design_results_returns_empty_table_when_com_call_fails() -> None:
    """If GetSummaryResultsColumn itself raises (as it would on real
    SAP2000 if e.g. design hasn't been run), fall back to an empty table
    instead of crashing."""
    model = FakeSapObject().SapModel

    def broken(*args, **kwargs):
        raise RuntimeError("design not run")

    model.DesignConcrete.GetSummaryResultsColumn = broken

    reader = SapModelReader(model)
    table = reader.concrete_design_results()

    assert table["rows"]
    assert "Sin datos" in str(table["rows"][0][-1]) or table["columns"][-1] == "Aviso"


def test_concrete_design_results_check_mode_reports_real_ratio() -> None:
    """MyOption=1 ("Check" mode) has a meaningful PMMRatio — must be
    surfaced as a real numeric D/C with OK/FAIL status."""
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.concrete_design_results()

    check_row = next(r for r in table["rows"] if r[0] == "C1")
    assert check_row[1] == "0.720"
    assert check_row[4] == "OK"


def test_concrete_design_results_design_mode_does_not_fabricate_ratio() -> None:
    """MyOption=2 ("Design" mode) has no meaningful PMMRatio — must NOT be
    reported as OK/FAIL with a fabricated number, only a clear N/A status."""
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.concrete_design_results()

    design_row = next(r for r in table["rows"] if r[0] == "C2")
    assert design_row[1] == ""
    assert design_row[4] == "N/A (modo Diseño)"


def test_concrete_design_results_filters_by_frame_name_and_sets_summary() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.concrete_design_results(frame_name="C1")

    assert len(table["rows"]) == 1
    assert table["rows"][0][0] == "C1"
    assert "C1" in table["summary_message"]
    assert table["rows"][0][1] in table["summary_message"]


def test_build_design_ratios_picks_up_check_mode_concrete_column_and_skips_design_mode() -> None:
    """End-to-end: build_design_ratios (feeds the 3D viewer's
    "D/C Diseño" color mode) must see the real Check-mode column ratio and
    silently skip the Design-mode row (no numeric ratio to fabricate),
    rather than crashing or showing a made-up value."""
    from bridge.viewer.sap2000.results_views import build_design_ratios

    reader = SapModelReader(FakeSapObject().SapModel)
    ratios = build_design_ratios(reader)

    assert ratios["C1"] == 0.72
    assert "C2" not in ratios
