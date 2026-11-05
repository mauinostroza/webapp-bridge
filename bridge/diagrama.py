"""SAP2000 acciones para Diagrama de interacción: lectura de grupos,
combinaciones y demandas de columnas.

Puerto literal de ``sap2000_bridge.py`` (proyecto de escritorio original,
``concrete_section_designer/app/``): ``list_groups``,
``list_load_combinations``, ``get_group_demands``, ``get_selected_demands`` y
sus helpers (``_parse_name_list``, ``_parse_frame_force``, ...) al patrón de
este repositorio: sin conexión COM propia, cada operación recibe el
``SapModel`` ya conectado a través del actor STA de la sesión y las rutas se
instalan desde ``bridge.actions.install_routes`` con el mismo envoltorio
``call`` y la misma autenticación por token, igual que ``bridge/espectro.py``.

Mismas llamadas COM y mismos argumentos que el original. Las unidades
presentes de SAP2000 se fuerzan a kN-m (``SetPresentUnits(6)``) durante la
lectura y se restauran siempre al terminar (``try/finally``). SAP2000 reporta
P con tracción positiva; se invierte a compresión positiva (convención del
motor). No se porta ``create_sd_*`` (Section Designer descontinuada).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, field_validator

from bridge.actions import _valid_combo_name, _valid_name

_FRAME_OBJECT_TYPE = 2  # GroupDef.GetAssignments: 1=Point, 2=Frame, 5=Area
_SELECTED_FRAME_TYPE = 2  # SelectObj.GetSelected: 1=Joint,2=Frame,3=Cable,4=Tendon,5=Area,6=Solid,7=Link
_EUNITS_KN_M = 6  # eUnits.kN_m

_MAX_ITEMS = 500


class SapDiagramaError(RuntimeError):
    """Espejo de ``Sap2000ConnectionError`` del original.

    ``detail`` sigue el mismo criterio estructurado que
    ``bridge.espectro.SapEspectroError``: el envoltorio ``call`` de
    ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


def _parse_name_list(ret: Any) -> list[str]:
    """Normaliza la salida de un ``GetNameList()`` COM (variantes de firma
    corta/larga) a una lista de strings."""
    if isinstance(ret, (list, tuple)) and ret and all(isinstance(x, str) for x in ret):
        return list(ret)
    if not isinstance(ret, tuple):
        return []
    if len(ret) >= 3:
        try:
            ret_code = int(ret[0])
            count = int(ret[1])
            names = ret[2]
            if ret_code == 0 and isinstance(names, (list, tuple)):
                return [str(n) for n in names[:count]]
        except Exception:
            pass
    if len(ret) >= 2 and isinstance(ret[1], (list, tuple)):
        names = ret[1]
        try:
            count = int(ret[0])
        except Exception:
            count = len(names)
        return [str(n) for n in names[:count]]
    return []


def list_load_combinations(model: Any) -> list[str]:
    """Nombres de combinaciones de carga definidas en el modelo (``RespCombo``)."""
    try:
        return _parse_name_list(model.RespCombo.GetNameList())
    except Exception as exc:
        raise SapDiagramaError(f"No se pudo leer RespCombo.GetNameList(): {exc}") from exc


def list_groups(model: Any) -> list[str]:
    """Nombres de grupos definidos en el modelo (``GroupDef``)."""
    try:
        return _parse_name_list(model.GroupDef.GetNameList())
    except Exception as exc:
        raise SapDiagramaError(f"No se pudo leer GroupDef.GetNameList(): {exc}") from exc


def _parse_group_assignments(ret: Any) -> tuple[list[int], list[str]]:
    """Normaliza ``GroupDef.GetAssignments`` a (tipos_objeto, nombres_objeto)."""
    if not isinstance(ret, (list, tuple)):
        return [], []
    for i in range(len(ret) - 1):
        tipos, nombres = ret[i], ret[i + 1]
        if (
            isinstance(tipos, (list, tuple))
            and isinstance(nombres, (list, tuple))
            and len(tipos) == len(nombres)
            and len(tipos) > 0
        ):
            try:
                return [int(t) for t in tipos], [str(n) for n in nombres]
            except Exception:
                continue
    return [], []


