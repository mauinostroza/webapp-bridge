"""K y L CALCULADOS por el diseño de acero (resultados, SOLO LECTURA).

Distinto de `steel_k.py`, que lee/escribe los OVERWRITES. Aquí se leen los
valores que el módulo de diseño usó tras ejecutarse, de la tabla de SAP2000
«Steel Details 2 - PMM Details - AISC 360-16» (nombre REAL visto con la sonda; Design Data > Steel Frame >
Steel Detail Data). El modelo no se modifica nunca.

VERIFICADO en SAP2000 23.3.1 (AISC 360-16, diseño ejecutado) con la sonda:
- «Steel Design 2 - PMM Details - AISC 360-16» NO figura en GetAvailableTables pero
  se lee directamente: 55 columnas (Frame, DesignSect, Combo, Location, TotalRatio,
  XLMajor, XLMinor, XLLTB, K1Major... KLTB) y UNA fila por barra (la gobernante);
- «Steel Details 2 - PMM Details» y «Steel Details 1 - Summary Data» SÍ figuran pero
  devuelven basura (17 columnas vacías): se descartan.
Por eso se recorren TODOS los candidatos y se usa el primero con lectura válida.

Aún sin verificar en todas las versiones (no inventar certezas):
- el nombre de la tabla puede variar con el código de diseño: se aceptan las
  que empiezan por «Steel Details 2 - PMM Details» o «Steel Design 2 - PMM Details»;
- columnas de K/L con «Unitless» al final («K1Major Unitless») se aceptan;
- columnas de combinación/estación/ratio/barra: Combo, Location (estación),
  TotalRatio (ratio), DesignSect y Frame, más variantes por normalización;
- las filas pueden ser por barra x combinación x estación (si hay varias por barra).

Si el diseño no se ha ejecutado (tabla ausente o vacía) no es un error:
`available` False con el motivo.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

from bridge.viewer.sap2000.reader_utils import (
    _available_database_tables,
    _parse_database_table_result,
)
from bridge.viewer.sap2000.steel_k import _norm, find_frame_column, find_l_columns

#: Claves de salida, en orden: 5 K y 3 L.
DESIGN_K_FIELDS: tuple[str, ...] = ("K1 Major", "K1 Minor", "K2 Major", "K2 Minor", "K LTB")
DESIGN_L_FIELDS: tuple[str, ...] = ("L Major", "L Minor", "L LTB")
DESIGN_FIELDS: tuple[str, ...] = DESIGN_K_FIELDS + DESIGN_L_FIELDS

#: Nombre probado directamente aunque no figure en la lista. En SAP2000 23.3.1 esta lectura
#: devuelve BASURA (verificado): se descarta y se sigue con el candidato siguiente.
HYPOTHESIS_DESIGN_TABLE = "Steel Details 2 - PMM Details - AISC 360-16"
#: Nombre REAL legible en SAP2000 23.3.1 (verificado), aunque NO figure en GetAvailableTables.
ALT_DESIGN_TABLE = "Steel Design 2 - PMM Details - AISC 360-16"
_PREFIXES = ("steeldetails2pmmdetails", "steeldesign2pmmdetails")

NOT_RUN_REASON = (
    "No hay resultados de diseño de acero (tabla «Steel Details 2 - PMM Details» ausente o vacía): "
    "ejecute el diseño de acero antes (Design > Steel Frame Design > Start Design/Check)."
)

GARBAGE_REASON = (
    "SAP2000 devolvió datos de otra tabla o columnas vacías: probablemente el diseño de acero "
    "no está ejecutado en esta sesión. Ejecútelo (Design > Steel Frame Design > Start "
    "Design/Check of Structure) y vuelva a leer."
)

_UNITLESS = "unitless"
_K_EXACT = {"K1 Major": "k1major", "K1 Minor": "k1minor", "K2 Major": "k2major",
            "K2 Minor": "k2minor", "K LTB": "kltb"}
_L_EXACT = {"L Major": ("xlmajor", "lmajor", "lratiomajor", "xlratiomajor"),
            "L Minor": ("xlminor", "lminor", "lratiominor", "xlratiominor"),
            "L LTB": ("xlltb", "lltb", "ltb", "lratioltb", "xlratioltb")}


@dataclass(frozen=True)
class DesignKResult:
    available: bool
    reason: str = ""
    table_key: str = ""
    columns: Mapping[str, str] = field(default_factory=dict)
    #: barra -> {campo: float|None}
    frames: Mapping[str, Mapping[str, float | None]] = field(default_factory=dict)
    #: barra -> {combo, station, ratio, rows, designSect}
    governing: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def _core(name: str) -> str:
    n = _norm(name)
    return n[: -len(_UNITLESS)] if n.endswith(_UNITLESS) else n


def is_garbage_table(columns: list[str]) -> bool:
    """Lectura BASURA de SAP2000 (visto en real con el diseño sin ejecutar): todas las
    columnas con nombre vacío, o ninguna columna de barra ni K/L reconocible. No es una
    tabla leída: nunca se deben presentar sus filas."""
    if not columns or all(not str(c).strip() for c in columns):
        return True
    return not find_design_columns(columns) and not any("frame" in _norm(c) for c in columns)


#: Firmas de GetTableForDisplayArray a probar, en orden. La primera es la VERIFICADA en
#: SAP2000 23.3.1 para la tabla de diseño; las demás son las de reader_utils.
_FIRMAS_TABLA: tuple[Callable[[Any, str], Any], ...] = (
    lambda db, key: db.GetTableForDisplayArray(key, [], "ALL", 0, [], 0, []),
    lambda db, key: db.GetTableForDisplayArray(key, "", "ALL"),
    lambda db, key: db.GetTableForDisplayArray(key, [], "", 0, [], 0, []),
    lambda db, key: db.GetTableForDisplayArray(key, "", "", 0, [], 0, []),
)


Tabla = tuple[list[str], list[list[Any]]]


def leer_tabla_valida(db: Any, key: str) -> tuple[Tabla | None, Tabla | None]:
    """(válida, basura). Prueba cada firma y devuelve la PRIMERA lectura válida (no basura y con
    filas). A diferencia de `_get_database_table_for_display`, una lectura basura de una firma no
    impide probar la siguiente. Si ninguna es válida y alguna fue basura, la primera basura
    (solo para diagnóstico: nunca se presenta). Si todas fallan con excepción, relanza la última.
    Solo lectura."""
    basura: Tabla | None = None
    respondio = False
    last_exc: Exception | None = None
    for call in _FIRMAS_TABLA:
        try:
            parsed = _parse_database_table_result(call(db, key))
        except Exception as exc:  # noqa: BLE001 - firma no soportada: se prueba la siguiente
            last_exc = exc
            continue
        respondio = True
        if parsed is None or not parsed[1]:
            continue
        tabla: Tabla = (list(parsed[0]), [list(r) for r in parsed[1]])
        if is_garbage_table(tabla[0]):
            basura = basura or tabla
            continue
        return tabla, None
    if basura is None and not respondio and last_exc is not None:
        raise last_exc
    return None, basura


def find_design_columns(columns: list[str]) -> dict[str, str]:
    """Columna real de cada K/L de diseño reconocida (subconjunto de DESIGN_FIELDS).
    Tolera «Unitless» al final y prefijos/sufijos con unidades entre paréntesis."""
    found: dict[str, str] = {}
    for fld, target in _K_EXACT.items():
        pool = [c for c in columns if _core(c) == target] or [
            c for c in columns if _core(c).endswith(target)
        ]
        if pool:
            found[fld] = min(pool, key=lambda c: len(_norm(c)))
    for fld, exact in _L_EXACT.items():
        pool = [c for c in columns if _core(c) in exact]
        if pool:
            found[fld] = min(pool, key=lambda c: len(_norm(c)))
    # Última red: reconocimiento de L del lector de overwrites (sin sufijo Unitless).
    stripped = {c: _core(c) for c in columns}
    extra = find_l_columns([v for v in stripped.values()])
    for fld, short in extra.items():
        if fld not in found:
            orig = next((c for c, v in stripped.items() if v == short), None)
            if orig:
                found[fld] = orig
    return found


def _pick(columns: list[str], exact: tuple[str, ...], contains: tuple[str, ...] = (),
          exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    cands = [c for c in columns if c not in exclude]
    for c in cands:
        if _core(c) in exact:
            return c
    pool = [c for c in cands if any(t in _core(c) for t in contains)]
    return min(pool, key=lambda c: len(_norm(c))) if pool else None


def design_table_candidates(available: list[str]) -> list[tuple[str, bool]]:
    """(clave, listada): primero las listadas cuyo nombre empieza por «Steel Design 2 -
    PMM Details» (AISC 360-16 antes), después la hipótesis directa si no estaba."""
    def prio(t: str) -> int:
        n = _norm(t)
        return 0 if "36016" in n else 1 if "360" in n else 2

    listed = sorted((t for t in available if _norm(t).startswith(_PREFIXES)), key=prio)
    keys = [(t, True) for t in listed]
    for name in (HYPOTHESIS_DESIGN_TABLE, ALT_DESIGN_TABLE):
        if name.lower() not in {t.lower() for t in listed}:
            keys.append((name, False))
    return keys


def _num(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def read_design_k_factors(model: Any) -> DesignKResult:
    """Lee los K/L calculados por el diseño. Solo lectura. Por barra devuelve la
    fila gobernante (mayor ratio; sin columna de ratio, la primera)."""
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return DesignKResult(False, "El modelo no expone DatabaseTables.")
    try:
        available = _available_database_tables(db)
    except Exception as exc:  # noqa: BLE001
        return DesignKResult(False, f"No se pudo listar las tablas del modelo: {exc}")
    tried: list[str] = []
    unrecognized: list[str] = []
    garbage: list[str] = []
    for key, listed in design_table_candidates(available):
        try:
            parsed, basura = leer_tabla_valida(db, key)
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{key} (error al leerla: {exc})")
            continue
        if parsed is None:
            if basura is not None:
                garbage.append(key)
            else:
                tried.append(f"{key} ({'vacía' if listed else 'no listada; sin datos'})")
            continue
        columns, rows = parsed
        cols = find_design_columns(columns)
        if not cols:
            unrecognized.append(f"{key} (sin columnas K/L reconocidas; columnas: {', '.join(columns[:20])})")
            continue
        return _build(key, listed, columns, rows, cols)
    if unrecognized:
        return DesignKResult(False, "La tabla de resultados de diseño no trae columnas K/L reconocidas "
                             "(nombres sin verificar contra SAP2000 real): " + "; ".join(unrecognized) + ".")
    if garbage:
        return DesignKResult(False, GARBAGE_REASON, notes=("Lectura descartada: " + "; ".join(garbage),))
    return DesignKResult(False, NOT_RUN_REASON)


def _build(key: str, listed: bool, columns: list[str], rows: list[list[Any]],
           cols: dict[str, str]) -> DesignKResult:
    idx = {c: i for i, c in enumerate(columns)}
    used = set(cols.values())
    frame_col = find_frame_column([c for c in columns if c not in used])
    combo_col = _pick(columns, ("combo", "combination", "loadcombo", "outputcase", "loadcase", "case"),
                      ("combo",), used | {frame_col or ""})
    # SAP2000 23.3.1 llama «Location» a la posición a lo largo de la barra (la estación).
    station_col = _pick(columns, ("station", "stationlocation", "location"), ("station",), used)
    ratio_col = _pick(columns, ("ratio", "pmmratio", "totalratio", "dcratio"), ("ratio",), used)
    sect_col = _pick(columns, ("designsect", "section", "designsection"), ("designsect",), used)
    notes: list[str] = []
    if not listed:
        notes.append(f"La tabla '{key}' no figuraba en la lista de tablas disponibles; se leyó directamente.")
    missing = [f for f in DESIGN_FIELDS if f not in cols]
    if missing:
        notes.append("La tabla no trae columna para: " + ", ".join(missing))
    if ratio_col is None:
        notes.append("Sin columna de ratio: se muestra la primera fila de cada barra, no la gobernante.")
    if frame_col is None:
        return DesignKResult(False, f"La tabla '{key}' no trae columna de barra reconocible.")

    def cell(row: list[Any], col: str | None) -> Any:
        if col is None:
            return None
        i = idx[col]
        return row[i] if i < len(row) else None

    groups: dict[str, list[list[Any]]] = {}
    for row in rows:
        name = str(cell(row, frame_col) or "").strip()
        if name:
            groups.setdefault(name, []).append(row)
    frames: dict[str, dict[str, float | None]] = {}
    governing: dict[str, dict[str, Any]] = {}
    for name, grp in groups.items():
        best = grp[0]
        if ratio_col is not None:
            scored = [(r, _num(cell(r, ratio_col))) for r in grp]
            scored = [(r, v) for r, v in scored if v is not None]
            if scored:
                best = max(scored, key=lambda p: p[1])[0]  # empate: la primera
            else:
                notes.append(f"barra {name}: ningún ratio interpretable; se usa la primera fila")
        vals: dict[str, float | None] = {}
        for fld, col in cols.items():
            v = _num(cell(best, col))
            if v is None and str(cell(best, col) or "").strip():
                notes.append(f"barra {name}, {fld}: valor no interpretable '{cell(best, col)}'")
            vals[fld] = v
        frames[name] = vals
        station = _num(cell(best, station_col))
        combo = cell(best, combo_col)
        governing[name] = {
            "combo": None if combo is None else str(combo),
            "station": station if station is not None else (None if cell(best, station_col) is None else str(cell(best, station_col))),
            "ratio": _num(cell(best, ratio_col)),
            "designSect": None if sect_col is None or cell(best, sect_col) is None else str(cell(best, sect_col)),
            "rows": len(grp),
        }
        for fld, col in cols.items():
            distinct = sorted({v for v in (_num(cell(r, col)) for r in grp) if v is not None})
            if len(distinct) > 1 and (max(distinct) - min(distinct)) > 1e-9:
                notes.append(
                    f"barra {name}: {fld} difiere entre filas ({', '.join(f'{d:g}' for d in distinct)}); "
                    f"se muestra el de la fila gobernante (combinación {governing[name]['combo']}, "
                    f"estación {governing[name]['station']})"
                )
    return DesignKResult(True, "", key, dict(cols), frames, governing, tuple(notes))
