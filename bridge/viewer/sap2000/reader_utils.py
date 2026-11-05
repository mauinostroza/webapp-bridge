# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _table(name: str, columns: list[str], rows: list[list[Any]]) -> dict:
    return {"name": name, "columns": columns, "rows": rows, "rows_flat": [str(row[-1]) for row in rows]}


def _data_values(table: dict) -> list[str]:
    values = []
    for row in table.get("rows", []):
        if row and str(row[0]).lower() != "total":
            values.append(str(row[0]))
    return values


def _empty_table(name: str, columns: list[str]) -> dict:
    safe_columns = columns if columns else ["Message"]
    row = [""] * len(safe_columns)
    row[-1] = "Sin datos devueltos por SAP2000 para esta consulta. Verifica que el modelo tenga datos de este tipo o prueba otra lectura."
    return _table(name, safe_columns, [row])


def _name_table(name: str, func, limit: int | None = None) -> dict:
    names = _names(func())
    rows = [["Total", len(names)]] + [[item, ""] for item in names]
    if limit:
        rows = rows[:limit]
    return _table(name, ["Dato", "Cantidad"], rows)


def _name_table_from_sources(
    model: Any,
    name: str,
    api_func,
    database_name_fields: list[str],
    limit: int | None = None,
) -> dict:
    source = "API"
    errors: list[str] = []
    try:
        names = _names(api_func())
    except Exception as exc:
        names = []
        errors.append(f"API: {exc}")

    if not names:
        db_names, db_errors = _database_named_values(
            model,
            _aliases_for(name),
            database_name_fields,
        )
        if db_names:
            names = db_names
            source = "DatabaseTables"
        errors.extend(db_errors)

    rows = [["Total", len(names)]] + [[item, ""] for item in names]
    if not names and errors:
        rows.append(["Aviso", "; ".join(errors[:6])])
    if limit:
        rows = rows[:limit]
    table = _table(name, ["Dato", "Cantidad"], rows)
    table["metadata"] = {"source": source, "errors": errors}
    return table


def _last(value: Any) -> Any:
    if isinstance(value, tuple):
        return value[-1]
    return value


def _safe_value(func) -> Any:
    try:
        return _last(func())
    except Exception as exc:
        return f"No disponible: {exc}"


def _count_names(func) -> int:
    try:
        return len(_names(func()))
    except Exception:
        return 0


def _first_matching_table(available: list[str], candidates: list[str]) -> str:
    matches = _match_database_table_keys(candidates, available)
    return matches[0] if matches else ""


def _names(result: Any) -> list[str]:
    if isinstance(result, (list, tuple)):
        if len(result) >= 2 and isinstance(result[0], int):
            parsed = _names(result[1])
            if parsed:
                return parsed
        if result and all(isinstance(item, str) for item in result):
            return [str(item) for item in result]
        for item in result:
            if isinstance(item, (list, tuple)) and all(isinstance(x, str) for x in item):
                return [str(x) for x in item]
            if isinstance(item, str):
                parsed = _parse_names_literal(item)
                if parsed:
                    return parsed
        for item in reversed(result):
            if isinstance(item, (list, tuple)):
                return [str(x) for x in item]
            if isinstance(item, str):
                parsed = _parse_names_literal(item)
                if parsed:
                    return parsed
                return [item]
        return []
    if isinstance(result, str):
        parsed = _parse_names_literal(result)
        if parsed:
            return parsed
        if _looks_like_count_or_return_code(result):
            return []
        return [result]
    return []


def _parse_names_literal(value: str) -> list[str]:
    text = str(value or "").strip()
    if not (text.startswith("(") or text.startswith("[")) or "'" not in text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return []
    if isinstance(parsed, (list, tuple)) and all(isinstance(item, str) for item in parsed):
        return [str(item) for item in parsed]
    return []


def _looks_like_count_or_return_code(value: str) -> bool:
    text = str(value or "").strip()
    return text.isdigit() or (text.startswith("-") and text[1:].isdigit())


