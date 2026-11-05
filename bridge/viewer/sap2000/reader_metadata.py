# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import math
from typing import Any

from bridge.viewer.sap2000.reader_utils import (
    _table,
    _empty_table,
    _name_table,
    _name_table_from_sources,
    _data_values,
    _names,
    _get_names,
    _count_names,
    _safe_value,
    _last,
    _first_matching_table,
    _selection_rows,
    _available_database_tables,
    _match_database_table_keys,
)
from bridge.viewer.sap2000.reader_results import _select_all_output_cases, _result_output_cases
from bridge.viewer.sap2000.reader_geometry import _group_memberships, _frame_geometry
from bridge.viewer.sap2000.table_aliases import aliases_for
from bridge.viewer.sap2000.units import extract_result_units


def _format_present_units(value: Any) -> str:
    """Formatea las unidades presentes en un formato legible.

    Si extract_result_units devuelve un dict con unidades válidas, devuelve
    un string del formato "force, length" (p.ej. "tonf, m").
    Si no puede extraer unidades (formato no reconocible), devuelve el valor
    original como string.
    """
    try:
        units = extract_result_units(value)
        if units:
            # Formatea como "force, length", p.ej. "tonf, m"
            return f"{units['force']}, {units['length']}"
        # Si extract_result_units devolvió vacío, usa el valor original
        return str(value)
    except Exception:
        # Si algo falla en la extracción, usa el valor original
        return str(value)


def model_info(model: Any, limit: int | None = None) -> dict:
    values = []
    for label, func in [
        ("File", lambda: model.GetModelFilename()),
        ("Database units", lambda: _format_present_units(_last(model.GetDatabaseUnits()))),
        ("Present units", lambda: _format_present_units(_last(model.GetPresentUnits()))),
        ("Locked", lambda: model.GetModelIsLocked()),
    ]:
        try:
            values.append([label, _last(func())])
        except Exception as exc:
            values.append([label, f"No disponible: {exc}"])
    return _table("model_info", ["Field", "Value"], values)


def diagnostics(model: Any, limit: int | None = None, sections_data: dict | None = None) -> dict:
    db = getattr(model, "DatabaseTables", None)
    database_tables = _available_database_tables(db) if db is not None else []
    setup = _select_all_output_cases(model)
    rows = [
        ["Modelo", "", _safe_value(lambda: model.GetModelFilename())],
        ["Unidades presentes", "", _safe_value(lambda: _format_present_units(_last(model.GetPresentUnits())))],
        ["Modelo bloqueado", "", _safe_value(lambda: model.GetModelIsLocked())],
        ["Materiales", _count_names(lambda: model.PropMaterial.GetNameList()), ""],
        ["Secciones", len(_data_values(sections_data)) if sections_data else 0, ""],
        ["Grupos", _count_names(lambda: model.GroupDef.GetNameList()), ""],
        [
            "Patrones de carga",
            _count_names(lambda: model.LoadPatterns.GetNameList()),
            "",
        ],
        ["Casos de carga", _count_names(lambda: model.LoadCases.GetNameList()), ""],
        [
            "Combinaciones",
            _count_names(lambda: model.RespCombo.GetNameList()),
            "",
        ],
        ["Joints", _count_names(lambda: model.PointObj.GetNameList()), ""],
        ["Frames", _count_names(lambda: model.FrameObj.GetNameList()), ""],
        ["Areas", _count_names(lambda: model.AreaObj.GetNameList()), ""],
        [
            "Casos seleccionados para resultados",
            setup.get("selected_cases", 0),
            "",
        ],
        [
            "Combinaciones seleccionadas para resultados",
            setup.get("selected_combinations", 0),
            "",
        ],
        [
            "Errores al seleccionar resultados",
            len(setup.get("selection_errors", [])),
            "; ".join(setup.get("selection_errors", [])),
        ],
        ["Tablas internas SAP2000", len(database_tables), ""],
        [
            "Tabla de fuerzas encontrada",
            "",
            _first_matching_table(
                database_tables,
                [
                    "Element Forces - Frames",
                    "Frame Output Station Forces",
                    "Frame Forces",
                ],
            ),
        ],
        [
            "Tabla de desplazamientos encontrada",
            "",
            _first_matching_table(
                database_tables,
                ["Joint Displacements", "Joint Displacements - Absolute"],
            ),
        ],
    ]
    return _table("model_diagnostics", ["Dato", "Cantidad", "Valor"], rows[:limit] if limit else rows)


def materials(model: Any, limit: int | None = None) -> dict:
    return _name_table("materials", model.PropMaterial.GetNameList, limit)


def sections(model: Any, limit: int | None = None) -> dict:
    rows: list[list[str]] = []
    for prop_name in ("PropFrame", "PropArea", "PropCable", "PropTendon"):
        obj = getattr(model, prop_name, None)
        if not obj:
            continue
        try:
            for name in _get_names(obj):
                rows.append([name, "", prop_name.replace("Prop", "")])
        except Exception as exc:
            rows.append(
                [f"No disponible: {exc}", "", prop_name.replace("Prop", "")]
            )
    count = len(rows)
    rows = [["Total", count, ""]] + rows
    return _table("sections", ["Dato", "Cantidad", "Tipo"], rows[:limit] if limit else rows)


def groups(model: Any, limit: int | None = None) -> dict:
    return _name_table("groups", model.GroupDef.GetNameList, limit)


