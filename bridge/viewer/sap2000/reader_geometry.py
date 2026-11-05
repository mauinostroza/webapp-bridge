# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from typing import Any

from bridge.viewer.sap2000.reader_utils import (
    _get_names,
    _is_number,
    _find_name_column,
    _find_any_column,
    _available_database_tables,
    _match_database_table_keys,
    _get_database_table_for_display,
    _names,
    _database_named_values,
    all_load_patterns_displayed,
)
from bridge.viewer.sap2000.units import length_factor_to_meters


# ── Joint coordinates ──────────────────────────────────────────────


def _joint_coords_and_groups(
    model: Any,
) -> tuple[dict[str, tuple[float, float, float]], dict[str, list[str]], list[str]]:
    """Single COM pass for joint coordinates + group memberships.

    Shared by `_joint_coordinates` and `_frame_geometry` so callers that need
    both (e.g. `build_from_reader`) don't trigger the PointObj/GroupDef COM
    loops twice per model load.

    Coordinates are read via `DatabaseTables.GetTableForDisplayArray`
    ("Joint Coordinates") first — a single COM call returning every joint at
    once — since it's dramatically cheaper than one `GetCoordCartesian` call
    per joint. The per-object API loop only runs as a fallback when the
    table is unavailable/incomplete for this SAP2000 version, or to fill in
    any joints the table happened to miss.
    """
    errors: list[str] = []
    expected_names = _point_name_count(model, errors)
    coords = _joint_coordinates_from_database(model, errors)
    if not coords or (expected_names and len(coords) < expected_names):
        api_coords = _joint_coordinates_from_api(model, errors)
        for name, point in api_coords.items():
            coords.setdefault(name, point)

    # Ambas fuentes (DatabaseTables y PointObj.GetCoordCartesian) devuelven
    # coordenadas en las "present units" activas de SAP2000 (pueden ser mm,
    # cm, ft, in...), no necesariamente metros. Se normaliza una sola vez
    # aqui, tras el merge, para que joint_coordinates/frame_geometry/
    # frame_length y el visor 3D (que todos consumen este mismo dict) vean
    # siempre metros sin arriesgar doble conversion.
    try:
        length_factor = length_factor_to_meters(model.GetPresentUnits())
    except Exception:
        length_factor = 1.0
    if length_factor != 1.0:
        coords = {
            name: (x * length_factor, y * length_factor, z * length_factor)
            for name, (x, y, z) in coords.items()
        }

    groups = _group_memberships(model, errors)
    return coords, groups, errors


def _point_name_count(model: Any, errors: list[str]) -> int:
    point_obj = getattr(model, "PointObj", None)
    if point_obj is None:
        return 0
    try:
        return len(_get_names(point_obj))
    except Exception as exc:
        errors.append(f"PointObj.GetNameList: {exc}")
        return 0


def _joint_coordinates(model: Any) -> tuple[list[tuple[str, tuple[float, float, float], list[str]]], list[str]]:
    coords, groups, errors = _joint_coords_and_groups(model)
    rows = [(name, point, groups.get(name, [])) for name, point in coords.items()]
    return rows, errors


def _joint_coordinates_from_api(
    model: Any, errors: list[str]
) -> dict[str, tuple[float, float, float]]:
    point_obj = getattr(model, "PointObj", None)
    if point_obj is None:
        errors.append("PointObj no disponible")
        return {}
    try:
        names = _get_names(point_obj)
    except Exception as exc:
        errors.append(f"PointObj.GetNameList: {exc}")
        return {}

    coords: dict[str, tuple[float, float, float]] = {}
    for name in names:
        try:
            coords[name] = _point_coordinates(point_obj, name)
        except Exception as exc:
            errors.append(f"PointObj.GetCoordCartesian {name}: {exc}")
    return coords


def _point_coordinates(point_obj: Any, name: str) -> tuple[float, float, float]:
    attempts = [
        lambda: point_obj.GetCoordCartesian(name),
        lambda: point_obj.GetCoordCartesian(name, "Global"),
        lambda: point_obj.GetCoordCartesian(name, 0, 0, 0),
        lambda: point_obj.GetCoordCartesian(name, 0, 0, 0, "Global"),
    ]
    last: Any = None
    for call in attempts:
        try:
            parsed = _parse_xyz(call())
            if parsed is not None:
                return parsed
        except Exception as exc:
            last = exc
    if isinstance(last, Exception):
        raise last
    raise RuntimeError("SAP2000 no devolvio coordenadas parseables")


def _parse_xyz(result: Any) -> tuple[float, float, float] | None:
    if not isinstance(result, (tuple, list)):
        return None
    values = list(result)
    if len(values) >= 4 and isinstance(values[-1], int) and all(_is_number(value) for value in values[:3]):
        return (float(values[0]), float(values[1]), float(values[2]))
    if len(values) >= 4 and isinstance(values[0], int) and all(_is_number(value) for value in values[1:4]):
        return (float(values[1]), float(values[2]), float(values[3]))
    numbers = [value for value in values if _is_number(value)]
    if len(numbers) >= 3:
        return (float(numbers[0]), float(numbers[1]), float(numbers[2]))
    return None


