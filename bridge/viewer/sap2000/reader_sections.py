# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from typing import Any

from bridge.viewer.sap2000.reader_utils import (
    _get_names,
    _available_database_tables,
    _match_database_table_keys,
    _get_database_table_for_display,
    _find_name_column,
    _find_any_column,
    _is_number,
)
from bridge.viewer.sap2000.table_aliases import aliases_for
from bridge.viewer.sap2000.units import length_factor_to_meters


def get_material_for_section(frame_prop: Any, section_name: str) -> str:
    """Helper: retrieve the material assigned to a given frame section name via PropFrame.GetMaterial.

    Returns material name or '' if not readable. Used by MeshBuilder for 'Material' color mode.
    """
    if frame_prop is None or not section_name:
        return ""
    for call in (
        lambda: frame_prop.GetMaterial(section_name),
        lambda: frame_prop.GetMaterial(section_name, ""),
    ):
        try:
            res = call()
            if isinstance(res, (list, tuple)):
                for item in reversed(res):
                    if isinstance(item, str) and item.strip():
                        return item.strip()
            elif isinstance(res, str) and res.strip():
                return res.strip()
        except Exception:
            continue
    return ""


def frame_section_properties(model: Any, limit: int | None = None) -> dict:
    """
    Lee la geometría real de las secciones de frame para permitir extrusión 3D.
    Devuelve un diccionario con datos por nombre de sección + tabla normalizada.

    Soporta los tipos principales:
      - Rectangular (PropFrame.GetRectangle)
      - I / W (GetISection)
      - Tubular / Tube (GetTube)
      - Pipe / Circular (GetPipe)
      - Channel, Tee, Angle, etc. (con fallbacks)
      - Fallback: GetSectProps (propiedades generales) + nombre
    """
    frame_prop = getattr(model, "PropFrame", None)
    if frame_prop is None:
        return _empty_section_table("frame_section_properties", ["Section", "Type", "Notes"])

    try:
        names = _get_names(frame_prop)
    except Exception as exc:
        return _empty_section_table("frame_section_properties", ["Section", "Type", "Error"], error=str(exc))

    # PropFrame.GetRectangle/GetISection/GetTube/GetPipe/GetChannel/
    # GetSectProps devuelven dimensiones en las "present units" activas de
    # SAP2000 (mm/cm/ft/in...), no necesariamente metros. Se aplica el
    # factor una sola vez aqui, al ensamblar el dict de secciones (no dentro
    # de cada parser individual), para que quede un unico punto de
    # conversion por seccion.
    try:
        length_factor = length_factor_to_meters(model.GetPresentUnits())
    except Exception:
        length_factor = 1.0

    rows: list[list[Any]] = []
    sections: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    # Batch read (one COM call for every section) before falling back to the
    # per-section probing loop (up to ~14 COM calls/section: GetRectangle,
    # GetISection, GetTube, GetPipe, GetChannel, GetSectProps — each tried
    # with two signatures — plus GetMaterial). See
    # _frame_section_properties_from_database's docstring for the all-or-
    # nothing contract.
    batch = _frame_section_properties_from_database(frame_prop, model, errors)

    for name in names:
        if limit and len(rows) >= limit:
            break
        try:
            if batch is not None and name in batch:
                sec = dict(batch[name])
            else:
                sec = _read_one_frame_section(frame_prop, name, errors)
                mat = get_material_for_section(frame_prop, name)
                sec["material"] = mat
            _convert_section_dimensions(sec, length_factor)
            sections[name] = sec
            rows.append(_section_to_row(name, sec))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            sections[name] = {"type": "unknown", "error": str(exc)}
            rows.append([name, "unknown", str(exc)[:80]])

    table = _table(
        "frame_section_properties",
        ["Section", "Type", "Width/Outer", "Depth/Height", "tf", "tw", "Notes"],
        rows[:limit] if limit else rows,
    )
    table["metadata"] = {
        "section_count": len(sections),
        "errors": errors[:20],
    }
    # For consumers that want the rich dict (used by mesh builder)
    table["sections"] = sections
    return table


