# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from typing import Any

from bridge.viewer.sap2000.reader_utils import (
    _table,
    _names,
    _is_diagnostic_table,
    _diagnostic_result_table,
    _finalize_result_table,
    _first_int,
    _last_int,
    _available_database_tables,
    _match_database_table_keys,
    _get_database_table_for_display,
    _safe_names,
    _contains_name,
    _combined_names,
    _unique,
    _database_named_values,
    _find_name_column,
    _result_filter_tokens,
)
from bridge.viewer.sap2000.table_aliases import aliases_for, remember_alias
from bridge.viewer.sap2000.units import extract_result_units


# ── SAP Results API table ──────────────────────────────────────────


def _sap_results_table(
    name: str,
    func,
    item: str,
    labels: list[str],
    setup: dict[str, Any],
    limit: int | None = None,
    prefer_object: bool = False,
) -> dict:
    result = _call_result_function(func, item, prefer_object=prefer_object)
    if not isinstance(result, (tuple, list)):
        return _diagnostic_result_table(
            name, result, setup, "SAP2000 devolvio un resultado no tabular."
        )

    values = list(result)
    number_results = _first_int(values)
    ret_code = _last_int(values)
    arrays = [x for x in values if isinstance(x, (list, tuple))]
    max_len = max((len(a) for a in arrays), default=0)

    if not arrays or max_len == 0 or number_results == 0:
        return _diagnostic_result_table(
            name,
            values,
            setup,
            "SAP2000 no devolvio filas de resultados. Revisa que el analisis este ejecutado y que existan casos/combinaciones seleccionables.",
            number_results=number_results,
            ret_code=ret_code,
        )

    if len(arrays) < len(labels):
        return _diagnostic_result_table(
            name,
            values,
            setup,
            "SAP2000 devolvio menos arrays de los esperados "
            f"({len(arrays)} de {len(labels)}); se rechaza la tabla para no "
            "desalinear columnas con datos de otra magnitud.",
            number_results=number_results,
            ret_code=ret_code,
        )

    columns = labels[: len(arrays)]
    if len(columns) < len(arrays):
        columns.extend(f"Value {i + 1}" for i in range(len(columns), len(arrays)))

    rows = []
    for i in range(max_len):
        rows.append([a[i] if i < len(a) else "" for a in arrays])
    if limit:
        rows = rows[:limit]
    table = _table(name, columns, rows)
    table["metadata"] = {
        "source": "ResultsAPI",
        "number_results": number_results,
        "sap_return": ret_code,
        "row_count": len(rows),
        "columns": columns,
        **_truncation_metadata(number_results, len(rows)),
        **setup,
    }
    return table


def _sap_all_selected_results_table(
    name: str,
    func,
    labels: list[str],
    setup: dict[str, Any],
    limit: int | None = None,
) -> dict:
    """Like _sap_results_table, but for COM result functions with NO
    (Name, ItemTypeElm) input — they report all items already selected for
    output as a side effect of a prior selection call (e.g.
    Results.SectionCutAnalysis, which reports every section cut selected
    via Results.Setup.SetSectionCutSelectedForOutput/SelectAllSectionCutsForOutput)."""
    try:
        result = func()
    except Exception as exc:
        return _diagnostic_result_table(name, str(exc), setup, f"Error al leer resultados: {exc}")

    if not isinstance(result, (tuple, list)):
        return _diagnostic_result_table(
            name, result, setup, "SAP2000 devolvio un resultado no tabular."
        )

    values = list(result)
    number_results = _first_int(values)
    ret_code = _last_int(values)
    arrays = [x for x in values if isinstance(x, (list, tuple))]
    max_len = max((len(a) for a in arrays), default=0)

    if not arrays or max_len == 0 or number_results == 0:
        return _diagnostic_result_table(
            name,
            values,
            setup,
            "SAP2000 no devolvio resultados. Revisa que existan section cuts "
            "seleccionados para output y que el analisis este ejecutado.",
            number_results=number_results,
            ret_code=ret_code,
        )

    if len(arrays) < len(labels):
        return _diagnostic_result_table(
            name,
            values,
            setup,
            "SAP2000 devolvio menos arrays de los esperados "
            f"({len(arrays)} de {len(labels)}); se rechaza la tabla para no "
            "desalinear columnas con datos de otra magnitud.",
            number_results=number_results,
            ret_code=ret_code,
        )

    columns = labels[: len(arrays)]
    if len(columns) < len(arrays):
        columns.extend(f"Value {i + 1}" for i in range(len(columns), len(arrays)))

    rows = []
    for i in range(max_len):
        rows.append([a[i] if i < len(a) else "" for a in arrays])
    if limit:
        rows = rows[:limit]
    table = _table(name, columns, rows)
    table["metadata"] = {
        "source": "ResultsAPI",
        "number_results": number_results,
        "sap_return": ret_code,
        "row_count": len(rows),
        "columns": columns,
        **_truncation_metadata(number_results, len(rows)),
        **setup,
    }
    return table