def get_group_frame_names(model: Any, group_name: str) -> list[str]:
    """Nombres de los Frame objects (columnas) asignados a ``group_name``."""
    try:
        try:
            ret = model.GroupDef.GetAssignments(group_name, 0, [], [])
        except Exception:
            ret = model.GroupDef.GetAssignments(group_name)
    except Exception as exc:
        raise SapDiagramaError(f"No se pudo leer GroupDef.GetAssignments('{group_name}'): {exc}") from exc

    tipos, nombres = _parse_group_assignments(ret)
    return [nombres[i] for i in range(len(nombres)) if tipos[i] == _FRAME_OBJECT_TYPE]


def _select_combos_for_output(model: Any, combo_names: list[str]) -> None:
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception:
        pass
    for combo in combo_names:
        try:
            model.Results.Setup.SetComboSelectedForOutput(combo, True)
        except Exception as exc:
            raise SapDiagramaError(
                f"No se pudo seleccionar la combinación '{combo}' para output: {exc}"
            ) from exc


def _parse_frame_force(ret: Any) -> list[dict[str, Any]]:
    """Normaliza ``Results.FrameForce`` (firma corta v21+: arrays al final,
    código de retorno al final) a filas ``{frame, station, combo, p, v2, v3, m2, m3}``.

    Busca los arrays de datos entre los elementos de la tupla en vez de
    asumir una posición fija, porque la firma exacta varía por versión de
    SAP2000.
    """
    arrays = [x for x in ret if isinstance(x, (list, tuple))]
    if len(arrays) < 9:
        raise SapDiagramaError(
            f"Results.FrameForce devolvió {len(arrays)} arreglos, se esperaban al menos 9."
        )

    # Orden documentado (OAPI): Obj, ObjSta, Elm, ElmSta, LoadCase, StepType,
    # StepNum, P, V2, V3, T, M2, M3 (13 arrays).
    obj, obj_sta, _elm, _elm_sta, load_case, _step_type, _step_num, p, v2, v3, _t, m2, m3 = (
        arrays[-13:] if len(arrays) >= 13 else (arrays + [[]] * (13 - len(arrays)))[-13:]
    )

    n = min(len(obj), len(obj_sta), len(load_case), len(p), len(m2), len(m3))
    # Cortantes: si SAP2000 no los entrega (arreglos más cortos) se toman como 0.
    v2 = list(v2) + [0.0] * max(n - len(v2), 0)
    v3 = list(v3) + [0.0] * max(n - len(v3), 0)
    return [
        {
            "frame": str(obj[i]),
            "station": float(obj_sta[i]),
            "combo": str(load_case[i]),
            "p": float(p[i]),
            "v2": float(v2[i]),
            "v3": float(v3[i]),
            "m2": float(m2[i]),
            "m3": float(m3[i]),
        }
        for i in range(n)
    ]


def get_selected_frame_names(model: Any) -> list[str]:
    """Nombres de los Frame objects actualmente seleccionados en SAP2000
    (``SapModel.SelectObj.GetSelected``)."""
    try:
        try:
            ret = model.SelectObj.GetSelected(0, [], [])
        except Exception:
            ret = model.SelectObj.GetSelected()
    except Exception as exc:
        raise SapDiagramaError(f"No se pudo leer SelectObj.GetSelected(): {exc}") from exc

    tipos, nombres = _parse_group_assignments(ret)
    return [nombres[i] for i in range(len(nombres)) if tipos[i] == _SELECTED_FRAME_TYPE]


def _get_demands_for_frames(
    model: Any, frame_names: list[str], combo_names: list[str]
) -> list[dict[str, Any]]:
    """Extrae (P, M2, M3) de cada frame en ``frame_names``, en TODAS las
    estaciones de salida a lo largo de la barra, para cada combinación.

    Fuerza las unidades presentes de SAP2000 a kN-m antes de leer
    ``Results.FrameForce`` y restaura las originales al terminar, incluso si
    la extracción falla a mitad de camino.
    """
    try:
        original_units = model.GetPresentUnits()
    except Exception:
        original_units = None

    try:
        model.SetPresentUnits(_EUNITS_KN_M)
    except Exception as exc:
        raise SapDiagramaError(f"No se pudo fijar las unidades de SAP2000 a kN-m: {exc}") from exc

    try:
        _select_combos_for_output(model, combo_names)

        rows: list[dict[str, Any]] = []
        for frame_name in frame_names:
            try:
                ret = model.Results.FrameForce(frame_name, 0)
            except Exception as exc:
                raise SapDiagramaError(f"Results.FrameForce('{frame_name}') falló: {exc}") from exc

            for row in (r for r in _parse_frame_force(ret) if r["combo"] in combo_names):
                rows.append(
                    {
                        "frame": row["frame"],
                        "combo": row["combo"],
                        "station": row["station"],
                        # SAP2000 reporta P de tracción positiva; se invierte a
                        # compresión positiva (convención del motor).
                        "n_kn": -row["p"],
                        # Ejes de la sección del motor: x = ancho (eje local 3), y = canto (eje
                        # local 2). Mx es el momento en torno a x (M3) y My en torno a y (M2),
                        # igual que la regresión contra las tablas de SAP2000 del Diagrama.
                        "mx_knm": row["m3"],
                        "my_knm": row["m2"],
                        # Cortante en x = V3 (a lo largo del eje local 3) y en y = V2; en módulo.
                        "vu_x_kn": abs(row["v3"]),
                        "vu_y_kn": abs(row["v2"]),
                    }
                )
        return rows
    finally:
        if original_units is not None:
            try:
                model.SetPresentUnits(original_units)
            except Exception:
                pass


