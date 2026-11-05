"""
Tests para la lectura del angulo de ejes locales de frame
(_frame_local_axes_from_database / _frame_local_axes_data en
reader_geometry.py, y SapModelReader.frame_local_axes), siguiendo el mismo
patron que tests/test_frame_section_batch.py: batch vs. loop por-objeto,
contrato todo-o-nada, conteo de llamadas COM, filtro por nombre y limit.

Cubre ademas el caso especifico de este dato: un frame con ejes locales
"avanzados" (Advanced=True) no debe devolver nunca un angulo fabricado.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_geometry import (
    _frame_local_axes_data,
    _frame_local_axes_from_database,
)


def test_frame_local_axes_prefer_database_table_over_per_frame_com_calls() -> None:
    """Con la tabla batch disponible, no debe llamarse GetLocalAxes para los
    frames tipicos (F1/F2) que la tabla ya cubre — solo F3 (advanced, que la
    tabla descarta a propósito) cae al loop por-objeto."""
    fake = FakeSapObject().SapModel
    call_names: list[str] = []
    original = fake.FrameObj.GetLocalAxes

    def counting(name, *args):
        call_names.append(name)
        return original(name, *args)

    fake.FrameObj.GetLocalAxes = counting

    reader = SapModelReader(fake)
    result = reader.frame_local_axes()

    assert call_names == ["F3"], "solo debe llamar GetLocalAxes para el frame que el batch no cubrio"
    rows = {r[0]: (r[1], r[2]) for r in result["rows"]}
    assert rows["F1"] == (0.0, False)
    assert rows["F2"] == (15.0, False)


def test_frame_local_axes_advanced_frame_falls_back_and_reports_unavailable() -> None:
    """F3 esta marcado Advanced=Yes en la tabla batch -> se omite de ella y
    cae al loop por-objeto, que via GetLocalAxes tambien confirma Advanced,
    asi que el angulo debe quedar en None (nunca 0.0 ni cualquier otro valor
    inventado), y Advanced=True en la fila."""
    fake = FakeSapObject().SapModel
    call_names: list[str] = []
    original = fake.FrameObj.GetLocalAxes

    def counting(name, *args):
        call_names.append(name)
        return original(name, *args)

    fake.FrameObj.GetLocalAxes = counting

    reader = SapModelReader(fake)
    result = reader.frame_local_axes()

    # Solo F3 (el unico ausente del batch) debe generar una llamada COM.
    assert call_names == ["F3"]
    rows = {r[0]: (r[1], r[2]) for r in result["rows"]}
    assert rows["F3"] == (None, True)


def test_frame_local_axes_batch_falls_back_when_table_missing_columns() -> None:
    """Si falta la columna Angle, la funcion batch debe devolver None y el
    caller cae al loop por-objeto para los 3 frames, intacto."""
    fake = FakeSapObject().SapModel
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def broken_get_table(key: str, *args):
        result = original_get_table(key, *args)
        if key == "Frame Local Axes Assignments 1 - Typical":
            parsed_key, fields, num_fields, num_rows, data, ret_code = result
            broken_fields = [f for f in fields if f != "Angle"]
            return (parsed_key, broken_fields, len(broken_fields), num_rows, [], ret_code)
        return result

    fake.DatabaseTables.GetTableForDisplayArray = broken_get_table

    errors: list[str] = []
    assert _frame_local_axes_from_database(fake, errors) is None

    call_count = {"n": 0}
    original_get_axes = fake.FrameObj.GetLocalAxes

    def counting(name, *args):
        call_count["n"] += 1
        return original_get_axes(name, *args)

    fake.FrameObj.GetLocalAxes = counting

    reader = SapModelReader(fake)
    result = reader.frame_local_axes()

    assert call_count["n"] == 3, "sin tabla batch, debe pedir el angulo a cada uno de los 3 frames"
    rows = {r[0]: (r[1], r[2]) for r in result["rows"]}
    assert rows["F1"] == (0.0, False)
    assert rows["F2"] == (15.0, False)
    assert rows["F3"] == (None, True)


def test_frame_local_axes_from_database_returns_expected_dict() -> None:
    """El batch cubre F1/F2 (tipicos) y omite F3 (advanced)."""
    fake = FakeSapObject().SapModel
    result = _frame_local_axes_from_database(fake, [])
    assert result == {"F1": 0.0, "F2": 15.0}


def test_frame_local_axes_data_per_object_advanced_returns_none_angle() -> None:
    """Contrato directo de _frame_local_axes_data: Advanced=True -> angulo
    None, nunca el Ang crudo devuelto por SAP2000."""
    fake = FakeSapObject().SapModel
    angle, advanced = _frame_local_axes_data(fake.FrameObj, "F3")
    assert advanced is True
    assert angle is None


def test_frame_local_axes_data_per_object_typical_returns_angle() -> None:
    fake = FakeSapObject().SapModel
    angle, advanced = _frame_local_axes_data(fake.FrameObj, "F2")
    assert advanced is False
    assert angle == 15.0


def test_frame_local_axes_respects_frame_name_filter() -> None:
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_local_axes(frame_name="F2")

    rows = result["rows"]
    assert len(rows) == 1
    assert rows[0][0] == "F2"
    assert rows[0][1] == 15.0
    assert rows[0][2] is False
    assert "grados" in result.get("summary_message", "")


def test_frame_local_axes_summary_message_for_advanced_frame() -> None:
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_local_axes(frame_name="F3")

    summary = result.get("summary_message", "")
    assert "F3" in summary
    assert "avanzados" in summary.lower()
    assert "no esta disponible" in summary.lower() or "no está disponible" in summary.lower()


def test_frame_local_axes_not_found_message() -> None:
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_local_axes(frame_name="NONEXISTENT")

    summary = result.get("summary_message", "")
    assert "No encontre" in summary
    assert "NONEXISTENT" in summary


def test_frame_local_axes_respects_limit() -> None:
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_local_axes(limit=2)

    rows = result["rows"]
    assert len(rows) == 2


def test_frame_local_axes_limit_breaks_the_fallback_loop_early() -> None:
    """Con la tabla batch inutilizada, limit=1 debe cortar el bucle apenas
    se junta 1 fila, no recorrer los 3 frames y truncar despues."""
    fake = FakeSapObject().SapModel
    original_table = fake.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Local Axes Assignments 1 - Typical":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake.DatabaseTables.GetTableForDisplayArray = no_table

    call_count = {"n": 0}
    original_get_axes = fake.FrameObj.GetLocalAxes

    def counting(name, *args):
        call_count["n"] += 1
        return original_get_axes(name, *args)

    fake.FrameObj.GetLocalAxes = counting

    reader = SapModelReader(fake)
    result = reader.frame_local_axes(limit=1)

    assert len(result["rows"]) == 1
    assert call_count["n"] == 1, "debe cortar el loop apenas junta 1 fila, no leer los 3 frames"


def test_frame_local_axes_action_registered_in_table_for_action() -> None:
    """El nuevo metodo debe estar accesible via el dict de acciones, igual
    que frame_assigned_section/frame_releases."""
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.table_for_action("frame_local_axes", {"frame": "F2"})

    assert result["rows"][0][0] == "F2"
    assert result["rows"][0][1] == 15.0


def test_frame_local_axes_measures_com_call_reduction() -> None:
    """Documentacion del ahorro de llamadas COM (mismo patron que las demas
    tablas Fase 17): con batch, 0 llamadas GetLocalAxes para F1/F2 (F3
    siempre cuesta 1 llamada porque el batch nunca lo puede cubrir)."""
    fake_batch = FakeSapObject().SapModel
    calls_batch = {"n": 0}
    original_batch = fake_batch.FrameObj.GetLocalAxes

    def counting_batch(name, *args):
        calls_batch["n"] += 1
        return original_batch(name, *args)

    fake_batch.FrameObj.GetLocalAxes = counting_batch
    SapModelReader(fake_batch).frame_local_axes()

    fake_loop = FakeSapObject().SapModel
    original_table = fake_loop.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Local Axes Assignments 1 - Typical":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake_loop.DatabaseTables.GetTableForDisplayArray = no_table
    calls_loop = {"n": 0}
    original_loop = fake_loop.FrameObj.GetLocalAxes

    def counting_loop(name, *args):
        calls_loop["n"] += 1
        return original_loop(name, *args)

    fake_loop.FrameObj.GetLocalAxes = counting_loop
    SapModelReader(fake_loop).frame_local_axes()

    assert calls_batch["n"] == 1, "solo F3 (advanced, ausente del batch) debe generar llamada"
    assert calls_loop["n"] == 3
    assert calls_loop["n"] - calls_batch["n"] == 2
