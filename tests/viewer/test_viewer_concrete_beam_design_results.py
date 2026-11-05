"""
Fase 23: resultados de diseño de vigas de hormigón (áreas de refuerzo
requeridas) vía DesignConcrete.GetSummaryResultsBeam. A diferencia de
steel_design_results/concrete_design_results (columnas, Fase 18/19), SAP2000
no reporta un ratio D/C para vigas — solo áreas de acero requeridas — así
que esta tabla no alimenta build_design_ratios ni el modo de color "D/C
Diseño" del visor 3D, es solo tabla/consulta.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader


def test_concrete_beam_design_results_calls_real_getsummaryresultsbeam_method() -> None:
    """Prueba que el reader invoca DesignConcrete.GetSummaryResultsBeam (la
    API real), no un atajo inexistente en SAP2000 real."""
    model = FakeSapObject().SapModel
    calls = []
    original = model.DesignConcrete.GetSummaryResultsBeam

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    model.DesignConcrete.GetSummaryResultsBeam = spy

    reader = SapModelReader(model)
    reader.concrete_beam_design_results()

    assert len(calls) == 1


def test_concrete_beam_design_results_returns_empty_table_when_com_call_fails() -> None:
    model = FakeSapObject().SapModel

    def broken(*args, **kwargs):
        raise RuntimeError("design not run")

    model.DesignConcrete.GetSummaryResultsBeam = broken

    reader = SapModelReader(model)
    table = reader.concrete_beam_design_results()

    assert table["rows"]
    assert "Sin datos" in str(table["rows"][0][-1]) or table["columns"][-1] == "Aviso"


def test_concrete_beam_design_results_reports_rebar_areas_not_a_ratio() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)
    table = reader.concrete_beam_design_results()

    assert table["columns"] == ["Frame", "Location", "Top Area", "Bot Area", "Vmajor Area"]
    assert len(table["rows"]) == 2
    b1 = next(r for r in table["rows"] if r[0] == "B1")
    assert b1[2] == "12.5000"
    assert b1[3] == "6.3000"
    assert b1[4] == "2.1000"


def test_concrete_beam_design_results_filters_by_frame_name_and_sets_summary() -> None:
    reader = SapModelReader(FakeSapObject().SapModel)

    table = reader.concrete_beam_design_results(frame_name="B2")

    assert len(table["rows"]) == 1
    assert table["rows"][0][0] == "B2"
    assert "B2" in table["summary_message"]


def test_concrete_beam_design_results_not_used_by_build_design_ratios() -> None:
    """No hay ratio comparable para vigas — build_design_ratios (que
    alimenta el modo de color D/C del visor) no debe tocar esta tabla."""
    from bridge.viewer.sap2000.results_views import build_design_ratios

    reader = SapModelReader(FakeSapObject().SapModel)
    ratios = build_design_ratios(reader)

    assert "B1" not in ratios
    assert "B2" not in ratios