def _joint_coordinates_from_database(
    model: Any, errors: list[str]
) -> dict[str, tuple[float, float, float]]:
    tables = _database_geometry_tables(
        model,
        _aliases_for("joint_coordinates"),
        ["Joint", "Point", "Object", "Obj", "Name"],
        errors,
    )
    coords: dict[str, tuple[float, float, float]] = {}
    for columns, rows in tables:
        name_index = _find_name_column(columns, ["Joint", "Point", "Object", "Obj", "Name"])
        x_index = _find_any_column(columns, ["X", "GlobalX", "CoordX", "XCoord"])
        y_index = _find_any_column(columns, ["Y", "GlobalY", "CoordY", "YCoord"])
        z_index = _find_any_column(columns, ["Z", "GlobalZ", "CoordZ", "ZCoord"])
        if name_index is None or x_index is None or y_index is None or z_index is None:
            continue
        for row in rows:
            if max(name_index, x_index, y_index, z_index) >= len(row):
                continue
            if not (_is_number(row[x_index]) and _is_number(row[y_index]) and _is_number(row[z_index])):
                continue
            coords[str(row[name_index])] = (
                float(row[x_index]),
                float(row[y_index]),
                float(row[z_index]),
            )
    return coords


# ── Frame geometry ─────────────────────────────────────────────────


