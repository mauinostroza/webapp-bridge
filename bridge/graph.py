"""SAP Graph: lectura de un modelo YA EXISTENTE en SAP2000 para el puente compartido.

Puerto de ``ModelReader.read_model`` y de sus métodos ``_read_*_real`` de
``app/sap/reader.py`` (proyecto SAP Graph), más ``UNITS``, ``_units_name`` y el
envoltorio de respuesta de ``read_model`` en ``bridge/sap_graph_agent.py``.
Sólo lectura: ninguna llamada ``Set*``, ``Add*`` ni ``Delete*``.

Al patrón de este repositorio (ver ``bridge/hormigon.py`` y ``bridge/esbelteces.py``):
sin clases de conector ni modo simulado. Cada función recibe el ``SapModel`` ya
conectado a través del actor STA de la sesión y las rutas se instalan desde
``bridge.actions.install_routes`` con el mismo envoltorio ``call``.

Las tuplas de SAP Graph se escribieron con el código de retorno AL INICIO
``(ret, salidas…)``. ``read_model`` aplica ``bridge.com_orden.normalizar`` para que
el modelo entregue las tuplas en ese orden, sea cual sea el enlace COM real.

Alcance: materiales isótropos, secciones de frame (rectangular, circular, I),
secciones de shell, puntos con sus restricciones, frames, áreas (shells), patrones
de carga y, en ``assignments``, las cargas aplicadas referidas por nombre (distribuidas
en barras, fuerzas en nudos y presión uniforme en shells; solo en ejes globales o
gravedad). No se leen casos/combinaciones con sus coeficientes.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from typing import Any

from bridge.com_orden import detectar_codigo_primero, normalizar

MAX_RESPONSE_BYTES = 40 * 1024 * 1024  # mismo límite que MAX_RESPONSE_BYTES de sap_graph_agent.py

# Tabla de unidades presentes de SAP2000 (eUnits), copiada de sap_graph_agent.py.
UNITS = {
    1: "lb · in · °F",
    2: "lb · ft · °F",
    3: "kip · in · °F",
    4: "kip · ft · °F",
    5: "kN · mm · °C",
    6: "kN · m · °C",
    7: "kgf · mm · °C",
    8: "kgf · m · °C",
    9: "N · mm · °C",
    10: "N · m · °C",
    11: "tonf · mm · °C",
    12: "tonf · m · °C",
    13: "kN · cm · °C",
    14: "kgf · cm · °C",
    15: "N · cm · °C",
    16: "tonf · cm · °C",
}

# Inversa de ``_MAT_TYPE_CODES`` de app/sap/builder.py (eMatType).
_MAT_TYPE_NAMES = {1: "Steel", 2: "Concrete", 3: "NoDesign", 4: "Aluminum", 5: "ColdFormed", 6: "Rebar"}
# Solo estos 3 están en las opciones de MaterialIsotropicNode; el resto cae a "Other".
_MAT_TYPE_TO_NODE_CHOICE = {"Steel": "Steel", "Concrete": "Concrete", "Aluminum": "Aluminum"}

_SHELL_TYPE_NAMES = {1: "Shell-Thin", 2: "Shell-Thick", 3: "Membrane", 4: "Plate"}


class GraphReadError(RuntimeError):
    """Error de lectura de SAP2000 para SAP Graph.

    ``detail`` sigue el criterio estructurado de ``bridge.hormigon.SapHormigonError``:
    el envoltorio ``call`` de ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    ``code="payload_too_large"`` lo traduce la ruta a 413 (ver ``install_routes``).
    """

    def __init__(self, message: str, code: str = "sap_error"):
        super().__init__(message)
        self.detail = {"code": code, "message": message}


def _json_pure(value: Any) -> Any:
    """Convierte a JSON puro: tuplas a listas, NaN/inf a None, claves a texto."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_pure(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_pure(v) for v in value]
    return str(value)


def _units_name(model: Any) -> str | None:
    """Unidades presentes como texto (solo getter: nunca ``SetPresentUnits``)."""
    try:
        code = model.GetPresentUnits()
    except Exception:
        return None
    if isinstance(code, (tuple, list)):
        candidates = [int(v) for v in code if type(v) in (int, float) and int(v) in UNITS]
        code = candidates[0] if len(candidates) == 1 else None
    try:
        return UNITS.get(int(code), f"Unidades SAP no reconocidas ({code})")
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Lectores por tipo. Cada uno recibe el modelo y la lista de avisos a la que
# se añaden los fallos parciales (un objeto que falla no aborta la lectura).
# ---------------------------------------------------------------------------


