"""Resultados en bloque del Viewer: reacciones, esfuerzos de varias barras y envolventes.

Solo lectura, sobre el mismo lector que ``core/results.py``. Reglas:

* Una sola lectura COM por petición (``Results.FrameForce("ALL")`` /
  ``Results.JointReact("ALL")``); nunca barra a barra. SAP2000 devuelve todas las filas
  de los casos seleccionados: se filtra por ``ids`` y se pagina en Python. Los cursores
  siguientes reutilizan una caché de 2 min de la lectura de la primera página para no
  volver a leer el modelo entero en cada página.
* La selección de salida del usuario (casos y combinaciones marcados en SAP2000) se
  guarda antes de leer y se restaura después (``preserve_output_selection``).
* Las unidades salen siempre; ``toMeters`` es ``None`` si la unidad de longitud no se
  reconoce (nunca se supone metros).
* Un caso se valida contra la lista real del modelo: 404 ``case_not_found``.
"""

from __future__ import annotations

import base64
import binascii
import json
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator

from bridge.viewer.core.results import ResultsError, _finite, _notes, _reader, _require_rows, _units, list_cases
from bridge.viewer.sap2000.reader_results import _load_case_names, _load_combination_names
from bridge.viewer.sap2000.units import _LENGTH_UNIT_TO_METERS

MAX_FRAMES_PER_PAGE = 500
MAX_CASES = 30
CACHE_TTL_SECONDS = 120.0
CACHE_MAX_ENTRIES = 3

FRAME_COMPONENTS = ("P", "V2", "V3", "T", "M2", "M3")
REACTION_COMPONENTS = ("F1", "F2", "F3", "M1", "M2", "M3")


# --- Selección de salida ------------------------------------------------------


def _selected_flag(ret: Any) -> bool | None:
    """Lee el booleano de ``Get*SelectedForOutput`` sin depender del orden del retorno."""
    if isinstance(ret, (tuple, list)):
        for value in ret:
            if isinstance(value, bool):
                return value
        return None
    return ret if isinstance(ret, bool) else None


def _capture_selection(model: Any) -> tuple[list[str], list[str]] | None:
    """Casos y combinaciones marcados para salida, o ``None`` si no se puede leer."""
    setup = model.Results.Setup
    selected: list[list[str]] = []
    for names, getter in (
        (_load_case_names(model), setup.GetCaseSelectedForOutput),
        (_load_combination_names(model), setup.GetComboSelectedForOutput),
    ):
        flags = [(n, _selected_flag(getter(n))) for n in names]
        # Si algún valor no se pudo interpretar, mejor no «restaurar» una selección inventada.
        if any(flag is None for _, flag in flags):
            return None
        selected.append([n for n, flag in flags if flag])
    return selected[0], selected[1]


@contextmanager
def preserve_output_selection(model: Any, warnings: list[str] | None = None) -> Iterator[None]:
    """Restaura la selección de salida del usuario al terminar la lectura.

    Leer resultados deselecciona y vuelve a seleccionar casos (``Results.Setup``), un
    estado de visualización de SAP2000 que el usuario no espera ver cambiado.
    """
    avisos = warnings if warnings is not None else []
    try:
        saved = _capture_selection(model)
    except Exception:
        saved = None
    if saved is None:
        avisos.append("No se pudo leer la selección de salida de SAP2000: puede haber cambiado tras la lectura.")
    try:
        yield
    finally:
        if saved is not None:
            cases, combos = saved
            try:
                setup = model.Results.Setup
                setup.DeselectAllCasesAndCombosForOutput()
                for name in cases:
                    setup.SetCaseSelectedForOutput(name, True)
                for name in combos:
                    setup.SetComboSelectedForOutput(name, True)
            except Exception as exc:  # noqa: BLE001 - restaurar es de mejor esfuerzo
                avisos.append(f"No se pudo restaurar la selección de salida de SAP2000: {exc}")


# --- Utilidades -----------------------------------------------------------------


def _col(columns: list[str], *names: str) -> int | None:
    for name in names:
        if name in columns:
            return columns.index(name)
    return None


def _cols(table: dict) -> list[str]:
    return [str(c).strip().casefold() for c in table.get("columns", [])]


def _unit_block(table: dict, warnings: list[str]) -> tuple[dict, float | None]:
    units = _units(table)
    force, length = units.get("force"), units.get("length")
    factor = _LENGTH_UNIT_TO_METERS.get(length) if length else None
    if not force or factor is None:
        warnings.append("Unidades de resultado no reconocidas: no se asume ninguna conversión (toMeters: null).")
    return {"force": force or None, "length": length or None}, factor


