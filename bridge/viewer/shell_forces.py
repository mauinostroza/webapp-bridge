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

Verificado con SAP2000 v23.3.1 (sonda de shells): los alias de tablas y columnas, la interpretación por columnas de
``GetTransformationMatrix`` y que la tabla de fuerzas no trae ``StepNum``. Las listas de alias están al principio del módulo
por si otra versión usa otros nombres.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict
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
    split_return,
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
# Results.AreaJointForceShell (verificado con SAP2000 v23.3.1, variante 0): 12 arreglos tras el código de retorno y el conteo.
_ORDEN_API_NUDOS = ("Obj", "Elm", "PointElm", "LoadCase", "StepType", "StepNum", "F1", "F2", "F3", "M1", "M2", "M3")
COMPONENTES_NUDOS = ("F1", "F2", "F3", "M1", "M2", "M3")

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


def _filas_de_tabla(
    model: Any, elementos: dict[str, Any], casos: list[str], componentes: list[str], avisos: list[str] | None = None
) -> Filas | None:
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
    if i_num is None and avisos is not None and any(k[3].startswith("Step:") for k in filas):
        # Verificado con SAP2000 v23.3.1: la tabla no trae StepNum; los pasos múltiples siguen el orden de las filas.
        avisos.append(f"La tabla '{TABLA_FUERZAS}' no trae StepNum: los pasos múltiples se ordenan por el orden de las filas.")
    return filas or None


def _llamar_area_force(
    model: Any, nombre: str, tipo: int, metodo_api: str = "AreaForceShell", n_salidas: int = 23
) -> Any:
    metodo = getattr(model.Results, metodo_api)
    try:
        return metodo(nombre, tipo)
    except Exception:  # noqa: BLE001
        vacio: list = []
        return metodo(nombre, tipo, 0, vacio, vacio, vacio, vacio, vacio, vacio, *([vacio] * (n_salidas - 6)))


def _filas_de_api(
    raw: Any,
    elementos: dict[str, Any],
    casos: list[str],
    componentes: list[str],
    solo_casos: set[str] | None = None,
    orden: tuple[str, ...] = _ORDEN_API,
) -> Filas:
    arrays = _response_arrays(raw)
    if len(arrays) < len(orden):
        return {}
    idx = {nombre: i for i, nombre in enumerate(orden)}
    pedidos = set(casos) if solo_casos is None else solo_casos
    filas: Filas = {}
    n = len(arrays[idx["Elm"]])
    for k in range(n):
        elm, esquina = str(arrays[idx["Elm"]][k]), str(arrays[idx["PointElm"]][k])
        caso = str(arrays[idx["LoadCase"]][k])
        if elm not in elementos or caso not in pedidos:
            continue
        paso = _paso(arrays[idx["StepType"]][k])
        num = _finito(arrays[idx["StepNum"]][k])
        clave = (elm, esquina, caso, paso if paso != "Step" else f"Step:{num if num is not None else k}")
        filas[clave] = {c: _finito(arrays[idx[c]][k]) for c in componentes}
        filas[clave]["_num"] = num
        filas[clave]["_faltan"] = []
    return filas


def _casos_con_filas(filas: Filas) -> set[str]:
    return {k[2] for k in filas}


VIA_API = "Results.AreaForceShell(ALL)"
VIA_TABLA = "tabla"