def _read_points(model: Any, avisos: list[str]) -> dict[str, dict]:
    points: dict[str, dict] = {}
    try:
        ret, n, names = model.PointObj.GetNameList()
    except Exception as e:
        avisos.append(f"PointObj.GetNameList: {e}")
        return points
    for name in names or []:
        try:
            ret, x, y, z = model.PointObj.GetCoordCartesian(name)
            restraint = None
            try:
                ret_r, restraint_vals = model.PointObj.GetRestraint(name)
                if ret_r == 0 and any(restraint_vals):
                    restraint = list(restraint_vals)
            except Exception as e:
                avisos.append(f"GetRestraint({name}): {e}")
            points[name] = {"name": name, "x": x, "y": y, "z": z, "restraint": restraint}
        except Exception as e:
            avisos.append(f"GetCoordCartesian({name}): {e}")
    return points


def _read_frames(model: Any, avisos: list[str]) -> list[dict]:
    frames: list[dict] = []
    try:
        ret, n, names = model.FrameObj.GetNameList()
    except Exception as e:
        avisos.append(f"FrameObj.GetNameList: {e}")
        return frames
    for name in names or []:
        try:
            ret, p1, p2 = model.FrameObj.GetPoints(name)
            ret2, section, is_auto = model.FrameObj.GetSection(name)
            frames.append({"name": name, "p1": p1, "p2": p2, "section": section})
        except Exception as e:
            avisos.append(f"Frame {name}: {e}")
    return frames


def _read_shells(model: Any, avisos: list[str]) -> list[dict]:
    shells: list[dict] = []
    try:
        ret, n, names = model.AreaObj.GetNameList()
    except Exception as e:
        avisos.append(f"AreaObj.GetNameList: {e}")
        return shells
    for name in names or []:
        try:
            ret, num_pts, pt_names = model.AreaObj.GetPoints(name)
            # AreaObj.GetProperty(Name, ByRef PropName): no confirmado contra SAP2000 real
            # en el original; simétrico a FrameObj.GetSection.
            section = ""
            try:
                ret2, section = model.AreaObj.GetProperty(name)
            except Exception as e:
                avisos.append(f"AreaObj.GetProperty({name}): {e}")
            shells.append({"name": name, "points": list(pt_names or []), "section": section})
        except Exception as e:
            avisos.append(f"Shell {name}: {e}")
    return shells


def _read_materials(model: Any, avisos: list[str]) -> dict[str, dict]:
    materials: dict[str, dict] = {}
    try:
        ret, n, names = model.PropMaterial.GetNameList()
    except Exception as e:
        avisos.append(f"PropMaterial.GetNameList: {e}")
        return materials
    for name in names or []:
        try:
            # GetMaterial(Name, ByRef MatType, Color, Notes, GUID): orden inferido en el original.
            ret, mat_type_code, color, notes, guid = model.PropMaterial.GetMaterial(name)
            mat_type = _MAT_TYPE_TO_NODE_CHOICE.get(_MAT_TYPE_NAMES.get(mat_type_code, ""), "Other")
            E, nu, rho = 25000.0, 0.2, 2400.0
            try:
                # GetMPIsotropic(Name, ByRef E, U, A, Temp)
                ret2, E, nu, a_coef, temp = model.PropMaterial.GetMPIsotropic(name)
            except Exception as e:
                avisos.append(f"GetMPIsotropic({name}): {e}")
            try:
                # GetWeightAndMass(Name, ByRef MyOption, Value)
                ret3, weight, mass = model.PropMaterial.GetWeightAndMass(name)
                rho = mass or rho
            except Exception as e:
                avisos.append(f"GetWeightAndMass({name}): {e}")
            materials[name] = {"name": name, "type": mat_type, "E": E, "nu": nu, "rho": rho, "fc": 0.0}
        except Exception as e:
            avisos.append(f"Material {name}: {e}")
    return materials