def _case_values(model: Any, cases: list[str]) -> list[tuple[str, str]]:
    """(nombre pedido, valor de filtro) validando contra la lista real del modelo."""
    known = list_cases(model)["cases"]
    out: list[tuple[str, str]] = []
    for case in cases:
        for item in known:
            if case in (item["value"], item["name"]):
                out.append((item["name"], item["value"]))
                break
        else:
            raise ResultsError(404, "case_not_found", f"El caso o combinación {case!r} no existe en el modelo.")
    return out


def _filter_for(values: list[str]) -> str:
    return values[0] if len(values) == 1 else "MULTI:" + "||".join(values)


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        offset = int(data["o"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise ResultsError(400, "bad_cursor", "Cursor inválido.") from exc
    if offset < 0:
        raise ResultsError(400, "bad_cursor", "Cursor inválido.")
    return offset


def _limit(limit: int | None) -> int:
    if limit is None:
        return MAX_FRAMES_PER_PAGE
    if limit < 1:
        raise ResultsError(400, "bad_limit", "limit debe ser al menos 1.")
    return min(limit, MAX_FRAMES_PER_PAGE)


def _parse_ids(ids: str | None) -> list[str] | None:
    if ids is None or not ids.strip():
        return None
    out = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
    return out or None


# --- Caché de lecturas (solo para páginas siguientes) ------------------------------

_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> dict | None:
    with _cache_lock:
        item = _cache.get(key)
        if item is None or time.monotonic() - item[0] > CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return item[1]


def _cache_put(key: tuple, value: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- Lectura de tablas ---------------------------------------------------------------


def _read_frame_rows(model: Any, filter_value: str, what: str) -> tuple[dict[str, list[dict]], dict, list[str]]:
    """Una lectura ``FrameForce("ALL")`` → ``{barra: [estaciones con su caso]}``."""
    table = _reader(model, filter_value).frame_forces(limit=None)
    _require_rows(table, what)
    cols = _cols(table)
    i_obj = _col(cols, "obj", "frame")
    i_sta = _col(cols, "objsta", "station")
    i_case = _col(cols, "loadcase", "outputcase", "case", "combo")
    i_step = _col(cols, "steptype")
    comps = {c: _col(cols, c.casefold()) for c in FRAME_COMPONENTS}
    if i_obj is None or i_sta is None or any(i is None for i in comps.values()):
        raise ResultsError(409, "no_results", "La tabla de fuerzas no trae las columnas esperadas.")
    frames: dict[str, list[dict]] = {}
    for row in table["rows"]:
        x = _finite(row[i_sta])
        if x is None:
            continue
        item = {
            "x": x,
            "step": str(row[i_step]) if i_step is not None else "",
            "case": str(row[i_case]).strip() if i_case is not None else "",
            **{c: _finite(row[i]) for c, i in comps.items()},
        }
        frames.setdefault(str(row[i_obj]).strip(), []).append(item)
    return frames, table, _notes(table)


def _read_reaction_rows(model: Any, filter_value: str, what: str) -> tuple[dict[str, list[dict]], dict, list[str]]:
    table = _reader(model, filter_value).joint_reactions(limit=None)
    _require_rows(table, what)
    cols = _cols(table)
    i_obj = _col(cols, "obj", "joint")
    i_case = _col(cols, "loadcase", "outputcase", "case", "combo")
    i_step = _col(cols, "steptype")
    comps = {c: _col(cols, c.casefold()) for c in REACTION_COMPONENTS}
    if i_obj is None or any(i is None for i in comps.values()):
        raise ResultsError(409, "no_results", "La tabla de reacciones no trae las columnas esperadas.")
    nodes: dict[str, list[dict]] = {}
    for row in table["rows"]:
        item = {
            "step": str(row[i_step]) if i_step is not None else "",
            "case": str(row[i_case]).strip() if i_case is not None else "",
            **{c: _finite(row[i]) for c, i in comps.items()},
        }
        nodes.setdefault(str(row[i_obj]).strip(), []).append(item)
    return nodes, table, _notes(table)


def _page(order: list[str], offset: int, limit: int) -> tuple[list[str], str | None]:
    chunk = order[offset : offset + limit]
    nxt = _encode_cursor(offset + limit) if offset + limit < len(order) else None
    return chunk, nxt


def _frame_order(frames: dict[str, list[dict]], ids: list[str] | None, warnings: list[str]) -> list[str]:
    if ids is None:
        return sorted(frames, key=lambda n: (len(n), n))
    missing = [i for i in ids if i not in frames]
    if missing:
        warnings.append(f"Sin resultados para {len(missing)} barra(s): {', '.join(missing[:10])}" + ("…" if len(missing) > 10 else "") + ".")
    return [i for i in ids if i in frames]


def _station(item: dict) -> dict:
    return {"x": item["x"], "step": item["step"], **{c: item[c] for c in FRAME_COMPONENTS}}


# --- 1. Reacciones ------------------------------------------------------------------


def _extreme(rows: list[dict], comp: str) -> float | None:
    """Valor de mayor módulo (con su signo) entre los pasos de un nodo."""
    values = [r[comp] for r in rows if r[comp] is not None]
    return max(values, key=abs) if values else None


def reactions(model: Any, case: str) -> dict:
    warnings: list[str] = []
    with preserve_output_selection(model, warnings):
        (name, value), = _case_values(model, [case])
        nodes_rows, table, notes = _read_reaction_rows(model, value, "reacciones")
    warnings.extend(notes)
    nodes: dict[str, list[float | None]] = {}
    multi = 0
    for node, rows in nodes_rows.items():
        if len(rows) > 1:
            multi += 1
        vector = [_extreme(rows, c) for c in REACTION_COMPONENTS]
        if any(v is not None for v in vector):
            nodes[node] = vector
    if multi:
        warnings.append(
            f"{multi} nodo(s) con varios pasos (StepType/StepNum): se muestra el valor de mayor módulo por componente."
        )
    units, _factor = _unit_block(table, warnings)
    forces = [[v[i] for v in nodes.values()] for i in range(3)]
    complete = all(v is not None for column in forces for v in column)
    if multi or not complete:
        total = None
        if not complete:
            warnings.append("Hay reacciones sin valor numérico: no se calcula la suma de fuerzas.")
    else:
        total = [sum(column) for column in forces] + [None, None, None]
    peak = max((abs(v) for vec in nodes.values() for v in vec if v is not None), default=0.0)
    return {
        "case": name,
        "nodes": nodes,
        "units": units,
        "toMeters": _factor,
        "sum": total,
        "max": peak,
        "warnings": warnings,
    }


# --- 2. Esfuerzos de barras en bloque -------------------------------------------------


def frames_block(model: Any, case: str, ids: str | None, limit: int | None, cursor: str | None) -> dict:
    page_size = _limit(limit)
    offset = _decode_cursor(cursor)
    wanted = _parse_ids(ids)
    warnings: list[str] = []
    with preserve_output_selection(model, warnings):
        (name, value), = _case_values(model, [case])
        key = ("frames", value)
        cached = _cache_get(key) if cursor else None
        if cached is None:
            frames, table, notes = _read_frame_rows(model, value, f"fuerzas de las barras ({name})")
            units, factor = _unit_block(table, [])
            cached = {"frames": frames, "units": units, "toMeters": factor, "notes": notes}
            _cache_put(key, cached)
    frames: dict[str, list[dict]] = cached["frames"]
    unit_warnings: list[str] = []
    if cached["units"]["force"] is None or cached["toMeters"] is None:
        unit_warnings.append("Unidades de resultado no reconocidas: no se asume ninguna conversión (toMeters: null).")
    order = _frame_order(frames, wanted, warnings)
    if wanted is not None and not order:
        raise ResultsError(409, "no_results", "Ninguna de las barras pedidas tiene resultados en ese caso.")
    chunk, nxt = _page(order, offset, page_size)
    out: dict[str, list[dict]] = {}
    for fid in chunk:
        stations = [_station(s) for s in frames[fid]]
        stations.sort(key=lambda s: (s["step"], s["x"]))
        out[fid] = stations
    return {
        "case": name,
        "frames": out,
        "units": cached["units"],
        "toMeters": cached["toMeters"],
        "next": nxt,
        "warnings": warnings + cached["notes"] + unit_warnings,
    }


# --- 3. Envolvente ----------------------------------------------------------------------


def _better(current: tuple[float, str] | None, value: float | None, case: str, want_max: bool):
    if value is None:
        return current
    if current is None or (value > current[0] if want_max else value < current[0]):
        return (value, case)
    return current


def _envelope_frame(rows: list[dict]) -> tuple[list[dict], list[dict], dict]:
    slots: dict[float, dict[str, dict[str, tuple[float, str] | None]]] = {}
    overall: dict[str, dict[str, tuple[float, str] | None]] = {c: {"max": None, "min": None} for c in FRAME_COMPONENTS}
    for r in rows:
        slot = slots.setdefault(round(r["x"], 6), {c: {"max": None, "min": None} for c in FRAME_COMPONENTS})
        for c in FRAME_COMPONENTS:
            slot[c]["max"] = _better(slot[c]["max"], r[c], r["case"], True)
            slot[c]["min"] = _better(slot[c]["min"], r[c], r["case"], False)
            overall[c]["max"] = _better(overall[c]["max"], r[c], r["case"], True)
            overall[c]["min"] = _better(overall[c]["min"], r[c], r["case"], False)
    maxes, mins = [], []
    for x in sorted(slots):
        maxes.append({"x": x, **{c: (slots[x][c]["max"][0] if slots[x][c]["max"] else None) for c in FRAME_COMPONENTS}})
        mins.append({"x": x, **{c: (slots[x][c]["min"][0] if slots[x][c]["min"] else None) for c in FRAME_COMPONENTS}})
    governing = {
        c: {
            "max": overall[c]["max"][1] if overall[c]["max"] else None,
            "min": overall[c]["min"][1] if overall[c]["min"] else None,
        }
        for c in FRAME_COMPONENTS
    }
    return maxes, mins, governing


def _envelope_node(rows: list[dict]) -> tuple[list[float | None], list[float | None], dict]:
    maxes: list[float | None] = []
    mins: list[float | None] = []
    governing: dict[str, dict[str, str | None]] = {}
    for c in REACTION_COMPONENTS:
        hi = lo = None
        for r in rows:
            hi = _better(hi, r[c], r["case"], True)
            lo = _better(lo, r[c], r["case"], False)
        maxes.append(hi[0] if hi else None)
        mins.append(lo[0] if lo else None)
        governing[c] = {"max": hi[1] if hi else None, "min": lo[1] if lo else None}
    return maxes, mins, governing


def envelope(
    model: Any, kind: str, cases: str | None, ids: str | None, limit: int | None, cursor: str | None
) -> dict:
    if kind not in ("reactions", "frames"):
        raise ResultsError(400, "bad_kind", "kind debe ser 'reactions' o 'frames'.")
    names = [c.strip() for c in (cases or "").split(",") if c.strip()]
    if not names:
        raise ResultsError(400, "no_cases", "Indique al menos un caso en cases.")
    names = list(dict.fromkeys(names))
    if len(names) > MAX_CASES:
        raise ResultsError(400, "too_many_cases", f"Se admiten como máximo {MAX_CASES} casos por petición.")
    page_size = _limit(limit)
    offset = _decode_cursor(cursor)
    wanted = _parse_ids(ids)
    warnings: list[str] = []
    with preserve_output_selection(model, warnings):
        resolved = _case_values(model, names)
        values = [v for _, v in resolved]
        key = ("envelope", kind, tuple(values))
        cached = _cache_get(key) if cursor else None
        if cached is None:
            reader = _read_frame_rows if kind == "frames" else _read_reaction_rows
            data, table, notes = reader(model, _filter_for(values), "resultados para la envolvente")
            units, factor = _unit_block(table, [])
            cached = {"data": data, "units": units, "toMeters": factor, "notes": notes}
            _cache_put(key, cached)
    data: dict[str, list[dict]] = cached["data"]
    unit_warnings: list[str] = []
    if cached["units"]["force"] is None or cached["toMeters"] is None:
        unit_warnings.append("Unidades de resultado no reconocidas: no se asume ninguna conversión (toMeters: null).")
    read_cases = {r["case"] for rows in data.values() for r in rows}
    absent = [n for n, _ in resolved if n not in read_cases]
    if absent:
        warnings.append(f"Sin filas en los resultados para: {', '.join(absent)}.")
    order = _frame_order(data, wanted, warnings) if kind == "frames" else sorted(data, key=lambda n: (len(n), n))
    if wanted is not None and not order:
        raise ResultsError(409, "no_results", "Ninguno de los elementos pedidos tiene resultados.")
    if kind == "frames":
        chunk, nxt = _page(order, offset, page_size)
        max_out: dict[str, Any] = {}
        min_out: dict[str, Any] = {}
        governing: dict[str, Any] = {}
        for fid in chunk:
            max_out[fid], min_out[fid], governing[fid] = _envelope_frame(data[fid])
    else:
        nxt = None
        max_out, min_out, governing = {}, {}, {}
        for node in order:
            if wanted is not None and node not in wanted:
                continue
            hi, lo, gov = _envelope_node(data[node])
            if any(v is not None for v in hi):
                max_out[node], min_out[node], governing[node] = hi, lo, gov
    return {
        "kind": kind,
        "cases": [n for n, _ in resolved],
        "max": max_out,
        "min": min_out,
        "governing": governing,
        "units": cached["units"],
        "toMeters": cached["toMeters"],
        "next": nxt,
        "warnings": warnings + cached["notes"] + unit_warnings,
    }