def _get_names(obj: Any) -> list[str]:
    try:
        names = _names(obj.GetNameList())
        if names:
            return names
    except TypeError:
        pass
    except Exception:
        raise

    attempts = [
        (0, []),
        (0, tuple()),
        ([],),
    ]
    last_error: Exception | None = None
    for args in attempts:
        try:
            names = _names(obj.GetNameList(*args))
            if names:
                return names
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def _selection_rows(result: Any) -> list[list[str]]:
    if not isinstance(result, tuple):
        return []
    arrays = [x for x in result if isinstance(x, (list, tuple))]
    if len(arrays) >= 2:
        return [[str(t), str(n)] for t, n in zip(arrays[0], arrays[1])]
    return []


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalize_label(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _find_name_column(columns: list[str], candidates: list[str]) -> int | None:
    normalized = {_normalize_label(column): index for index, column in enumerate(columns)}
    for candidate in candidates:
        index = normalized.get(_normalize_label(candidate))
        if index is not None:
            return index
    for index, column in enumerate(columns):
        text = _normalize_label(column)
        if text in {"name", "loadpat", "loadpattern", "loadcase", "case", "combo", "combination"}:
            return index
    if len(columns) == 1:
        return 0
    return None


def _find_any_column(columns: list[str], candidates: list[str]) -> int | None:
    normalized = {_normalize_label(column): index for index, column in enumerate(columns)}
    for candidate in candidates:
        index = normalized.get(_normalize_label(candidate))
        if index is not None:
            return index
    return None


def _matching_column_indexes(columns: list[str], candidates: list[str]) -> list[int]:
    normalized_candidates = {_normalize_label(candidate) for candidate in candidates}
    indexes: list[int] = []
    for index, column in enumerate(columns):
        if _normalize_label(column) in normalized_candidates:
            indexes.append(index)
    return indexes


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _is_diagnostic_table(table: dict) -> bool:
    return table.get("columns") == ["Dato", "Cantidad", "Valor"]


def _raw_count(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1


def _first_int(values: list[Any]) -> int | None:
    for value in values:
        if isinstance(value, int):
            return value
    return None


def _last_int(values: list[Any]) -> int | None:
    for value in reversed(values):
        if isinstance(value, int):
            return value
    return None


# ── Database table helpers ──────────────────────────────────────────


def _available_database_tables(db: Any) -> list[str]:
    for call in (
        lambda: db.GetAvailableTables(),
        lambda: db.GetAvailableTables(0, [], [], []),
        lambda: db.GetAllTables(),
    ):
        try:
            names = _names(call())
            if names:
                return names
        except Exception:
            continue
    return []


def _match_database_table_keys(candidate_keys: list[str], available: list[str]) -> list[str]:
    if not available:
        return []
    normalized = {item.lower(): item for item in available}
    matches: list[str] = []
    for key in candidate_keys:
        exact = normalized.get(key.lower())
        if exact:
            matches.append(exact)
            continue
        key_tokens = [token for token in key.lower().replace("-", " ").split() if len(token) > 2]
        for available_key in available:
            text = available_key.lower()
            if all(token in text for token in key_tokens[:3]):
                matches.append(available_key)
                break
    return _unique(matches)


def _get_database_table_for_display(db: Any, key: str) -> tuple[list[str], list[list[Any]]] | None:
    attempts = [
        lambda: db.GetTableForDisplayArray(key, "", "ALL"),
        lambda: db.GetTableForDisplayArray(key, [], "ALL", 0, [], 0, []),
        lambda: db.GetTableForDisplayArray(key, [], "", 0, [], 0, []),
        lambda: db.GetTableForDisplayArray(key, "", "", 0, [], 0, []),
    ]
    last: Any = None
    for call in attempts:
        try:
            result = call()
        except Exception as exc:
            last = exc
            continue
        parsed = _parse_database_table_result(result)
        if parsed is not None:
            return parsed
        last = result
    if isinstance(last, Exception):
        raise last
    return None


def _parse_database_table_result(result: Any) -> tuple[list[str], list[list[Any]]] | None:
    if not isinstance(result, (tuple, list)):
        return None
    values = list(result)
    arrays = [list(item) for item in values if isinstance(item, (list, tuple))]
    if len(arrays) < 2:
        return None

    name_array = _pick_field_names_array(arrays)
    if name_array is None:
        return None
    columns = [str(item) for item in name_array]

    flat_data = _pick_flat_table_data(arrays, len(columns), exclude=name_array)
    if not flat_data:
        return columns, []

    rows = []
    width = len(columns)
    for index in range(0, len(flat_data), width):
        chunk = flat_data[index : index + width]
        if len(chunk) == width:
            rows.append(chunk)
    return columns, rows


def _pick_field_names_array(arrays: list[list[Any]]) -> list[Any] | None:
    """Returns the actual array object (not a copy) chosen as the column-name
    echo, so callers can exclude it by identity when picking the flat data
    array — needed because for a 1-row table both arrays have equal length
    and can't be told apart by length alone (see _pick_flat_table_data)."""
    for array in arrays:
        if array and all(isinstance(item, str) for item in array):
            lowered = [item.lower() for item in array]
            if any(item in lowered for item in ("outputcase", "loadcase", "case", "frame", "object", "obj", "element", "elm")):
                return array
    for array in arrays:
        if array and all(isinstance(item, str) for item in array):
            return array
    return None


def _pick_field_names(arrays: list[list[Any]]) -> list[str]:
    array = _pick_field_names_array(arrays)
    return [str(item) for item in array] if array is not None else []


def _pick_flat_table_data(arrays: list[list[Any]], width: int, exclude: list[Any] | None = None) -> list[Any]:
    # len(array) >= width (not strict >) so a genuine 1-row table (data array
    # the same length as the column-name array) isn't discarded — `exclude`
    # (the actual column-name array, by identity) is what tells the two
    # apart in that case, not length.
    candidates = [
        array
        for array in arrays
        if array is not exclude and len(array) >= width and len(array) % width == 0
    ]
    if not candidates:
        return []
    return max(candidates, key=len)


def _database_named_values(
    model: Any,
    candidate_keys: list[str],
    name_fields: list[str],
) -> tuple[list[str], list[str]]:
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return [], ["DatabaseTables no disponible"]

    available = _available_database_tables(db)
    keys = _match_database_table_keys(candidate_keys, available) or candidate_keys
    errors: list[str] = []
    values: list[str] = []
    for key in keys:
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            continue
        if not parsed:
            errors.append(f"{key}: sin datos parseables")
            continue
        columns, rows = parsed
        column_index = _find_name_column(columns, name_fields)
        if column_index is None:
            errors.append(f"{key}: columna de nombre no encontrada")
            continue
        for row in rows:
            if column_index < len(row) and str(row[column_index]).strip():
                values.append(str(row[column_index]).strip())
        if values:
            return _unique(values), errors
        errors.append(f"{key}: 0 nombres")
    return [], errors


# ── Result table filtering helpers ─────────────────────────────────


def _filter_table_by_result(table: dict, result_filter: str | None) -> dict:
    if not result_filter or _is_diagnostic_table(table):
        return table
    tokens = _result_filter_tokens(result_filter)
    if not tokens or len(tokens) != 1:
        return table
    token = tokens[0]
    selected = token.split(":", 1)[1].strip().casefold() if ":" in token else token.strip().casefold()
    if not selected:
        return table
    columns = [str(column) for column in table.get("columns", [])]
    result_index = _find_name_column(columns, ["OutputCase", "LoadCase", "Case", "Combo", "Combination"])
    if result_index is None:
        return table
    rows = [
        row
        for row in table.get("rows", [])
        if result_index < len(row) and str(row[result_index]).strip().casefold() == selected
    ]
    filtered = dict(table)
    filtered["rows"] = rows
    metadata = dict(filtered.get("metadata", {}))
    metadata["result_filter_applied"] = result_filter
    filtered["metadata"] = metadata
    if rows:
        filtered["rows_flat"] = [str(row[-1]) for row in rows]
        return filtered
    return _diagnostic_result_table(
        str(table.get("name", "results")),
        "",
        metadata,
        f"No hubo filas para el filtro {result_filter}.",
    )


def _finalize_result_table(
    table: dict,
    result_filter: str | None,
    object_name: str | None,
    object_columns: list[str],
    limit: int | None,
) -> dict:
    table = _filter_table_by_result(table, result_filter)
    table = _filter_table_by_object(table, object_name, object_columns)
    table = _limit_table_rows(table, limit)
    if not _is_diagnostic_table(table):
        _refresh_result_metadata(table)
    return table


def _filter_table_by_object(table: dict, object_name: str | None, column_candidates: list[str]) -> dict:
    if not object_name or _is_diagnostic_table(table):
        return table
    selected = object_name.strip().casefold()
    if not selected:
        return table
    columns = [str(column) for column in table.get("columns", [])]
    indexes = _matching_column_indexes(columns, column_candidates)
    if not indexes:
        return _diagnostic_result_table(
            str(table.get("name", "results")),
            "",
            dict(table.get("metadata", {})),
            f"No se encontro una columna compatible para filtrar por {object_name}.",
        )

    rows = [
        row
        for row in table.get("rows", [])
        if any(index < len(row) and str(row[index]).strip().casefold() == selected for index in indexes)
    ]
    filtered = dict(table)
    filtered["rows"] = rows
    filtered["rows_flat"] = [str(row[-1]) for row in rows]
    metadata = dict(filtered.get("metadata", {}))
    metadata["object_filter"] = object_name
    metadata["object_filter_columns"] = [columns[index] for index in indexes]
    metadata["row_count_before_object_filter"] = len(table.get("rows", []))
    filtered["metadata"] = metadata
    if rows:
        return filtered
    return _diagnostic_result_table(
        str(table.get("name", "results")),
        "",
        metadata,
        f"No hubo filas para el identificador {object_name}.",
    )


def _filter_table_by_group(table: dict, model: Any, group_name: str | None, action: str) -> dict:
    if not group_name or _is_diagnostic_table(table):
        return table
    group = group_name.strip()
    if not group:
        return table

    from bridge.viewer.sap2000.reader_geometry import _group_memberships, _allowed_group_objects

    errors: list[str] = []
    memberships = _group_memberships(model, errors)
    group_members = {name for name, groups in memberships.items() if any(item.casefold() == group.casefold() for item in groups)}
    if not group_members:
        metadata = dict(table.get("metadata", {}))
        metadata["group_filter"] = group
        metadata["group_filter_errors"] = errors
        return _diagnostic_result_table(
            str(table.get("name", "results")),
            "",
            metadata,
            f"No se encontraron objetos asignados al grupo {group}.",
        )

    allowed_names = _allowed_group_objects(model, group, action, group_members)
    columns = [str(column) for column in table.get("columns", [])]
    if action in {"frame_forces", "frame_geometry", "model_geometry", "geometry_3d"}:
        indexes = _matching_column_indexes(columns, ["Frame", "Obj", "Object", "Elm", "Element"])
    elif action in {"joint_displacements", "joint_reactions", "joint_coordinates"}:
        indexes = _matching_column_indexes(columns, ["Joint", "Obj", "Object", "Elm", "Element", "IEnd", "JEnd"])
    else:
        indexes = []

    if not indexes:
        metadata = dict(table.get("metadata", {}))
        metadata["group_filter"] = group
        metadata["group_filter_errors"] = errors
        return _diagnostic_result_table(
            str(table.get("name", "results")),
            "",
            metadata,
            f"No se encontro una columna compatible para filtrar por el grupo {group}.",
        )

    allowed_casefold = {name.casefold() for name in allowed_names}
    rows = [
        row
        for row in table.get("rows", [])
        if any(index < len(row) and str(row[index]).strip().casefold() in allowed_casefold for index in indexes)
    ]
    filtered = dict(table)
    filtered["rows"] = rows
    filtered["rows_flat"] = [str(row[-1]) for row in rows]
    metadata = dict(filtered.get("metadata", {}))
    metadata["group_filter"] = group
    metadata["group_members"] = sorted(group_members)
    metadata["group_allowed_objects"] = sorted(allowed_names)
    metadata["group_filter_columns"] = [columns[index] for index in indexes]
    metadata["row_count_before_group_filter"] = len(table.get("rows", []))
    if errors:
        metadata["group_filter_errors"] = errors
    filtered["metadata"] = metadata
    if rows:
        return filtered
    return _diagnostic_result_table(
        str(table.get("name", "results")),
        "",
        metadata,
        f"No hubo filas para el grupo {group}.",
    )


def _limit_table_rows(table: dict, limit: int | None) -> dict:
    if not limit or _is_diagnostic_table(table):
        return table
    rows = list(table.get("rows", []))
    if len(rows) <= limit:
        return table
    limited = dict(table)
    limited["rows"] = rows[:limit]
    limited["rows_flat"] = [str(row[-1]) for row in rows[:limit]]
    metadata = dict(limited.get("metadata", {}))
    metadata["limited_to"] = limit
    metadata["row_count_before_limit"] = len(rows)
    limited["metadata"] = metadata
    return limited


def _refresh_result_metadata(table: dict) -> None:
    metadata = dict(table.get("metadata", {}))
    metadata["row_count"] = len(table.get("rows", []))
    metadata["columns"] = list(table.get("columns", []))
    table["metadata"] = metadata


def _diagnostic_result_table(
    name: str,
    raw_result: Any,
    setup: dict[str, Any],
    message: str,
    number_results: int | None = None,
    ret_code: int | None = None,
) -> dict:
    rows = [
        ["Mensaje", "", message],
        ["NumberResults", 1 if number_results is None else number_results, ""],
        ["SAP return", 1 if ret_code is None else ret_code, ""],
        ["Casos seleccionados", setup.get("selected_cases", 0), ""],
        ["Combinaciones seleccionadas", setup.get("selected_combinations", 0), ""],
        ["Errores de seleccion", len(setup.get("selection_errors", [])), "; ".join(setup.get("selection_errors", []))],
        ["Resultado crudo", _raw_count(raw_result), str(raw_result)],
    ]
    return _table(name, ["Dato", "Cantidad", "Valor"], rows)


# ── Result filter helpers ──────────────────────────────────────────


def _result_filter_tokens(result_filter: str | None) -> list[str]:
    if not result_filter:
        return []
    value = str(result_filter).strip()
    if value.startswith("MULTI:"):
        return [item.strip() for item in value.removeprefix("MULTI:").split("||") if item.strip()]
    return [item.strip() for item in value.split(";") if item.strip()]


def _safe_names(func) -> list[str]:
    try:
        return _names(func())
    except Exception:
        return []


def _contains_name(names: list[str], value: str) -> bool:
    return any(item.lower() == value.lower() for item in names)


def _combined_names(api_func, fallback_func) -> list[str]:
    names = _safe_names(api_func)
    if names:
        return names
    try:
        return fallback_func()
    except Exception:
        return []


def _aliases_for(kind: str) -> list[str]:
    from bridge.viewer.sap2000.table_aliases import aliases_for
    return aliases_for(kind)


# ── Patrones «para mostrar» (selección de visualización de SAP2000) ─────
#
# Las lecturas por lote de DatabaseTables (GetTableForDisplayArray) solo
# devuelven filas de los load patterns marcados «para mostrar» en SAP2000
# (DatabaseTables.SetLoadPatternsSelectedForDisplay). El camino por objeto
# (FrameObj.GetLoadDistributed...) sí devuelve todos. Este context manager
# muestra temporalmente todos los patrones mientras dura la lectura y restaura
# SIEMPRE la selección original al salir. Nunca lanza por sí mismo: cualquier
# fallo se anota en `errors` y la lectura continúa sin el cambio.


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unpack_selection_result(result: Any) -> list[str]:
    """Separa ``[ret, lista]`` de ``GetLoadPatternsSelectedForDisplay`` sin
    asumir el orden: exactamente un entero (código de retorno, 0 = correcto)
    y exactamente una tupla/lista (nombres). Cualquier otra forma es error."""
    if not isinstance(result, (list, tuple)):
        raise RuntimeError(f"respuesta inesperada: {result!r}")
    codes = [item for item in result if _is_plain_int(item)]
    arrays = [item for item in result if isinstance(item, (list, tuple))]
    if len(codes) != 1 or len(arrays) != 1:
        raise RuntimeError(f"respuesta inesperada: {result!r}")
    if codes[0] != 0:
        raise RuntimeError(f"código de retorno {codes[0]}")
    return [str(name) for name in arrays[0]]


def _single_return_code(result: Any) -> int:
    """Código de retorno de ``SetLoadPatternsSelectedForDisplay``, con el
    entero en cualquier posición (o el propio valor si no es tupla)."""
    if _is_plain_int(result):
        return result
    if isinstance(result, (list, tuple)):
        codes = [item for item in result if _is_plain_int(item)]
        if len(codes) == 1:
            return codes[0]
    raise RuntimeError(f"respuesta sin código de retorno: {result!r}")


def _read_display_selection(db: Any) -> list[str]:
    getter = db.GetLoadPatternsSelectedForDisplay
    last: Exception | None = None
    for call in (lambda: getter(), lambda: getter([])):
        try:
            return _unpack_selection_result(call())
        except Exception as exc:
            last = exc
    raise RuntimeError(f"GetLoadPatternsSelectedForDisplay: {last}")


class _SelectionRejected(RuntimeError):
    """SAP2000 devolvió código distinto de 0: la selección no cambió."""


def _write_display_selection(db: Any, names: list[str]) -> None:
    setter = db.SetLoadPatternsSelectedForDisplay
    last: Exception | None = None
    for payload in (list(names), tuple(names)):
        try:
            result = setter(payload)
        except Exception as exc:
            last = exc
            continue
        code = _single_return_code(result)
        if code != 0:
            raise _SelectionRejected(f"SetLoadPatternsSelectedForDisplay devolvió {code}")
        return
    raise RuntimeError(f"SetLoadPatternsSelectedForDisplay no aceptó la firma: {last}")


@contextmanager
def all_load_patterns_displayed(model: Any, errors: list[str]) -> Iterator[None]:
    """Muestra todos los load patterns durante la lectura y restaura la selección.

    Orden: lee la selección actual, la sustituye por todos los nombres de
    ``LoadPatterns.GetNameList`` y cede el control al cuerpo. Al salir
    (también si el cuerpo lanza) restaura la selección original, incluida una
    lista vacía. Las excepciones del cuerpo se propagan sin tocar; los fallos
    propios (sin API, modelo bloqueado, firma distinta) solo se anotan en
    ``errors`` y, si no se pudo leer la selección original, no se escribe nada.
    """
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        # Sin DatabaseTables no hay lectura por lote: nada que mostrar ni que restaurar.
        yield
        return
    original: list[str] | None = None
    attempted = False
    try:
        original = _read_display_selection(db)
        names = _get_names(model.LoadPatterns)
        if names:
            # Antes de escribir: una excepción a mitad de Set también obliga a restaurar.
            attempted = True
            _write_display_selection(db, names)
    except _SelectionRejected as exc:
        attempted = False  # SAP2000 no aplicó el cambio: nada que restaurar.
        errors.append(_display_warning(f"no se pudieron mostrar todos los patrones ({exc})"))
    except Exception as exc:
        errors.append(_display_warning(f"no se pudieron mostrar todos los patrones ({exc})"))
    try:
        yield
    finally:
        if attempted and original is not None:
            try:
                _write_display_selection(db, original)
            except Exception as exc:
                errors.append(
                    f"Patrones para mostrar: no se pudo restaurar la selección original ({exc}); "
                    "revisa Display > Show Load Patterns en SAP2000."
                )


def _display_warning(detail: str) -> str:
    return (
        f"Patrones para mostrar: {detail}; la lectura por lote solo trae los patrones "
        "seleccionados en SAP2000 (sin el cambio temporal)."
    )