def _read_one_frame_section(model: Any, name: str) -> dict | None:
    """Prueba cada forma en cascada (rectangular, circular, I). ``None`` si ninguna encaja."""
    try:
        res = model.PropFrame.GetRectangle(name)
        if res[0] == 0:
            return {"name": name, "shape": "Rectangular", "material": res[2], "t3": res[3], "t2": res[4]}
    except Exception:
        pass
    try:
        res = model.PropFrame.GetCircle(name)
        if res[0] == 0:
            return {"name": name, "shape": "Circle", "material": res[2], "t3": res[3], "t2": res[3]}
    except Exception:
        pass
    try:
        res = model.PropFrame.GetISection(name)
        if res[0] == 0:
            return {
                "name": name,
                "shape": "I-WideFlange",
                "material": res[2],
                "t3": res[3],
                "t2": res[4],
                "tf": res[5],
                "tw": res[6],
            }
    except Exception:
        pass
    return None


def _read_frame_sections(model: Any, avisos: list[str]) -> dict[str, dict]:
    sections: dict[str, dict] = {}
    try:
        ret, n, names = model.PropFrame.GetNameList()
    except Exception as e:
        avisos.append(f"PropFrame.GetNameList: {e}")
        return sections
    for name in names or []:
        sec = _read_one_frame_section(model, name)
        if sec is not None:
            sections[name] = sec
        else:
            avisos.append(
                f"Seccion frame {name}: forma no reconocida (solo se "
                "soporta importar Rectangular/Circle/I-WideFlange)"
            )
    return sections


def _read_shell_sections(model: Any, avisos: list[str]) -> dict[str, dict]:
    sections: dict[str, dict] = {}
    try:
        ret, n, names = model.PropArea.GetNameList()
    except Exception as e:
        avisos.append(f"PropArea.GetNameList: {e}")
        return sections
    for name in names or []:
        try:
            # GetShell_1(Name, ByRef ShellType, IncludeDrilling, MatProp, MatAngle, Thickness, ...)
            res = model.PropArea.GetShell_1(name)
            shell_type_code = res[1] if len(res) > 1 else 1
            material = res[3] if len(res) > 3 else ""
            thickness = res[5] if len(res) > 5 else 0.15
            shell_type = _SHELL_TYPE_NAMES.get(shell_type_code, "Shell-Thin")
            sections[name] = {"name": name, "thickness": thickness, "type": shell_type, "material": material}
        except Exception as e:
            avisos.append(f"Seccion shell {name}: {e}")
    return sections


def _read_load_patterns(model: Any, avisos: list[str]) -> dict[str, dict]:
    patterns: dict[str, dict] = {}
    try:
        ret, n, names = model.LoadPatterns.GetNameList()
    except Exception as e:
        avisos.append(f"LoadPatterns.GetNameList: {e}")
        return patterns
    for name in names or []:
        # Tal como el original: tipo y multiplicador de peso propio fijos (no se leen).
        patterns[name] = {"name": name, "load_type": "DEAD", "selfwt_mult": 0.0}
    return patterns


# ---------------------------------------------------------------------------
# Cargas aplicadas («assignments»)
#
# Salidas posicionales del OAPI (código de retorno ya normalizado al inicio):
#   FrameObj.GetLoadDistributed: n, FrameName[], LoadPat[], MyType[], CSys[], Dir[],
#                                RD1[], RD2[], Dist1[], Dist2[], Val1[], Val2[]   (11 arreglos)
#   PointObj.GetLoadForce:       n, PointName[], LoadPat[], LCStep[], CSys[], F1[], F2[], F3[],
#                                M1[], M2[], M3[]                                   (10 arreglos)
#   AreaObj.GetLoadUniform:      n, AreaName[], LoadPat[], CSys[], Dir[], Value[]   (5 arreglos)
# Dir: 4/5/6 = Global X/Y/Z, 10 = Gravity (el resto, ejes locales o proyectados, se omite).
# ---------------------------------------------------------------------------

_DIR_NAMES = {4: "GlobalX", 5: "GlobalY", 6: "GlobalZ", 10: "Gravity"}


def _dir_name(code: Any, csys: Any) -> str | None:
    """Dirección en el vocabulario de SAP Graph, o ``None`` si no es global ni gravedad."""
    try:
        name = _DIR_NAMES.get(int(code))
    except (TypeError, ValueError):
        return None
    if name is None:
        return None
    # Gravity no depende del sistema de coordenadas; X/Y/Z solo valen en «Global».
    if name != "Gravity" and str(csys).strip().casefold() != "global":
        return None
    return name


