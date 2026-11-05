"""Factores K de diseño de acero (AISC 360-16): lectura y escritura.

K1 mayor, K1 menor, K2 mayor y K2 menor son overwrites de diseño de cada
barra de acero. Este módulo los lee y los escribe a través de
`DatabaseTables`, y la razón es de honestidad, no de comodidad:

- **No hay documentación verificada de la API de diseño de acero en este
  repositorio.** Los números de ítem de `DesignSteel.AISC360_16` y los
  nombres exactos de tabla y campo no se pueden comprobar sin un SAP2000
  real. Inventarlos produciría un código que pasa los tests y falla en el
  modelo.
- Las tablas de `DatabaseTables` son **autodescriptivas**: la tabla de
  overwrites y sus columnas se localizan en ejecución por la lista de tablas
  disponibles y por los nombres de columna, y un K "Program Determined"
  llega como texto, distinguible de un número. Mismo contrato todo-o-nada que
  el resto de lecturas batch: si no se reconoce la tabla, no se lee y se dice
  por qué; nunca se rellena con un valor plausible.
- **Toda escritura se relee y se compara.** Nunca se anuncia éxito sin
  verificarlo, porque la semántica exacta de
  `SetTableForEditingArray` + `ApplyEditedTables` para overwrites es una
  hipótesis hasta probarla contra SAP2000.

Un K que el usuario no ha sobrescrito es "Program Determined": SAP2000 lo
calcula en el diseño y el valor guardado no es el que se usa. Se informa como
**automático**, sin valor, nunca como un número.

Relaciones de longitud no arriostrada (L Major / L Minor / L LTB): se leen
y se pueden escribir, y son OPCIONALES en la tabla. Si la tabla no las trae,
la lectura sigue siendo válida con las cuatro K y una nota, y pedir escribir
una L es un error claro sin tocar nada. Los nombres de columna que se buscan
son HIPÓTESIS sin verificar contra un SAP2000 real (usa el diagnóstico para
ver las columnas reales). Una L admite cualquier número positivo y finito (sin
tope, puede ser > 1) o "auto" (Program Determined).
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
# NOTA: respecto a esa copia vendorizada se añade lógica propia: lectura de las
# relaciones de longitud no arriostrada (L_FIELDS, find_l_columns, _parse_ratio,
# KFactorsResult.length_columns/lengths y su parte en _build_result y diagnose,
# y la escritura de L en _validate_changes/write_k_factors).
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping

from bridge.viewer.sap2000.reader_utils import (
    _available_database_tables,
    _get_database_table_for_display,
    _last_int,
)

#: Los cuatro factores, en el orden en que se muestran.
K_FIELDS: tuple[str, ...] = ("K1 Major", "K1 Minor", "K2 Major", "K2 Minor")

#: Rótulos para la interfaz, en español.
K_LABELS: dict[str, str] = {
    "K1 Major": "K1 mayor",
    "K1 Minor": "K1 menor",
    "K2 Major": "K2 mayor",
    "K2 Minor": "K2 menor",
}

#: Relaciones de longitud no arriostrada: opcionales en la tabla.
L_FIELDS: tuple[str, ...] = ("L Major", "L Minor", "L LTB")

#: Rótulos para la interfaz, en español.
L_LABELS: dict[str, str] = {
    "L Major": "L mayor",
    "L Minor": "L menor",
    "L LTB": "L LTB",
}

#: Nota cuando la tabla no trae ninguna columna de longitud.
NO_L_NOTE = "Esta tabla no trae relaciones de longitud no arriostrada"

#: Texto con el que SAP2000 marca un overwrite no sobrescrito.
AUTO_TEXT = "Program Determined"

#: Un K razonable está entre 0 y 10; fuera de eso es casi seguro un error de
#: tecleo, y se rechaza antes de tocar el modelo.
K_MIN = 0.1
K_MAX = 10.0

#: Tolerancia al comparar el valor releído con el pedido: SAP2000 puede
#: devolver el número redondeado a su formato de presentación.
VERIFY_TOLERANCE = 5e-4

KValueRequest = float | Literal["auto"]


class KFactorError(RuntimeError):
    """Error mostrable al usuario al leer o escribir factores K."""


class KFactorLockedError(KFactorError):
    """SAP2000 rechazó la escritura y el modelo está bloqueado.

    Lo captura quien aplica el cambio para ofrecer, con una segunda
    confirmación explícita, desbloquear: desbloquear descarta los
    resultados de análisis."""


class KFactorVerifyError(KFactorError):
    """La escritura no se pudo verificar al releer: el valor real en SAP2000
    no es el pedido. `result` lleva lo que se leyó, para informar del valor
    verdadero en vez de fingir éxito."""

    def __init__(self, message: str, result: "WriteResult") -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class KValue:
    """Un factor K de una barra.

    value: el número, o None si es automático o no hay dato.
    auto: True si es "Program Determined" (SAP2000 lo calcula en el diseño).
    Ambos vacíos (value None, auto False) significan SIN DATO."""

    value: float | None
    auto: bool = False

    @property
    def has_data(self) -> bool:
        return self.auto or self.value is not None


@dataclass(frozen=True)
class KFactorsResult:
    """Resultado de leer los factores K de todas las barras.

    available False => no se pudo leer; `reason` explica por qué y `frames`
    está vacío. Nunca se devuelven valores parciales o supuestos."""

    available: bool
    reason: str = ""
    table_key: str = ""
    frame_column: str = ""
    columns: Mapping[str, str] = field(default_factory=dict)
    frames: Mapping[str, Mapping[str, KValue]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    #: Columna real de cada L hallada (subconjunto de L_FIELDS, o vacío).
    length_columns: Mapping[str, str] = field(default_factory=dict)
    #: barra -> {L...: KValue}; solo las columnas L halladas. `frames` sigue
    #: llevando únicamente las K.
    lengths: Mapping[str, Mapping[str, KValue]] = field(default_factory=dict)
    #: Columna KLTB si existe, y sus valores por barra (solo interno: NO se
    #: expone en GET /k, cuyo contrato son 7 claves por barra).
    kltb_column: str = ""
    kltb: Mapping[str, KValue] = field(default_factory=dict)
    #: True si en esta tabla el valor 0 de K/L significa «Program Determined»
    #: (SAP2000 real); entonces «auto» se escribe como "0". HIPÓTESIS sin verificar.
    auto_as_zero: bool = False


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: KValue
    requested: KValueRequest
    after: KValue
    ok: bool


@dataclass(frozen=True)
class WriteResult:
    frame: str
    table_key: str
    changes: tuple[FieldChange, ...]

    @property
    def verified(self) -> bool:
        return all(c.ok for c in self.changes)


# ─────────────────────────────────────────────────────────────────────────────
# Descubrimiento de la tabla
# ─────────────────────────────────────────────────────────────────────────────


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def find_k_columns(columns: list[str]) -> dict[str, str] | None:
    """Columna real de cada factor K, o None si falta alguno.

    Se compara por nombre normalizado (sin espacios, signos ni mayúsculas) y
    se acepta que la columna se llame "K1Major", "K1 Major" o "Effective
    Length Factor (K1 Major)". Con varias candidatas gana la de nombre más
    corto, para no confundir "K1Major" con una columna derivada."""
    found: dict[str, str] = {}
    for fld in K_FIELDS:
        target = _norm(fld)
        exact_end = [c for c in columns if _norm(c).endswith(target)]
        contains = [c for c in columns if target in _norm(c)]
        pool = exact_end or contains
        if not pool:
            return None
        found[fld] = min(pool, key=lambda c: len(_norm(c)))
    return found


#: «xlmajor/xlminor/xlltb» son los nombres REALES de la tabla «Overwrites - Steel
#: Design - AISC 360-16» (comprobado con la sonda en un SAP2000 real); el resto
#: son variantes de hipótesis. Se admite «Unitless» al final (ver `_core_name`).
_L_EXACT: dict[str, tuple[str, ...]] = {
    "L Major": ("xlmajor", "lmajor"),
    "L Minor": ("xlminor", "lminor"),
    "L LTB": ("xlltb", "ltb", "lltb"),
}
_L_AXIS: dict[str, str] = {"L Major": "major", "L Minor": "minor", "L LTB": "ltb"}


def _core_name(text: Any) -> str:
    """Nombre normalizado sin el sufijo «Unitless» de la interfaz."""
    n = _norm(text)
    return n[: -len("unitless")] if n.endswith("unitless") else n


def find_kltb_column(columns: list[str]) -> str | None:
    """Columna KLTB (opcional; solo se usa internamente y en /k/design, no
    forma parte de las 7 claves de GET /k)."""
    pool = [c for c in columns if _core_name(c) == "kltb"]
    return min(pool, key=lambda c: len(_norm(c))) if pool else None


def find_l_columns(columns: list[str]) -> dict[str, str]:
    """Columnas de relación de longitud no arriostrada que se reconocen.

    OPCIONAL: devuelve un subconjunto de L_FIELDS, o {} si no hay ninguna.
    Acepta "LRatioMajor", "L Ratio Major", "Unbraced Length Ratio (Major)" y
    "L Major" (y Minor/LTB). Se exige que el nombre normalizado contenga
    "lratio" o "unbracedlengthratio", o sea exactamente lmajor/lminor/ltb/
    lltb, para no confundir "K1Major" ni columnas derivadas. Con varias
    candidatas gana la de nombre más corto. Los nombres son una hipótesis."""
    found: dict[str, str] = {}
    for fld in L_FIELDS:
        axis = _L_AXIS[fld]
        pool = []
        for c in columns:
            n = _core_name(c)
            if n in _L_EXACT[fld] or (axis in n and ("lratio" in n or "unbracedlengthratio" in n)):
                pool.append(c)
        if pool:
            found[fld] = min(pool, key=lambda c: len(_norm(c)))
    return found


def _looks_like_l(columns: list[str]) -> list[str]:
    """Columnas que parecen de longitud pero no se reconocieron (diagnóstico)."""
    known = set(find_l_columns(columns).values())
    out = []
    for c in columns:
        n = _norm(c)
        if c not in known and (
            "lratio" in n
            or "unbraced" in n
            or (n.startswith("l") and any(a in n for a in ("major", "minor", "ltb")))
        ):
            out.append(c)
    return out


def find_frame_column(columns: list[str]) -> str | None:
    for col in columns:
        if _norm(col) == "frame":
            return col
    for col in columns:
        if "frame" in _norm(col):
            return col
    return columns[0] if columns else None


#: Nombres de tabla que se intentan leer directamente aunque SAP2000 no los
#: liste (solo lista las tablas con datos). TODOS son HIPÓTESIS sin verificar
#: contra un SAP2000 real: el primero es el nombre de CSI que indicó el
#: usuario para AISC 360-16; los otros dos son patrones sugeridos, no
#: comprobados. Se leen en este orden y gana el primero legible con columnas K.
HYPOTHESIS_TABLE_NAMES: tuple[str, ...] = (
    # Nombre REAL (sonda en SAP2000 real, AISC 360-16): listada, 45 columnas.
    "Overwrites - Steel Design - AISC 360-16",
    "Steel Design Overwrites 1 - General - AISC 360-16",
    "Steel Design 2 - Overwrites - AISC 360-16",
    "Frame Overwrites - Steel Design - AISC 360-16",
)


def _overwrite_table_candidates(available: list[str]) -> list[str]:
    """Tablas de overwrites de diseño de acero, con las de AISC 360-16
    primero. Se aceptan las de otro código (SAP2000 solo expone las del
    código activo) pero el resultado dice de qué tabla salen los datos.

    Primero las que contienen «steel» y «overwrite»; después, como red de
    seguridad por si el nombre real varía, cualquier otra cuyo nombre
    contenga «overwrite» o «steel design» (sin distinguir mayúsculas)."""

    def prioridad(t: str) -> int:
        n = _norm(t)
        if "36016" in n:
            return 0
        if "360" in n:
            return 1
        return 2

    steel = [t for t in available if "steel" in t.lower() and "overwrite" in t.lower()]
    otras = [
        t
        for t in available
        if t not in steel
        and ("overwrite" in t.lower() or "steel design" in t.lower())
        # «Steel Design 2 - PMM Details» son RESULTADOS de diseño, no overwrites.
        and "pmmdetails" not in _norm(t)
    ]
    # Las que dicen «overwrite» antes que las que solo dicen «steel design».
    otras.sort(key=lambda t: (0 if "overwrite" in t.lower() else 1, prioridad(t)))
    return sorted(steel, key=prioridad) + otras


def table_probe_keys(available: list[str]) -> list[tuple[str, bool]]:
    """Claves a intentar leer: (clave, listada). Primero las candidatas de la
    lista de tablas disponibles; después las hipótesis de
    `HYPOTHESIS_TABLE_NAMES` que SAP2000 no lista (sin repetir ninguna)."""
    keys: list[tuple[str, bool]] = [(t, True) for t in _overwrite_table_candidates(available)]
    vistos = {k.lower() for k, _ in keys}
    listadas = {t.lower() for t in available}
    for name in HYPOTHESIS_TABLE_NAMES:
        if name.lower() in vistos:
            continue
        vistos.add(name.lower())
        keys.append((name, name.lower() in listadas))
    return keys


def _parse_k(raw: Any) -> tuple[KValue, str | None]:
    """Interpreta una celda. Devuelve el valor y, si el texto no se entiende,
    una nota; nunca inventa un número."""
    if raw is None:
        return KValue(None, False), None
    text = str(raw).strip()
    if text == "":
        return KValue(None, False), None
    low = text.lower()
    if "program" in low or low == "auto" or "automatic" in low:
        return KValue(None, True), None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return KValue(None, False), f"valor no interpretable '{text}'"
    if not math.isfinite(value):
        return KValue(None, False), f"valor no finito '{text}'"
    return KValue(value, False), None


def _es_cero(raw: Any) -> bool:
    """True si la celda es el número 0 (en SAP2000 real, K/XL = 0 es «Program Determined»)."""
    if raw is None or isinstance(raw, bool):
        return False
    try:
        return float(str(raw).strip().replace(",", ".")) == 0.0
    except ValueError:
        return False


def _parse_ratio(raw: Any) -> tuple[KValue, str | None]:
    """Como `_parse_k`, pero un ratio <= 0 o no finito es SIN DATO con nota.
    "Program Determined" es automático (sin valor). Sin tope superior."""
    kv, note = _parse_k(raw)
    if kv.value is not None and kv.value <= 0:
        return KValue(None, False), f"relación no positiva '{str(raw).strip()}'"
    return kv, note


def read_k_factors(model: Any) -> KFactorsResult:
    """Lee los cuatro factores K de todas las barras con una sola lectura de
    tabla. Todo-o-nada: si no se reconoce la tabla, `available` es False con
    el motivo y no se devuelve nada."""
    db = getattr(model, "DatabaseTables", None)
    if db is None:
        return KFactorsResult(False, "El modelo no expone DatabaseTables.")
    try:
        available = _available_database_tables(db)
    except Exception as exc:  # noqa: BLE001
        return KFactorsResult(False, f"No se pudo listar las tablas del modelo: {exc}")
    probes = table_probe_keys(available)
    listed = [k for k, listada in probes if listada]

    rejected: list[str] = []
    for key, listada in probes:
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception as exc:  # noqa: BLE001
            if listada:
                rejected.append(f"{key} (error al leerla: {exc})")
            else:
                rejected.append(f"{key} (no listada; lectura directa fallida: {exc})")
            continue
        if parsed is None:
            rejected.append(f"{key} ({'no se pudo interpretar' if listada else 'no listada; lectura directa sin datos'})")
            continue
        columns, rows = parsed
        k_cols = find_k_columns(columns)
        frame_col = find_frame_column(columns)
        if k_cols is None or frame_col is None:
            rejected.append(f"{key} (sin columnas K1/K2 mayor/menor; columnas: {', '.join(columns[:12])})")
            continue
        result = _build_result(key, columns, rows, k_cols, frame_col, find_l_columns(columns))
        if not listada:
            result = replace(
                result,
                notes=(f"La tabla '{key}' no figuraba en la lista de tablas disponibles; se leyó directamente.",)
                + result.notes,
            )
        return result

    if not listed:
        return KFactorsResult(
            False,
            "El modelo no tiene ninguna tabla de overwrites de diseño de acero "
            "(ni listada ni legible por nombre directo). "
            "Comprueba que el código de diseño de acero esté asignado, que alguna barra "
            "tenga un overwrite asignado y usa "
            "'Modelo → Diagnóstico de factores K' para ver qué tablas hay. "
            "Intentos: " + "; ".join(rejected) + ".",
        )
    return KFactorsResult(
        False,
        "Ninguna tabla de overwrites de acero trae los factores K1/K2 mayor/menor. "
        "Tablas revisadas: " + "; ".join(rejected) + ".",
    )


def _build_result(
    key: str,
    columns: list[str],
    rows: list[list[Any]],
    k_cols: dict[str, str],
    frame_col: str,
    l_cols: Mapping[str, str] | None = None,
) -> KFactorsResult:
    l_cols = dict(l_cols or {})
    index = {c: i for i, c in enumerate(columns)}
    fi = index[frame_col]
    frames: dict[str, dict[str, KValue]] = {}
    lengths: dict[str, dict[str, KValue]] = {}
    notes: list[str] = []
    if not l_cols:
        notes.append(NO_L_NOTE)
    elif len(l_cols) < len(L_FIELDS):
        faltan = ", ".join(f for f in L_FIELDS if f not in l_cols)
        notes.append(f"{NO_L_NOTE} para: {faltan}")
    kltb_col = find_kltb_column(columns)
    kltb: dict[str, KValue] = {}
    # ¿Representa esta tabla «automático» con 0 (SAP2000 real) en lugar de texto?
    celdas_kl = [r[index[c]] for r in rows for c in [*k_cols.values(), *l_cols.values()] if index[c] < len(r)]
    hay_texto_auto = any("program" in str(v).lower() for v in celdas_kl)
    hay_cero = any(_es_cero(v) for v in celdas_kl)
    auto_as_zero = hay_cero and not hay_texto_auto
    for row in rows:
        if fi >= len(row):
            continue
        name = str(row[fi]).strip()
        if not name:
            continue
        if name in frames:
            notes.append(f"barra {name} repetida en la tabla: se conserva la primera fila")
            continue
        values: dict[str, KValue] = {}
        for fld, col in k_cols.items():
            ci = index[col]
            raw = row[ci] if ci < len(row) else None
            value, note = _parse_k(raw)
            if auto_as_zero and _es_cero(raw):
                value, note = KValue(None, True), None  # 0 = Program Determined
            if note:
                notes.append(f"barra {name}, {K_LABELS[fld]}: {note}")
            values[fld] = value
        frames[name] = values
        if kltb_col:
            ci = index[kltb_col]
            kv, _ = _parse_k(row[ci] if ci < len(row) else None)
            kltb[name] = KValue(None, True) if auto_as_zero and _es_cero(row[ci] if ci < len(row) else None) else kv
        lvals: dict[str, KValue] = {}
        for fld, col in l_cols.items():
            ci = index[col]
            raw = row[ci] if ci < len(row) else None
            value, note = _parse_ratio(raw)
            if auto_as_zero and _es_cero(raw):
                value, note = KValue(None, True), None  # 0 = Program Determined
            if note:
                notes.append(f"barra {name}, {L_LABELS[fld]}: {note}")
            lvals[fld] = value
        if lvals:
            lengths[name] = lvals
    if auto_as_zero:
        notes.append("En esta tabla el valor 0 de K/L se interpreta como «Program Determined» (automático).")
    return KFactorsResult(
        True, "", key, frame_col, dict(k_cols), frames, tuple(notes), l_cols, lengths,
        kltb_col or "", kltb, auto_as_zero,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────────────────────


def _validate_changes(cambios: Mapping[str, KValueRequest]) -> dict[str, KValueRequest]:
    if not cambios:
        raise ValueError("No hay ningún cambio que aplicar.")
    limpio: dict[str, KValueRequest] = {}
    for fld, valor in cambios.items():
        es_l = fld in L_FIELDS
        if fld not in K_FIELDS and not es_l:
            raise ValueError(
                f"Campo desconocido: {fld!r}. Usa uno de {', '.join(K_FIELDS + L_FIELDS)}."
            )
        label = L_LABELS[fld] if es_l else K_LABELS[fld]
        if valor == "auto":
            limpio[fld] = "auto"
            continue
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(f"{label}: el valor debe ser un número o 'auto', no {valor!r}.")
        v = float(valor)
        if es_l:
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f"{label}: {valor} fuera de rango; debe ser un número positivo y finito.")
        elif not math.isfinite(v) or not (K_MIN <= v <= K_MAX):
            raise ValueError(
                f"{label}: {valor} fuera de rango; un K razonable está entre {K_MIN} y {K_MAX}."
            )
        limpio[fld] = v
    return limpio


def field_label(fld: str) -> str:
    return L_LABELS.get(fld) or K_LABELS.get(fld) or fld


def _format_k(valor: KValueRequest, auto_as_zero: bool = False) -> str:
    if valor == "auto":
        # HIPÓTESIS sin verificar: si la tabla devuelve 0 como «Program Determined»,
        # se escribe "0"; si no, el texto «Program Determined».
        return "0" if auto_as_zero else AUTO_TEXT
    return f"{float(valor):.6g}"


def _matches(expected: KValueRequest, got: KValue) -> bool:
    if expected == "auto":
        return got.auto
    return (not got.auto) and got.value is not None and abs(got.value - float(expected)) <= VERIFY_TOLERANCE


def _model_locked(model: Any) -> bool:
    try:
        return bool(model.GetModelIsLocked())
    except Exception:  # noqa: BLE001
        return False


def write_k_factors(
    model: Any, frame: str, cambios: Mapping[str, KValueRequest]
) -> WriteResult:
    """Escribe factores K y/o relaciones L de UNA barra y los verifica releyendo.

    `cambios` es ``{"K1 Major": 0.8, "K2 Minor": "auto", "L Major": 2.0}``. Solo se
    tocan los campos indicados. No crea overwrites en una barra que no los
    tenga (una barra de hormigón no tiene K de acero).

    Lanza `KFactorLockedError` si SAP2000 rechaza la escritura con el modelo
    bloqueado (sin desbloquear por su cuenta), `KFactorVerifyError` si al
    releer el valor no es el pedido, y `KFactorError` en cualquier otro fallo.
    Nunca devuelve un resultado sin haberlo verificado."""
    cambios = _validate_changes(cambios)
    antes = read_k_factors(model)
    if not antes.available:
        raise KFactorError(antes.reason)
    if frame not in antes.frames:
        raise KFactorError(
            f"La barra {frame} no tiene overwrites de diseño de acero en '{antes.table_key}' "
            "(¿no es una barra de acero o no tiene diseño asignado?). No se crea ninguno."
        )

    todas = {**antes.columns, **antes.length_columns}
    sin_columna = [f for f in cambios if f not in todas]
    if sin_columna:
        raise KFactorError(
            f"La tabla '{antes.table_key}' no trae la columna de "
            + ", ".join(L_LABELS.get(f, f) for f in sin_columna)
            + ". No se escribe nada."
        )

    db = model.DatabaseTables
    fields = [antes.frame_column] + [todas[f] for f in cambios]
    data = [frame] + [_format_k(v, antes.auto_as_zero) for v in cambios.values()]

    try:
        res = db.SetTableForEditingArray(antes.table_key, 0, fields, 1, data)
    except Exception as exc:  # noqa: BLE001
        raise KFactorError(f"SAP2000 no aceptó la edición de la tabla '{antes.table_key}': {exc}") from exc
    if _last_int(list(res) if isinstance(res, (tuple, list)) else [res]) not in (0, None):
        _cancel_editing(db)
        raise KFactorError(
            f"SAP2000 rechazó los datos de edición de '{antes.table_key}' (código {_last_int(list(res))})."
        )

    try:
        applied = db.ApplyEditedTables(True, 0, 0, 0, 0, "")
    except Exception as exc:  # noqa: BLE001
        _cancel_editing(db)
        raise KFactorError(f"No se pudieron aplicar las ediciones: {exc}") from exc
    _check_apply(model, db, applied)

    despues = read_k_factors(model)
    if not despues.available or frame not in despues.frames:
        raise KFactorError(
            "Se aplicó la edición pero no se pudo releer la barra para verificarla: "
            + (despues.reason or f"{frame} ya no aparece en la tabla")
        )
    def _val(res: KFactorsResult, fld: str) -> KValue:
        if fld in L_FIELDS:
            return res.lengths.get(frame, {}).get(fld, KValue(None, False))
        return res.frames[frame][fld]

    changes = tuple(
        FieldChange(
            field=fld,
            before=_val(antes, fld),
            requested=valor,
            after=_val(despues, fld),
            ok=_matches(valor, _val(despues, fld)),
        )
        for fld, valor in cambios.items()
    )
    result = WriteResult(frame, antes.table_key, changes)
    if not result.verified:
        malos = [c for c in changes if not c.ok]
        detalle = "; ".join(
            f"{field_label(c.field)}: pedido {_format_k(c.requested, antes.auto_as_zero)}, en SAP2000 {_describe(c.after)}"
            for c in malos
        )
        raise KFactorVerifyError(
            f"SAP2000 no dejó los valores pedidos en la barra {frame} ({detalle}).", result
        )
    return result


def _describe(v: KValue) -> str:
    if v.auto:
        return "automático"
    return "sin dato" if v.value is None else f"{v.value:g}"


def _cancel_editing(db: Any) -> None:
    try:
        db.CancelTableEditing()
    except Exception:  # noqa: BLE001
        pass


def _check_apply(model: Any, db: Any, applied: Any) -> None:
    """Interpreta el resultado de ApplyEditedTables: (NumFatalErrors,
    NumErrorMsgs, NumWarnMsgs, NumInfoMsgs, ImportLog, ret)."""
    items = list(applied) if isinstance(applied, (tuple, list)) else [applied]
    ints = [i for i in items if isinstance(i, int) and not isinstance(i, bool)]
    texts = [t for t in items if isinstance(t, str) and t.strip()]
    ret = ints[-1] if ints else 0
    fatal = ints[0] if len(ints) > 1 else 0
    if ret == 0 and fatal == 0:
        return
    _cancel_editing(db)
    log = " ".join(texts)[:400]
    if _model_locked(model):
        raise KFactorLockedError(
            "SAP2000 rechazó la escritura con el modelo bloqueado"
            + (f" ({log})" if log else "")
            + "."
        )
    raise KFactorError(
        f"SAP2000 rechazó la escritura (errores fatales: {fatal}, código: {ret})"
        + (f": {log}" if log else ".")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Diagnóstico
# ─────────────────────────────────────────────────────────────────────────────


def diagnose(model: Any, max_rows: int = 3) -> str:
    """Informe de texto para pegar al desarrollador cuando la lectura no
    reconoce la tabla. Es la única vía de corregir nombres sin acceso a un
    SAP2000 real."""
    out: list[str] = ["DIAGNÓSTICO DE FACTORES K", ""]

    try:
        code = model.DesignSteel.GetCode()
        out.append(f"Código de diseño de acero (DesignSteel.GetCode): {code!r}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"Código de diseño de acero: no disponible ({exc})")

    db = getattr(model, "DatabaseTables", None)
    if db is None:
        out.append("El modelo no expone DatabaseTables.")
        return "\n".join(out)
    try:
        available = _available_database_tables(db)
    except Exception as exc:  # noqa: BLE001
        out.append(f"No se pudo listar las tablas: {exc}")
        return "\n".join(out)
    out.append(f"Tablas disponibles: {len(available)}")

    overwrite = [t for t in available if "overwrite" in t.lower()]
    out.append("")
    out.append(f"Tablas con 'Overwrite' en el nombre ({len(overwrite)}):")
    out.extend(f"  - {t}" for t in overwrite[:60])
    if not overwrite:
        out.append("  (ninguna)")

    probes = table_probe_keys(available)
    candidates = [k for k, listada in probes if listada]
    out.append("")
    out.append(f"Candidatas de acero listadas ({len(candidates)}): {candidates}")
    out.append(
        "No listadas, probadas por lectura directa (HIPÓTESIS): "
        f"{[k for k, listada in probes if not listada]}"
    )
    for key in [k for k, _ in probes][:6]:
        out.append("")
        out.append(f"== {key}")
        try:
            parsed = _get_database_table_for_display(db, key)
        except Exception as exc:  # noqa: BLE001
            out.append(f"  error al leerla: {exc}")
            continue
        if parsed is None:
            out.append("  no se pudo interpretar el resultado")
            continue
        columns, rows = parsed
        out.append(f"  columnas ({len(columns)}): {columns}")
        out.append(f"  filas: {len(rows)}")
        for row in rows[:max_rows]:
            out.append(f"  {row}")
        out.append(f"  columnas K reconocidas: {find_k_columns(columns)}")
        out.append(f"  columnas L reconocidas: {find_l_columns(columns)}")
        out.append(f"  columnas que parecen L y no se reconocieron: {_looks_like_l(columns)}")
        out.append(f"  columna de barra: {find_frame_column(columns)}")

    out.append("")
    result = read_k_factors(model)
    out.append(
        f"Resultado de la lectura: {'OK' if result.available else 'NO DISPONIBLE'}"
        + (f" — {result.reason}" if result.reason else f" ({len(result.frames)} barras, tabla '{result.table_key}')")
    )
    for note in result.notes[:10]:
        out.append(f"  nota: {note}")
    return "\n".join(out)
