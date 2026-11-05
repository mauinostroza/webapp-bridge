"""
Tarea 26.5 / 26.4 (lectura): _sap_results_table y _sap_all_selected_results_table
asignaban columnas a arrays por posicion (labels[i] <-> arrays[i]). Si SAP2000
devuelve menos arrays de los esperados (p.ej. StepType vacio en un analisis sin
pasos), todos los arrays posteriores se desplazan una posicion respecto a su
etiqueta: los valores de V2 quedarian etiquetados como P, los de M3 como M2,
etc. -- sin ningun error. Estos tests fijan el contrato: menos arrays que
labels => se rechaza la tabla entera (no se desplazan etiquetas); mas arrays
que labels sigue siendo el caso benigno de relleno "Value N".

Tambien fijan el contrato de truncamiento (26.4, parte de lectura): la tabla
debe marcar metadata["truncated"] cuando number_results (el total real que
reporto SAP2000) supera las filas que quedaron tras el limite, y NO marcarlo
cuando number_results es 0/None/no fiable.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.reader_results import _sap_results_table, _sap_all_selected_results_table
from bridge.viewer.sap2000.reader_utils import _is_diagnostic_table


LABELS = ["Frame", "Station", "OutputCase", "StepType", "P", "V2", "V3", "T", "M2", "M3"]


def _fake_func(*, number_results, arrays, ret_code=0):
    """Imita la forma real de retorno de una funcion Results.* de SAP2000:
    (NumberResults, array1, array2, ..., ret_code). Acepta cualquier firma de
    llamada (item, item_type) porque _call_result_function la invoca asi."""

    def func(item, item_type):
        return (number_results, *arrays, ret_code)

    return func


def test_fewer_arrays_than_labels_rejects_table_instead_of_shifting() -> None:
    """Caso del bug: SAP2000 devuelve 8 arrays quando se esperan 10 columnas
    (p.ej. StepType y M3 vacios/ausentes). No debe desplazar V2 a la posicion
    de P ni nada por el estilo: debe rechazar y devolver una tabla de
    diagnostico."""
    n_rows = 3
    # Solo 8 arrays de datos, para 10 labels esperados.
    arrays = [
        [f"F{i}" for i in range(n_rows)],  # Frame
        [0.0, 1.0, 2.0],  # Station
        ["DEAD"] * n_rows,  # OutputCase
        ["Combination"] * n_rows,  # StepType
        [10.0, 20.0, 30.0],  # P
        [1.0, 2.0, 3.0],  # V2
        [0.1, 0.2, 0.3],  # V3
        [5.0, 6.0, 7.0],  # T
    ]
    func = _fake_func(number_results=n_rows, arrays=arrays)

    table = _sap_results_table("Frame Forces", func, "ALL", LABELS, setup={})

    assert _is_diagnostic_table(table), "debe rechazar la tabla, no desplazar columnas"
    # La tabla de diagnostico no debe contener las columnas reales desalineadas.
    assert table["columns"] == ["Dato", "Cantidad", "Valor"]


def test_more_arrays_than_labels_still_fills_value_n_benign_case() -> None:
    """Caso benigno preexistente: si SAP2000 devuelve mas arrays de los que
    hay labels, el relleno 'Value N' debe seguir funcionando."""
    short_labels = ["Frame", "Station"]
    arrays = [
        ["F1", "F2"],
        [0.0, 1.0],
        [10.0, 20.0],  # array extra sin label -> "Value 3"
    ]
    func = _fake_func(number_results=2, arrays=arrays)

    table = _sap_results_table("Frame Forces", func, "ALL", short_labels, setup={})

    assert not _is_diagnostic_table(table)
    assert table["columns"] == ["Frame", "Station", "Value 3"]
    assert table["rows"] == [["F1", 0.0, 10.0], ["F2", 1.0, 20.0]]


def test_normal_complete_case_gives_same_columns_as_before() -> None:
    """Caso normal: numero de arrays == numero de labels -> columnas exactas,
    sin relleno ni rechazo."""
    n_rows = 2
    arrays = [
        ["F1", "F2"],  # Frame
        [0.0, 1.0],  # Station
        ["DEAD", "DEAD"],  # OutputCase
        ["Combination", "Combination"],  # StepType
        [10.0, 20.0],  # P
        [1.0, 2.0],  # V2
        [0.1, 0.2],  # V3
        [5.0, 6.0],  # T
        [0.5, 0.6],  # M2
        [3.0, 4.0],  # M3
    ]
    func = _fake_func(number_results=n_rows, arrays=arrays)

    table = _sap_results_table("Frame Forces", func, "ALL", LABELS, setup={})

    assert not _is_diagnostic_table(table)
    assert table["columns"] == LABELS
    assert table["rows"][0] == ["F1", 0.0, "DEAD", "Combination", 10.0, 1.0, 0.1, 5.0, 0.5, 3.0]
    assert table["rows"][1] == ["F2", 1.0, "DEAD", "Combination", 20.0, 2.0, 0.2, 6.0, 0.6, 4.0]


def test_truncation_marked_when_number_results_exceeds_returned_rows() -> None:
    """Con limit=1 y number_results=5, solo queda 1 fila -> debe marcarse
    truncado con el total real disponible."""
    arrays = [
        ["F1", "F2", "F3", "F4", "F5"],
        [0.0, 1.0, 2.0, 3.0, 4.0],
    ]
    func = _fake_func(number_results=5, arrays=arrays)

    table = _sap_results_table(
        "Frame Forces", func, "ALL", ["Frame", "Station"], setup={}, limit=1
    )

    assert not _is_diagnostic_table(table)
    assert table["metadata"]["truncated"] is True
    assert table["metadata"]["total_results_available"] == 5
    assert table["metadata"]["row_count"] == 1


def test_truncation_not_marked_when_all_rows_returned() -> None:
    """number_results coincide exactamente con las filas devueltas -> no hay
    truncamiento."""
    arrays = [
        ["F1", "F2"],
        [0.0, 1.0],
    ]
    func = _fake_func(number_results=2, arrays=arrays)

    table = _sap_results_table("Frame Forces", func, "ALL", ["Frame", "Station"], setup={})

    assert table["metadata"]["truncated"] is False


def test_truncation_not_marked_when_number_results_unreliable() -> None:
    """number_results ausente (None) -- no debe marcarse truncado ni con
    falso positivo, aunque el limit recorte filas: mas vale no informar que
    des-informar."""

    def func(item, item_type):
        # Ningun elemento es int (ni NumberResults ni ret_code) -> _first_int
        # no encuentra nada -> None. No debe confundirse con "0 filas" ni con
        # un total fiable.
        return (["F1", "F2", "F3"], [0.0, 1.0, 2.0], "OK")

    table = _sap_results_table(
        "Frame Forces", func, "ALL", ["Frame", "Station"], setup={}, limit=1
    )

    assert table["metadata"]["number_results"] is None
    assert table["metadata"]["truncated"] is False


def test_truncation_not_marked_when_number_results_is_zero_but_rows_exist() -> None:
    """Caso limite: number_results=0 ya dispara el camino de "sin filas" mas
    arriba (tabla de diagnostico), asi que no llega a evaluarse truncamiento
    con datos reales -- se confirma aqui que sigue siendo diagnostico y no
    una tabla con truncated=True espurio."""
    arrays = [["F1"], [0.0]]
    func = _fake_func(number_results=0, arrays=arrays)

    table = _sap_results_table("Frame Forces", func, "ALL", ["Frame", "Station"], setup={})

    assert _is_diagnostic_table(table)


def test_all_selected_results_table_rejects_fewer_arrays_than_labels() -> None:
    """Mismo contrato de 26.5 para el otro constructor de tablas (funciones
    Results.* sin (Name, ItemTypeElm), p.ej. SectionCutAnalysis)."""
    labels = ["SectionCut", "OutputCase", "StepType", "F1", "F2", "F3"]
    arrays = [
        ["SC1", "SC2"],
        ["DEAD", "DEAD"],
        ["Combination", "Combination"],
        [1.0, 2.0],
    ]

    def func():
        return (2, *arrays, 0)

    table = _sap_all_selected_results_table("Section Cuts", func, labels, setup={})

    assert _is_diagnostic_table(table)


def test_all_selected_results_table_normal_case_and_truncation() -> None:
    labels = ["SectionCut", "OutputCase", "F1"]
    arrays = [
        ["SC1", "SC2", "SC3"],
        ["DEAD", "DEAD", "DEAD"],
        [1.0, 2.0, 3.0],
    ]

    def func():
        return (3, *arrays, 0)

    table = _sap_all_selected_results_table("Section Cuts", func, labels, setup={}, limit=2)

    assert not _is_diagnostic_table(table)
    assert table["columns"] == labels
    assert table["metadata"]["truncated"] is True
    assert table["metadata"]["total_results_available"] == 3