def _load_arrays(getter: Callable[..., Any], name: str, count: int) -> list[list[Any]]:
    """Arreglos de salida de un ``Get*Load*`` de un objeto, o ``[]`` si no tiene cargas.

    Prueba la forma corta y la explícita (``n``, arreglos vacíos, ``ItemType``) y exige
    ``(ret, n, arreglo × count)`` con ``ret == 0``; un arreglo ``None`` es una lista vacía.
    """
    last: Exception | None = None
    for args in ((name,), (name, 0, *([] for _ in range(count)), 0)):
        try:
            raw = getter(*args)
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        if not isinstance(raw, (list, tuple)) or len(raw) != count + 2:
            last = ValueError("respuesta con forma inesperada")
            continue
        if raw[0] != 0:
            last = ValueError(f"SAP return={raw[0]}")
            continue
        return [list(a) if isinstance(a, (list, tuple)) else [] for a in raw[2:]]
    raise last or ValueError("sin respuesta")


def _getter(model: Any, obj: str, method: str) -> Callable[..., Any] | None:
    """El método de lectura de cargas, o ``None`` si el modelo no lo expone (se omite sin aviso)."""
    fn = getattr(getattr(model, obj, None), method, None)
    return fn if callable(fn) else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _rows(arrays: list[list[Any]]) -> list[tuple]:
    return list(zip(*arrays, strict=False))


def _group(items: dict[tuple, dict], key: tuple, base: dict, field: str, member: str) -> None:
    """Agrupa los objetos con exactamente la misma carga en un solo elemento."""
    item = items.setdefault(key, {**base, field: []})
    if member not in item[field]:
        item[field].append(member)


def _frame_distributed(model: Any, frames: list[dict], avisos: list[str]) -> list[dict]:
    getter = _getter(model, "FrameObj", "GetLoadDistributed")
    if getter is None:
        return []
    items: dict[tuple, dict] = {}
    for frame in frames:
        name = frame["name"]
        try:
            arrays = _load_arrays(getter, name, 11)
        except Exception as e:  # noqa: BLE001
            avisos.append(f"GetLoadDistributed({name}): {e}")
            continue
        for _n, pattern, kind, csys, code, rd1, rd2, _d1, _d2, v1, v2 in _rows(arrays):
            direction = _dir_name(code, csys)
            w1, w2, r1, r2 = _num(v1), _num(v2), _num(rd1), _num(rd2)
            if kind != 1 or direction is None or None in (w1, w2, r1, r2) or (w1 == 0 and w2 == 0):
                continue  # solo fuerzas, en ejes globales o gravedad
            key = (pattern, direction, w1, w2, r1, r2)
            base = {"kind": "frame_distributed", "pattern": pattern, "dir": direction}
            base |= {"w1": w1, "w2": w2, "r1": r1, "r2": r2}
            _group(items, key, base, "frames", name)
    return list(items.values())


def _point_loads(model: Any, points: dict[str, dict], avisos: list[str]) -> list[dict]:
    getter = _getter(model, "PointObj", "GetLoadForce")
    if getter is None:
        return []
    items: dict[tuple, dict] = {}
    for name in points:
        try:
            arrays = _load_arrays(getter, name, 10)
        except Exception as e:  # noqa: BLE001
            avisos.append(f"GetLoadForce({name}): {e}")
            continue
        for _n, pattern, _step, csys, *valores in _rows(arrays):
            comp = [_num(v) for v in valores]
            if str(csys).strip().casefold() != "global" or None in comp or not any(comp):
                continue
            key = (pattern, *comp)
            base = {"kind": "point_load", "pattern": pattern, "forces": comp[:3], "moments": comp[3:]}
            _group(items, key, base, "points", name)
    return list(items.values())