def _truncation_metadata(number_results: int | None, row_count: int) -> dict[str, Any]:
    """Marca la tabla como truncada solo cuando number_results es un total
    fiable (entero positivo) y supera las filas que efectivamente quedaron
    tras el limite. Ante cualquier duda (None, 0, valor no fiable) NO marca
    truncamiento, para no des-informar con un "truncado" falso."""
    if isinstance(number_results, int) and number_results > 0 and number_results > row_count:
        return {"truncated": True, "total_results_available": number_results}
    return {"truncated": False}


def _call_result_function(func, item: str, prefer_object: bool = False) -> Any:
    item_types = (0, 2) if prefer_object else (2, 0)
    for item_type in item_types:
        try:
            result = func(item, item_type)
            if _has_result_rows(result):
                return result
            last_result = result
        except Exception as exc:
            last_result = exc
    return last_result


def _has_result_rows(result: Any) -> bool:
    if not isinstance(result, (tuple, list)):
        return False
    arrays = [x for x in result if isinstance(x, (list, tuple))]
    return any(len(a) > 0 for a in arrays)


# ── Output case selection ──────────────────────────────────────────


def _select_output_cases(
    model: Any, result_filter: str | None = None
) -> dict[str, Any]:
    setup = {
        "selected_cases": 0,
        "selected_combinations": 0,
        "selection_errors": [],
        "selected_case_names": [],
        "selected_combination_names": [],
        "result_filter": result_filter or "ALL",
    }

    # Intenta leer las present units del modelo para incluirlas en el metadata.
    # Si falla, simplemente no se añade — la tabla se construye igual.
    try:
        present_units = model.GetPresentUnits()
        result_units = extract_result_units(present_units)
        if result_units:
            setup["result_units"] = result_units
    except Exception:
        # Si no se puede leer GetPresentUnits(), no falla todo, solo se omite
        pass

    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception as exc:
        setup["selection_errors"].append(f"Deselect: {exc}")

    case_names = _load_case_names(model)
    combo_names = _load_combination_names(model)
    result_filters = _result_filter_tokens(result_filter)

    if len(result_filters) > 1:
        for item in result_filters:
            if item.startswith("CASE:"):
                case = item.split(":", 1)[1]
                try:
                    _select_case(model, case)
                    setup["selected_cases"] += 1
                    setup["selected_case_names"].append(case)
                except Exception as exc:
                    setup["selection_errors"].append(f"Case {case}: {exc}")
            elif item.startswith("COMBO:"):
                combo = item.split(":", 1)[1]
                try:
                    _select_combo(model, combo)
                    setup["selected_combinations"] += 1
                    setup["selected_combination_names"].append(combo)
                except Exception as exc:
                    setup["selection_errors"].append(f"Combo {combo}: {exc}")
            elif item.startswith("OUTPUT:"):
                continue
            elif _contains_name(case_names, item):
                try:
                    _select_case(model, item)
                    setup["selected_cases"] += 1
                    setup["selected_case_names"].append(item)
                except Exception as exc:
                    setup["selection_errors"].append(f"Case {item}: {exc}")
            elif _contains_name(combo_names, item):
                try:
                    _select_combo(model, item)
                    setup["selected_combinations"] += 1
                    setup["selected_combination_names"].append(item)
                except Exception as exc:
                    setup["selection_errors"].append(f"Combo {item}: {exc}")
            else:
                setup["selection_errors"].append(
                    f"Filtro de resultados no encontrado: {item}"
                )
        return setup

    if result_filter and result_filter.startswith("CASE:"):
        case = result_filter.split(":", 1)[1]
        try:
            _select_case(model, case)
            setup["selected_cases"] = 1
            setup["selected_case_names"].append(case)
        except Exception as exc:
            setup["selection_errors"].append(f"Case {case}: {exc}")
        return setup

    if result_filter and result_filter.startswith("COMBO:"):
        combo = result_filter.split(":", 1)[1]
        try:
            _select_combo(model, combo)
            setup["selected_combinations"] = 1
            setup["selected_combination_names"].append(combo)
        except Exception as exc:
            setup["selection_errors"].append(f"Combo {combo}: {exc}")
        return setup

    if result_filter and result_filter.startswith("OUTPUT:"):
        result_filter = None

    if result_filter and result_filter != "ALL":
        if _contains_name(case_names, result_filter):
            try:
                _select_case(model, result_filter)
                setup["selected_cases"] = 1
                setup["selected_case_names"].append(result_filter)
            except Exception as exc:
                setup["selection_errors"].append(
                    f"Case {result_filter}: {exc}"
                )
            return setup
        if _contains_name(combo_names, result_filter):
            try:
                _select_combo(model, result_filter)
                setup["selected_combinations"] = 1
                setup["selected_combination_names"].append(result_filter)
            except Exception as exc:
                setup["selection_errors"].append(
                    f"Combo {result_filter}: {exc}"
                )
            return setup
        setup["selection_errors"].append(
            f"Filtro de resultados no encontrado: {result_filter}"
        )

    for case in case_names:
        try:
            _select_case(model, case)
            setup["selected_cases"] += 1
            setup["selected_case_names"].append(case)
        except Exception as exc:
            setup["selection_errors"].append(f"Case {case}: {exc}")

    for combo in combo_names:
        try:
            _select_combo(model, combo)
            setup["selected_combinations"] += 1
            setup["selected_combination_names"].append(combo)
        except Exception as exc:
            setup["selection_errors"].append(f"Combo {combo}: {exc}")
    return setup


