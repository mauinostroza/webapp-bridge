"""
Tests para la extracción y propagación de unidades de SAP2000 en el metadata
de las tablas de resultados.

Cubre:
- Extracción correcta de unidades desde varios sistemas de unidades
- Formato no reconocido → vacío, no adivinado
- Que un GetPresentUnits que falla no rompe la lectura de resultados
- Que las unidades aparecen en el metadata
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from unittest.mock import MagicMock
from bridge.viewer.sap2000.units import extract_result_units
from bridge.viewer.sap2000.reader_results import _sap_results_table, _sap_all_selected_results_table, _database_table
from bridge.viewer.sap2000.reader_utils import _is_diagnostic_table


class TestExtractResultUnits:
    """Tests para la función extract_result_units."""

    def test_extract_units_tonf_m_c(self) -> None:
        """Caso típico en español: toneladas-fuerza, metros."""
        result = extract_result_units("tonf_m_C")
        assert result["force"] == "tonf"
        assert result["length"] == "m"
        assert result["label"] == "tonf_m_C"

    def test_extract_units_kn_mm_c(self) -> None:
        """Caso típico: kilonewtons, milímetros."""
        result = extract_result_units("kN_mm_C")
        assert result["force"] == "kN"
        assert result["length"] == "mm"
        assert result["label"] == "kN_mm_C"

    def test_extract_units_from_numeric_code(self) -> None:
        """Convierte código numérico a nombre y extrae unidades."""
        # Código 12 = "tonf_m_C"
        result = extract_result_units(12)
        assert result["force"] == "tonf"
        assert result["length"] == "m"

    def test_extract_units_kgf_cm_c(self) -> None:
        """Caso con centímetros: kilogramo-fuerza, centímetros."""
        result = extract_result_units("kgf_cm_C")
        assert result["force"] == "kgf"
        assert result["length"] == "cm"

    def test_extract_units_kip_ft_f(self) -> None:
        """Caso inglés: kips (miles de libras), pies."""
        result = extract_result_units("kip_ft_F")
        assert result["force"] == "kip"
        assert result["length"] == "ft"

    def test_extract_units_lb_in_f(self) -> None:
        """Caso inglés pequeño: libras, pulgadas."""
        result = extract_result_units("lb_in_F")
        assert result["force"] == "lb"
        assert result["length"] == "in"

    def test_extract_units_n_m_c(self) -> None:
        """Caso SI puro: newtons, metros."""
        result = extract_result_units("N_m_C")
        assert result["force"] == "N"
        assert result["length"] == "m"

    def test_extract_units_unknown_code_returns_empty(self) -> None:
        """Código numérico desconocido devuelve vacío."""
        result = extract_result_units(999)
        assert result == {}

    def test_extract_units_invalid_format_returns_empty(self) -> None:
        """Formato no reconocible devuelve vacío, no adivina."""
        result = extract_result_units("invalid_format")
        assert result == {}

    def test_extract_units_missing_temperature_returns_empty(self) -> None:
        """Formato incompleto (faltan temperaturas) devuelve vacío."""
        result = extract_result_units("tonf_m")
        assert result == {}

    def test_extract_units_invalid_force_token_returns_empty(self) -> None:
        """Token de fuerza no reconocido devuelve vacío."""
        result = extract_result_units("megaforce_m_C")
        assert result == {}

    def test_extract_units_invalid_length_token_returns_empty(self) -> None:
        """Token de longitud no reconocido devuelve vacío."""
        result = extract_result_units("tonf_parsec_C")
        assert result == {}

    def test_extract_units_none_input_returns_empty(self) -> None:
        """None como entrada devuelve vacío."""
        result = extract_result_units(None)
        assert result == {}

    def test_extract_units_always_dict_never_none(self) -> None:
        """Siempre devuelve un dict (vacío si no reconocible), nunca None."""
        result = extract_result_units("garbage")
        assert isinstance(result, dict)
        assert result == {}


class TestUnitsInMetadata:
    """Tests para verificar que las unidades se propagan al metadata."""

    def test_sap_results_table_includes_units_from_setup(self) -> None:
        """_sap_results_table incluye unidades si están en el setup."""
        arrays = [
            ["F1", "F2"],
            [0.0, 1.0],
            [10.0, 20.0],
        ]

        def func(item, item_type):
            return (2, *arrays, 0)

        setup = {
            "selected_cases": 1,
            "result_units": {"force": "tonf", "length": "m", "label": "tonf_m_C"},
        }
        table = _sap_results_table(
            "Test Table", func, "ALL", ["Frame", "Station", "Force"], setup=setup
        )

        assert not _is_diagnostic_table(table)
        assert "result_units" in table["metadata"]
        assert table["metadata"]["result_units"]["force"] == "tonf"
        assert table["metadata"]["result_units"]["length"] == "m"

    def test_sap_results_table_works_without_units_in_setup(self) -> None:
        """_sap_results_table funciona normalmente si no hay unidades en setup."""
        arrays = [
            ["F1", "F2"],
            [0.0, 1.0],
            [10.0, 20.0],
        ]

        def func(item, item_type):
            return (2, *arrays, 0)

        setup = {"selected_cases": 1}  # Sin result_units
        table = _sap_results_table(
            "Test Table", func, "ALL", ["Frame", "Station", "Force"], setup=setup
        )

        assert not _is_diagnostic_table(table)
        # result_units no está presente si no se pasó en setup
        assert "result_units" not in table["metadata"]

    def test_all_selected_results_table_includes_units_from_setup(self) -> None:
        """_sap_all_selected_results_table incluye unidades si están en setup."""
        arrays = [
            ["SC1", "SC2"],
            ["DEAD", "DEAD"],
            [1.0, 2.0],
        ]

        def func():
            return (2, *arrays, 0)

        setup = {
            "selected_cases": 1,
            "result_units": {"force": "kN", "length": "mm", "label": "kN_mm_C"},
        }
        table = _sap_all_selected_results_table(
            "Section Cuts", func, ["SectionCut", "OutputCase", "F1"], setup=setup
        )

        assert not _is_diagnostic_table(table)
        assert "result_units" in table["metadata"]
        assert table["metadata"]["result_units"]["force"] == "kN"
        assert table["metadata"]["result_units"]["length"] == "mm"


class TestUnitsIntegration:
    """Tests de integración: verificar que una tabla construida con setup que
    incluye result_units las mantiene después de _refresh_result_metadata."""

    def test_units_survive_refresh_result_metadata(self) -> None:
        """Las unidades en metadata sobreviven a _refresh_result_metadata."""
        from bridge.viewer.sap2000.reader_utils import _refresh_result_metadata

        arrays = [
            ["F1", "F2"],
            [10.0, 20.0],
        ]

        def func(item, item_type):
            return (2, *arrays, 0)

        setup = {
            "selected_cases": 1,
            "result_units": {"force": "tonf", "length": "m", "label": "tonf_m_C"},
        }
        table = _sap_results_table(
            "Test Table", func, "ALL", ["Frame", "Force"], setup=setup
        )

        # Simula lo que hace _finalize_result_table
        _refresh_result_metadata(table)

        # Las unidades deben sobrevivir al refresh
        assert "result_units" in table["metadata"]
        assert table["metadata"]["result_units"]["force"] == "tonf"


class TestModelInfoUnits:
    """Tests para verificar que model_info muestra las unidades de forma legible."""

    def test_model_info_formats_present_units_with_valid_code(self) -> None:
        """model_info muestra unidades en formato legible (p.ej. 'tonf, m')."""
        from bridge.viewer.sap2000.reader_metadata import model_info
        from bridge.viewer.sap2000.fake_model import FakeSapObject

        fake = FakeSapObject().SapModel
        fake.GetModelFilename = lambda: "test_model.sdb"
        fake.GetDatabaseUnits = lambda: "tonf_m_C"
        fake.GetPresentUnits = lambda: 12  # Código numérico para tonf_m_C
        fake.GetModelIsLocked = lambda: False

        table = model_info(fake)

        # Busca la fila de "Present units"
        present_units_row = None
        for row in table["rows"]:
            if row[0] == "Present units":
                present_units_row = row
                break

        assert present_units_row is not None, "No se encontró la fila de 'Present units'"
        # El valor debe ser legible como "tonf, m", no "12" o "tonf_m_C"
        assert present_units_row[1] == "tonf, m"

    def test_model_info_formats_present_units_with_string_code(self) -> None:
        """model_info maneja cadenas de unidades (p.ej. 'kN_mm_C') correctamente."""
        from bridge.viewer.sap2000.reader_metadata import model_info
        from bridge.viewer.sap2000.fake_model import FakeSapObject

        fake = FakeSapObject().SapModel
        fake.GetModelFilename = lambda: "test_model.sdb"
        fake.GetDatabaseUnits = lambda: "kN_mm_C"
        fake.GetPresentUnits = lambda: "kN_mm_C"  # Cadena de unidades
        fake.GetModelIsLocked = lambda: False

        table = model_info(fake)

        # Busca la fila de "Present units"
        present_units_row = None
        for row in table["rows"]:
            if row[0] == "Present units":
                present_units_row = row
                break

        assert present_units_row is not None
        # El valor debe ser "kN, mm"
        assert present_units_row[1] == "kN, mm"

    def test_model_info_handles_unrecognizable_units_format(self) -> None:
        """Si el formato no es reconocible, model_info muestra el valor original."""
        from bridge.viewer.sap2000.reader_metadata import model_info
        from bridge.viewer.sap2000.fake_model import FakeSapObject

        fake = FakeSapObject().SapModel
        fake.GetModelFilename = lambda: "test_model.sdb"
        fake.GetDatabaseUnits = lambda: "unknown_unit"
        fake.GetPresentUnits = lambda: "invalid_format"
        fake.GetModelIsLocked = lambda: False

        table = model_info(fake)

        # Busca la fila de "Present units"
        present_units_row = None
        for row in table["rows"]:
            if row[0] == "Present units":
                present_units_row = row
                break

        assert present_units_row is not None
        # Si el formato no es reconocible, devuelve el valor original
        assert present_units_row[1] == "invalid_format"

    def test_model_info_handles_get_present_units_failure(self) -> None:
        """Si GetPresentUnits falla, model_info muestra un mensaje de error."""
        from bridge.viewer.sap2000.reader_metadata import model_info
        from bridge.viewer.sap2000.fake_model import FakeSapObject

        fake = FakeSapObject().SapModel
        fake.GetModelFilename = lambda: "test_model.sdb"
        fake.GetDatabaseUnits = lambda: "tonf_m_C"

        def raise_error():
            raise RuntimeError("COM error")

        fake.GetPresentUnits = raise_error
        fake.GetModelIsLocked = lambda: False

        table = model_info(fake)

        # Busca la fila de "Present units"
        present_units_row = None
        for row in table["rows"]:
            if row[0] == "Present units":
                present_units_row = row
                break

        assert present_units_row is not None
        # Debe mostrar un mensaje de error
        assert "No disponible" in present_units_row[1]