def _shell_pressures(model: Any, shells: list[dict], avisos: list[str]) -> list[dict]:
    getter = _getter(model, "AreaObj", "GetLoadUniform")
    if getter is None:
        return []
    items: dict[tuple, dict] = {}
    for shell in shells:
        name = shell["name"]
        try:
            arrays = _load_arrays(getter, name, 5)
        except Exception as e:  # noqa: BLE001
            avisos.append(f"GetLoadUniform({name}): {e}")
            continue
        for _n, pattern, csys, code, valor in _rows(arrays):
            direction = _dir_name(code, csys)
            p = _num(valor)
            if direction is None or p is None or p == 0:
                continue
            base = {"kind": "shell_pressure", "pattern": pattern, "dir": direction, "p": p}
            _group(items, (pattern, direction, p), base, "shells", name)
    return list(items.values())


def _read_assignments(
    model: Any, points: dict[str, dict], frames: list[dict], shells: list[dict], avisos: list[str]
) -> list[dict]:
    """Cargas aplicadas por nombre: distribuidas en barras, fuerzas en nudos y presión en shells."""
    return [
        *_frame_distributed(model, frames, avisos),
        *_point_loads(model, points, avisos),
        *_shell_pressures(model, shells, avisos),
    ]


# ---------------------------------------------------------------------------
# Lectura completa y respuesta
# ---------------------------------------------------------------------------


def read_model(model: Any) -> dict:
    """Lee materiales, secciones, puntos, frames, shells, patrones de carga y cargas aplicadas.

    Devuelve el mismo dict que ``ModelReader.read_model`` del original, con la
    clave ``warnings``. Antes de leer se normaliza el orden del código de retorno.
    """
    avisos: list[str] = []
    if detectar_codigo_primero(model) is None:
        avisos.append(
            "No se pudo determinar el orden del código de retorno de SAP2000; "
            "si el modelo aparece vacío, revise que haya un modelo abierto."
        )
    model = normalizar(model, destino_primero=True)
    materials = _read_materials(model, avisos)
    frame_sections = _read_frame_sections(model, avisos)
    shell_sections = _read_shell_sections(model, avisos)
    points = _read_points(model, avisos)
    frames = _read_frames(model, avisos)
    shells = _read_shells(model, avisos)
    load_patterns = _read_load_patterns(model, avisos)
    assignments = _read_assignments(model, points, frames, shells, avisos)
    return {
        "materials": materials,
        "frame_sections": frame_sections,
        "shell_sections": shell_sections,
        "points": points,
        "frames": frames,
        "shells": shells,
        "load_patterns": load_patterns,
        "assignments": assignments,
        "warnings": avisos,
    }


def read_model_response(model: Any) -> dict:
    """Respuesta ``{model, units, warnings, read_ms}`` de ``GET /v1/sap/actions/graph/model``.

    Lanza ``GraphReadError`` con ``code="payload_too_large"`` si el JSON serializado
    supera ``MAX_RESPONSE_BYTES``.
    """
    started = time.perf_counter()
    try:
        raw = read_model(model)
    except Exception as exc:  # noqa: BLE001
        raise GraphReadError(f"No se pudo leer el modelo de SAP2000: {exc}") from exc
    avisos = [str(w) for w in (raw.pop("warnings", None) or [])]
    respuesta = {
        "model": _json_pure(raw),
        "units": _units_name(model) or "",
        "warnings": avisos,
        "read_ms": int((time.perf_counter() - started) * 1000),
    }
    cuerpo = json.dumps(respuesta, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(cuerpo) > MAX_RESPONSE_BYTES:
        raise GraphReadError(
            f"El modelo supera el máximo de {MAX_RESPONSE_BYTES // (1024 * 1024)} MB del puente.",
            code="payload_too_large",
        )
    return respuesta


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``GET /v1/sap/actions/graph/model`` con el envoltorio ``call`` de
    ``bridge.actions.install_routes`` (actor STA, token y mapeo de errores).

    ``call`` convierte cualquier excepción en 409 y copia su ``.detail`` como cuerpo,
    así que el 413 no sale de ``call``: la ruta traduce aquí ``payload_too_large``.

    La lectura espera hasta 60 s al hilo COM (``bridge.com_tiempo``); pasado ese
    tiempo ``call`` responde 409 con el mensaje de tiempo agotado.
    """
    from fastapi import HTTPException

    from bridge.com_tiempo import con_limite

    @app.get("/v1/sap/actions/graph/model")
    def graph_model():
        try:
            with con_limite(60.0):
                return call(lambda model: read_model_response(model))
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "payload_too_large":
                raise HTTPException(413, exc.detail) from exc
            raise
