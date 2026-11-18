"""La selección de salida de SAP2000 del usuario queda igual tras cada lectura (``preserve_output_selection``)."""

from __future__ import annotations

import pytest

from bridge.seleccion_salida import con_avisos, preserve_output_selection
from bridge.viewer.sap2000.fake_model import FakeResultsModel
from bridge.viewer.sap2000.model_reader import SapModelReader


def _estado(model) -> tuple[set[str], set[str]]:
    setup = model.Results.Setup
    return set(setup.cases), set(setup.combos)


def _modelo_con_seleccion() -> FakeResultsModel:
    model = FakeResultsModel()
    model.Results.Setup.cases.update({"Live"})
    model.Results.Setup.combos.update({"ENV"})
    return model


@pytest.mark.parametrize(
    "metodo",
    ["joint_displacements", "frame_forces", "section_cut_forces", "area_forces", "joint_reactions"],
)
def test_lector_del_visor_devuelve_la_seleccion_del_usuario(metodo):
    model = _modelo_con_seleccion()
    antes = _estado(model)
    try:
        getattr(SapModelReader(model), metodo)()
    except Exception:  # noqa: BLE001 - el fake puede no tener datos de todo; la selección debe volver igual
        pass
    assert _estado(model) == antes == ({"Live"}, {"ENV"})


def test_contexto_restaura_aunque_la_lectura_falle():
    model = _modelo_con_seleccion()
    with pytest.raises(RuntimeError), preserve_output_selection(model):
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
        model.Results.Setup.SetCaseSelectedForOutput("Dead", True)
        raise RuntimeError("falla la lectura")
    assert _estado(model) == ({"Live"}, {"ENV"})


def test_aviso_si_no_se_puede_leer_la_seleccion():
    model = _modelo_con_seleccion()

    def roto(_name):
        raise OSError("sin acceso")

    model.Results.Setup.GetCaseSelectedForOutput = roto
    avisos: list[str] = []
    with preserve_output_selection(model, avisos):
        pass
    assert avisos and "No se pudo leer la selección de salida" in avisos[0]


def test_aviso_si_no_se_puede_restaurar():
    model = _modelo_con_seleccion()
    avisos: list[str] = []
    with preserve_output_selection(model, avisos):

        def roto():
            raise OSError("bloqueado")

        model.Results.Setup.DeselectAllCasesAndCombosForOutput = roto
    assert avisos and "No se pudo restaurar la selección de salida" in avisos[0]


def test_con_avisos_solo_añade_la_clave_si_hay_avisos():
    assert con_avisos({"a": 1}, []) == {"a": 1}
    assert con_avisos({"a": 1}, ["x"]) == {"a": 1, "warnings": ["x"]}


# --- Acciones del puente con sus modelos falsos: se sustituye ``Results.Setup`` por uno con estado ---------------------


def _con_estado(model, monkeypatch, casos, combos, marcados_caso=(), marcados_combo=()):
    """Añade al ``Results.Setup`` del fake una selección con estado, conservando su comportamiento propio."""

    class Estado:
        cases = set(marcados_caso)
        combos = set(marcados_combo)

    setup = model.Results.Setup
    original_deselect = getattr(setup, "DeselectAllCasesAndCombosForOutput", lambda: 0)
    original_set_case = getattr(setup, "SetCaseSelectedForOutput", lambda *_a: 0)
    original_set_combo = getattr(setup, "SetComboSelectedForOutput", lambda *_a: 0)

    def deselect():
        Estado.cases.clear()
        Estado.combos.clear()
        return original_deselect()

    def set_case(name, selected=True):
        (Estado.cases.add if selected else Estado.cases.discard)(name)
        return original_set_case(name)

    def set_combo(name, selected=True):
        (Estado.combos.add if selected else Estado.combos.discard)(name)
        return original_set_combo(name)

    setup.DeselectAllCasesAndCombosForOutput = deselect
    setup.SetCaseSelectedForOutput = set_case
    setup.SetComboSelectedForOutput = set_combo
    setup.GetCaseSelectedForOutput = lambda n: (n in Estado.cases, 0)
    setup.GetComboSelectedForOutput = lambda n: (n in Estado.combos, 0)
    import bridge.viewer.sap2000.reader_results as rr

    monkeypatch.setattr(rr, "_load_case_names", lambda _m: list(casos))
    monkeypatch.setattr(rr, "_load_combination_names", lambda _m: list(combos))
    return Estado


def test_hormigon_get_frame_forces_restaura(monkeypatch):
    from bridge.hormigon import get_frame_forces
    from tests.test_bridge_hormigon import FakeModel

    model = FakeModel()
    setup = _con_estado(model, monkeypatch, ["D"], ["C1", "C2"], ["D"], [])
    get_frame_forces(model, "B1", ["C1", "C2"])
    assert (setup.cases, setup.combos) == ({"D"}, set())


def test_espectro_calcular_ps_restaura(monkeypatch):
    from bridge.espectro import calcular_ps
    from tests.test_bridge_espectro import FakeModel

    model = FakeModel(base_react={"MS1": ([10.0], [0.0], [100.0])})
    setup = _con_estado(model, monkeypatch, ["MS1", "D"], [], ["D"], [])
    calcular_ps(model, [{"case": "MS1", "factor": 1.0}])
    assert (setup.cases, setup.combos) == ({"D"}, set())


def test_equilibrio_get_frame_data_restaura(monkeypatch):
    from bridge.equilibrio import get_frame_data
    from tests.test_bridge_equilibrio import FakeModel

    model = FakeModel()
    setup = _con_estado(model, monkeypatch, ["SISMO"], ["MALO", "BUENO"], [], ["BUENO"])
    get_frame_data(model, "D1", "BUENO", "SISMO")
    assert (setup.cases, setup.combos) == (set(), {"BUENO"})
