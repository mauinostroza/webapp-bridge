"""Esfuerzos de shell por esquina de elemento de malla (solo lectura): ``POST viewer/foundations/shell-forces``.

Entrega a la web la malla de análisis en 3D, los valores por esquina de elemento (``F11…V23``) por caso o
combinación y ``StepType``, y los ejes locales de cada elemento, para pintar mapas de esfuerzos y recrear cortes
(integración por franja, Wood-Armer, envolventes) sin crear section cuts nativos (que obligarían a desbloquear el
modelo y borrarían los resultados). El puente entrega datos crudos por esquina: no promedia, no gira tensores ni corta.

Garantías (ver docs/SAP_ACTIONS.md, «Fundaciones SAP2000 en el Viewer»):

* No modifica el modelo: no desbloquea, no analiza, no edita tablas ni crea objetos. Con el modelo sin analizar responde
  ``409 model_not_run`` ANTES de tocar la selección de salida o las unidades.
* Toda la lectura se hace en Ton_m_C (``bridge.actions._with_ton_m_c``) y las unidades del usuario se restauran siempre.
* La selección de salida del usuario y, si se puede capturar, la selección «para mostrar» de las tablas quedan como
  estaban (``preserve_output_selection`` y ``_preservar_seleccion_tablas``).
* Cada respaldo usado se dice en ``warnings``; un valor que no se lee es ``null`` o una fila omitida con aviso, nunca 0.

Nombres de tablas, columnas y firmas de COM NO verificados contra SAP2000 real: las listas de alias están al principio del
módulo para ajustarlas tras la sonda sin cambiar el contrato.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from bridge.actions import (
    _call_display_table,
    _get_name_list,
    _parse_generic_table,
    _response_arrays,
    _with_ton_m_c,
    load_cases_and_combos,
    model_is_locked,
)
from bridge.foundations_shells import MSG_CORRIDO_SIN_RESULTADOS, MSG_MODELO_NO_CORRIDO
from bridge.viewer.core import results_bulk as bulk
from bridge.seleccion_salida import _marcar
from bridge.viewer.core.results import ResultsError

log = logging.getLogger(__name__)

COMPONENTES = ("F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23")
MAX_FILAS = 200_000  # filas de resultados por respuesta; se corta en el límite de un caso completo

# Tablas y columnas (candidatos sin distinguir mayúsculas). NO verificados contra SAP2000 real.
TABLA_AREAS = "Objects And Elements - Areas"
TABLA_JOINTS = "Objects And Elements - Joints"
TABLA_FUERZAS = "Element Forces - Area Shells"
ALIAS_AREA_ELM = ("areaelem", "areaelm", "element", "elem", "elm")
ALIAS_AREA_OBJ = ("areaobject", "area", "object", "areaobj")
ALIAS_AREA_NUDOS = (
    ("elemjt1", "elemjt2", "elemjt3", "elemjt4"),
    ("joint1", "joint2", "joint3", "joint4"),
    ("jointelem1", "jointelem2", "jointelem3", "jointelem4"),
)
ALIAS_JOINT_NOMBRE = ("jointelem", "elemjoint", "joint", "jointobject", "pointelm", "point")
ALIAS_JOINT_X = ("globalx", "xorr", "x")
ALIAS_JOINT_Y = ("globaly", "y")
ALIAS_JOINT_Z = ("globalz", "z")
ALIAS_F_ELM = ("areaelem", "areaelm", "elm", "element")
ALIAS_F_ESQUINA = ("joint", "pointelm", "point")
ALIAS_F_CASO = ("outputcase", "loadcase", "case")
ALIAS_F_STEPTYPE = ("steptype",)
ALIAS_F_STEPNUM = ("stepnum",)

# Orden de los arreglos de Results.AreaForceShell (tras el código de retorno y el conteo).
_ORDEN_API = (
    "Obj", "Elm", "PointElm", "LoadCase", "StepType", "StepNum", "F11", "F22", "F12", "FMax", "FMin",
    "FAngle", "FVM", "M11", "M22", "M12", "MMax", "MMin", "MAngle", "V13", "V23", "VMax", "VAngle",
)  # fmt: skip
_IDX_API = {nombre: i for i, nombre in enumerate(_ORDEN_API)}

_TOL_UNITARIO = 1e-3
_ORDEN_STEP = {"": 0, "Max": 1, "Min": 2, "Step": 3}


# --------------------------------------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------------------------------------


def _finito(valor: Any) -> float | None:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _indice(lower: dict[str, int], candidatos: tuple[str, ...]) -> int | None:
    return next((lower[c] for c in candidatos if c in lower), None)


def _tabla(model: Any, nombre: str) -> tuple[list[str], list[str]]:
    return _parse_generic_table(_call_display_table(model, nombre))


def _paso(texto: Any) -> str:
    t = str(texto or "").strip().lower()
    if t == "":
        return ""
    if t == "max":
        return "Max"
    if t == "min":
        return "Min"
    return "Step"


def _resta(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cruz(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _punto(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norma(a: tuple[float, ...]) -> float:
    return math.sqrt(_punto(a, a))


def _unitario(a: tuple[float, ...]) -> tuple[float, float, float] | None:
    n = _norma(a)
    return None if n < 1e-12 else (a[0] / n, a[1] / n, a[2] / n)


# --------------------------------------------------------------------------------------------------------------------
# Selección «para mostrar» de DatabaseTables (las tablas de resultados pueden obedecerla en lugar de Results.Setup)
# --------------------------------------------------------------------------------------------------------------------


def _lista_str(raw: Any) -> list[str] | None:
    """Primer arreglo de textos de una respuesta COM; ``None`` si el código de retorno (primero o último) no es 0."""
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    ok = (isinstance(raw[-1], int) and raw[-1] == 0) or (isinstance(raw[0], int) and raw[0] == 0)
    if not ok:
        return None
    for v in raw:
        if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
            return [str(x) for x in v]
    return []


def _capturar_mostrar(model: Any) -> tuple[list[str], list[str]] | None:
    """(casos, combinaciones) marcados «para mostrar»; ``None`` si no se pueden capturar (no se toca nada)."""
    db = model.DatabaseTables
    try:
        casos = _lista_str(db.GetLoadCasesSelectedForDisplay([]))
        combos = _lista_str(db.GetLoadCombinationsSelectedForDisplay([]))
    except Exception:  # noqa: BLE001
        return None
    if casos is None or combos is None:
        return None
    return casos, combos


@contextmanager
def _preservar_seleccion_tablas(
    model: Any, casos: list[str], combos: list[str], avisos: list[str]
) -> Iterator[bool]:
    """Marca para mostrar solo lo pedido y lo devuelve al terminar. Cede ``True`` si pudo tocar esa selección.

    Si no se puede capturar la selección del usuario, no se toca (cede ``False``) y la lectura usa los respaldos.
    """
    guardada = _capturar_mostrar(model)
    if guardada is None:
        yield False
        return
    db = model.DatabaseTables
    try:
        db.SetLoadCasesSelectedForDisplay(list(casos))
        db.SetLoadCombinationsSelectedForDisplay(list(combos))
    except Exception:  # noqa: BLE001
        avisos.append("No se pudo cambiar la selección «para mostrar» de las tablas; se usan los respaldos.")
        _restaurar_mostrar(model, guardada, avisos)
        yield False
        return
    try:
        yield True
    finally:
        _restaurar_mostrar(model, guardada, avisos)


def _restaurar_mostrar(model: Any, guardada: tuple[list[str], list[str]], avisos: list[str]) -> None:
    try:
        model.DatabaseTables.SetLoadCasesSelectedForDisplay(list(guardada[0]))
        model.DatabaseTables.SetLoadCombinationsSelectedForDisplay(list(guardada[1]))
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"No se pudo restaurar la selección «para mostrar» de las tablas de SAP2000: {exc}")


# --------------------------------------------------------------------------------------------------------------------
# Geometría de la malla de análisis
# --------------------------------------------------------------------------------------------------------------------


def _coord_de_respuesta(raw: Any) -> tuple[float, float, float] | None:
    """(x, y, z) de una respuesta COM con el código de retorno al inicio, al final o ausente."""
    if not isinstance(raw, (list, tuple)):
        return None
    flotantes = [v for v in raw if isinstance(v, float)]
    if len(flotantes) == 3:
        vals: list[Any] = flotantes
    elif len(raw) >= 4 and isinstance(raw[0], int) and not isinstance(raw[0], bool):
        if int(raw[0]) != 0:
            return None
        vals = list(raw[1:4])
    elif len(raw) >= 4:
        if isinstance(raw[3], int) and int(raw[3]) != 0:
            return None
        vals = list(raw[0:3])
    elif len(raw) == 3:
        vals = list(raw)
    else:
        return None
    xyz = [_finito(v) for v in vals]
    return None if any(v is None for v in xyz) else (xyz[0], xyz[1], xyz[2])  # type: ignore[return-value]


def _coord_punto_elemento(model: Any, nombre: str) -> tuple[float, float, float] | None:
    """Coordenadas de un nudo de ELEMENTO (``PointElm``): los nudos internos de una malla automática no son ``PointObj``."""
    for miembro in (model.PointElm, model.PointObj):
        for args in ((nombre,), (nombre, 0.0, 0.0, 0.0)):
            try:
                xyz = _coord_de_respuesta(miembro.GetCoordCartesian(*args))
            except Exception:  # noqa: BLE001
                continue
            if xyz is not None:
                return xyz
    return None


def _geometria_tabla(
    model: Any, areas: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[float, float, float]]] | None:
    """Geometría por tablas (lote). ``None`` si las tablas no existen o no traen las columnas esperadas."""
    cols_a, datos_a = _tabla(model, TABLA_AREAS)
    cols_j, datos_j = _tabla(model, TABLA_JOINTS)
    if not cols_a or not datos_a or not cols_j or not datos_j:
        return None
    la = {c.lower(): i for i, c in enumerate(cols_a)}
    lj = {c.lower(): i for i, c in enumerate(cols_j)}
    i_elm, i_obj = _indice(la, ALIAS_AREA_ELM), _indice(la, ALIAS_AREA_OBJ)
    i_nudos: list[int] | None = None
    for grupo in ALIAS_AREA_NUDOS:
        idx = [la[c] for c in grupo if c in la]
        if len(idx) >= 3:
            i_nudos = idx
            break
    i_nom, i_x, i_y, i_z = (
        _indice(lj, ALIAS_JOINT_NOMBRE),
        _indice(lj, ALIAS_JOINT_X),
        _indice(lj, ALIAS_JOINT_Y),
        _indice(lj, ALIAS_JOINT_Z),
    )
    if None in (i_elm, i_obj, i_nom, i_x, i_y, i_z) or i_nudos is None:
        return None
    pedidas = set(areas)
    por_area: dict[str, list[tuple[str, list[str]]]] = {a: [] for a in areas}
    n_cols = len(cols_a)
    for f in range(len(datos_a) // n_cols):
        fila = datos_a[f * n_cols : (f + 1) * n_cols]
        area = str(fila[i_obj])  # type: ignore[index]
        if area not in pedidas:
            continue
        nudos = [str(fila[i]).strip() for i in i_nudos if str(fila[i]).strip()]
        por_area[area].append((str(fila[i_elm]), nudos))  # type: ignore[index]
    coords: dict[str, tuple[float, float, float]] = {}
    n_cj = len(cols_j)
    for f in range(len(datos_j) // n_cj):
        fila = datos_j[f * n_cj : (f + 1) * n_cj]
        xyz = [_finito(fila[i]) for i in (i_x, i_y, i_z)]  # type: ignore[index]
        if all(v is not None for v in xyz):
            coords[str(fila[i_nom])] = (xyz[0], xyz[1], xyz[2])  # type: ignore[index, assignment]
    elementos: dict[str, dict[str, Any]] = {}
    for area in areas:
        for elm, nudos in por_area[area]:
            elementos[elm] = {"area": area, "nodes": nudos}
    return (elementos, coords) if elementos else None


def _nombres_de(raw: Any) -> list[str]:
    for arr in _response_arrays(raw):
        if arr and all(isinstance(x, str) for x in arr):
            return list(arr)
    return []


def _geometria_objeto(
    model: Any, areas: list[str], avisos: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[float, float, float]]]:
    """Respaldo por objeto: ``AreaObj.GetElm`` → ``AreaElm.GetPoints`` → ``PointElm.GetCoordCartesian``."""
    elementos: dict[str, dict[str, Any]] = {}
    coords: dict[str, tuple[float, float, float]] = {}
    sin_nudos = 0
    for area in areas:
        try:
            elms = _nombres_de(model.AreaObj.GetElm(area))
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"No se pudieron leer los elementos del área '{area}': {exc}")
            continue
        for elm in elms:
            try:
                nudos = _nombres_de(model.AreaElm.GetPoints(elm))
            except Exception:  # noqa: BLE001
                nudos = []
            if not nudos:
                sin_nudos += 1
                continue
            elementos[elm] = {"area": area, "nodes": nudos}
    if sin_nudos:
        avisos.append(f"{sin_nudos} elementos sin nudos legibles se omitieron.")
    return elementos, coords


def _geometria(
    model: Any, areas: list[str], max_elementos: int, avisos: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[float]], bool]:
    """(elementos, nudos, truncado) de la malla de análisis de ``areas``."""
    t0 = time.perf_counter()
    try:
        lote = _geometria_tabla(model, areas)
    except Exception as exc:  # noqa: BLE001
        log.info("shell-forces: geometría por tablas falló: %s", exc)
        lote = None
    if lote is not None:
        elementos, coords = lote
    else:
        elementos, coords = _geometria_objeto(model, areas, avisos)
        avisos.append(
            "Geometría leída por objeto (AreaObj.GetElm / AreaElm.GetPoints / PointElm) porque las tablas de "
            f"'{TABLA_AREAS}' y '{TABLA_JOINTS}' no estuvieron disponibles."
        )
    if not elementos:
        raise ResultsError(409, "no_results", "No se pudo leer la malla de análisis de las áreas pedidas.")
    truncado = len(elementos) > max_elementos
    if truncado:
        omitidos = len(elementos) - max_elementos
        elementos = dict(list(elementos.items())[:max_elementos])
        avisos.append(f"Se devolvieron los primeros {max_elementos} elementos; se omitieron {omitidos} (max_elements).")
    # Coordenadas faltantes: se leen por PointElm solo para los nudos que usan los elementos conservados.
    usados = list(dict.fromkeys(n for e in elementos.values() for n in e["nodes"]))
    leidos_api = 0
    for nudo in usados:
        if nudo not in coords:
            xyz = _coord_punto_elemento(model, nudo)
            if xyz is not None:
                coords[nudo] = xyz
                leidos_api += 1
    if leidos_api:
        avisos.append(f"{leidos_api} nudos de elemento sin coordenadas en la tabla se leyeron con PointElm.")
    validos: dict[str, dict[str, Any]] = {}
    descartados = 0
    for elm, datos in elementos.items():
        nudos = datos["nodes"]
        if len(nudos) < 3 or any(n not in coords for n in nudos):
            descartados += 1
            continue
        validos[elm] = datos
    if descartados:
        avisos.append(f"{descartados} elementos con menos de 3 nudos o con nudos sin coordenadas se omitieron.")
    if not validos:
        raise ResultsError(409, "no_results", "Ningún elemento de la malla tiene geometría válida.")
    nodos = {n: [coords[n][0], coords[n][1], coords[n][2]] for e in validos.values() for n in e["nodes"]}
    log.info("shell-forces: geometría de %d elementos en %.3f s", len(validos), time.perf_counter() - t0)
    return validos, nodos, truncado


# --------------------------------------------------------------------------------------------------------------------
# Fuerzas por esquina
# --------------------------------------------------------------------------------------------------------------------

# Clave de fila: (elemento, esquina, caso, paso) → ({componente: valor}, número de paso)
Filas = dict[tuple[str, str, str, str], dict[str, Any]]


def _filas_de_tabla(model: Any, elementos: dict[str, Any], casos: list[str], componentes: list[str]) -> Filas | None:
    cols, datos = _tabla(model, TABLA_FUERZAS)
    if not cols or not datos:
        return None
    lower = {c.lower(): i for i, c in enumerate(cols)}
    i_elm, i_esq, i_caso = _indice(lower, ALIAS_F_ELM), _indice(lower, ALIAS_F_ESQUINA), _indice(lower, ALIAS_F_CASO)
    if None in (i_elm, i_esq, i_caso):
        return None
    i_tipo, i_num = _indice(lower, ALIAS_F_STEPTYPE), _indice(lower, ALIAS_F_STEPNUM)
    idx_comp = {c: lower.get(c.lower()) for c in componentes}
    if all(v is None for v in idx_comp.values()):
        return None
    pedidos = set(casos)
    filas: Filas = {}
    n_cols = len(cols)
    for f in range(len(datos) // n_cols):
        fila = datos[f * n_cols : (f + 1) * n_cols]
        elm, caso = str(fila[i_elm]), str(fila[i_caso])  # type: ignore[index]
        if elm not in elementos or caso not in pedidos:
            continue
        paso = _paso(fila[i_tipo]) if i_tipo is not None else ""
        num = _finito(fila[i_num]) if i_num is not None else None
        esquina = str(fila[i_esq])  # type: ignore[index]
        clave = (elm, esquina, caso, paso if paso != "Step" else f"Step:{num if num is not None else len(filas)}")
        filas[clave] = {c: _finito(fila[i]) if i is not None else None for c, i in idx_comp.items()}
        filas[clave]["_num"] = num
        filas[clave]["_faltan"] = [c for c, i in idx_comp.items() if i is None]
    return filas or None


def _llamar_area_force(model: Any, nombre: str, tipo: int) -> Any:
    metodo = model.Results.AreaForceShell
    try:
        return metodo(nombre, tipo)
    except Exception:  # noqa: BLE001
        vacio: list = []
        return metodo(nombre, tipo, 0, vacio, vacio, vacio, vacio, vacio, vacio, *([vacio] * 23))


def _filas_de_api(
    raw: Any, elementos: dict[str, Any], casos: list[str], componentes: list[str], solo_casos: set[str] | None = None
) -> Filas:
    arrays = _response_arrays(raw)
    if len(arrays) < len(_ORDEN_API):
        return {}
    pedidos = set(casos) if solo_casos is None else solo_casos
    filas: Filas = {}
    n = len(arrays[_IDX_API["Elm"]])
    for k in range(n):
        elm, esquina = str(arrays[_IDX_API["Elm"]][k]), str(arrays[_IDX_API["PointElm"]][k])
        caso = str(arrays[_IDX_API["LoadCase"]][k])
        if elm not in elementos or caso not in pedidos:
            continue
        paso = _paso(arrays[_IDX_API["StepType"]][k])
        num = _finito(arrays[_IDX_API["StepNum"]][k])
        clave = (elm, esquina, caso, paso if paso != "Step" else f"Step:{num if num is not None else k}")
        filas[clave] = {c: _finito(arrays[_IDX_API[c]][k]) for c in componentes}
        filas[clave]["_num"] = num
        filas[clave]["_faltan"] = []
    return filas


def _casos_con_filas(filas: Filas) -> set[str]:
    return {k[2] for k in filas}


def _fuerzas(
    model: Any,
    elementos: dict[str, Any],
    areas: list[str],
    casos: list[str],
    componentes: list[str],
    mostrar_ok: bool,
    avisos: list[str],
) -> Filas:
    """Lee los esfuerzos por la primera vía que devuelva filas; los casos que falten se piden a la siguiente."""
    t0 = time.perf_counter()
    acumuladas: Filas = {}
    vias: list[str] = []

    def incorporar(nuevas: Filas, nombre: str, solo: set[str] | None = None) -> None:
        añadidas = 0
        for clave, valores in nuevas.items():
            if solo is not None and clave[2] not in solo:
                continue
            if clave not in acumuladas:
                acumuladas[clave] = valores
                añadidas += 1
        if añadidas:
            vias.append(nombre)

    def pendientes() -> set[str]:
        return set(casos) - _casos_con_filas(acumuladas)

    # a) tabla
    completar_b: list[str] = []
    try:
        de_tabla = _filas_de_tabla(model, elementos, casos, componentes)
    except Exception as exc:  # noqa: BLE001
        log.info("shell-forces: tabla de fuerzas falló: %s", exc)
        de_tabla = None
    if de_tabla:
        faltan_comp = sorted({c for v in de_tabla.values() for c in v["_faltan"]})
        if faltan_comp:
            completar_b = faltan_comp
        incorporar(de_tabla, "tabla")
        if not mostrar_ok and pendientes():
            avisos.append(
                "No se pudo capturar la selección «para mostrar» de las tablas: la tabla puede traer solo los casos "
                "que el usuario tiene mostrados; los demás se piden por otra vía."
            )
    # b) Results.AreaForceShell("ALL", GroupElm) — también completa componentes que la tabla no trae
    if pendientes() or completar_b:
        try:
            raw = _llamar_area_force(model, "ALL", 2)
            de_api = _filas_de_api(raw, elementos, casos, componentes)
        except Exception as exc:  # noqa: BLE001
            log.info("shell-forces: AreaForceShell(ALL) falló: %s", exc)
            de_api = {}
        if completar_b and de_api:
            comp_set = set(completar_b)
            for clave, valores in acumuladas.items():
                if clave in de_api:
                    for c in comp_set:
                        valores[c] = de_api[clave][c]
                    valores["_faltan"] = []
            avisos.append(
                f"Componentes {', '.join(sorted(comp_set))} completados con Results.AreaForceShell (la tabla no los trae)."
            )
        incorporar(de_api, "Results.AreaForceShell(ALL)", pendientes() or None)
    # c) por objeto
    if pendientes():
        de_obj: Filas = {}
        for area in areas:
            try:
                de_obj.update(_filas_de_api(_llamar_area_force(model, area, 0), elementos, casos, componentes))
            except Exception as exc:  # noqa: BLE001
                log.info("shell-forces: AreaForceShell(%s) falló: %s", area, exc)
        incorporar(de_obj, "Results.AreaForceShell por área", pendientes() or None)
    # d) por elemento, solo los que sigan sin filas
    if pendientes():
        con_filas = {k[0] for k in acumuladas}
        sin_filas = [e for e in elementos if e not in con_filas]
        de_elm: Filas = {}
        for elm in sin_filas:
            try:
                de_elm.update(_filas_de_api(_llamar_area_force(model, elm, 1), elementos, casos, componentes))
            except Exception:  # noqa: BLE001
                continue
        if sin_filas:
            avisos.append(f"{len(sin_filas)} elementos se leyeron uno a uno (Results.AreaForceShell por elemento).")
        incorporar(de_elm, "Results.AreaForceShell por elemento", pendientes() or None)
    if vias and vias[0] != "tabla":
        avisos.append(f"Esfuerzos leídos por {', '.join(dict.fromkeys(vias))} (la tabla no estuvo disponible).")
    elif len(set(vias)) > 1:
        avisos.append(f"Esfuerzos leídos por varias vías: {', '.join(dict.fromkeys(vias))}.")
    log.info("shell-forces: %d filas por %s en %.3f s", len(acumuladas), vias, time.perf_counter() - t0)
    return acumuladas


# --------------------------------------------------------------------------------------------------------------------
# Ejes locales
# --------------------------------------------------------------------------------------------------------------------


def _normal_geometrica(nodos: list[str], coords: dict[str, list[float]]) -> tuple[float, float, float] | None:
    p = [tuple(coords[n]) for n in nodos]
    if len(p) == 3:
        n = _cruz(_resta(p[1], p[0]), _resta(p[2], p[0]))
    else:
        n = _cruz(_resta(p[2], p[0]), _resta(p[3], p[1]))
    return _unitario(n)


def _ejes_validos(v1: Any, v2: Any, v3: Any, n_geo: tuple[float, float, float]) -> bool:
    for v in (v1, v2, v3):
        if abs(_norma(v) - 1.0) > _TOL_UNITARIO:
            return False
    if max(abs(_punto(v1, v2)), abs(_punto(v1, v3)), abs(_punto(v2, v3))) > _TOL_UNITARIO:
        return False
    if _punto(_cruz(v1, v2), v3) < 1 - _TOL_UNITARIO:
        return False
    return abs(_punto(v3, n_geo)) > 0.99


def _ejes_desde_matriz(
    valores: list[float], n_geo: tuple[float, float, float]
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], bool] | None:
    """Ejes (v1, v2, v3, traspuesta) de una matriz de 9 valores; se prueba por columnas y luego traspuesta.

    Interpretación por COLUMNAS según la documentación CSI ([Global] = [matriz]·[Local]); no verificada: si solo la
    traspuesta valida, se usa y se avisa. Aislado aquí para ajustarlo tras la sonda.
    """
    c = valores
    por_columnas = ((c[0], c[3], c[6]), (c[1], c[4], c[7]), (c[2], c[5], c[8]))
    por_filas = ((c[0], c[1], c[2]), (c[3], c[4], c[5]), (c[6], c[7], c[8]))
    if _ejes_validos(*por_columnas, n_geo):
        return (*por_columnas, False)
    if _ejes_validos(*por_filas, n_geo):
        return (*por_filas, True)
    return None


def _nueve_valores(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)):
        return None
    for v in raw:
        if isinstance(v, (list, tuple)) and len(v) == 9:
            vals = [_finito(x) for x in v]
            return None if any(x is None for x in vals) else vals  # type: ignore[return-value]
    planos = [_finito(v) for v in raw if isinstance(v, float)]
    if len(planos) == 9 and all(v is not None for v in planos):
        return planos  # type: ignore[return-value]
    return None


def _matriz_elemento(model: Any, elm: str) -> list[float] | None:
    for args in ((elm,), (elm, [0.0] * 9)):
        try:
            vals = _nueve_valores(model.AreaElm.GetTransformationMatrix(*args))
        except Exception:  # noqa: BLE001
            continue
        if vals is not None:
            return vals
    return None


def _matriz_objeto(model: Any, area: str) -> list[float] | None:
    for args in ((area, [0.0] * 9, True), (area, True)):
        try:
            vals = _nueve_valores(model.AreaObj.GetTransformationMatrix(*args))
        except Exception:  # noqa: BLE001
            continue
        if vals is not None:
            return vals
    return None


def _angulo_objeto(model: Any, area: str) -> float:
    try:
        raw = model.AreaObj.GetLocalAxes(area)
    except Exception:  # noqa: BLE001
        return 0.0
    if isinstance(raw, (list, tuple)):
        for v in raw:
            if isinstance(v, float) and math.isfinite(v):
                return v
    return 0.0


def _ejes_por_regla(n_geo: tuple[float, float, float], angulo_grados: float) -> tuple[tuple, tuple, tuple]:
    """Regla por defecto de SAP2000: eje 1 horizontal (``Z × v3``); en losas horizontales, eje 2 = +Y; luego el ángulo."""
    v3 = n_geo
    if abs(_punto(v3, (0.0, 0.0, 1.0))) < 1 - _TOL_UNITARIO:
        v1 = _unitario(_cruz((0.0, 0.0, 1.0), v3)) or (1.0, 0.0, 0.0)
        v2 = _cruz(v3, v1)
    else:
        v2 = (0.0, 1.0, 0.0)
        v1 = _cruz(v2, v3)
    a = math.radians(angulo_grados)
    c, s = math.cos(a), math.sin(a)
    v1g = tuple(c * v1[i] + s * v2[i] for i in range(3))
    v2g = tuple(-s * v1[i] + c * v2[i] for i in range(3))
    return v1g, v2g, v3


def _ortonormalizar(v1: Any, v2: Any, v3: Any) -> tuple[list[float], list[float], list[float]]:
    e1 = _unitario(v1) or (1.0, 0.0, 0.0)
    proy = _punto(v2, e1)
    e2 = _unitario((v2[0] - proy * e1[0], v2[1] - proy * e1[1], v2[2] - proy * e1[2])) or (0.0, 1.0, 0.0)
    e3 = _cruz(e1, e2)
    return list(e1), list(e2), list(e3)


def _ejes(
    model: Any, elementos: dict[str, Any], nodos: dict[str, list[float]], avisos: list[str]
) -> dict[str, dict[str, list[float]]]:
    t0 = time.perf_counter()
    ejes: dict[str, dict[str, list[float]]] = {}
    pendientes: list[str] = []
    traspuestas = 0
    normales = {e: _normal_geometrica(d["nodes"], nodos) for e, d in elementos.items()}
    for elm in elementos:
        n_geo = normales[elm]
        valores = _matriz_elemento(model, elm)
        res = _ejes_desde_matriz(valores, n_geo) if valores is not None and n_geo is not None else None
        if res is None:
            pendientes.append(elm)
            continue
        v1, v2, v3, tras = res
        traspuestas += tras
        e1, e2, e3 = _ortonormalizar(v1, v2, v3)
        ejes[elm] = {"v1": e1, "v2": e2, "v3": e3}
    if traspuestas:
        avisos.append(f"Ejes de {traspuestas} elementos: la matriz de SAP2000 solo validó traspuesta (se usó así).")
    # b) matriz por objeto, validada contra la normal de cada elemento
    por_objeto = 0
    for area in dict.fromkeys(elementos[e]["area"] for e in pendientes):
        valores = _matriz_objeto(model, area)
        if valores is None:
            continue
        for elm in [e for e in pendientes if elementos[e]["area"] == area]:
            n_geo = normales[elm]
            res = _ejes_desde_matriz(valores, n_geo) if n_geo is not None else None
            if res is not None:
                e1, e2, e3 = _ortonormalizar(res[0], res[1], res[2])
                ejes[elm] = {"v1": e1, "v2": e2, "v3": e3}
                por_objeto += 1
    pendientes = [e for e in pendientes if e not in ejes]
    if por_objeto:
        avisos.append(f"Ejes de {por_objeto} elementos leídos con AreaObj.GetTransformationMatrix (no por elemento).")
    # c) regla por defecto + ángulo del objeto
    derivados = 0
    angulos: dict[str, float] = {}
    for elm in pendientes:
        n_geo = normales[elm]
        if n_geo is None:
            continue
        area = elementos[elm]["area"]
        if area not in angulos:
            angulos[area] = _angulo_objeto(model, area)
        v1, v2, v3 = _ejes_por_regla(n_geo, angulos[area])
        e1, e2, e3 = _ortonormalizar(v1, v2, v3)
        ejes[elm] = {"v1": e1, "v2": e2, "v3": e3}
        derivados += 1
    if derivados:
        avisos.append(f"Ejes de {derivados} elementos derivados de la geometría (no confirmados con SAP2000).")
    log.info("shell-forces: ejes de %d elementos en %.3f s", len(ejes), time.perf_counter() - t0)
    return ejes


# --------------------------------------------------------------------------------------------------------------------
# Huella de la malla
# --------------------------------------------------------------------------------------------------------------------


def _coord_txt(v: float) -> str:
    texto = f"{v:.4f}"
    return "0.0000" if float(texto) == 0.0 else texto


def mesh_hash(elementos: dict[str, dict[str, Any]], nodos: dict[str, list[float]]) -> str:
    """Huella estable de la malla devuelta (conectividad y coordenadas a 0,1 mm); independiente de casos y ejes."""
    lineas = ["shellmesh-v1"]
    for elm in sorted(elementos):
        d = elementos[elm]
        lineas.append(f"E|{elm}|{d['area']}|{','.join(d['nodes'])}")
    for n in sorted(nodos):
        x, y, z = nodos[n]
        lineas.append(f"N|{n}|{_coord_txt(x)}|{_coord_txt(y)}|{_coord_txt(z)}")
    return "sha256:" + hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------------------------------
# Función pública
# --------------------------------------------------------------------------------------------------------------------


def _validar_componentes(componentes: list[str] | None) -> list[str]:
    if componentes is None:
        return list(COMPONENTES)
    if not componentes or len(set(componentes)) != len(componentes) or any(c not in COMPONENTES for c in componentes):
        raise ResultsError(422, "invalid_components", f"components debe ser un subconjunto sin repetir de {list(COMPONENTES)}.")
    return list(componentes)


def _validar_areas(model: Any, pedidas: list[str], avisos: list[str]) -> list[str]:
    existentes = _get_name_list(model.AreaObj, "AreaObj.GetNameList")
    if not existentes:
        raise ResultsError(409, "no_areas", "El modelo no tiene áreas (shells).")
    if not pedidas:
        return existentes
    conjunto = set(existentes)
    validas = [a for a in dict.fromkeys(pedidas) if a in conjunto]
    faltan = [a for a in dict.fromkeys(pedidas) if a not in conjunto]
    if faltan:
        avisos.append(f"Áreas inexistentes omitidas: {', '.join(faltan)}.")
    if not validas:
        raise ResultsError(422, "unknown_area", f"Ninguna de las áreas pedidas existe en el modelo: {', '.join(faltan)}.")
    return validas


def _validar_casos(
    model: Any, cases: list[str] | None, combos: list[str] | None, avisos: list[str]
) -> tuple[list[str], list[str]]:
    reales = load_cases_and_combos(model)
    todos_casos, todos_combos = reales["load_cases"], reales["combos"]
    if not cases and not combos:
        return list(todos_casos), list(todos_combos)
    casos_ok = [c for c in dict.fromkeys(cases or []) if c in set(todos_casos)]
    combos_ok = [c for c in dict.fromkeys(combos or []) if c in set(todos_combos)]
    faltan = [c for c in dict.fromkeys([*(cases or []), *(combos or [])]) if c not in set(todos_casos) | set(todos_combos)]
    if faltan:
        avisos.append(f"Casos o combinaciones inexistentes omitidos: {', '.join(faltan)}.")
    if not casos_ok and not combos_ok:
        raise ResultsError(
            422,
            "unknown_case",
            f"Ninguno de los casos o combinaciones pedidos existe. Disponibles: {', '.join([*todos_casos, *todos_combos])}.",
        )
    return casos_ok, combos_ok


def _marcar_salida(model: Any, casos: list[str], combos: list[str]) -> None:
    setup = model.Results.Setup
    setup.DeselectAllCasesAndCombosForOutput()
    for c in casos:
        _marcar(setup.SetCaseSelectedForOutput, c)
    for c in combos:
        _marcar(setup.SetComboSelectedForOutput, c)


def _leer(
    model: Any,
    areas: list[str],
    casos: list[str],
    combos: list[str],
    componentes: list[str],
    incluir_ejes: bool,
    max_elementos: int,
    avisos: list[str],
) -> dict[str, Any]:
    elementos, nodos, truncado = _geometria(model, areas, max_elementos, avisos)
    pedidos = [*casos, *combos]
    _marcar_salida(model, casos, combos)
    with _preservar_seleccion_tablas(model, casos, combos, avisos) as mostrar_ok:
        filas = _fuerzas(model, elementos, areas, pedidos, componentes, mostrar_ok, avisos)
    if not filas:
        raise ResultsError(409, "no_results", MSG_CORRIDO_SIN_RESULTADOS)
    # Filas de esquinas que no pertenecen al elemento: se descartan con aviso.
    ajenas = [k for k in filas if k[1] not in elementos[k[0]]["nodes"]]
    if ajenas:
        avisos.append(f"{len(ajenas)} filas con una esquina que no pertenece al elemento se descartaron.")
        for k in ajenas:
            del filas[k]
    con_filas = _casos_con_filas(filas)
    for caso in pedidos:
        if caso not in con_filas:
            avisos.append(f"El caso o combinación '{caso}' no devolvió resultados (¿no corrido o sin convergencia?).")
    con_resultados = {k[0] for k in filas}
    sin_resultados = [e for e in elementos if e not in con_resultados]
    if sin_resultados:
        avisos.append(f"{len(sin_resultados)} elementos sin resultados para lo pedido.")

    # Ordenar y truncar por caso completo.
    por_caso: dict[str, list[tuple[Any, ...]]] = {c: [] for c in pedidos}
    pos_elm = {e: i for i, e in enumerate(elementos)}
    for (elm, esquina, caso, paso), valores in filas.items():
        tipo = "Step" if paso.startswith("Step:") else paso
        pos_nudo = elementos[elm]["nodes"].index(esquina)
        num = valores.get("_num")
        orden = (pos_elm[elm], pos_nudo, _ORDEN_STEP[tipo], num if num is not None else 0.0)
        por_caso[caso].append((orden, [elm, esquina, caso, tipo, *[valores.get(c) for c in componentes]]))
    rows: list[list[Any]] = []
    omitidos: list[str] = []
    for caso in pedidos:
        lista = sorted(por_caso[caso], key=lambda t: t[0])
        if not lista:
            continue
        if rows and len(rows) + len(lista) > MAX_FILAS:
            omitidos.append(caso)
            continue
        rows.extend(fila for _, fila in lista)
    if omitidos:
        truncado = True
        avisos.append(
            f"Se alcanzó el tope de {MAX_FILAS} filas: se omitieron los casos {', '.join(omitidos)}; pídelos en otra llamada."
        )
    elif len(rows) > MAX_FILAS:
        truncado = True
        avisos.append(f"El primer caso ya supera el tope de {MAX_FILAS} filas; reduce max_elements.")
    ejes = _ejes(model, elementos, nodos, avisos) if incluir_ejes else {}
    return {
        "elements": elementos,
        "nodes": nodos,
        "axes": ejes,
        "rows": rows,
        "truncated": truncado,
        "mesh_hash": mesh_hash(elementos, nodos),
    }


def shell_forces(model: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Esfuerzos de shell por esquina de elemento, malla 3D y ejes locales. Solo lectura; ver el docstring del módulo."""
    componentes = _validar_componentes(body.get("components"))
    incluir_ejes = bool(body.get("include_axes", True))
    max_elementos = int(body.get("max_elements") or 20_000)
    if not model_is_locked(model):
        raise ResultsError(409, "model_not_run", MSG_MODELO_NO_CORRIDO)
    avisos: list[str] = []
    areas = _validar_areas(model, list(body.get("areas") or []), avisos)
    casos, combos = _validar_casos(model, body.get("cases"), body.get("combos"), avisos)
    try:
        with bulk.preserve_output_selection(model, avisos):
            datos = _with_ton_m_c(
                model,
                lambda: _leer(model, areas, casos, combos, componentes, incluir_ejes, max_elementos, avisos),
            )
    except RuntimeError as exc:
        if "present units" in str(exc) or "SetPresentUnits" in str(exc):
            raise ResultsError(409, "units_not_restored", f"Unidades de SAP2000: {exc}") from exc
        raise
    return {
        "ok": True,
        "units": {"force": "tonf", "length": "m"},
        "model_locked": True,
        "truncated": datos["truncated"],
        "warnings": avisos,
        "mesh_hash": datos["mesh_hash"],
        "elements": datos["elements"],
        "nodes": datos["nodes"],
        "axes": datos["axes"],
        "columns": ["elm", "node", "case", "step", *componentes],
        "rows": datos["rows"],
    }
