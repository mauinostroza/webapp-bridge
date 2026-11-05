# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.reader_geometry import (
    _frame_section,
    _frame_assigned_section_from_database,
)
from bridge.viewer.sap2000.model_reader import SapModelReader


def test_frame_assigned_section_prefer_database_table_over_per_frame_com_calls() -> None:
    """Fase 17: GetTableForDisplayArray is now the primary path, not the loop."""
    fake = FakeSapObject().SapModel
    call_count = {"n": 0}
    original = fake.FrameObj.GetSection

    def counting_get_section(name, *args):
        call_count["n"] += 1
        return original(name, *args)

    fake.FrameObj.GetSection = counting_get_section

    reader = SapModelReader(fake)
    result = reader.frame_assigned_section()

    assert call_count["n"] == 0, "no debe llamar GetSection por frame si la tabla batch trae todo"
    assert result["name"] == "frame_assigned_section"
    rows = result["rows"]
    assert len(rows) == 3
    assert {(r[0], r[1]) for r in rows} == {
        ("F1", "W21X44"),
        ("F2", "B30x60"),
        ("F3", "W18X35"),
    }


def test_frame_assigned_section_batch_falls_back_when_table_missing_columns() -> None:
    """Si faltan columnas en la tabla, cae al loop por objeto."""
    fake = FakeSapObject().SapModel
    # Sabotear la tabla para que no tenga la columna "Section"
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def broken_get_table(key: str, *args):
        result = original_get_table(key, *args)
        if key == "Frame Section Assignments":
            # Devolver tabla sin la columna "Section"
            parsed_key, fields, num_fields, num_rows, data, ret_code = result
            # Quitar la columna "Section" (índice 1)
            broken_fields = [f for f in fields if f != "Section"]
            return (parsed_key, broken_fields, len(broken_fields), num_rows, [], ret_code)
        return result

    fake.DatabaseTables.GetTableForDisplayArray = broken_get_table

    call_count = {"n": 0}
    original_get_section = fake.FrameObj.GetSection

    def counting_get_section(name, *args):
        call_count["n"] += 1
        return original_get_section(name, *args)

    fake.FrameObj.GetSection = counting_get_section

    reader = SapModelReader(fake)
    result = reader.frame_assigned_section()

    # Sin tabla, debe caer al loop
    assert call_count["n"] == 3, "debe llamar GetSection por cada frame si tabla no es válida"
    rows = result["rows"]
    assert len(rows) == 3
    assert {(r[0], r[1]) for r in rows} == {
        ("F1", "W21X44"),
        ("F2", "B30x60"),
        ("F3", "W18X35"),
    }


def test_frame_assigned_section_respects_frame_name_filter() -> None:
    """El filtro frame_name sigue funcionando igual que antes."""
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_assigned_section(frame_name="F2")

    rows = result["rows"]
    assert len(rows) == 1
    assert rows[0][0] == "F2"
    assert rows[0][1] == "B30x60"
    assert "No encontre un frame llamado" not in result.get("summary_message", "")


def test_frame_assigned_section_respects_limit() -> None:
    """El limit sigue funcionando igual que antes."""
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_assigned_section(limit=2)

    rows = result["rows"]
    assert len(rows) == 2


def test_frame_assigned_section_batch_path_matches_per_object_loop_result() -> None:
    """Equivalencia: tabla batch y loop por-objeto devuelven los mismos datos."""
    fake = FakeSapObject().SapModel

    # Lectura con batch (la ruta actual)
    reader = SapModelReader(fake)
    result_batch = reader.frame_assigned_section()
    rows_batch = {(r[0], r[1], r[2]) for r in result_batch["rows"]}

    # Lectura por loop (sabotear tabla para forzar fallback)
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Section Assignments":
            # No devolver tabla para ese caso
            return (key, [], 0, 0, [], 1)
        return original_get_table(key, *args)

    fake.DatabaseTables.GetTableForDisplayArray = no_table
    result_loop = SapModelReader(fake).frame_assigned_section()
    rows_loop = {(r[0], r[1], r[2]) for r in result_loop["rows"]}

    assert rows_batch == rows_loop, "batch y loop deben devolver los mismos datos"


def test_frame_assigned_section_from_database_returns_none_if_table_invalid() -> None:
    """Si no hay tabla válida, devuelve None."""
    fake = FakeSapObject().SapModel
    result = _frame_assigned_section_from_database(fake, [])
    # Con tabla válida en fake_model, debe devolver un dict, no None
    assert isinstance(result, dict)
    assert result == {
        "F1": ("W21X44", ""),
        "F2": ("B30x60", ""),
        "F3": ("W18X35", ""),
    }


def test_frame_assigned_section_summary_message() -> None:
    """Mensaje resumen cuando se busca por frame_name específico."""
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_assigned_section(frame_name="F1")

    summary = result.get("summary_message", "")
    assert "F1" in summary
    assert "W21X44" in summary
    assert "asignada la seccion" in summary.lower()


def test_frame_assigned_section_not_found_message() -> None:
    """Mensaje cuando no se encuentra el frame buscado."""
    fake = FakeSapObject().SapModel
    reader = SapModelReader(fake)
    result = reader.frame_assigned_section(frame_name="NONEXISTENT")

    summary = result.get("summary_message", "")
    assert "No encontre" in summary
    assert "NONEXISTENT" in summary


def test_frame_assigned_section_measures_com_call_reduction() -> None:
    """Documentación del ahorro de llamadas COM.

    Con la lectura batch: 0 llamadas a FrameObj.GetSection
    Sin batch (fallback): N llamadas a FrameObj.GetSection (una por frame)

    En un modelo de 5000 barras: 5000 llamadas COM ahorradas.
    """
    # Con batch (tabla disponible)
    fake_batch = FakeSapObject().SapModel
    calls_batch = {"n": 0}
    original = fake_batch.FrameObj.GetSection

    def counting(name, *args):
        calls_batch["n"] += 1
        return original(name, *args)

    fake_batch.FrameObj.GetSection = counting
    SapModelReader(fake_batch).frame_assigned_section()

    # Sin batch (tabla no disponible)
    fake_loop = FakeSapObject().SapModel
    original_table = fake_loop.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Section Assignments":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake_loop.DatabaseTables.GetTableForDisplayArray = no_table
    calls_loop = {"n": 0}
    original_loop = fake_loop.FrameObj.GetSection

    def counting_loop(name, *args):
        calls_loop["n"] += 1
        return original_loop(name, *args)

    fake_loop.FrameObj.GetSection = counting_loop
    SapModelReader(fake_loop).frame_assigned_section()

    # Verificar ahorro
    assert calls_batch["n"] == 0, "Con batch no debe haber llamadas"
    assert calls_loop["n"] == 3, "Sin batch debe llamar una vez por frame"
    assert calls_loop["n"] - calls_batch["n"] == 3, "Se ahorran 3 llamadas COM"
