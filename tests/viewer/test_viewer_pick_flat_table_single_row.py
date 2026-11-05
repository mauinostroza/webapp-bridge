"""
Fase 22: fix del bug preexistente en _pick_flat_table_data — una tabla
DatabaseTables real con exactamente 1 registro no se podía distinguir de
su propio array de nombres de columna por longitud (ambos miden `width`),
así que la fila se descartaba en silencio. Documentado desde la Fase 17
(ver comentario en sap2000/fake_model.py junto a "Frame Loads -
Distributed") y nunca corregido hasta ahora; todos los fixtures de test
existentes usaban >=2 filas a propósito para evitarlo.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.reader_utils import (
    _get_database_table_for_display,
    _parse_database_table_result,
)


def test_parse_database_table_result_keeps_a_genuine_single_row() -> None:
    """Antes del fix: la fila se perdía porque len(data) == len(fields)
    fallaba la comparación estricta `>`."""
    fields = ["Frame", "LoadPat", "Type"]
    data = ["F1", "Dead", "Force"]
    # Forma real de GetTableForDisplayArray: (fields, data, ret_code) una vez
    # que ya se extrajeron los arrays de la tupla completa (key, fields,
    # num_fields, num_records, data, ret_code) — _parse_database_table_result
    # solo mira los arrays de tipo list/tuple dentro del resultado.
    result = ("TableKey", fields, len(fields), 1, data, 0)

    parsed = _parse_database_table_result(result)

    assert parsed is not None
    columns, rows = parsed
    assert columns == fields
    assert rows == [["F1", "Dead", "Force"]]


def test_parse_database_table_result_still_picks_the_longer_multi_row_data() -> None:
    """Caso normal (>=2 filas) sigue funcionando igual que antes del fix."""
    fields = ["Frame", "LoadPat", "Type"]
    data = ["F1", "Dead", "Force", "F2", "Live", "Force"]
    result = ("TableKey", fields, len(fields), 2, data, 0)

    parsed = _parse_database_table_result(result)

    assert parsed is not None
    columns, rows = parsed
    assert columns == fields
    assert rows == [["F1", "Dead", "Force"], ["F2", "Live", "Force"]]


def test_get_database_table_for_display_parses_single_row_from_real_api_shape() -> None:
    """Integración con la firma real de GetTableForDisplayArray (varias
    variantes de argumentos, la primera que funcione gana)."""

    class _FakeDb:
        def GetTableForDisplayArray(self, key, *args):
            fields = ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"]
            data = ["J1", "Yes", "Yes", "Yes", "No", "No", "No"]
            return (key, fields, len(fields), 1, data, 0)

    parsed = _get_database_table_for_display(_FakeDb(), "Joint Restraint Assignments")

    assert parsed is not None
    columns, rows = parsed
    assert columns == ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"]
    assert rows == [["J1", "Yes", "Yes", "Yes", "No", "No", "No"]]
