"""Parse typed CSI outputs by their declared positions, never numeric guessing.

CSI OAPI GetRectangle: FileName, MatProp, T3, T2, Color, Notes, GUID, ret.
GetISection adds Tf, Tw, T2b, Tfb after T2. Section depth follows local 2.
Reference: https://docs.csiamerica.com/help-files/etabs-api-2016/html/65422adf-074b-b1a1-dbdd-f84c50cb66c0.htm
The reduced FakeSapObject tuples are accepted only via the explicit demo path.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import math
from typing import Any

import re

from bridge.viewer.sap2000.reader_utils import _available_database_tables, _get_database_table_for_display, _match_database_table_keys
from bridge.viewer.sap2000.table_aliases import aliases_for
from bridge.viewer.sap2000.units import length_factor_to_meters

# Shape, getter, ordered dimensional fields. Colors never become dimensions.
GETTERS = (
    ('rectangular', 'GetRectangle', ('depth', 'width')),
    ('i', 'GetISection', ('depth', 'flange_width', 'tf', 'tw', 'bottom_flange_width', 'bottom_flange_thickness')),
    ('tube', 'GetTube', ('height', 'width', 'tf', 'tw')),
    ('pipe', 'GetPipe', ('outer_diameter', 'wall_thickness')),
    ('channel', 'GetChannel', ('depth', 'flange_width', 'tf', 'tw')),
    ('tee', 'GetTee', ('depth', 'flange_width', 'tf', 'tw')),
    ('angle', 'GetAngle', ('depth', 'width', 'tf', 'tw')),
    ('circle', 'GetCircle', ('diameter',)),
)


def _read_sections_by_getters(model: Any, names: set[str], units: str, warnings: list[str]) -> dict:
    """Un getter tipado por familia, hasta 13 llamadas COM por sección (ver
    `read_sections`, que lo usa solo como respaldo)."""
    result = {}
    prop = getattr(model, 'PropFrame', None)
    factor = length_factor_to_meters(units)
    for name in sorted(names):
        for shape, method, fields in GETTERS:
            getter = getattr(prop, method, None)
            if getter is None:
                continue
            for args in ((name,), (name, '', '', *([0.] * len(fields)), -1, '', '')):
                try:
                    raw = getter(*args)
                    if not isinstance(raw, (tuple, list)) or len(raw) != len(fields) + 6:
                        continue
                    if raw[-1] != 0 or not all(isinstance(s, str) for s in raw[:2]):
                        continue
                    values = [float(x) * factor for x in raw[2:2 + len(fields)]]
                    if not all(math.isfinite(x) and x > 0 for x in values):
                        continue
                    section = {'type': shape, 'material': raw[1], **dict(zip(fields, values))}
                    result[name] = section
                    break
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                except Exception:
                    # A typed getter normally fails for a different section family.
                    continue
            if name in result:
                break
        if name not in result:
            result[name] = {'type': 'unknown', 'material': ''}
            warnings.append(f'Sección {name}: contorno no disponible; se muestra su eje alámbrico.')
    return result



# ── Lectura por tabla (una llamada para todas las secciones) ────────────────
#
# `DatabaseTables` devuelve forma y dimensiones de TODAS las secciones en una
# sola llamada, frente a las 1–13 llamadas COM por sección de los getters
# tipados (medido: 300 secciones en I = 900 llamadas; en ángulo = 3.900).
#
# Los nombres de tabla y de columna NO están confirmados contra un SAP2000
# real, así que esta vía es de "todo o nada" POR SECCIÓN: una sección solo se
# resuelve aquí si trae todas las columnas y dimensiones positivas que su forma
# exige. Cualquier otra cosa (columna ausente, valor cero, forma desconocida)
# no se adivina: esa sección pasa a los getters tipados.
#
# En particular, una viga en I solo se acepta si la tabla trae también el ala
# inferior (t2b, tfb). Sin ellas no se puede saber si es simétrica y dibujarla
# simétrica sería inventar geometría.

_SHAPES = {
    "iwideflange": "i", "isection": "i", "wideflange": "i",
    "rectangular": "rectangular", "rectangle": "rectangular",
    "boxtube": "tube", "tube": "tube", "box": "tube",
    "pipe": "pipe", "circle": "circle", "circular": "circle",
    "channel": "channel", "tee": "tee", "angle": "angle",
}
# Columnas que exige cada forma (claves de _COLUMNS).
_REQUIRED = {
    "rectangular": ("t3", "t2"),
    "i": ("t3", "t2", "tf", "tw", "t2b", "tfb"),
    "tube": ("t3", "t2", "tf", "tw"),
    "pipe": ("t3", "tf"),
    "circle": ("t3",),
    "channel": ("t3", "t2", "tf", "tw"),
    "tee": ("t3", "t2", "tf", "tw"),
    "angle": ("t3", "t2", "tf", "tw"),
}
_COLUMNS = {
    "name": ("sectionname", "section", "name", "framesection"),
    "material": ("material", "matprop", "materialname"),
    "shape": ("shape", "secttype", "sectionshape", "sectiontype"),
    "t3": ("t3", "depth"), "t2": ("t2", "width"), "tf": ("tf",), "tw": ("tw",),
    "t2b": ("t2b",), "tfb": ("tfb",),
}


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _column_index(columns: list[str], names: tuple[str, ...]) -> int | None:
    normalized = [_norm(c) for c in columns]
    for wanted in names:
        if wanted in normalized:
            return normalized.index(wanted)
    return None


def _section_from_row(shape: str, material: str, dims: dict[str, float]) -> dict | None:
    """Sección en el esquema de `GETTERS`, o None si falta algo."""
    d = dims
    if shape == "rectangular":
        out = {"depth": d["t3"], "width": d["t2"]}
    elif shape == "i":
        out = {"depth": d["t3"], "flange_width": d["t2"], "tf": d["tf"], "tw": d["tw"],
               "bottom_flange_width": d["t2b"], "bottom_flange_thickness": d["tfb"]}
    elif shape == "tube":
        out = {"height": d["t3"], "width": d["t2"], "tf": d["tf"], "tw": d["tw"]}
    elif shape == "pipe":
        out = {"outer_diameter": d["t3"], "wall_thickness": d["tf"]}
    elif shape == "circle":
        out = {"diameter": d["t3"]}
    elif shape in ("channel", "tee"):
        out = {"depth": d["t3"], "flange_width": d["t2"], "tf": d["tf"], "tw": d["tw"]}
    elif shape == "angle":
        out = {"depth": d["t3"], "width": d["t2"], "tf": d["tf"], "tw": d["tw"]}
    else:
        return None
    return {"type": shape, "material": material, **out}


def read_sections_from_table(model: Any, names: set[str], units: str) -> dict:
    """Secciones de `names` que la tabla resuelve COMPLETAS; el resto no
    aparece en el resultado (y las lee `_read_sections_by_getters`)."""
    db = getattr(model, "DatabaseTables", None)
    if db is None or not names:
        return {}
    try:
        available = _available_database_tables(db)
        keys = _match_database_table_keys(aliases_for("frame_section_properties"), available)
    except Exception:  # noqa: BLE001
        return {}
    factor = length_factor_to_meters(units)
    for key in keys:
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception:  # noqa: BLE001
            continue
        if not parsed:
            continue
        columns, rows = parsed
        idx = {k: _column_index(columns, v) for k, v in _COLUMNS.items()}
        if any(idx[k] is None for k in ("name", "material", "shape", "t3")):
            continue
        result: dict = {}
        for row in rows:
            name = str(row[idx["name"]]).strip() if idx["name"] < len(row) else ""
            if name not in names:
                continue
            shape = _SHAPES.get(_norm(row[idx["shape"]]) if idx["shape"] < len(row) else "")
            if shape is None:
                continue
            dims: dict[str, float] = {}
            for col in _REQUIRED[shape]:
                i = idx.get(col)
                if i is None or i >= len(row):
                    break
                try:
                    value = float(str(row[i]).replace(",", ".")) * factor
                except (TypeError, ValueError):
                    break
                if not (math.isfinite(value) and value > 0):
                    break
                dims[col] = value
            else:
                section = _section_from_row(shape, str(row[idx["material"]]).strip(), dims)
                if section is not None:
                    result[name] = section
        if result:
            return result
    return {}


def read_sections(
    model: Any, names: set[str], units: str, warnings: list[str], stats: dict | None = None
) -> dict:
    """Todas las secciones de `names`: por tabla las que se pueden resolver
    completas y por getters tipados el resto. Una sección que ninguna vía
    resuelve queda como `unknown` con aviso (se dibuja su eje, no un contorno
    inventado)."""
    from_table = read_sections_from_table(model, names, units)
    missing = set(names) - set(from_table)
    from_getters = _read_sections_by_getters(model, missing, units, warnings) if missing else {}
    if stats is not None:
        stats["sectionsFromTable"] = len(from_table)
        stats["sectionsFromGetters"] = len(from_getters)
    return {**from_table, **from_getters}


def area_property(
    model: Any, name: str, units: str, cache: dict | None = None
) -> tuple[str, float | None]:
    """GetProperty + SAP2000 GetShell full output; no fabricated thickness.

    `cache` guarda el espesor por NOMBRE DE PROPIEDAD: el espesor es de la
    propiedad, no del área, así que `GetShell` se llama una vez por propiedad
    (antes, una por área: 1.000 losas con una misma propiedad = 1.000 llamadas)."""
    obj = getattr(model, 'AreaObj', None)
    prop_name = ''
    for args in ((name,), (name, '')):
        try:
            raw = obj.GetProperty(*args)
            if isinstance(raw, (tuple, list)) and len(raw) == 2 and raw[-1] == 0 and isinstance(raw[0], str):
                prop_name = raw[0]
                break
        except Exception:
            continue
    if not prop_name:
        return '', None
    if cache is not None and prop_name in cache:
        return prop_name, cache[prop_name]
    prop = getattr(model, 'PropArea', None)
    for args in ((prop_name,), (prop_name, 0, False, '', 0., 0., 0., -1, '', '')):
        try:
            raw = prop.GetShell(*args)
            # ShellType, IncludeDrillingDOF, MatProp, MatAng, Thickness,
            # BendingThickness, Color, Notes, GUID, ret.
            if isinstance(raw, (tuple, list)) and len(raw) == 10 and raw[-1] == 0 and isinstance(raw[2], str):
                t = float(raw[4]) * length_factor_to_meters(units)
                if math.isfinite(t) and t > 0:
                    if cache is not None:
                        cache[prop_name] = t
                    return prop_name, t
        except Exception:
            continue
    if cache is not None:
        cache[prop_name] = None
    return prop_name, None