def load_patterns(model: Any, limit: int | None = None) -> dict:
    return _name_table_from_sources(
        model,
        "load_patterns",
        lambda: model.LoadPatterns.GetNameList(),
        ["LoadPat", "LoadPattern", "Load Pattern", "Pattern", "Name"],
        limit,
    )


def load_cases(model: Any, limit: int | None = None) -> dict:
    return _name_table_from_sources(
        model,
        "load_cases",
        lambda: model.LoadCases.GetNameList(),
        ["LoadCase", "Case", "CaseName", "Load Case", "Name"],
        limit,
    )


def load_combinations(model: Any, limit: int | None = None) -> dict:
    return _name_table_from_sources(
        model,
        "load_combinations",
        lambda: model.RespCombo.GetNameList(),
        [
            "Combo",
            "ComboName",
            "Combination",
            "CombinationName",
            "LoadCombo",
            "Name",
        ],
        limit,
    )


def load_combination_definitions(model: Any, errors: list[str]) -> list[dict[str, Any]]:
    """Definiciones de las combinaciones de respuesta sin expandir.

    Usa RespCombo.GetNameList y RespCombo.GetCaseList por combinación (el mismo
    orden de argumentos que bridge/combinaciones.py). Devuelve
    ``[{"name", "items": [{"case", "factor", "combo"}]}]``: ``combo`` es True
    cuando el término es otra combinación, que core/combinations.py expande.
    Una combinación que no se puede leer se omite y se anota en ``errors``.
    """
    resp_combo = getattr(model, "RespCombo", None)
    if resp_combo is None:
        return []
    try:
        names = _get_names(resp_combo)
    except Exception as exc:
        errors.append(f"RespCombo.GetNameList: {exc}")
        return []
    definitions: list[dict[str, Any]] = []
    for name in names:
        try:
            terms = _combo_terms(_call_get_case_list(resp_combo, name))
        except Exception as exc:
            errors.append(f"RespCombo.GetCaseList {name}: {exc}")
            continue
        definitions.append({
            "name": name,
            "items": [
                {"case": term["name"], "factor": term["factor"], "combo": term["item_type"] == 1}
                for term in terms
            ],
        })
    return definitions


def _call_get_case_list(resp_combo: Any, name: str) -> Any:
    attempts: tuple[tuple[Any, ...], ...] = (
        (name,),
        (name, 0, [], [], []),
        (name, 0, (), (), ()),
    )
    last: Exception | None = None
    for args in attempts:
        try:
            return resp_combo.GetCaseList(*args)
        except TypeError as exc:
            last = exc
    raise ValueError(f"la API COM rechazó los argumentos de GetCaseList: {last}")


def _combo_terms(result: Any) -> list[dict[str, Any]]:
    """Términos (tipo, nombre, factor) de GetCaseList: tipo 0 = caso, 1 = combinación.

    Las listas llegan en el orden de la firma COM (tipos, nombres, factores); los
    escalares (conteo, código de retorno) se ignoran tras validar el retorno.
    """
    if not isinstance(result, (tuple, list)) or not result:
        raise ValueError("respuesta inesperada al consultar los componentes")
    if isinstance(result[-1], int) and result[-1] != 0:
        raise ValueError(f"código de retorno {result[-1]}")
    arrays = [list(value) for value in result if isinstance(value, (tuple, list))]
    if len(arrays) < 3:
        raise ValueError("respuesta incompleta al consultar los componentes")
    types, names, factors = arrays[0], arrays[1], arrays[2]
    count = min(len(types), len(names), len(factors))
    terms: list[dict[str, Any]] = []
    for index in range(count):
        name = str(names[index]).strip()
        if not name:
            continue
        factor = float(factors[index])
        if not math.isfinite(factor):
            raise ValueError(f"factor no finito en {name}")
        terms.append({"item_type": int(types[index]), "name": name, "factor": factor})
    return terms


def available_result_filters(model: Any) -> list[dict[str, str]]:
    cases = _data_values(load_cases(model, limit=500))
    combos = _data_values(load_combinations(model, limit=500))
    discovered = _result_output_cases(model)

    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for name in cases:
        normalized = name.casefold()
        if normalized in seen:
            continue
        items.append(
            {
                "label": f"Caso/Estado: {name}",
                "value": f"CASE:{name}",
                "kind": "case",
                "name": name,
            }
        )
        seen.add(normalized)

    for name in combos:
        normalized = name.casefold()
        if normalized in seen:
            continue
        items.append(
            {
                "label": f"Combinacion: {name}",
                "value": f"COMBO:{name}",
                "kind": "combo",
                "name": name,
            }
        )
        seen.add(normalized)

    for name in discovered:
        normalized = name.casefold()
        if normalized in seen:
            continue
        items.append(
            {
                "label": f"Resultado: {name}",
                "value": f"OUTPUT:{name}",
                "kind": "output",
                "name": name,
            }
        )
        seen.add(normalized)

    return items


def database_tables(model: Any, limit: int | None = None) -> dict:
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return _table(
            "database_tables",
            ["Dato", "Cantidad"],
            [["DatabaseTables no disponible", ""]],
        )
    names = _available_database_tables(db)
    rows = [["Total", len(names)]] + [[name, ""] for name in names]
    if limit:
        rows = rows[:limit]
    return _table("database_tables", ["Dato", "Cantidad"], rows)


def selection_properties(model: Any, limit: int | None = None) -> dict:
    try:
        result = model.SelectObj.GetSelected()
        values = _selection_rows(result)
    except Exception as exc:
        values = [["error", str(exc)]]
    return _table("selection", ["ObjectType", "Name"], values)
