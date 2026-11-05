# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_geometry import _joint_coordinates, _frame_geometry
from bridge.viewer.sap2000.reader_sections import frame_section_properties, area_shell_thickness


# ── Coordenadas de joints ──────────────────────────────────────────────


def test_joint_coordinates_convert_millimeters_to_meters() -> None:
    """FakeSapObject usa por defecto J2=(6.0, 0.0, 0.0) asumiendo metros. Si
    las present units activas son mm, ese mismo 6.0 crudo representa 6mm, y
    debe normalizarse a 0.006 m."""
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C

    rows, errors = _joint_coordinates(model)

    by_name = {name: point for name, point, _groups in rows}
    assert by_name["J2"] == (0.006, 0.0, 0.0)
    assert by_name["J3"] == (0.006, 0.004, 0.003)
    assert not errors


def test_joint_coordinates_leave_meters_unconverted() -> None:
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 12  # tonf_m_C

    rows, _errors = _joint_coordinates(model)

    by_name = {name: point for name, point, _groups in rows}
    assert by_name["J2"] == (6.0, 0.0, 0.0)
    assert by_name["J3"] == (6.0, 4.0, 3.0)


def test_frame_geometry_inherits_normalized_coordinates() -> None:
    """frame_geometry consume el mismo dict de coordenadas que
    _joint_coordinates (via _joint_coords_and_groups), asi que la conversion
    aguas arriba tambien debe reflejarse aqui sin duplicarse."""
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C

    frames, _errors = _frame_geometry(model)

    f1 = next(f for f in frames if f["name"] == "F1")
    assert f1["xi"] == 0.0
    assert f1["xj"] == 0.006  # J2.X = 6.0 mm -> 0.006 m


def test_frame_length_reports_meters_and_true_unit_label() -> None:
    """frame_length calcula la longitud a partir de coordenadas ya
    normalizadas a metros; la columna 'Unit' y metadata['unit'] deben decir
    'm', nunca el token crudo de las present units (mm en este caso)."""
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C
    reader = SapModelReader(model)

    table = reader.frame_length(frame_name="F1")

    # F1 va de J1(0,0,0) a J2(6mm,0,0) -> 0.006 m normalizado -> longitud 0.006 m
    assert table["rows"][0][1] == 0.006
    assert table["rows"][0][2] == "m"
    assert table["metadata"]["unit"] == "m"
    assert "0.006" in table["summary_message"]
    assert table["summary_message"].endswith("m de longitud.")


# ── Dimensiones de sección ─────────────────────────────────────────────


def test_frame_section_dimensions_convert_millimeters_to_meters() -> None:
    """B30x60 esta definida en el fake como width=0.30, depth=0.60 (metros).
    Con present units en mm, esos mismos numeros crudos representan 0.30mm y
    0.60mm, asi que deben normalizarse multiplicando por 0.001."""
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C

    table = frame_section_properties(model)

    sec = table["sections"]["B30x60"]
    assert sec["width"] == 0.30 * 0.001
    assert sec["depth"] == 0.60 * 0.001


def test_frame_section_dimensions_leave_meters_unconverted() -> None:
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 12  # tonf_m_C

    table = frame_section_properties(model)

    sec = table["sections"]["B30x60"]
    assert sec["width"] == 0.30
    assert sec["depth"] == 0.60


def test_frame_section_area_scales_with_factor_squared() -> None:
    """El area de una seccion 'general' (via GetSectProps) es una magnitud
    de superficie: debe escalar con el factor de longitud al cuadrado, no
    linealmente."""
    from bridge.viewer.sap2000.reader_sections import _convert_section_dimensions

    sec = {"type": "general", "area": 4.0}
    _convert_section_dimensions(sec, length_factor=0.001)

    assert sec["area"] == 4.0 * (0.001 ** 2)


def test_frame_section_dimensions_convert_isection_fields() -> None:
    """Calls GetISection directly (bypassing the GetRectangle/GetISection/...
    probing order in _read_one_frame_section) to isolate the "i" parser and
    check its fields get converted like every other section field. Since the
    fake's getters now raise for a name that isn't their shape (matching
    real SAP2000 — see fake_model.py), W18X35 would resolve to "i" via that
    probing order too; this test just doesn't rely on it."""
    from bridge.viewer.sap2000.reader_sections import _parse_isection_result, _convert_section_dimensions

    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C
    res = model.PropFrame.GetISection("W18X35")
    sec = _parse_isection_result(res)
    assert sec is not None

    _convert_section_dimensions(sec, length_factor=0.001)

    assert sec["depth"] == 0.46 * 0.001
    assert sec["flange_width"] == 0.19 * 0.001
    assert sec["tf"] == 0.014 * 0.001
    assert sec["tw"] == 0.009 * 0.001


# ── Espesor de shell: no doble-convertir el default ─────────────────────


def test_shell_default_thickness_not_converted_twice_when_unreadable() -> None:
    """Cuando GetShell no devuelve un espesor legible, area_shell_thickness
    usa el default 0.15 (ya en metros) sin volver a multiplicarlo por el
    factor de unidades — de lo contrario, en un modelo en mm el default
    saldria como 0.00015."""
    model = FakeSapObject().SapModel
    model.GetPresentUnits = lambda: 5  # kN_mm_C

    def _unreadable(name: str, *args):
        return (name, "", -1.0, 0, 0, 0.0, 0)  # sin valor positivo -> t queda None

    model.PropArea.GetShell = _unreadable

    table = area_shell_thickness(model)

    assert table["thicknesses"]["LOSA_15CM"] == 0.15
