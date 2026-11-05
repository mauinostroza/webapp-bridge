"""
Tests para la lectura batch de propiedades de sección de frame
(_frame_section_properties_from_database, reader_sections.py) y de
conectividad de áreas (_area_points_from_database, reader_geometry.py),
siguiendo el mismo patrón de tests/test_frame_section_batch.py (batch vs.
loop por-objeto, contrato todo-o-nada, conteo de llamadas COM).

También cubre la Tarea 1 del plan: el fake honesto en fake_model.py
(_FrameProp.GetRectangle/GetISection/GetTube/GetPipe ahora lanzan cuando el
nombre no corresponde a su tipo, en vez de devolver siempre datos
rectangulares) — sin esto, el camino de sondeo en `_read_one_frame_section`
nunca se ejercitaba de verdad.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_sections import (
    _read_one_frame_section,
    _frame_section_properties_from_database,
    frame_section_properties,
)
from bridge.viewer.sap2000.reader_geometry import _area_points_from_database, _area_points


# ── Tarea 1: el fake ya no miente sobre la forma de la sección ──────────


def test_fake_getters_raise_when_name_does_not_match_their_shape() -> None:
    """GetRectangle debe fallar para una sección I, y viceversa — como hace
    SAP2000 real. Antes de este fix, GetRectangle nunca lanzaba y toda
    sección salía "rectangular"."""
    fake = FakeSapObject().SapModel
    frame_prop = fake.PropFrame

    # W18X35 es una sección "i" en el fake -> GetRectangle debe fallar.
    try:
        frame_prop.GetRectangle("W18X35")
        raised = False
    except Exception:
        raised = True
    assert raised, "GetRectangle no debe aceptar una seccion I"

    # ... pero GetISection si debe funcionar para ella.
    res = frame_prop.GetISection("W18X35")
    assert res[0] == 0.46  # depth

    # B30x60 es rectangular -> GetISection debe fallar.
    try:
        frame_prop.GetISection("B30x60")
        raised = False
    except Exception:
        raised = True
    assert raised, "GetISection no debe aceptar una seccion rectangular"


def test_read_one_frame_section_probes_to_the_right_shape() -> None:
    """Con el fake honesto, _read_one_frame_section debe resolver W18X35 y
    W21X44 como 'i' (via GetISection, tras fallar GetRectangle) y B30x60
    como 'rectangular' (via GetRectangle) — no todo como 'rectangular'."""
    fake = FakeSapObject().SapModel
    errors: list[str] = []

    sec_i = _read_one_frame_section(fake.PropFrame, "W18X35", errors)
    assert sec_i["type"] == "i"
    assert sec_i["depth"] == 0.46

    sec_i2 = _read_one_frame_section(fake.PropFrame, "W21X44", errors)
    assert sec_i2["type"] == "i"

    sec_rect = _read_one_frame_section(fake.PropFrame, "B30x60", errors)
    assert sec_rect["type"] == "rectangular"
    assert sec_rect["width"] == 0.30

    # Ninguna de las tres es un fallo de sondeo (todas resolvieron por un
    # getter tipado), asi que `errors` no debe registrar nada para ellas.
    assert errors == []


def test_read_one_frame_section_records_probing_failure_for_unknown_section() -> None:
    """Una seccion que no existe en ningun getter tipado debe caer al
    fallback general Y dejar rastro en `errors` (antes, el parametro
    `errors` se recibia y nunca se usaba)."""
    fake = FakeSapObject().SapModel
    errors: list[str] = []

    sec = _read_one_frame_section(fake.PropFrame, "SECCION_INEXISTENTE", errors)

    assert sec["type"] == "general"
    assert len(errors) == 1
    assert "SECCION_INEXISTENTE" in errors[0]


# ── Tarea 2: lectura batch de propiedades de sección ─────────────────────


def test_frame_section_properties_prefer_database_table_over_per_section_com_calls() -> None:
    """Con la tabla 'Frame Section Properties 01 - General' disponible, no
    debe llamarse ningun getter tipado ni GetMaterial por seccion."""
    fake = FakeSapObject().SapModel
    call_counts = {"n": 0}

    def _count(original):
        def wrapper(name, *args):
            call_counts["n"] += 1
            return original(name, *args)
        return wrapper

    fake.PropFrame.GetRectangle = _count(fake.PropFrame.GetRectangle)
    fake.PropFrame.GetISection = _count(fake.PropFrame.GetISection)
    fake.PropFrame.GetTube = _count(fake.PropFrame.GetTube)
    fake.PropFrame.GetPipe = _count(fake.PropFrame.GetPipe)
    fake.PropFrame.GetSectProps = _count(fake.PropFrame.GetSectProps)
    fake.PropFrame.GetMaterial = _count(fake.PropFrame.GetMaterial)

    reader = SapModelReader(fake)
    table = reader.frame_section_properties()

    assert call_counts["n"] == 0, "no debe llamar getters por seccion si la tabla batch trae todo"
    sections = table["sections"]
    assert sections["W18X35"]["type"] == "i"
    assert sections["W18X35"]["material"] == "A992Fy50"
    assert sections["W21X44"]["type"] == "i"
    assert sections["B30x60"]["type"] == "rectangular"
    assert sections["B30x60"]["material"] == "CONC_280"


def test_frame_section_properties_batch_falls_back_when_table_missing_columns() -> None:
    """Si falta la columna Material (o cualquier columna necesaria), la
    funcion batch debe devolver None y el caller cae al loop por-seccion,
    intacto (todo-o-nada, ninguna fila del batch se usa aunque el resto de
    columnas estuviera bien)."""
    fake = FakeSapObject().SapModel
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def broken_get_table(key: str, *args):
        result = original_get_table(key, *args)
        if key == "Frame Section Properties 01 - General":
            parsed_key, fields, num_fields, num_rows, data, ret_code = result
            broken_fields = [f for f in fields if f != "Material"]
            return (parsed_key, broken_fields, len(broken_fields), num_rows, [], ret_code)
        return result

    fake.DatabaseTables.GetTableForDisplayArray = broken_get_table

    errors: list[str] = []
    result = _frame_section_properties_from_database(fake.PropFrame, fake, errors)
    assert result is None

    call_counts = {"n": 0}
    original_material = fake.PropFrame.GetMaterial

    def counting_material(name, *args):
        call_counts["n"] += 1
        return original_material(name, *args)

    fake.PropFrame.GetMaterial = counting_material

    reader = SapModelReader(fake)
    table = reader.frame_section_properties()

    assert call_counts["n"] == 3, "sin tabla batch, debe pedir material por cada una de las 3 secciones"
    sections = table["sections"]
    assert sections["W18X35"]["type"] == "i"
    assert sections["B30x60"]["type"] == "rectangular"


def test_frame_section_properties_batch_path_matches_per_object_loop_result() -> None:
    """Equivalencia: batch y loop por-objeto deben devolver la misma forma,
    dimensiones y material para cada sección."""
    fake_batch = FakeSapObject().SapModel
    table_batch = SapModelReader(fake_batch).frame_section_properties()

    fake_loop = FakeSapObject().SapModel
    original_get_table = fake_loop.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Section Properties 01 - General":
            return (key, [], 0, 0, [], 1)
        return original_get_table(key, *args)

    fake_loop.DatabaseTables.GetTableForDisplayArray = no_table
    table_loop = SapModelReader(fake_loop).frame_section_properties()

    for name in ("W18X35", "W21X44", "B30x60"):
        batch_sec = table_batch["sections"][name]
        loop_sec = table_loop["sections"][name]
        assert batch_sec["type"] == loop_sec["type"]
        assert batch_sec.get("material") == loop_sec.get("material")


def test_frame_section_properties_unit_conversion_still_applied_via_batch() -> None:
    """El camino batch tambien debe pasar por _convert_section_dimensions —
    no hay que saltarse la conversion de unidades solo porque los datos
    vinieron de la tabla en vez del loop por-objeto."""
    fake = FakeSapObject().SapModel
    fake.GetPresentUnits = lambda: 5  # kN_mm_C

    table = frame_section_properties(fake)

    sec = table["sections"]["B30x60"]
    assert sec["width"] == 0.30 * 0.001
    assert sec["depth"] == 0.60 * 0.001


def test_frame_section_properties_measures_com_call_reduction() -> None:
    """Documentacion del ahorro de llamadas COM: sin batch, cada una de las
    3 secciones necesita 1 llamada a GetMaterial ademas del sondeo tipado
    (que aqui resuelve en el primer o segundo intento gracias al fake
    honesto); con batch, cero."""
    fake_batch = FakeSapObject().SapModel
    calls_batch = {"n": 0}
    original_material_batch = fake_batch.PropFrame.GetMaterial

    def counting_batch(name, *args):
        calls_batch["n"] += 1
        return original_material_batch(name, *args)

    fake_batch.PropFrame.GetMaterial = counting_batch
    SapModelReader(fake_batch).frame_section_properties()

    fake_loop = FakeSapObject().SapModel
    original_table = fake_loop.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Frame Section Properties 01 - General":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake_loop.DatabaseTables.GetTableForDisplayArray = no_table
    calls_loop = {"n": 0}
    original_material_loop = fake_loop.PropFrame.GetMaterial

    def counting_loop(name, *args):
        calls_loop["n"] += 1
        return original_material_loop(name, *args)

    fake_loop.PropFrame.GetMaterial = counting_loop
    SapModelReader(fake_loop).frame_section_properties()

    assert calls_batch["n"] == 0
    assert calls_loop["n"] == 3
    assert calls_loop["n"] - calls_batch["n"] == 3


# ── Tarea 3: lectura batch de conectividad de áreas ───────────────────────


def test_area_points_from_database_groups_and_sorts_by_point_index() -> None:
    """La fixture 'Connectivity - Area' del fake trae las filas de A1 fuera
    de orden (J1, J3, J2, J4) a propósito — el resultado debe quedar
    ordenado por PointIndex (J1, J2, J3, J4), no en el orden de las filas."""
    fake = FakeSapObject().SapModel
    errors: list[str] = []

    result = _area_points_from_database(fake, errors)

    assert result is not None
    assert result["A1"] == ["J1", "J2", "J3", "J4"]


def test_area_points_from_database_returns_none_without_point_index_column() -> None:
    """Sin una columna de indice de punto reconocible no se puede garantizar
    el orden del poligono — debe devolver None (no un orden adivinado), a
    diferencia del resto de *_from_database donde basta con encontrar las
    columnas de valor."""
    fake = FakeSapObject().SapModel
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def broken_get_table(key: str, *args):
        result = original_get_table(key, *args)
        if key == "Connectivity - Area":
            parsed_key, fields, num_fields, num_rows, data, ret_code = result
            broken_fields = [f for f in fields if f != "PointIndex"]
            return (parsed_key, broken_fields, len(broken_fields), num_rows, [], ret_code)
        return result

    fake.DatabaseTables.GetTableForDisplayArray = broken_get_table

    errors: list[str] = []
    result = _area_points_from_database(fake, errors)

    assert result is None


def test_area_geometry_prefers_batch_over_per_area_com_calls() -> None:
    fake = FakeSapObject().SapModel
    call_counts = {"n": 0}
    original = fake.AreaObj.GetPoints

    def counting(name, *args):
        call_counts["n"] += 1
        return original(name, *args)

    fake.AreaObj.GetPoints = counting

    reader = SapModelReader(fake)
    table = reader.area_geometry()

    assert call_counts["n"] == 0, "no debe llamar AreaObj.GetPoints si la tabla batch trae todo"
    row = next(r for r in table["rows"] if r[0] == "A1")
    assert row[2] == "J1; J2; J3; J4"


def test_area_geometry_falls_back_to_per_area_loop_and_keeps_correct_order() -> None:
    """Sin columna de indice de punto en la tabla, area_geometry debe caer
    al loop por-objeto (AreaObj.GetPoints) y seguir devolviendo el orden
    correcto del poligono (ya que ese orden viene garantizado por
    AreaObj.GetPoints, no por una fila de tabla sin ordenar)."""
    fake = FakeSapObject().SapModel
    original_get_table = fake.DatabaseTables.GetTableForDisplayArray

    def broken_get_table(key: str, *args):
        result = original_get_table(key, *args)
        if key == "Connectivity - Area":
            parsed_key, fields, num_fields, num_rows, data, ret_code = result
            broken_fields = [f for f in fields if f != "PointIndex"]
            return (parsed_key, broken_fields, len(broken_fields), num_rows, [], ret_code)
        return result

    fake.DatabaseTables.GetTableForDisplayArray = broken_get_table

    call_counts = {"n": 0}
    original_get_points = fake.AreaObj.GetPoints

    def counting(name, *args):
        call_counts["n"] += 1
        return original_get_points(name, *args)

    fake.AreaObj.GetPoints = counting

    reader = SapModelReader(fake)
    table = reader.area_geometry()

    assert call_counts["n"] == 1, "debe llamar GetPoints por cada una de las areas (1) si tabla no es valida"
    row = next(r for r in table["rows"] if r[0] == "A1")
    assert row[2] == "J1; J2; J3; J4"


def test_area_geometry_measures_com_call_reduction() -> None:
    fake_batch = FakeSapObject().SapModel
    calls_batch = {"n": 0}
    original = fake_batch.AreaObj.GetPoints

    def counting(name, *args):
        calls_batch["n"] += 1
        return original(name, *args)

    fake_batch.AreaObj.GetPoints = counting
    SapModelReader(fake_batch).area_geometry()

    fake_loop = FakeSapObject().SapModel
    original_table = fake_loop.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Connectivity - Area":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake_loop.DatabaseTables.GetTableForDisplayArray = no_table
    calls_loop = {"n": 0}
    original_loop = fake_loop.AreaObj.GetPoints

    def counting_loop(name, *args):
        calls_loop["n"] += 1
        return original_loop(name, *args)

    fake_loop.AreaObj.GetPoints = counting_loop
    SapModelReader(fake_loop).area_geometry()

    assert calls_batch["n"] == 0
    assert calls_loop["n"] == 1
    assert calls_loop["n"] - calls_batch["n"] == 1


# ── Tarea 4: el limit corta el bucle de verdad (no solo al final) ────────


def test_area_geometry_limit_breaks_the_fallback_loop_early() -> None:
    """Con la tabla batch inutilizada y varias areas, limit=1 debe cortar el
    bucle apenas se junta 1 fila — no seguir llamando GetPoints para las
    demas areas y descartar el resto despues (el bug original)."""
    fake = FakeSapObject().SapModel
    # Añadir mas areas de prueba al fake para poder distinguir "corta temprano"
    # de "recorre todas y luego trunca".
    fake.AreaObj.names = ["A1", "A2", "A3", "A4"]
    fake.AreaObj.area_points["A2"] = ["J1", "J2"]
    fake.AreaObj.area_points["A3"] = ["J1", "J2"]
    fake.AreaObj.area_points["A4"] = ["J1", "J2"]

    original_table = fake.DatabaseTables.GetTableForDisplayArray

    def no_table(key: str, *args):
        if key == "Connectivity - Area":
            return (key, [], 0, 0, [], 1)
        return original_table(key, *args)

    fake.DatabaseTables.GetTableForDisplayArray = no_table

    call_counts = {"n": 0}
    original_get_points = fake.AreaObj.GetPoints

    def counting(name, *args):
        call_counts["n"] += 1
        return original_get_points(name, *args)

    fake.AreaObj.GetPoints = counting

    reader = SapModelReader(fake)
    table = reader.area_geometry(limit=1)

    assert len(table["rows"]) == 1
    assert call_counts["n"] == 1, "debe cortar el bucle tras la primera fila, no recorrer las 4 areas"


def test_area_geometry_limit_still_respected_with_batch_available() -> None:
    """Con batch disponible, limit sigue recortando el resultado (aunque no
    ahorre llamadas COM, porque el batch ya es una sola llamada para todo
    el modelo)."""
    fake = FakeSapObject().SapModel
    fake.AreaObj.names = ["A1", "A2", "A3"]
    fake.AreaObj.area_points["A2"] = ["J1", "J2"]
    fake.AreaObj.area_points["A3"] = ["J1", "J2"]

    reader = SapModelReader(fake)
    table = reader.area_geometry(limit=1)

    assert len(table["rows"]) == 1