def _fuerzas(
    model: Any,
    elementos: dict[str, Any],
    areas: list[str],
    casos: list[str],
    combos: list[str],
    componentes: list[str],
    avisos: list[str],
    metodo_api: str = "AreaForceShell",
    orden: tuple[str, ...] = _ORDEN_API,
    con_tabla: bool = True,
) -> Filas:
    """Lee los esfuerzos: ``Results.AreaForceShell("ALL", GroupElm)`` primero y la tabla como respaldo.

    Verificado con SAP2000 v23.3.1: con 10 000 elementos la API tarda 1,14 s y la tabla 20,4 s, así que la API va primero
    (con la selección de salida ya hecha) y la tabla solo se lee para los casos que la API no devolvió; usarla se avisa en
    ``warnings``. Después vienen la API por área y por elemento. Cada caso se lee por una sola vía.
    """
    t0 = time.perf_counter()
    pedidos = [*casos, *combos]
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
        return set(pedidos) - _casos_con_filas(acumuladas)

    # a) Results.AreaForceShell("ALL", GroupElm): una sola llamada, con la selección de salida ya hecha.
    try:
        de_api = _filas_de_api(
            _llamar_area_force(model, "ALL", 2, metodo_api, len(orden)), elementos, pedidos, componentes, None, orden
        )
    except Exception as exc:  # noqa: BLE001
        log.info("shell-forces: %s(ALL) falló: %s", metodo_api, exc)
        de_api = {}
    via_api = f"Results.{metodo_api}(ALL)"
    incorporar(de_api, via_api)
    # b) tabla «Element Forces - Area Shells» (respaldo, ~18 veces más lenta): solo para los casos que falten.
    if con_tabla and pendientes():
        with _preservar_seleccion_tablas(model, casos, combos, avisos) as mostrar_ok:
            try:
                de_tabla = _filas_de_tabla(model, elementos, pedidos, componentes, avisos)
            except Exception as exc:  # noqa: BLE001
                log.info("shell-forces: tabla de fuerzas falló: %s", exc)
                de_tabla = None
            if de_tabla:
                faltan = sorted({c for v in de_tabla.values() for c in v["_faltan"]})
                if faltan:
                    avisos.append(
                        f"La tabla '{TABLA_FUERZAS}' no trae los componentes {', '.join(faltan)}: quedan como null."
                    )
                incorporar(de_tabla, VIA_TABLA, pendientes())
                if not mostrar_ok and pendientes():
                    avisos.append(
                        "No se pudo capturar la selección «para mostrar» de las tablas: la tabla puede traer solo los "
                        "casos que el usuario tiene mostrados; los demás se piden por otra vía."
                    )
    # c) por objeto
    if pendientes():
        de_obj: Filas = {}
        for area in areas:
            try:
                de_obj.update(
                    _filas_de_api(
                        _llamar_area_force(model, area, 0, metodo_api, len(orden)),
                        elementos,
                        pedidos,
                        componentes,
                        None,
                        orden,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.info("shell-forces: %s(%s) falló: %s", metodo_api, area, exc)
        incorporar(de_obj, f"Results.{metodo_api} por área", pendientes())
    # d) por elemento, solo los que sigan sin filas
    if pendientes():
        con_filas = {k[0] for k in acumuladas}
        sin_filas = [e for e in elementos if e not in con_filas]
        de_elm: Filas = {}
        for elm in sin_filas:
            try:
                de_elm.update(
                    _filas_de_api(
                        _llamar_area_force(model, elm, 1, metodo_api, len(orden)),
                        elementos,
                        pedidos,
                        componentes,
                        None,
                        orden,
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        if sin_filas:
            avisos.append(f"{len(sin_filas)} elementos se leyeron uno a uno (Results.{metodo_api} por elemento).")
        incorporar(de_elm, f"Results.{metodo_api} por elemento", pendientes())
    if vias and vias[0] != via_api:
        avisos.append(
            f"Esfuerzos leídos por {', '.join(dict.fromkeys(vias))} (respaldo: {via_api} no devolvió "
            "filas)."
        )
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


MAX_CACHE_EJES = 8  # mallas distintas que se recuerdan (la más antigua sale primero)
_CACHE_EJES: OrderedDict[tuple[Any, ...], tuple[dict[str, dict[str, list[float]]], list[str]]] = OrderedDict()
_CACHE_EJES_LOCK = threading.Lock()


def limpiar_cache_ejes() -> None:
    """Vacía la caché de ejes (las pruebas y un cambio de modelo la usan)."""
    with _CACHE_EJES_LOCK:
        _CACHE_EJES.clear()


def _angulo_y_avanzado(model: Any, area: str) -> tuple[float, bool | None]:
    """Ángulo y bandera ``Advanced`` de los ejes locales del área (``AreaObj.GetLocalAxes``); ``(0, None)`` si no se lee."""
    try:
        raw = model.AreaObj.GetLocalAxes(area)
    except Exception:  # noqa: BLE001
        return 0.0, None
    angulo = next((v for v in raw if isinstance(v, float) and math.isfinite(v)), 0.0) if isinstance(raw, (list, tuple)) else 0.0
    avanzado = next((v for v in raw if isinstance(v, bool)), None) if isinstance(raw, (list, tuple)) else None
    return angulo, avanzado


def _clave_cache_ejes(model: Any, elementos: dict[str, Any], huella: str) -> tuple[Any, ...]:
    """Clave de la caché: ``mesh_hash`` más el ángulo y la bandera ``Advanced`` de cada área.

    El ``mesh_hash`` solo cubre conectividad y coordenadas; girar los ejes locales de un área no cambia la malla pero sí
    los ejes, así que se añaden esos valores (una llamada COM por área, no por elemento).
    """
    areas = dict.fromkeys(d["area"] for d in elementos.values())
    return (huella, tuple((a, *_angulo_y_avanzado(model, a)) for a in areas))


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
    model: Any, elementos: dict[str, Any], nodos: dict[str, list[float]], avisos: list[str], huella: str | None = None
) -> dict[str, dict[str, list[float]]]:
    """Ejes locales por elemento; con ``huella`` (``mesh_hash``) se recuerdan en memoria entre lecturas de la misma malla."""
    clave = _clave_cache_ejes(model, elementos, huella) if huella else None
    if clave is not None:
        with _CACHE_EJES_LOCK:
            guardado = _CACHE_EJES.get(clave)
            if guardado is not None:
                _CACHE_EJES.move_to_end(clave)
        if guardado is not None:
            ejes_guardados, avisos_guardados = guardado
            avisos.extend(avisos_guardados)
            avisos.append("Ejes locales tomados de la caché en memoria (misma malla y mismos ejes de área).")
            return {e: {k: list(v) for k, v in ejes.items()} for e, ejes in ejes_guardados.items()}
    avisos_calculo: list[str] = []
    ejes = _calcular_ejes(model, elementos, nodos, avisos_calculo)
    avisos.extend(avisos_calculo)
    if clave is not None:
        with _CACHE_EJES_LOCK:
            _CACHE_EJES[clave] = ({e: {k: list(v) for k, v in d.items()} for e, d in ejes.items()}, avisos_calculo)
            while len(_CACHE_EJES) > MAX_CACHE_EJES:
                _CACHE_EJES.popitem(last=False)
    return ejes


def _calcular_ejes(
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
# Espesor de cada área (campos opcionales de la respuesta)
# --------------------------------------------------------------------------------------------------------------------

_TIPO_LAYERED = 6  # eShellType: 1 Shell-Thin, 2 Shell-Thick, 3 Membrane, 4 Plate-Thin, 5 Plate-Thick, 6 Layered


class _EspesorError(RuntimeError):
    """La sección no tiene un espesor único legible (layered, sólido axisimétrico o error de lectura)."""


def _leer_espesor_seccion(model: Any, seccion: str) -> tuple[float, float]:
    """(Thickness, Bending) en metros (se llama con las unidades en Ton_m_C) de ``PropArea.GetShell_1`` o ``GetShell``.

    Orden de las salidas: ``ShellType, [IncludeDrillingDOF,] MatProp, MatAngle, Thickness, Bending, Color, Notes, GUID``; los tres
    únicos reales son ``MatAngle``, ``Thickness`` y ``Bending``, así que se leen por tipo y valen para las dos firmas.
    """
    ultimo: Exception | None = None
    for nombre in ("GetShell_1", "GetShell"):
        metodo = getattr(model.PropArea, nombre, None)
        if metodo is None:
            continue
        for args in ((seccion,), (seccion, 0, False, "", 0.0, 0.0, 0.0, -1, "", "")):
            try:
                raw = metodo(*args)
                codigo, salidas = split_return(raw)
            except Exception as exc:  # noqa: BLE001
                ultimo = exc
                continue
            if codigo != 0:
                ultimo = RuntimeError(f"{nombre}({seccion!r}) devolvió el código {codigo}")
                continue
            tipo = next((v for v in salidas if isinstance(v, int) and not isinstance(v, bool)), None)
            if tipo == _TIPO_LAYERED:
                raise _EspesorError(f"la sección '{seccion}' es de tipo layered/no lineal: no tiene un espesor único")
            reales = [v for v in salidas if isinstance(v, float)]
            if len(reales) < 3:
                ultimo = RuntimeError(f"{nombre}({seccion!r}) no devolvió espesor")
                continue
            espesor, flexion = reales[1], reales[2]
            if not (math.isfinite(espesor) and espesor > 0):
                raise _EspesorError(f"la sección '{seccion}' no tiene un espesor positivo ({espesor!r})")
            return espesor, flexion if math.isfinite(flexion) and flexion > 0 else espesor
    raise _EspesorError(f"no se pudo leer el espesor de la sección '{seccion}': {ultimo or 'sin PropArea.GetShell'}")


def _nombre_seccion(model: Any, area: str) -> str:
    for args in ((area,), (area, "")):
        try:
            codigo, salidas = split_return(model.AreaObj.GetProperty(*args))
        except Exception:  # noqa: BLE001
            continue
        nombre = next((v for v in salidas if isinstance(v, str) and v), "")
        if codigo == 0 and nombre:
            return nombre
    raise _EspesorError(f"no se pudo leer la sección del área '{area}' (AreaObj.GetProperty)")


def espesores(model: Any, areas: list[str], avisos: list[str]) -> dict[str, dict[str, Any]]:
    """Espesor de cada área: ``thickness`` (membrana, m), ``thickness_bending`` (solo donde difiere) y ``sections``.

    Solo lectura. ``AreaObj.GetProperty`` se llama una vez por área y ``PropArea.GetShell_1`` (o ``GetShell``) una vez por
    SECCIÓN en cada llamada a la ruta. Las áreas sin espesor único (layered, sólidos, error de lectura) se omiten de
    ``thickness`` y ``thickness_bending`` —``sections`` conserva el nombre si se pudo leer— y se avisan en ``warnings``; nunca
    hacen fallar la ruta.
    """
    secciones: dict[str, str] = {}
    por_seccion: dict[str, tuple[float, float] | str] = {}  # (espesor, flexión) o el motivo del fallo
    espesor: dict[str, float] = {}
    flexion: dict[str, float] = {}
    sin_espesor: dict[str, str] = {}
    for area in dict.fromkeys(areas):
        try:
            nombre = _nombre_seccion(model, area)
        except _EspesorError as exc:
            sin_espesor[area] = str(exc)
            continue
        secciones[area] = nombre
        if nombre not in por_seccion:
            try:
                por_seccion[nombre] = _leer_espesor_seccion(model, nombre)
            except _EspesorError as exc:
                por_seccion[nombre] = str(exc)
        dato = por_seccion[nombre]
        if isinstance(dato, str):
            sin_espesor[area] = dato
            continue
        espesor[area] = dato[0]
        if abs(dato[1] - dato[0]) > 1e-12:
            flexion[area] = dato[1]
    if sin_espesor:
        motivos = dict.fromkeys(sin_espesor.values())
        lista = ", ".join(list(sin_espesor)[:10]) + (f" y {len(sin_espesor) - 10} más" if len(sin_espesor) > 10 else "")
        avisos.append(
            f"{len(sin_espesor)} áreas sin espesor único se omitieron de 'thickness' ({lista}): " + "; ".join(list(motivos)[:3]) + "."
        )
    return {"thickness": espesor, "thickness_bending": flexion, "sections": secciones}


CAMPOS_ESPESOR = ("thickness", "thickness_bending", "sections")


def campos_espesor(datos: dict[str, Any]) -> dict[str, Any]:
    """Los campos de espesor de ``datos`` que traen algo (opcionales: sin datos no aparecen en la respuesta)."""
    return {k: datos[k] for k in CAMPOS_ESPESOR if datos.get(k)}


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


def _a_ejes_del_elemento(
    rows: list[list[Any]], ejes: dict[str, dict[str, list[float]]], componentes: list[str]
) -> None:
    """Lleva EN SU SITIO fuerzas (F1..F3) y momentos (M1..M3) de ejes globales a los ejes locales del elemento.

    ``local_k = v_k · global`` con los ejes locales del elemento. Si falta algún valor de un trío, el trío queda en ``null``:
    no se inventa nada. Un elemento sin ejes deja sus valores como están (``_leer`` ya lo avisó).
    """
    tripletas = [
        [4 + componentes.index(c) for c in trio]
        for trio in (("F1", "F2", "F3"), ("M1", "M2", "M3"))
        if all(c in componentes for c in trio)
    ]
    for fila in rows:
        e = ejes.get(fila[0])
        if e is None:
            continue
        for idx in tripletas:
            v = [fila[i] for i in idx]
            if any(x is None for x in v):
                for i in idx:
                    fila[i] = None
                continue
            for k, i in enumerate(idx):
                eje = e[("v1", "v2", "v3")[k]]
                fila[i] = eje[0] * v[0] + eje[1] * v[1] + eje[2] * v[2]


def _leer(
    model: Any,
    areas: list[str],
    casos: list[str],
    combos: list[str],
    componentes: list[str],
    incluir_ejes: bool,
    max_elementos: int,
    avisos: list[str],
    *,
    metodo_api: str = "AreaForceShell",
    orden: tuple[str, ...] = _ORDEN_API,
    con_tabla: bool = True,
    a_elemento: bool = False,
) -> dict[str, Any]:
    """Lectura común de ``shell-forces`` y ``shell-joint-forces``; ``a_elemento`` lleva F1..M3 de ejes globales a los del elemento."""
    elementos, nodos, truncado = _geometria(model, areas, max_elementos, avisos)
    pedidos = [*casos, *combos]
    _marcar_salida(model, casos, combos)
    filas = _fuerzas(model, elementos, areas, casos, combos, componentes, avisos, metodo_api, orden, con_tabla)
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
    huella = mesh_hash(elementos, nodos)
    ejes = _ejes(model, elementos, nodos, avisos, huella) if (incluir_ejes or a_elemento) else {}
    if a_elemento:
        _a_ejes_del_elemento(rows, ejes, componentes)
    return {
        "elements": elementos,
        "nodes": nodos,
        "axes": ejes if incluir_ejes else {},
        "rows": rows,
        "truncated": truncado,
        "mesh_hash": huella,
        **espesores(model, list(dict.fromkeys(d["area"] for d in elementos.values())), avisos),
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
        **campos_espesor(datos),  # opcionales: solo si hay datos
    }