def _select_all_output_cases(model: Any) -> dict[str, Any]:
    return _select_output_cases(model)


def _set_selected(func, name: str) -> None:
    try:
        ret = func(name, True)
    except TypeError:
        ret = func(name)
    _ensure_sap_ok(ret, f"No se pudo seleccionar {name} para resultados.")


def _select_case(model: Any, name: str) -> None:
    _set_selected(model.Results.Setup.SetCaseSelectedForOutput, name)


def _select_combo(model: Any, name: str) -> None:
    _set_selected(model.Results.Setup.SetComboSelectedForOutput, name)


def _ensure_sap_ok(ret: Any, message: str) -> None:
    code = ret[0] if isinstance(ret, tuple) and ret else ret
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"{message} Codigo SAP2000: {code}")


def _load_case_names(model: Any) -> list[str]:
    return _combined_names(
        lambda: model.LoadCases.GetNameList(),
        lambda: _database_named_values(
            model,
            aliases_for("load_cases"),
            ["LoadCase", "Case", "CaseName", "Load Case", "Name"],
        )[0],
    )


def _load_combination_names(model: Any) -> list[str]:
    return _combined_names(
        lambda: model.RespCombo.GetNameList(),
        lambda: _database_named_values(
            model,
            aliases_for("load_combinations"),
            [
                "Combo",
                "ComboName",
                "Combination",
                "CombinationName",
                "LoadCombo",
                "Name",
            ],
        )[0],
    )


# ── Result output cases discovery ──────────────────────────────────


def _result_output_cases(model: Any) -> list[str]:
    values: list[str] = []
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return values
    available = _available_database_tables(db)
    for kind in ("frame_forces", "joint_displacements", "joint_reactions"):
        keys = _match_database_table_keys(aliases_for(kind), available) or aliases_for(kind)
        for key in keys:
            try:
                parsed = _get_database_table_for_display(db, key)
            except Exception:
                continue
            if not parsed:
                continue
            columns, rows = parsed
            result_index = _find_name_column(
                columns, ["OutputCase", "LoadCase", "Case", "Combo", "Combination"]
            )
            if result_index is None:
                continue
            for row in rows:
                if result_index < len(row):
                    text = str(row[result_index]).strip()
                    if text:
                        values.append(text)
    return _unique(values)


# ── Database table fallback ────────────────────────────────────────


def _database_table(
    model: Any,
    name: str,
    candidate_keys: list[str],
    setup: dict[str, Any],
    limit: int | None = None,
) -> dict | None:
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return None

    available = _available_database_tables(db)
    keys = _match_database_table_keys(candidate_keys, available)
    if not keys:
        keys = candidate_keys

    errors: list[str] = []
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
        if rows:
            if limit:
                rows = rows[:limit]
            table = _table(name, columns, rows)

            # Intenta leer las present units del modelo para incluirlas en metadata.
            # Si falla, simplemente no se añade — la tabla se construye igual.
            result_units = {}
            try:
                present_units = model.GetPresentUnits()
                result_units = extract_result_units(present_units)
            except Exception:
                pass

            table["metadata"] = {
                "source": f"DatabaseTables:{key}",
                "row_count": len(rows),
                "columns": columns,
                **({"result_units": result_units} if result_units else {}),
                **setup,
            }
            remember_alias(name, key)
            return table
        errors.append(f"{key}: 0 filas")

    if errors:
        rows = [
            [
                "Mensaje",
                "",
                "No se pudieron obtener resultados desde API directa ni desde DatabaseTables.",
            ],
            ["Casos seleccionados", setup.get("selected_cases", 0), ""],
            [
                "Combinaciones seleccionadas",
                setup.get("selected_combinations", 0),
                "",
            ],
            ["Tablas intentadas", len(keys), "; ".join(keys)],
            ["Errores", len(errors), " | ".join(errors[:8])],
        ]
        return _table(name, ["Dato", "Cantidad", "Valor"], rows)
    return None