def _read_one_frame_section(frame_prop: Any, name: str, errors: list[str]) -> dict[str, Any]:
    """Intenta leer una sección por todos los métodos conocidos.

    Sondea GetRectangle/GetISection/GetTube/GetPipe/GetChannel/GetSectProps
    en orden (cada uno con dos firmas) hasta que uno funcione. `errors`
    recibe un aviso cuando ninguno de los getters tipados (1-5) resuelve la
    forma y se cae al fallback general (6) — util para saber cuantas
    secciones no se pudieron clasificar sin abortar la lectura del resto."""
    last_exc: Exception | None = None
    # 1) Rectangular
    for call in (
        lambda: frame_prop.GetRectangle(name),
        lambda: frame_prop.GetRectangle(name, "", 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_rectangle_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # 2) I / Wide flange
    for call in (
        lambda: frame_prop.GetISection(name),
        lambda: frame_prop.GetISection(name, "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_isection_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # 3) Tube / Box
    for call in (
        lambda: frame_prop.GetTube(name),
        lambda: frame_prop.GetTube(name, "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_tube_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # 4) Pipe / Circular tube
    for call in (
        lambda: frame_prop.GetPipe(name),
        lambda: frame_prop.GetPipe(name, "", 0.0, 0.0, 0.0, 0, 0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_pipe_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # 5) Channel
    for call in (
        lambda: frame_prop.GetChannel(name),
        lambda: frame_prop.GetChannel(name, "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_channel_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # 6) General / Fallback using GetSectProps (area, I, etc) + basic shape guess
    for call in (
        lambda: frame_prop.GetSectProps(name),
        lambda: frame_prop.GetSectProps(name, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ):
        try:
            res = call()
            parsed = _parse_sectprops_result(res)
            if parsed:
                return parsed
        except Exception as exc:
            last_exc = exc
            continue

    # Last resort: ninguno de los 6 metodos parseo la seccion.
    if last_exc is not None:
        errors.append(
            f"{name}: no se pudo determinar la forma via getters tipados "
            f"(ultimo error: {last_exc})"
        )
    return {"type": "general", "notes": "No detailed geometry; using defaults for extrusion"}


_SECTION_LINEAR_FIELDS = (
    "width",
    "depth",
    "flange_width",
    "tf",
    "tw",
    "height",
    "outer_diameter",
    "wall_thickness",
)
_SECTION_AREA_FIELDS = ("area",)


def _convert_section_dimensions(sec: dict[str, Any], length_factor: float) -> None:
    """Scale a parsed section dict's geometric fields in place, from SAP2000's
    active present units to meters.

    Linear fields (width/depth/flange widths/thicknesses/diameters) scale by
    `length_factor`; `area` is a surface quantity, so it scales by
    `length_factor ** 2`. No-op when `length_factor` is 1.0 (already meters).
    """
    if length_factor == 1.0:
        return
    for key in _SECTION_LINEAR_FIELDS:
        if key in sec and isinstance(sec[key], (int, float)):
            sec[key] = sec[key] * length_factor
    for key in _SECTION_AREA_FIELDS:
        if key in sec and isinstance(sec[key], (int, float)):
            sec[key] = sec[key] * (length_factor ** 2)


def _frame_section_properties_from_database(
    frame_prop: Any, model: Any, errors: list[str]
) -> dict[str, dict[str, Any]] | None:
    """Batch read of frame section shape + dimensions + material via
    DatabaseTables ("Frame Section Properties 01 - General" table, one COM
    call for every section) instead of probing GetRectangle/GetISection/
    GetTube/GetPipe/GetChannel/GetSectProps (each with two signatures) plus
    GetMaterial per section — up to ~14 COM round-trips/section replaced by
    a single call for the whole model. Column names are guessed following
    common SAP2000 naming conventions (see table_aliases.py) — NOT confirmed
    against a real SAP2000 table.

    All-or-nothing per table, same contract as
    `_frame_assigned_section_from_database`: the name, shape, material and
    all four generic dimension columns (t3/t2/tf/tw — SAP2000's table keeps
    these for every row regardless of shape, blank/zero where unused) must
    all be found by exact name, or this returns None and the caller falls
    back to the per-section probing loop untouched. Never partially-guessed
    geometry.

    Returns dict[section_name] -> parsed section dict (same keys
    `_read_one_frame_section` would produce, plus 'material') for every
    section found in the table — NOT yet converted from present units to
    meters (the caller still runs `_convert_section_dimensions` once, same
    as every other path, so unit conversion never happens twice). Only None
    means "table unusable, use the per-section probing loop"."""
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return None
    try:
        available = _available_database_tables(db)
    except Exception as exc:
        errors.append(f"DatabaseTables.GetAvailableTables: {exc}")
        return None
    keys = _match_database_table_keys(aliases_for("frame_section_properties"), available) or aliases_for(
        "frame_section_properties"
    )

    result: dict[str, dict[str, Any]] = {}
    matched = False
    for key in keys:
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            continue
        if not parsed:
            continue
        columns, rows = parsed

        name_idx = _find_name_column(columns, ["SectionName", "Section", "Name", "Frame Section"])
        shape_idx = _find_any_column(columns, ["Shape", "SectType", "Section Shape", "SectionType"])
        material_idx = _find_any_column(columns, ["Material", "MatProp", "MaterialName"])
        t3_idx = _find_any_column(columns, ["t3", "T3", "Depth"])
        t2_idx = _find_any_column(columns, ["t2", "T2", "Width"])
        tf_idx = _find_any_column(columns, ["tf", "Tf", "FlangeThickness"])
        tw_idx = _find_any_column(columns, ["tw", "Tw", "WebThickness"])
        required = [name_idx, shape_idx, material_idx, t3_idx, t2_idx, tf_idx, tw_idx]
        if any(idx is None for idx in required):
            continue
        matched = True

        for row in rows:
            if max(required) >= len(row):
                continue
            name = str(row[name_idx]).strip()
            if not name:
                continue
            shape_text = str(row[shape_idx]).strip()
            material = str(row[material_idx]).strip()
            t3 = float(row[t3_idx]) if _is_number(row[t3_idx]) or _is_numeric_str(row[t3_idx]) else 0.0
            t2 = float(row[t2_idx]) if _is_number(row[t2_idx]) or _is_numeric_str(row[t2_idx]) else 0.0
            tf = float(row[tf_idx]) if _is_number(row[tf_idx]) or _is_numeric_str(row[tf_idx]) else 0.0
            tw = float(row[tw_idx]) if _is_number(row[tw_idx]) or _is_numeric_str(row[tw_idx]) else 0.0
            sec = _section_from_shape_and_dims(shape_text, t3, t2, tf, tw)
            sec["material"] = material
            result[name] = sec
    return result if matched else None


def _is_numeric_str(value: Any) -> bool:
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _map_section_shape_to_type(shape_text: str) -> str:
    """Map SAP2000's free-text "Shape" column (e.g. "I/Wide Flange",
    "Box/Tube", "Pipe", "Rectangular", "Channel") to the internal type
    strings `_read_one_frame_section`'s parsers already produce ("i",
    "tube", "pipe", "rectangular", "channel", "general") — kept identical so
    downstream consumers (MeshBuilder / 3D extrusion) never see a new type."""
    text = (shape_text or "").strip().casefold()
    if "pipe" in text:
        return "pipe"
    if "tube" in text or "box" in text:
        return "tube"
    if "channel" in text:
        return "channel"
    if "wide flange" in text or "i/wide" in text or text == "i" or text.startswith("i "):
        return "i"
    if "rect" in text:
        return "rectangular"
    return "general"


def _section_from_shape_and_dims(shape_text: str, t3: float, t2: float, tf: float, tw: float) -> dict[str, Any]:
    """Build a section dict from the batch table's generic t3/t2/tf/tw
    dimension columns, per shape. Field names match the ones
    `_parse_*_result` in this module already produce, so callers (row
    rendering, `_convert_section_dimensions`, MeshBuilder) don't need to
    know whether a section came from the batch table or the probing loop."""
    section_type = _map_section_shape_to_type(shape_text)
    if section_type == "rectangular":
        return {"type": "rectangular", "width": abs(t2), "depth": abs(t3), "notes": "Rectangular solid"}
    if section_type == "i":
        return {
            "type": "i",
            "depth": abs(t3),
            "flange_width": abs(t2),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "I / Wide flange section",
        }
    if section_type == "tube":
        return {
            "type": "tube",
            "height": abs(t3),
            "width": abs(t2),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "Rectangular tube / hollow section",
        }
    if section_type == "pipe":
        # SAP2000 convention for Pipe rows in this table: t3 = outer
        # diameter, tw = wall thickness (t2/tf unused, left blank/zero).
        return {
            "type": "pipe",
            "outer_diameter": abs(t3),
            "wall_thickness": abs(tw) or abs(tf),
            "notes": "Circular pipe / tube",
        }
    if section_type == "channel":
        return {
            "type": "channel",
            "depth": abs(t3),
            "flange_width": abs(t2),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "Channel section",
        }
    return {
        "type": "general",
        "notes": "Section shape not recognized from batch table; using defaults for extrusion",
    }


# ── Parsers for different result shapes ──────────────────────────────────────

def _parse_rectangle_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)) or len(res) < 4:
        return None
    # Common patterns: (width, depth, ...) or (ret, width, depth, ...)
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 2:
        w, d = nums[0], nums[1]
        return {
            "type": "rectangular",
            "width": abs(w),
            "depth": abs(d),
            "notes": "Rectangular solid",
        }
    return None


def _parse_isection_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)):
        return None
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 4:
        # Typical: d, bf, tf, tw  (depth, flange width, flange thick, web thick)
        d, bf, tf, tw = nums[0], nums[1], nums[2], nums[3]
        return {
            "type": "i",
            "depth": abs(d),
            "flange_width": abs(bf),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "I / Wide flange section",
        }
    return None


def _parse_tube_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)):
        return None
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 4:
        # h, b, tf, tw
        h, b, tf, tw = nums[0], nums[1], nums[2], nums[3]
        return {
            "type": "tube",
            "height": abs(h),
            "width": abs(b),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "Rectangular tube / hollow section",
        }
    return None


def _parse_pipe_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)):
        return None
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 2:
        d, t = nums[0], nums[1]
        return {
            "type": "pipe",
            "outer_diameter": abs(d),
            "wall_thickness": abs(t),
            "notes": "Circular pipe / tube",
        }
    return None


def _parse_channel_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)):
        return None
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 4:
        d, bf, tf, tw = nums[0], nums[1], nums[2], nums[3]
        return {
            "type": "channel",
            "depth": abs(d),
            "flange_width": abs(bf),
            "tf": abs(tf),
            "tw": abs(tw),
            "notes": "Channel section",
        }
    return None


def _parse_sectprops_result(res: Any) -> dict | None:
    if not isinstance(res, (list, tuple)):
        return None
    nums = [float(x) for x in res if isinstance(x, (int, float))]
    if len(nums) >= 2:
        # Area, I22, I33 etc. Not shape, but usable for approx extrusion or warning.
        area = nums[0] if nums else 0.0
        return {
            "type": "general",
            "area": area,
            "notes": "General section (used GetSectProps). Extrusion will use default profile.",
        }
    return None


# ── Area / Shell thickness ───────────────────────────────────────────────────

def area_shell_thickness(model: Any, limit: int | None = None) -> dict:
    """Lee espesores reales de secciones de área (losas, muros, shells)."""
    area_prop = getattr(model, "PropArea", None)
    if area_prop is None:
        return _empty_section_table("area_shell_thickness", ["Section", "Thickness", "Type"])

    try:
        names = _get_names(area_prop)
    except Exception as exc:
        return _empty_section_table("area_shell_thickness", ["Section", "Thickness", "Error"], error=str(exc))

    # PropArea.GetShell() returns the thickness in SAP2000's active "present
    # units" (Settings.units, e.g. mm/cm/ft), not necessarily meters — convert
    # so the value stored here (and shown to the user as "Thickness (m)")
    # is always actually in meters.
    try:
        length_factor = length_factor_to_meters(model.GetPresentUnits())
    except Exception:
        length_factor = 1.0

    rows: list[list[Any]] = []
    thicknesses: dict[str, float] = {}
    errors: list[str] = []

    for name in names:
        if limit and len(rows) >= limit:
            break
        t = None
        for call in (
            lambda: area_prop.GetShell(name),
            lambda: area_prop.GetShell(name, "", 0.0, 0, 0, 0.0),
        ):
            try:
                res = call()
                if isinstance(res, (list, tuple)):
                    for v in res:
                        if isinstance(v, (int, float)) and v > 0:
                            t = float(v)
                            break
                if t:
                    break
            except Exception:
                continue

        if t is None:
            t = 0.15  # sensible default, already in meters
            errors.append(f"{name}: thickness not readable, using default 0.15m")
        else:
            t = t * length_factor
        thicknesses[name] = t
        rows.append([name, round(t, 4), "shell"])

    table = _table("area_shell_thickness", ["Section", "Thickness (m)", "Type"], rows[:limit] if limit else rows)
    table["metadata"] = {"count": len(thicknesses), "errors": errors[:10]}
    table["thicknesses"] = thicknesses
    return table


# ── Helpers ──────────────────────────────────────────────────────────────────

def _table(name: str, columns: list[str], rows: list[list[Any]]) -> dict:
    return {
        "name": name,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }


def _empty_section_table(name: str, columns: list[str], error: str | None = None) -> dict:
    rows = [["(sin datos)", "", ""]]
    if error:
        rows[0][-1] = error
    return {
        "name": name,
        "columns": columns,
        "rows": rows,
        "row_count": 0,
        "metadata": {"errors": [error] if error else []},
        "sections": {},
    }


def _section_to_row(name: str, sec: dict[str, Any]) -> list[Any]:
    t = sec.get("type", "general")
    w = sec.get("width") or sec.get("flange_width") or sec.get("outer_diameter") or ""
    d = sec.get("depth") or sec.get("height") or ""
    tf = sec.get("tf") or sec.get("wall_thickness") or ""
    tw = sec.get("tw") or ""
    notes = sec.get("notes", "")
    if sec.get("error"):
        notes = sec["error"]
    return [name, t, w if w != "" else "-", d if d != "" else "-", tf if tf != "" else "-", tw if tw != "" else "-", notes[:60]]


# Convenience: enrich frame dicts with section geometry when available
def attach_section_geometry(frames: list[dict], section_props: dict[str, dict]) -> None:
    """Mutates frames in place, adding 'section_props' key when known."""
    for fr in frames:
        sname = fr.get("section") or fr.get("assigned_section")
        if not sname:
            continue
        if sname in section_props:
            fr["section_props"] = section_props[sname]
        else:
            # case-insensitive fallback
            for k, v in section_props.items():
                if k.casefold() == str(sname).casefold():
                    fr["section_props"] = v
                    break