def get_group_demands(model: Any, group_name: str, combo_names: list[str]) -> dict[str, Any]:
    """Demandas de cada columna del grupo, en todas las estaciones y para
    cada combinación. Port literal de ``get_group_demands``."""
    frame_names = get_group_frame_names(model, group_name)
    if not frame_names:
        raise SapDiagramaError(f"El grupo '{group_name}' no tiene ningún Frame object (columna) asignado.")
    rows = _get_demands_for_frames(model, frame_names, combo_names)
    return {"rows": rows, "n_frames": len(frame_names), "n_rows": len(rows)}


def get_selected_demands(model: Any, combo_names: list[str]) -> dict[str, Any]:
    """Igual que ``get_group_demands`` con los Frame objects seleccionados
    actualmente en SAP2000. Port literal de ``get_selected_demands``."""
    frame_names = get_selected_frame_names(model)
    if not frame_names:
        raise SapDiagramaError("No hay ningún Frame object (columna) seleccionado en SAP2000.")
    rows = _get_demands_for_frames(model, frame_names, combo_names)
    return {"rows": rows, "n_frames": len(frame_names), "n_rows": len(rows)}


def get_frames_demands(model: Any, frame_names: list[str], combo_names: list[str]) -> dict[str, Any]:
    """Igual que ``get_group_demands`` para una lista explícita de frames (p. ej. una selección guardada)."""
    if not frame_names:
        raise SapDiagramaError("La lista de frames está vacía.")
    rows = _get_demands_for_frames(model, frame_names, combo_names)
    return {"rows": rows, "n_frames": len(frame_names), "n_rows": len(rows)}


def get_group_frames(model: Any, group_name: str) -> dict[str, Any]:
    """Frames de un grupo de SAP2000, sin leer resultados."""
    return {"frames": get_group_frame_names(model, group_name)}


def get_selected_frames_list(model: Any) -> dict[str, Any]:
    """Frames seleccionados ahora en SAP2000, sin leer resultados."""
    return {"frames": get_selected_frame_names(model)}


def _num(valor: Any) -> float | None:
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _leer_seccion(model: Any, nombre: str) -> dict[str, Any]:
    """Forma y dimensiones (mm) de una sección de frame: rectangular (t3 = canto, t2 = ancho) o circular."""
    from bridge.viewer.sap2000.units import length_factor_to_meters

    try:
        factor = length_factor_to_meters(model.GetPresentUnits())
    except Exception:
        factor = 1.0
    info: dict[str, Any] = {
        "seccion": nombre,
        "tipo": "otra",
        "ancho_mm": None,
        "alto_mm": None,
        "diametro_mm": None,
    }
    prop = model.PropFrame
    for tipo, getter in (("rect", "GetRectangle"), ("circ", "GetCircle")):
        try:
            ret = getattr(prop, getter)(nombre)
        except Exception:
            continue
        if not isinstance(ret, (list, tuple)):
            continue
        # Con el código de retorno al inicio o al final: se buscan los números por posición de las
        # dimensiones (T3, T2 | T3) tras el nombre de archivo y el material (dos textos).
        textos = [i for i, v in enumerate(ret) if isinstance(v, str)]
        if len(textos) < 2:
            continue
        i0 = textos[1] + 1
        t = [_num(v) for v in ret[i0 : i0 + 2]]
        codigo = ret[0] if isinstance(ret[0], int) and not isinstance(ret[0], bool) else ret[-1]
        if codigo not in (0, None) and isinstance(codigo, int):
            continue
        if tipo == "rect" and len(t) == 2 and all(x and x > 0 for x in t):
            info.update(tipo="rect", alto_mm=t[0] * factor * 1000, ancho_mm=t[1] * factor * 1000)
            return info
        if tipo == "circ" and t and t[0] and t[0] > 0:
            info.update(tipo="circ", diametro_mm=t[0] * factor * 1000)
            return info
    return info


