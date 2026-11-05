"""Patrones «para mostrar» en las lecturas por lote de cargas del Viewer.

Modelo falso: las tablas batch de cargas solo devuelven filas de los patrones seleccionados
para mostrar (como SAP2000). Las lecturas deben traer todos los patrones mientras dura la
lectura y restaurar siempre la selección original. Sin la API, la lectura sigue con aviso.
"""

from __future__ import annotations

import pytest

from bridge.viewer.core import snapshot as snap
from bridge.viewer.sap2000 import reader_geometry
from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_utils import _unpack_selection_result, all_load_patterns_displayed

PATRONES_CON_VIENTO = ["Ds", "Dp", "EQX", "Dead", "Live", "Wind"]
CARGAS_DE_LOS_TRES = {"Dead", "Live", "Wind"}


def _modelo_con_viento():
    """Fake con un tercer patrón («Wind») con una fila en cada tabla batch de cargas."""
    model = FakeSapObject().SapModel
    model.LoadPatterns.names.append("Wind")
    db = model.DatabaseTables
    db.extra_table_rows["Frame Loads - Distributed"] = ["F3", "Wind", "Force", 4, 0.0, 1.0, 2.0, 2.0]
    db.extra_table_rows["Frame Loads - Point"] = ["F1", "Wind", "Force", 4, 0.5, 3.0]
    db.extra_table_rows["Joint Loads - Force"] = ["J1", "Wind", 0.0, 0.0, -1.0, 0.0, 0.0, 0.0]
    return model


def _patrones_de_tabla_raw(raw):
    _, fields, width, _, data, _ = raw
    indice = list(fields).index("LoadPat")
    return {str(data[i + indice]) for i in range(0, len(data), width)}


def _espia_set(db):
    """Registra los argumentos de cada SetLoadPatternsSelectedForDisplay sin cambiar su efecto."""
    llamadas: list[list[str]] = []
    original = db.SetLoadPatternsSelectedForDisplay

    def espia(names):
        llamadas.append(list(names))
        return original(names)

    db.SetLoadPatternsSelectedForDisplay = espia
    return llamadas


@pytest.mark.parametrize("metodo", ["frame_loads_list", "frame_point_loads_list", "joint_loads_list"])
def test_lectura_por_lote_trae_los_tres_patrones_con_uno_seleccionado(metodo):
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    # Premisa: sin el cambio, la tabla batch solo devuelve el patrón seleccionado.
    assert _patrones_de_tabla_raw(db.GetTableForDisplayArray("Frame Loads - Distributed")) == {"Dead"}

    table = getattr(SapModelReader(model), metodo)()

    assert {row[1] for row in table["rows"]} == CARGAS_DE_LOS_TRES
    assert db.display_selected_patterns == ["Dead"]


def test_snapshot_trae_las_cargas_de_los_tres_patrones_con_uno_seleccionado():
    model = _modelo_con_viento()
    model.DatabaseTables.display_selected_patterns = ["Dead"]

    data = snap.build_snapshot(SapModelReader(model), "demo", "Demo", "tonf_m_C")

    assert {load["pattern"] for load in data["loads"]} == CARGAS_DE_LOS_TRES
    assert model.DatabaseTables.display_selected_patterns == ["Dead"]
    assert not [w for w in data["warnings"] if "Patrones para mostrar" in w]


def test_seleccion_original_se_restaura_con_su_orden_tras_la_lectura():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Live", "Dead"]
    llamadas = _espia_set(db)

    SapModelReader(model).frame_loads_list()

    assert llamadas == [PATRONES_CON_VIENTO, ["Live", "Dead"]]
    assert db.display_selected_patterns == ["Live", "Dead"]


def test_seleccion_vacia_se_restaura_vacia():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = []

    table = SapModelReader(model).frame_loads_list()

    assert {row[1] for row in table["rows"]} == CARGAS_DE_LOS_TRES
    assert db.display_selected_patterns == []