def _frame_geometry(
    model: Any,
    shared: tuple[dict[str, tuple[float, float, float]], dict[str, list[str]]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if shared is not None:
        coords, groups = shared
    else:
        coords, groups, shared_errors = _joint_coords_and_groups(model)
        errors.extend(shared_errors)

    expected_names = _frame_name_count(model, errors)
    endpoints = _frame_endpoints_from_database(model, errors)
    if not endpoints or (expected_names and len(endpoints) < expected_names):
        api_endpoints = _frame_endpoints_from_api(model, errors)
        for name, pair in api_endpoints.items():
            endpoints.setdefault(name, pair)

    frames: list[dict[str, Any]] = []
    for name, (i_end, j_end) in endpoints.items():
        i_coord = coords.get(i_end, ("", "", ""))
        j_coord = coords.get(j_end, ("", "", ""))
        frames.append(
            {
                "name": name,
                "i_end": i_end,
                "j_end": j_end,
                "xi": i_coord[0],
                "yi": i_coord[1],
                "zi": i_coord[2],
                "xj": j_coord[0],
                "yj": j_coord[1],
                "zj": j_coord[2],
                "groups": groups.get(name, []),
            }
        )
    return frames, errors


def _frame_endpoints_from_api(
    model: Any, errors: list[str]
) -> dict[str, tuple[str, str]]:
    frame_obj = getattr(model, "FrameObj", None)
    if frame_obj is None:
        errors.append("FrameObj no disponible")
        return {}
    try:
        names = _get_names(frame_obj)
    except Exception as exc:
        errors.append(f"FrameObj.GetNameList: {exc}")
        return {}

    endpoints: dict[str, tuple[str, str]] = {}
    for name in names:
        try:
            endpoints[name] = _frame_points(frame_obj, name)
        except Exception as exc:
            errors.append(f"FrameObj.GetPoints {name}: {exc}")
    return endpoints


def _frame_points(frame_obj: Any, name: str) -> tuple[str, str]:
    attempts = [
        lambda: frame_obj.GetPoints(name),
        lambda: frame_obj.GetPoints(name, "", ""),
    ]
    last: Any = None
    for call in attempts:
        try:
            result = call()
        except Exception as exc:
            last = exc
            continue
        strings = (
            [str(value) for value in result if isinstance(value, str)]
            if isinstance(result, (tuple, list))
            else []
        )
        if len(strings) >= 2:
            return strings[0], strings[1]
        last = result
    if isinstance(last, Exception):
        raise last
    raise RuntimeError("SAP2000 no devolvio nodos I/J parseables")


def _frame_endpoints_from_database(
    model: Any, errors: list[str]
) -> dict[str, tuple[str, str]]:
    tables = _database_geometry_tables(
        model,
        _aliases_for("frame_geometry"),
        ["Frame", "Object", "Obj", "Name"],
        errors,
    )
    endpoints: dict[str, tuple[str, str]] = {}
    for columns, rows in tables:
        frame_index = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        i_index = _find_any_column(
            columns, ["JointI", "IEnd", "PointI", "I-End", "Joint I", "Point I"]
        )
        j_index = _find_any_column(
            columns, ["JointJ", "JEnd", "PointJ", "J-End", "Joint J", "Point J"]
        )
        if frame_index is None or i_index is None or j_index is None:
            continue
        for row in rows:
            if max(frame_index, i_index, j_index) >= len(row):
                continue
            frame = str(row[frame_index]).strip()
            i_end = str(row[i_index]).strip()
            j_end = str(row[j_index]).strip()
            if frame and i_end and j_end:
                endpoints[frame] = (i_end, j_end)
    return endpoints


# ── Group memberships ──────────────────────────────────────────────


def _group_memberships(model: Any, errors: list[str]) -> dict[str, list[str]]:
    group_def = getattr(model, "GroupDef", None)
    if group_def is None:
        return {}
    try:
        groups = _get_names(group_def)
    except Exception as exc:
        errors.append(f"GroupDef.GetNameList: {exc}")
        return {}

    memberships: dict[str, list[str]] = {}
    for group in groups:
        try:
            result = group_def.GetAssignments(group)
        except Exception as exc:
            errors.append(f"GroupDef.GetAssignments {group}: {exc}")
            continue
        for object_name in _assignment_names(result):
            memberships.setdefault(object_name, []).append(group)
    return memberships


def _assignment_names(result: Any) -> list[str]:
    if not isinstance(result, (tuple, list)):
        return []
    arrays = [list(item) for item in result if isinstance(item, (list, tuple))]
    string_arrays = [
        array
        for array in arrays
        if array and all(isinstance(item, str) for item in array)
    ]
    if string_arrays:
        return [str(item) for item in max(string_arrays, key=len)]
    return []


def _allowed_group_objects(
    model: Any, group_name: str, action: str, group_members: set[str]
) -> set[str]:
    if action in {"frame_forces", "frame_geometry", "model_geometry", "geometry_3d"}:
        return set(group_members)
    if action in {"joint_displacements", "joint_reactions", "joint_coordinates"}:
        allowed = {name for name in group_members if name.upper().startswith("J")}
        member_keys = {name.casefold() for name in group_members}
        # Only frame I/J endpoints are needed here, not joint coordinates or
        # group memberships (both of which _frame_geometry() would otherwise
        # read again on top of the _group_memberships() the caller already
        # ran) — read endpoints directly to avoid that redundant COM pass.
        errors: list[str] = []
        endpoints = _frame_endpoints_from_database(model, errors)
        if not endpoints:
            endpoints = _frame_endpoints_from_api(model, errors)
        else:
            api_endpoints = _frame_endpoints_from_api(model, [])
            for name, pair in api_endpoints.items():
                endpoints.setdefault(name, pair)
        for frame_name, (i_end, j_end) in endpoints.items():
            if frame_name.casefold() not in member_keys:
                continue
            for value in (i_end, j_end):
                if value:
                    allowed.add(value)
        return allowed or set(group_members)
    return set(group_members)


# ── Database geometry tables ───────────────────────────────────────


def _database_geometry_tables(
    model: Any,
    candidate_keys: list[str],
    required_name_fields: list[str],
    errors: list[str],
) -> list[tuple[list[str], list[list[Any]]]]:
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return []
    available = _available_database_tables(db)
    keys = _match_database_table_keys(candidate_keys, available) or candidate_keys
    tables: list[tuple[list[str], list[list[Any]]]] = []
    for key in keys:
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            continue
        if not parsed:
            continue
        columns, rows = parsed
        if _find_name_column(columns, required_name_fields) is not None:
            tables.append((columns, rows))
    return tables


def _frame_name_count(model: Any, errors: list[str]) -> int:
    frame_obj = getattr(model, "FrameObj", None)
    if frame_obj is None:
        return 0
    try:
        return len(_get_names(frame_obj))
    except Exception as exc:
        errors.append(f"FrameObj.GetNameList: {exc}")
        return 0


# ── Frame section / releases / loads helpers ───────────────────────


def _frame_section(frame_obj: Any, name: str) -> tuple[str, str]:
    section, sauto = "", ""
    for call in (
        lambda: frame_obj.GetSection(name),
        lambda: frame_obj.GetSection(name, "", ""),
    ):
        try:
            result = call()
        except Exception:
            continue
        if isinstance(result, (tuple, list)):
            strings = [str(value) for value in result if isinstance(value, str)]
            if strings:
                section = strings[0]
                sauto = strings[1] if len(strings) > 1 else ""
                return section, sauto
    return section, sauto


def _frame_assigned_section_from_database(
    model: Any, errors: list[str]
) -> dict[str, tuple[str, str]] | None:
    """Batch read of frame assigned sections via DatabaseTables (one COM call for
    every frame) instead of FrameObj.GetSection per frame (Fase 17). Column names
    are guessed following common naming conventions in SAP2000 tables — NOT
    confirmed against a real SAP2000 table (these are documented as candidates).
    All-or-nothing per table: if the required name column and section column
    aren't found by exact name, returns None and the caller falls back untouched.

    Returns a dict covering EVERY frame found in the table (including frames
    with no assigned section, as empty string) when matched — only None means
    "use the API loop"."""
    tables = _database_geometry_tables(
        model, _aliases_for("frame_assigned_section"), ["Frame", "Object", "Obj", "Name"], errors,
    )
    result: dict[str, tuple[str, str]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        section_idx = _find_any_column(columns, ["Section", "SectionName", "PropFrame", "Frame Section"])
        sauto_idx = _find_any_column(columns, ["AutoSelect", "AutoSelectSection", "AutoSelect Section", "AutoSect"])
        if name_idx is None or section_idx is None:
            continue
        matched = True
        for row in rows:
            needed = [name_idx, section_idx]
            if sauto_idx is not None:
                needed.append(sauto_idx)
            if max(needed) >= len(row):
                continue
            frame_name = str(row[name_idx]).strip()
            section = str(row[section_idx]).strip()
            sauto = ""
            if sauto_idx is not None and sauto_idx < len(row):
                sauto = str(row[sauto_idx]).strip()
            if frame_name:
                result[frame_name] = (section, sauto)
    return result if matched else None


def _frame_local_axes_data(frame_obj: Any, name: str) -> tuple[float | None, bool]:
    """Lectura por-frame del angulo de ejes locales via
    FrameObj.GetLocalAxes(Name, Ang, Advanced) — firma tomada de la
    documentacion de la API OAPI, NO verificada contra un SAP2000 real desde
    este repo (misma reserva que el resto de firmas de este modulo, ver
    `_frame_releases_data`). `Ang` es la rotacion en grados alrededor del eje
    local 1 respecto a la orientacion por defecto; `Advanced` indica si el
    frame usa ejes locales "avanzados" (definidos por vector/plano/joint de
    referencia en vez de un simple angulo).

    Cuando Advanced es True, un solo angulo NO describe la orientacion
    completa de esos ejes — por la regla del proyecto de no fabricar datos,
    se devuelve angulo=None (no un valor inventado o el ultimo Ang leido,
    que en SAP2000 puede quedar como resto de una configuracion previa y ya
    no aplicar). El flag `advanced` se devuelve siempre para que el caller
    distinga "no disponible por ejes avanzados" de "no se pudo leer".

    Devuelve (None, False) si la llamada falla o el resultado no se puede
    parsear (nunca fabrica un angulo en ese caso tampoco)."""
    for call in (
        lambda: frame_obj.GetLocalAxes(name),
        lambda: frame_obj.GetLocalAxes(name, 0.0, False),
    ):
        try:
            result = call()
        except Exception:
            continue
        parsed = _parse_local_axes_result(result)
        if parsed is None:
            continue
        angle, advanced = parsed
        if advanced:
            return None, True
        return angle, False
    return None, False


def _parse_local_axes_result(result: Any) -> tuple[float, bool] | None:
    if not isinstance(result, (tuple, list)):
        return None
    values = list(result)
    numbers = [v for v in values if _is_number(v)]
    bools = [v for v in values if isinstance(v, bool)]
    if not numbers:
        return None
    return float(numbers[0]), bool(bools[0]) if bools else False


def _frame_local_axes_from_database(
    model: Any, errors: list[str]
) -> dict[str, float] | None:
    """Batch read of the frame local-axes rotation angle via DatabaseTables
    (one COM call for every frame) instead of FrameObj.GetLocalAxes per
    frame. Column names are guesses (see table_aliases.py "frame_local_axes")
    — NOT confirmed against a real SAP2000 table. All-or-nothing per table:
    if the name column and an angle column aren't found by exact name,
    returns None and the caller falls back untouched (same contract as
    `_frame_assigned_section_from_database`).

    A row is skipped (not added to the dict) when an "Advanced" column is
    present and marks that frame as using advanced local axes — a single
    angle would misrepresent it, so that frame is simply absent from the
    returned dict and the caller falls back to `_frame_local_axes_data` for
    it specifically, which independently arrives at the same "not
    available" conclusion via FrameObj.GetLocalAxes's own Advanced flag.
    When no "Advanced" column is found at all, every row's angle is trusted
    as-is (a "Typical" table, by name, would only ever carry frames with a
    plain angle).

    Returns a dict covering every non-advanced frame found in the table —
    only None means "table unusable, use the API loop"."""
    tables = _database_geometry_tables(
        model, _aliases_for("frame_local_axes"), ["Frame", "Object", "Obj", "Name"], errors,
    )
    result: dict[str, float] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        angle_idx = _find_any_column(columns, ["Angle", "Ang", "LocalAxisAngle", "Local Axis Angle"])
        if name_idx is None or angle_idx is None:
            continue
        advanced_idx = _find_any_column(columns, ["Advanced", "AdvanceAxes", "Advanced Axes", "AdvancedAxes"])
        matched = True
        for row in rows:
            needed = [name_idx, angle_idx] + ([advanced_idx] if advanced_idx is not None else [])
            if max(needed) >= len(row):
                continue
            if advanced_idx is not None and _table_bool(row[advanced_idx]):
                continue
            frame_name = str(row[name_idx]).strip()
            angle_raw = row[angle_idx]
            if not frame_name or not (_is_number(angle_raw) or _is_numeric_str(angle_raw)):
                continue
            result[frame_name] = float(angle_raw)
    return result if matched else None


def _frame_releases_data(frame_obj: Any, name: str) -> tuple[list[bool], list[bool]]:
    default = [False] * 6
    for call in (
        lambda: frame_obj.GetReleases(name),
        lambda: frame_obj.GetReleases(
            name, [False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6, [0.0] * 6, [0.0] * 6
        ),
    ):
        try:
            result = call()
        except Exception:
            continue
        if isinstance(result, (tuple, list)):
            arrays = [
                list(item)
                for item in result
                if isinstance(item, (list, tuple)) and len(item) == 6
            ]
            bool_arrays = [
                [bool(v) for v in arr]
                for arr in arrays
                if all(isinstance(v, (bool, int)) for v in arr)
            ]
            if len(bool_arrays) >= 2:
                return bool_arrays[0], bool_arrays[1]
    return default, default


def _frame_releases_from_database(
    model: Any, errors: list[str]
) -> dict[str, tuple[list[bool], list[bool]]] | None:
    """Batch read of frame end releases via DatabaseTables (one COM call for
    every frame) instead of FrameObj.GetReleases per frame (Fase 17). Columns
    are guessed following the same P/V2/V3/T/M2/M3 local-axis naming SAP2000
    already uses for frame_forces (same DOF order as _frame_releases_data's
    U1..R3 bool arrays: P=U1, V2=U2, V3=U3, T=R1, M2=R2, M3=R3), suffixed I/J
    per end — NOT confirmed against a real SAP2000 table, see plan. All-or-
    nothing per table: if the 12 expected columns aren't all found by exact
    name, returns None and the caller falls back untouched. Returns a dict
    covering EVERY frame found in the table (including fully-continuous
    ones, as all-False) when matched — only None means "use the API loop"."""
    tables = _database_geometry_tables(
        model, _aliases_for("frame_releases"), ["Frame", "Object", "Obj", "Name"], errors,
    )
    dof_tokens = ["P", "V2", "V3", "T", "M2", "M3"]
    result: dict[str, tuple[list[bool], list[bool]]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        i_idx = [_find_any_column(columns, [f"{tok}I", f"I{tok}", f"{tok}-I"]) for tok in dof_tokens]
        j_idx = [_find_any_column(columns, [f"{tok}J", f"J{tok}", f"{tok}-J"]) for tok in dof_tokens]
        if name_idx is None or any(idx is None for idx in i_idx) or any(idx is None for idx in j_idx):
            continue
        matched = True
        for row in rows:
            needed = [name_idx, *i_idx, *j_idx]
            if max(needed) >= len(row):
                continue
            ii = [_table_bool(row[idx]) for idx in i_idx]
            jj = [_table_bool(row[idx]) for idx in j_idx]
            result[str(row[name_idx])] = (ii, jj)
    return result if matched else None


def _format_release_summary(name: str, ii: list[bool], jj: list[bool]) -> str:
    labels = ["U1", "U2", "U3", "R1", "R2", "R3"]
    i_release = [labels[i] for i, v in enumerate(ii) if v]
    j_release = [labels[i] for i, v in enumerate(jj) if v]
    if not i_release and not j_release:
        return f"El frame {name} no tiene liberaciones (totalmente continuo)."
    parts = []
    if i_release:
        parts.append(f"extremo I libera {', '.join(i_release)}")
    if j_release:
        parts.append(f"extremo J libera {', '.join(j_release)}")
    return f"El frame {name} tiene releases: {'; '.join(parts)}."


def _read_load_assignments(obj: Any, method: str, name: str, parser: Any,
                           errors: list[str] | None = None) -> list[dict[str, Any]]:
    func = getattr(obj, method, None)
    failures = []
    if not callable(func):
        if errors is not None:
            errors.append(f"{method} {name}: API no disponible")
        return []
    array_count = {"GetLoadDistributed": 11, "GetLoadPoint": 8, "GetLoadForce": 10}[method]
    explicit = (name, 0, *([] for _ in range(array_count)), 0)
    for args in ((name,), (name, 0), explicit):
        try:
            result = func(*args)
            if isinstance(result, int) and result != 0:
                raise ValueError(f"SAP return={result}")
            if not isinstance(result, (tuple, list)):
                raise ValueError("respuesta sin arrays")
            if result and isinstance(result[-1], int) and result[-1] != 0:
                raise ValueError(f"SAP return={result[-1]}")
            arrays = [item for item in result if isinstance(item, (tuple, list))]
            if not arrays:
                raise ValueError("respuesta sin arrays de asignaciones")
            if any(arrays):
                sizes = {len(arr) for arr in arrays}
                string_count = sum(bool(arr) and all(isinstance(v, str) for v in arr) for arr in arrays)
                numeric_count = sum(bool(arr) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in arr) for arr in arrays)
                minimum_numeric = 4 if method == "GetLoadPoint" else 6
                if len(sizes) != 1 or string_count < 2 or numeric_count < minimum_numeric:
                    raise ValueError("arrays de cargas incompletos o longitudes incompatibles")
            parsed = parser(name, result)
            if parsed or all(not arr for arr in arrays):
                return parsed
            raise ValueError("respuesta de cargas incompleta")
        except Exception as exc:
            failures.append(str(exc))
    if errors is not None:
        errors.append(f"{method} {name}: " + "; ".join(dict.fromkeys(failures)))
    return []


def _frame_distributed_loads(frame_obj: Any, name: str, errors: list[str] | None = None) -> list[dict[str, Any]]:
    return _read_load_assignments(frame_obj, "GetLoadDistributed", name, _parse_distributed_load_result, errors)


def _parse_distributed_load_result(name: str, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, (list, tuple)):
        return []
    arrays = [list(item) for item in result if isinstance(item, (list, tuple))]
    if not arrays:
        return []
    count = max((len(arr) for arr in arrays), default=0)
    if count == 0:
        return []
    string_arrays = [
        arr for arr in arrays if arr and all(isinstance(v, str) for v in arr)
    ]
    numeric_arrays = [
        arr
        for arr in arrays
        if arr and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in arr)
    ]
    frames_col = next(
        (arr for arr in string_arrays if len(arr) == count), [name] * count
    )
    patterns_col = next(
        (arr for arr in string_arrays[1:] if len(arr) == count), [""] * count
    )
    type_col = next(
        (arr for arr in numeric_arrays if len(arr) == count), [0] * count
    )
    dir_col = next(
        (arr for arr in numeric_arrays[1:] if len(arr) == count), [0] * count
    )
    remaining_numeric = [arr for arr in numeric_arrays if len(arr) == count][2:]
    dist1 = remaining_numeric[0] if len(remaining_numeric) > 0 else [0.0] * count
    dist2 = remaining_numeric[1] if len(remaining_numeric) > 1 else [0.0] * count
    val1 = remaining_numeric[-2] if len(remaining_numeric) >= 2 else [0.0] * count
    val2 = remaining_numeric[-1] if len(remaining_numeric) >= 1 else [0.0] * count
    rows = []
    for i in range(count):
        rows.append(
            {
                "frame": frames_col[i] if i < len(frames_col) else name,
                "pattern": patterns_col[i] if i < len(patterns_col) else "",
                "type": "Force"
                if (type_col[i] if i < len(type_col) else 1) == 1
                else "Moment",
                "dir": dir_col[i] if i < len(dir_col) else "",
                "dist1": dist1[i] if i < len(dist1) else "",
                "dist2": dist2[i] if i < len(dist2) else "",
                "val1": val1[i] if i < len(val1) else "",
                "val2": val2[i] if i < len(val2) else "",
            }
        )
    if len(string_arrays) >= 3:
        for i, row in enumerate(rows):
            row["csys"] = string_arrays[2][i] if i < len(string_arrays[2]) else ""
    return rows


def _frame_point_loads(frame_obj: Any, name: str, errors: list[str] | None = None) -> list[dict[str, Any]]:
    return _read_load_assignments(frame_obj, "GetLoadPoint", name, _parse_point_load_result, errors)


def _parse_point_load_result(name: str, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, (list, tuple)):
        return []
    arrays = [list(item) for item in result if isinstance(item, (list, tuple))]
    if not arrays:
        return []
    count = max((len(arr) for arr in arrays), default=0)
    if count == 0:
        return []
    string_arrays = [
        arr for arr in arrays if arr and all(isinstance(v, str) for v in arr)
    ]
    numeric_arrays = [
        arr
        for arr in arrays
        if arr and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in arr)
    ]
    frames_col = next(
        (arr for arr in string_arrays if len(arr) == count), [name] * count
    )
    patterns_col = next(
        (arr for arr in string_arrays[1:] if len(arr) == count), [""] * count
    )
    type_col = next(
        (arr for arr in numeric_arrays if len(arr) == count), [0] * count
    )
    dir_col = next(
        (arr for arr in numeric_arrays[1:] if len(arr) == count), [0] * count
    )
    remaining_numeric = [arr for arr in numeric_arrays if len(arr) == count][2:]
    dist = remaining_numeric[0] if len(remaining_numeric) > 0 else [0.0] * count
    val = remaining_numeric[-1] if len(remaining_numeric) >= 1 else [0.0] * count
    rows = []
    for i in range(count):
        rows.append(
            {
                "frame": frames_col[i] if i < len(frames_col) else name,
                "pattern": patterns_col[i] if i < len(patterns_col) else "",
                "type": "Force"
                if (type_col[i] if i < len(type_col) else 1) == 1
                else "Moment",
                "dir": dir_col[i] if i < len(dir_col) else "",
                "dist": dist[i] if i < len(dist) else "",
                "val": val[i] if i < len(val) else "",
            }
        )
    if len(string_arrays) >= 3:
        for i, row in enumerate(rows):
            row["csys"] = string_arrays[2][i] if i < len(string_arrays[2]) else ""
    return rows


def _frame_distributed_loads_from_database(
    model: Any, errors: list[str]
) -> dict[str, list[dict[str, Any]]] | None:
    """Batch read of frame distributed loads via DatabaseTables (one COM
    call for the whole model) instead of FrameObj.GetLoadDistributed per
    frame (Fase 17). Column names are guesses based on SAP2000's standard
    "Frame Loads - Distributed" table — NOT confirmed against a real
    instance, see plan. All-or-nothing: if pattern/type/dir/dist/val columns
    aren't all found by exact name, returns None and the caller falls back
    to the per-frame API loop untouched (never partially-guessed load
    values). A frame simply absent from the returned dict has no loads —
    only None means "table unusable, use the API loop"."""
    with all_load_patterns_displayed(model, errors):
        tables = _database_geometry_tables(
            model, _aliases_for("frame_loads_distributed"), ["Frame", "Object", "Obj", "Name"], errors,
        )
    result: dict[str, list[dict[str, Any]]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        pattern_idx = _find_any_column(columns, ["LoadPat", "Load Pattern", "Pattern"])
        type_idx = _find_any_column(columns, ["Type", "LoadType"])
        dir_idx = _find_any_column(columns, ["Dir", "Direction"])
        dist1_idx = _find_any_column(columns, ["RelDist1", "RelDistA"])
        dist2_idx = _find_any_column(columns, ["RelDist2", "RelDistB"])
        val1_idx = _find_any_column(columns, ["Val1", "FOverLA"])
        val2_idx = _find_any_column(columns, ["Val2", "FOverLB"])
        required = [name_idx, pattern_idx, type_idx, dir_idx, dist1_idx, dist2_idx, val1_idx, val2_idx]
        if any(idx is None for idx in required):
            continue
        csys_idx = _find_any_column(columns, ["CSys", "CoordSys", "Coordinate System"])
        matched = True
        for row in rows:
            if max(required) >= len(row):
                continue
            name = str(row[name_idx])
            result.setdefault(name, []).append({
                "frame": name,
                "pattern": row[pattern_idx],
                "type": row[type_idx],
                "dir": row[dir_idx],
                "dist1": row[dist1_idx],
                "dist2": row[dist2_idx],
                "val1": row[val1_idx],
                "val2": row[val2_idx],
            })
            if csys_idx is not None:
                result[name][-1]["csys"] = row[csys_idx] if csys_idx < len(row) else ""
    return result if matched else None


def _frame_point_loads_from_database(
    model: Any, errors: list[str]
) -> dict[str, list[dict[str, Any]]] | None:
    """Batch equivalent of _frame_point_loads (Fase 17) — same all-or-nothing
    guarantee as _frame_distributed_loads_from_database, columns guessed
    from SAP2000's standard "Frame Loads - Point" table."""
    with all_load_patterns_displayed(model, errors):
        tables = _database_geometry_tables(
            model, _aliases_for("frame_loads_point"), ["Frame", "Object", "Obj", "Name"], errors,
        )
    result: dict[str, list[dict[str, Any]]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Frame", "Object", "Obj", "Name"])
        pattern_idx = _find_any_column(columns, ["LoadPat", "Load Pattern", "Pattern"])
        type_idx = _find_any_column(columns, ["Type", "LoadType"])
        dir_idx = _find_any_column(columns, ["Dir", "Direction"])
        dist_idx = _find_any_column(columns, ["RelDist"])
        val_idx = _find_any_column(columns, ["Val", "FOverL"])
        required = [name_idx, pattern_idx, type_idx, dir_idx, dist_idx, val_idx]
        if any(idx is None for idx in required):
            continue
        csys_idx = _find_any_column(columns, ["CSys", "CoordSys", "Coordinate System"])
        matched = True
        for row in rows:
            if max(required) >= len(row):
                continue
            name = str(row[name_idx])
            result.setdefault(name, []).append({
                "frame": name,
                "pattern": row[pattern_idx],
                "type": row[type_idx],
                "dir": row[dir_idx],
                "dist": row[dist_idx],
                "val": row[val_idx],
            })
            if csys_idx is not None:
                result[name][-1]["csys"] = row[csys_idx] if csys_idx < len(row) else ""
    return result if matched else None


# ── Joint force loads ──────────────────────────────────────────────


def _joint_force_loads_from_database(
    model: Any, errors: list[str]
) -> dict[str, list[dict[str, Any]]] | None:
    """Batch equivalent of _joint_force_loads (Fase 17) — same all-or-nothing
    guarantee, columns guessed from SAP2000's standard "Joint Loads - Force"
    table (Joint, LoadPat, F1, F2, F3, M1, M2, M3)."""
    with all_load_patterns_displayed(model, errors):
        tables = _database_geometry_tables(
            model, _aliases_for("joint_loads_force"), ["Joint", "Point", "Object", "Obj", "Name"], errors,
        )
    result: dict[str, list[dict[str, Any]]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Joint", "Point", "Object", "Obj", "Name"])
        pattern_idx = _find_any_column(columns, ["LoadPat", "Load Pattern", "Pattern"])
        force_idx = [_find_any_column(columns, [label]) for label in ("F1", "F2", "F3", "M1", "M2", "M3")]
        required = [name_idx, pattern_idx, *force_idx]
        if any(idx is None for idx in required):
            continue
        csys_idx = _find_any_column(columns, ["CSys", "CoordSys", "Coordinate System"])
        matched = True
        for row in rows:
            if max(required) >= len(row):
                continue
            name = str(row[name_idx])
            forces = [float(row[idx]) if _is_numeric_text(row[idx]) else row[idx] for idx in force_idx]
            result.setdefault(name, []).append({
                "joint": name,
                "pattern": row[pattern_idx],
                "forces": forces,
            })
            if csys_idx is not None:
                result[name][-1]["csys"] = row[csys_idx] if csys_idx < len(row) else ""
    return result if matched else None


def _is_numeric_text(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _joint_force_loads(point_obj: Any, name: str, errors: list[str] | None = None) -> list[dict[str, Any]]:
    return _read_load_assignments(point_obj, "GetLoadForce", name, _parse_joint_force_result, errors)


def _parse_joint_force_result(name: str, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, (list, tuple)):
        return []
    arrays = [list(item) for item in result if isinstance(item, (list, tuple))]
    if not arrays:
        return []
    count = max(len(arr) for arr in arrays)
    string_arrays = [
        arr for arr in arrays if arr and all(isinstance(v, str) for v in arr)
    ]
    numeric_arrays = [
        arr
        for arr in arrays
        if arr and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in arr)
    ]
    joints = next(
        (arr for arr in string_arrays if len(arr) == count), [name] * count
    )
    patterns = next(
        (arr for arr in string_arrays[1:] if len(arr) == count), [""] * count
    )
    # LCStep may precede F1..M3 in the real OAPI signature.
    force_arrays = [arr for arr in numeric_arrays if len(arr) == count][-6:]
    while len(force_arrays) < 6:
        force_arrays.append([0.0] * count)
    rows = []
    for i in range(count):
        rows.append(
            {
                "joint": joints[i] if i < len(joints) else name,
                "pattern": patterns[i] if i < len(patterns) else "",
                "forces": [
                    force_arrays[k][i] if i < len(force_arrays[k]) else 0.0
                    for k in range(6)
                ],
            }
        )
    if len(string_arrays) >= 3:
        for i, row in enumerate(rows):
            row["csys"] = string_arrays[2][i] if i < len(string_arrays[2]) else ""
    return rows


# ── Joint restraints (supports) ──────────────────────────────────────


def _joint_restraint_data(point_obj: Any, name: str) -> list[bool] | None:
    """Read a joint's restraint DOFs via PointObj.GetRestraint.

    Returns a 6-bool list [U1, U2, U3, R1, R2, R3] (same DOF order used by
    frame releases and by SetRestraint in model_writer.py), or None when the
    joint has no restraint data (free joint / call not supported).
    """
    func = getattr(point_obj, "GetRestraint", None)
    if not callable(func):
        return None
    for call in (
        lambda: func(name),
        lambda: func(name, [False] * 6),
    ):
        try:
            result = call()
        except Exception:
            continue
        if not isinstance(result, (list, tuple)):
            continue
        bool_arrays = [
            [bool(v) for v in item]
            for item in result
            if isinstance(item, (list, tuple)) and len(item) == 6
            and all(isinstance(v, (bool, int)) for v in item)
        ]
        if bool_arrays:
            return bool_arrays[0]
    return None


def _table_bool(value: Any) -> bool:
    """SAP2000's DatabaseTables API returns everything as strings; boolean
    fields use the GUI's "Yes"/"No" convention."""
    return str(value).strip().casefold() in {"yes", "true", "1", "y"}


def _joint_restraints_from_database(model: Any, errors: list[str]) -> dict[str, list[bool]] | None:
    """Batch read of joint restraints via DatabaseTables (one COM call for
    every joint) instead of PointObj.GetRestraint per joint (Fase 17 — see
    plan). Only trusted if the joint name column AND all 6 DOF columns are
    found by exact name; otherwise returns None and the caller falls back to
    the per-joint API loop untouched — never partially-guessed data.

    Returns a dict covering EVERY joint found in the table (including
    unrestrained ones, as all-False) when the table matched, so the caller
    never needs to fall back per-joint just because a joint has no
    restraint — only None signals "table unusable, use the API loop"."""
    tables = _database_geometry_tables(
        model, _aliases_for("joint_restraints"), ["Joint", "Point", "Object", "Obj", "Name"], errors,
    )
    dof_labels = ["U1", "U2", "U3", "R1", "R2", "R3"]
    result: dict[str, list[bool]] = {}
    matched = False
    for columns, rows in tables:
        name_idx = _find_name_column(columns, ["Joint", "Point", "Object", "Obj", "Name"])
        dof_idx = [_find_any_column(columns, [label]) for label in dof_labels]
        if name_idx is None or any(idx is None for idx in dof_idx):
            continue
        matched = True
        for row in rows:
            if name_idx >= len(row) or max(dof_idx) >= len(row):
                continue
            result[str(row[name_idx])] = [_table_bool(row[idx]) for idx in dof_idx]
    return result if matched else None


# ── Constraints (Body/Diaphragm/Equal/etc.) ──────────────────────────


def _constraint_names_and_types(model: Any) -> list[tuple[str, str]]:
    """List constraint definitions via ConstraintDef.GetNameList +
    GetConstraint(name). Each constraint is read independently — one bad
    constraint never aborts the rest, same as the rest of this module."""
    constraint_def = getattr(model, "ConstraintDef", None)
    if constraint_def is None:
        return []
    try:
        names = _names(constraint_def.GetNameList())
    except Exception:
        return []
    result: list[tuple[str, str]] = []
    for name in names:
        c_type = ""
        try:
            ret = constraint_def.GetConstraint(name, "")
            if isinstance(ret, (list, tuple)):
                strings = [str(v) for v in ret if isinstance(v, str) and v]
                c_type = strings[0] if strings else ""
        except Exception:
            c_type = ""
        result.append((str(name), c_type or "Unknown"))
    return result


def _constraint_joints(model: Any, constraint_name: str) -> list[str]:
    """Joints assigned to a constraint via ConstraintDef.GetJoints(Name,
    NumberItems, PointName(), CSys). Returns [] on any failure — a
    constraint with unreadable joints is reported with an empty list, not
    fabricated data."""
    constraint_def = getattr(model, "ConstraintDef", None)
    if constraint_def is None:
        return []
    func = getattr(constraint_def, "GetJoints", None)
    if not callable(func):
        return []
    for call in (
        lambda: func(constraint_name, 0, [], "Global"),
        lambda: func(constraint_name, 0, []),
        lambda: func(constraint_name),
    ):
        try:
            result = call()
        except Exception:
            continue
        if not isinstance(result, (list, tuple)):
            continue
        string_arrays = [
            list(item)
            for item in result
            if isinstance(item, (list, tuple)) and item and all(isinstance(v, str) for v in item)
        ]
        if string_arrays:
            return [str(v) for v in string_arrays[0]]
    return []


# ── Area geometry ──────────────────────────────────────────────────


def _area_points_from_database(
    model: Any, errors: list[str]
) -> dict[str, list[str]] | None:
    """Batch read of area (shell) connectivity via DatabaseTables (one COM
    call for the whole model) instead of AreaObj.GetPoints per area.

    Particularidad: la tabla de conectividad de areas trae UNA FILA POR
    (area, punto), no una fila por area — hay que agrupar por area y
    ORDENAR los puntos. El orden define el poligono: si los vertices salen
    barajados, el area se dibuja como un "moño" (poligono cruzado), un
    fallo visual silencioso, no una excepcion. Por eso el contrato
    todo-o-nada aqui es mas estricto que en las demas *_from_database: si la
    tabla no trae una columna de INDICE DE PUNTO reconocible (para poder
    ordenar), se devuelve None y el caller cae al loop por-objeto — mejor
    lento que dibujar poligonos cruzados. Ver `_frame_distributed_loads_from_
    database` para el precedente de agrupacion multi-fila (ahi el orden de
    las filas no importa; aqui si).

    Columnas asumidas (candidatas, NO confirmadas contra un SAP2000 real,
    ver table_aliases.py "area_connectivity"): columna de nombre de area,
    columna de indice de punto (posicion del vertice en el poligono,
    1-based o 0-based, cualquiera sirve porque solo se usa para ordenar), y
    columna de nombre de joint/punto.

    Returns dict[area_name] -> lista ordenada de nombres de joint, para
    cada area encontrada en la tabla. Solo None significa "tabla
    inutilizable, usar el loop por-objeto"."""
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return None
    try:
        available = _available_database_tables(db)
    except Exception as exc:
        errors.append(f"DatabaseTables.GetAvailableTables: {exc}")
        return None
    keys = _match_database_table_keys(_aliases_for("area_connectivity"), available) or _aliases_for(
        "area_connectivity"
    )

    grouped: dict[str, list[tuple[float, str]]] = {}
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

        area_idx = _find_name_column(columns, ["Area", "Object", "Obj", "Name"])
        point_idx = _find_any_column(columns, ["Point", "Joint", "PointName", "JointName"])
        index_idx = _find_any_column(
            columns, ["PointIndex", "PointNum", "Point Number", "NumberItem", "PointOrder"]
        )
        required = [area_idx, point_idx, index_idx]
        if any(idx is None for idx in required):
            # Sin columna de indice reconocible no se puede garantizar el
            # orden del poligono: no hay fallback parcial aqui, se sigue
            # probando otras claves candidatas pero esta tabla no cuenta.
            continue
        matched = True

        for row in rows:
            if max(required) >= len(row):
                continue
            area = str(row[area_idx]).strip()
            point = str(row[point_idx]).strip()
            if not area or not point:
                continue
            order_raw = row[index_idx]
            order = float(order_raw) if _is_number(order_raw) or _is_numeric_str(order_raw) else 0.0
            grouped.setdefault(area, []).append((order, point))

    if not matched:
        return None

    result: dict[str, list[str]] = {}
    for area, pairs in grouped.items():
        pairs.sort(key=lambda item: item[0])
        result[area] = [point for _order, point in pairs]
    return result


def _is_numeric_str(value: Any) -> bool:
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _area_points(area_obj: Any, name: str) -> list[str]:
    func = getattr(area_obj, "GetPoints", None)
    if not callable(func):
        return []
    for call in (lambda: func(name), lambda: func(name, 0, [])):
        try:
            result = call()
        except Exception:
            continue
        if isinstance(result, (list, tuple)):
            string_arrays = [
                list(item)
                for item in result
                if isinstance(item, (list, tuple))
                and item
                and all(isinstance(v, str) for v in item)
            ]
            if string_arrays:
                return [str(p) for p in string_arrays[0]]
    return []


# ── Internal alias helper ──────────────────────────────────────────


def _aliases_for(kind: str) -> list[str]:
    from bridge.viewer.sap2000.table_aliases import aliases_for
    return aliases_for(kind)