def get_frames_info(model: Any, frame_names: list[str]) -> dict[str, Any]:
    """Sección de cada frame con su forma y dimensiones en mm (rectangular o circular)."""
    if not frame_names:
        raise SapDiagramaError("La lista de frames está vacía.")
    secciones: dict[str, dict[str, Any]] = {}
    filas = []
    for frame in frame_names:
        try:
            ret = model.FrameObj.GetSection(frame, "", "")
        except Exception as exc:
            raise SapDiagramaError(f"No se pudo leer FrameObj.GetSection('{frame}'): {exc}") from exc
        nombres = [v for v in ret if isinstance(v, str)] if isinstance(ret, (list, tuple)) else []
        if not nombres:
            raise SapDiagramaError(f"FrameObj.GetSection('{frame}') no devolvió el nombre de la sección.")
        nombre = nombres[0]
        if nombre not in secciones:
            secciones[nombre] = _leer_seccion(model, nombre)
        filas.append({"frame": frame, **secciones[nombre]})
    return {"frames": filas}


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


def _bounded_combos(value: list[str]) -> list[str]:
    if not value or len(value) > _MAX_ITEMS:
        raise ValueError("combos debe contener entre 1 y 500 combinaciones")
    return [_valid_combo_name(combo, "combinación") for combo in value]


class GroupDemandsBody(BaseModel):
    group: str
    combos: list[str]

    @field_validator("group")
    @classmethod
    def group_name(cls, value: str) -> str:
        return _valid_name(value, "grupo")

    @field_validator("combos")
    @classmethod
    def bounded_combos(cls, value: list[str]) -> list[str]:
        return _bounded_combos(value)


def _bounded_frames(value: list[str]) -> list[str]:
    if not value or len(value) > _MAX_ITEMS:
        raise ValueError("frames debe contener entre 1 y 500 elementos")
    return [_valid_name(frame, "frame") for frame in dict.fromkeys(value)]


class FramesBody(BaseModel):
    frames: list[str]

    @field_validator("frames")
    @classmethod
    def bounded_frames(cls, value: list[str]) -> list[str]:
        return _bounded_frames(value)


class FramesDemandsBody(FramesBody):
    combos: list[str]

    @field_validator("combos")
    @classmethod
    def bounded_combos(cls, value: list[str]) -> list[str]:
        return _bounded_combos(value)


class GroupBody(BaseModel):
    group: str

    @field_validator("group")
    @classmethod
    def group_name(cls, value: str) -> str:
        return _valid_name(value, "grupo")


class SelectedDemandsBody(BaseModel):
    combos: list[str]

    @field_validator("combos")
    @classmethod
    def bounded_combos(cls, value: list[str]) -> list[str]:
        return _bounded_combos(value)


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/diagrama/*`` con el mismo
    envoltorio ``call`` (actor STA, token y mapeo de errores a HTTP) que
    ``bridge.espectro``."""
    prefix = "/v1/sap/actions/diagrama"

    @app.get(f"{prefix}/groups")
    def diagrama_groups():
        return call(lambda model: {"groups": list_groups(model)})

    @app.get(f"{prefix}/combos")
    def diagrama_combos():
        return call(lambda model: {"combos": list_load_combinations(model)})

    @app.post(f"{prefix}/group-demands")
    def diagrama_group_demands(body: GroupDemandsBody):
        return call(lambda model: get_group_demands(model, body.group, body.combos))

    @app.post(f"{prefix}/selected-demands")
    def diagrama_selected_demands(body: SelectedDemandsBody):
        return call(lambda model: get_selected_demands(model, body.combos))

    @app.get(f"{prefix}/selected-frames")
    def diagrama_selected_frames():
        return call(lambda model: get_selected_frames_list(model))

    @app.post(f"{prefix}/group-frames")
    def diagrama_group_frames(body: GroupBody):
        return call(lambda model: get_group_frames(model, body.group))

    @app.post(f"{prefix}/frames-info")
    def diagrama_frames_info(body: FramesBody):
        return call(lambda model: get_frames_info(model, body.frames))

    @app.post(f"{prefix}/frames-demands")
    def diagrama_frames_demands(body: FramesDemandsBody):
        return call(lambda model: get_frames_demands(model, body.frames, body.combos))