def test_restaura_la_seleccion_aunque_la_lectura_lance_excepcion(monkeypatch):
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]

    def falla(*args, **kwargs):
        raise RuntimeError("COM caído a mitad de la lectura")

    monkeypatch.setattr(reader_geometry, "_database_geometry_tables", falla)
    with pytest.raises(RuntimeError, match="COM caído"):
        SapModelReader(model).frame_loads_list()

    assert db.display_selected_patterns == ["Dead"]


def test_context_manager_propaga_la_excepcion_del_cuerpo_y_restaura():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    errores: list[str] = []

    with pytest.raises(ValueError, match="a mitad"):
        with all_load_patterns_displayed(model, errores):
            assert db.display_selected_patterns == PATRONES_CON_VIENTO
            raise ValueError("a mitad")

    assert db.display_selected_patterns == ["Dead"]
    assert errores == []


def test_sin_api_de_seleccion_la_lectura_sigue_con_aviso():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    db.display_selection_supported = False

    table = SapModelReader(model).frame_loads_list()

    assert {row[1] for row in table["rows"]} == {"Dead"}  # comportamiento anterior
    assert any("Patrones para mostrar" in e for e in table["metadata"]["errors"])


def test_snapshot_sin_api_de_seleccion_no_falla_y_lleva_aviso():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    db.display_selection_supported = False

    data = snap.build_snapshot(SapModelReader(model), "demo", "Demo", "tonf_m_C")

    assert {load["pattern"] for load in data["loads"]} == {"Dead"}
    assert any("Patrones para mostrar" in w for w in data["warnings"])


def test_si_set_lanza_no_falla_y_no_deja_la_seleccion_cambiada(monkeypatch):
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]

    def lanza(*args, **kwargs):
        raise AttributeError("SetLoadPatternsSelectedForDisplay no disponible")

    monkeypatch.setattr(db, "SetLoadPatternsSelectedForDisplay", lanza, raising=False)
    errores: list[str] = []
    with all_load_patterns_displayed(model, errores):
        pass

    assert db.display_selected_patterns == ["Dead"]
    assert any("Patrones para mostrar" in e for e in errores)


def test_rechazo_de_set_modelo_bloqueado_solo_avisa_sin_restaurar():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    db.display_selection_rejects = True
    llamadas = _espia_set(db)

    table = SapModelReader(model).frame_loads_list()

    assert {row[1] for row in table["rows"]} == {"Dead"}
    assert llamadas == [PATRONES_CON_VIENTO]  # sin intento de restaurar: SAP no aplicó nada
    assert db.display_selected_patterns == ["Dead"]
    assert len(table["metadata"]["errors"]) == 1
    assert "devolvió 1" in table["metadata"]["errors"][0]


def test_snapshot_solo_escribe_la_seleccion_de_patrones_y_la_restaura():
    model = _modelo_con_viento()
    db = model.DatabaseTables
    db.display_selected_patterns = ["Dead"]
    llamadas = _espia_set(db)

    snap.build_snapshot(SapModelReader(model), "demo", "Demo", "tonf_m_C")

    # Tres lecturas batch de cargas (distribuidas, puntuales, joints): cada una muestra todo y restaura.
    assert llamadas == [PATRONES_CON_VIENTO, ["Dead"]] * 3
    assert db.display_selected_patterns == ["Dead"]


@pytest.mark.parametrize(
    ("respuesta", "esperado"),
    [
        ((0, ["A", "B"]), ["A", "B"]),
        ((["A"], 0), ["A"]),
        ([0, ("A",)], ["A"]),
        ((0, []), []),
    ],
)
def test_unpack_selection_acepta_ret_primero_o_ultimo_y_tuplas(respuesta, esperado):
    assert _unpack_selection_result(respuesta) == esperado


@pytest.mark.parametrize("respuesta", [(1, []), ("x", "y"), (0, ["A"], ["B"]), None])
def test_unpack_selection_rechaza_formas_no_reconocidas(respuesta):
    with pytest.raises(RuntimeError):
        _unpack_selection_result(respuesta)
