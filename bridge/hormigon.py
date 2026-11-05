"""SAP2000 acciones para Hormigón simple (armado de viga): lectura de frames,
combinaciones y esfuerzos crudos de una viga.

Puerto de la parte en vivo de ``sap2000_io.py`` del proyecto original
(``Sap2000Live.list_frames``, ``list_combos``, ``frame_geometry`` y
``frame_forces``), al patrón de este repositorio: sin conexión COM propia, cada
operación recibe el ``SapModel`` ya conectado a través del actor STA de la
sesión y las rutas se instalan desde ``bridge.actions.install_routes`` con el
mismo envoltorio ``call`` y la misma autenticación por token, igual que
``bridge/diagrama.py``.

El puente solo lee SAP2000: ``get_frame_forces`` devuelve las filas crudas
(estación, caso, paso, P, V2, M3) en tonf, tonf·m y m, en columnas paralelas, y
la conversión a demanda envolvente la hace el servidor. Las unidades presentes
de SAP2000 se fuerzan a tonf-m (``SetPresentUnits(12)``) durante la lectura y se
restauran siempre al terminar (``try/finally``). M3 se entrega sin invertir.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, field_validator

from bridge.actions import _valid_combo_name, _valid_name, split_return
from bridge.viewer.sap2000.units import UNIT_CODES, length_factor_to_meters, unit_label

_EUNITS_TONF_M = 12  # eUnits.tonf_m
_MAX_COMBOS = 50
_MAX_NAME = 100

_G = 9.80665
_LB = 0.45359237
# Factor unidad de fuerza de SAP2000 -> tonf (paridad con sap2000_io.force_factor).
_FUERZA_A_TONF = {
    "N": 1.0 / (_G * 1000.0),
    "kN": 1.0 / _G,
    "kgf": 1.0e-3,
    "tonf": 1.0,
    "lb": _LB * 1.0e-3,
    "kip": _LB,
}


def _nombre_combo(value: str) -> str:
    """Las combinaciones de SAP2000 admiten «:» (p. ej. «ASD1: DEAD + PIPING1»)."""
    value = _valid_combo_name(value, "combinación")
    if len(value) > _MAX_NAME:
        raise ValueError(f"combinación must be at most {_MAX_NAME} characters")
    return value


class SapHormigonError(RuntimeError):
    """Error de lectura de SAP2000 para Hormigón simple.

    ``detail`` sigue el mismo criterio estructurado que
    ``bridge.diagrama.SapDiagramaError``: el envoltorio ``call`` de
    ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


def _unpack(res: Any, what: str) -> tuple:
    """Salidas de una llamada COM con el código de retorno al inicio o al final
    (``sap2000_io._unpack`` del original sólo conoce el orden de comtypes)."""
    try:
        code, outs = split_return(res)
    except RuntimeError as exc:
        raise SapHormigonError(f"SAP2000 API: {what} devolvió una respuesta inesperada.") from exc
    if code != 0:
        raise SapHormigonError(f"SAP2000 API: {what} falló (código {code}).")
    return tuple(outs)


def list_frames(model: Any) -> list[str]:
    """Port de ``Sap2000Live.list_frames``."""
    try:
        _, names = _unpack(model.FrameObj.GetNameList(0, []), "FrameObj.GetNameList")
    except SapHormigonError:
        raise
    except Exception as exc:
        raise SapHormigonError(str(exc)) from exc
    return list(names or [])


def list_combos(model: Any, include_cases: bool = True) -> list[str]:
    """Port de ``Sap2000Live.list_combos``: combinaciones y, además, los casos
    de carga que no sean combinaciones."""
    try:
        _, combos = _unpack(model.RespCombo.GetNameList(0, []), "RespCombo.GetNameList")
        combos = list(combos or [])
        if not include_cases:
            return combos
        known = set(combos)
        _, cases = _unpack(model.LoadCases.GetNameList(0, []), "LoadCases.GetNameList")
    except SapHormigonError:
        raise
    except Exception as exc:
        raise SapHormigonError(str(exc)) from exc
    return combos + [c for c in (cases or []) if c not in known]


def _present_units(model: Any) -> tuple[float, float]:
    """Port de ``Sap2000Live.units``: (fuerza->tonf, longitud->m)."""
    code = int(model.GetPresentUnits())
    if code not in UNIT_CODES.values():
        raise SapHormigonError(f"Unidades de SAP2000 no soportadas (eUnits={code}).")
    etiqueta = unit_label(code)  # p. ej. "kip_ft_F"
    return _FUERZA_A_TONF[etiqueta.split("_")[0]], length_factor_to_meters(etiqueta)


def _frame_length_and_section(model: Any, name: str, length_to_m: float) -> tuple[float | None, str | None]:
    """Longitud (m) y sección de un frame, como ``Sap2000Live.frame_geometry``
    (``GetPoints`` + ``GetCoordCartesian`` + ``GetSection``). ``None`` si
    SAP2000 no puede entregarlos (el listado no debe fallar por una barra)."""
    try:
        p1, p2 = _unpack(model.FrameObj.GetPoints(name, "", ""), "FrameObj.GetPoints")
        c1 = _unpack(model.PointObj.GetCoordCartesian(p1, 0.0, 0.0, 0.0), "PointObj.GetCoordCartesian")
        c2 = _unpack(model.PointObj.GetCoordCartesian(p2, 0.0, 0.0, 0.0), "PointObj.GetCoordCartesian")
        length = math.dist(c1[:3], c2[:3]) * length_to_m
    except Exception:
        length = None
    try:
        prop, _auto = _unpack(model.FrameObj.GetSection(name, "", ""), "FrameObj.GetSection")
        section = str(prop)
    except Exception:
        section = None
    return length, section


def get_frames(model: Any) -> list[dict[str, Any]]:
    """Frames del modelo con su longitud (m) y sección."""
    names = list_frames(model)
    try:
        _force, length_to_m = _present_units(model)
    except Exception:
        length_to_m = 1.0
    frames = []
    for name in names:
        length, section = _frame_length_and_section(model, name, length_to_m)
        frames.append({"nombre": name, "longitud_m": length, "seccion": section})
    return frames


def _clean(value: Any) -> Any:
    """NaN e infinitos a ``None`` para que la respuesta sea JSON válido."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _frame_forces(model: Any, name: str, cases: list[str]) -> dict[str, list]:
    """Port de ``Sap2000Live.frame_forces`` con ``cases`` explícitos, en columnas paralelas."""
    force_to_tonf, length_to_m = _present_units(model)
    combo_names = set(list_combos(model, include_cases=False))
    if not cases:
        raise ValueError("No hay combos/casos para extraer.")
    setup = model.Results.Setup
    _unpack(setup.DeselectAllCasesAndCombosForOutput(), "DeselectAllCasesAndCombosForOutput")
    for c in cases:
        if c in combo_names:
            _unpack(setup.SetComboSelectedForOutput(c), f"SetComboSelectedForOutput({c})")
        else:
            _unpack(setup.SetCaseSelectedForOutput(c), f"SetCaseSelectedForOutput({c})")
    res = _unpack(_frame_force(model, name), "Results.FrameForce")
    n, _obj, obj_sta, _elm, _elm_sta, case, step, _stepn, P, V2, _V3, _T, _M2, M3 = res
    n = int(n)
    return {
        "estacion_m": [float(obj_sta[i] * length_to_m) for i in range(n)],
        "caso": [str(case[i]) for i in range(n)],
        "paso": [str(step[i]).strip() if step[i] else "" for i in range(n)],
        "P": [float(P[i] * force_to_tonf) for i in range(n)],
        "V2": [float(V2[i] * force_to_tonf) for i in range(n)],
        "M3": [float(M3[i] * force_to_tonf * length_to_m) for i in range(n)],
    }


def _frame_force(model: Any, name: str) -> Any:
    """``Results.FrameForce`` con la firma corta, que es la que funciona con pywin32 (SAP2000 23.3.1).

    La firma larga (ItemTypeElm, NumberResults y 13 arreglos de salida) solo se intenta si la corta falla:
    con pywin32 una larga de más de 16 argumentos lanza TypeError («takes from 1 to 17 positional arguments»).
    """
    try:
        return model.Results.FrameForce(name, 0)
    except Exception as corta:
        try:
            return model.Results.FrameForce(name, 0, 0, *[[] for _ in range(13)])
        except Exception as larga:
            raise SapHormigonError(
                f"Results.FrameForce('{name}') falló (firma corta: {corta}; firma larga: {larga})."
            ) from larga


def _leer_en_tonf_m(model: Any, frame: str, combos: list[str]) -> dict[str, list]:
    """Lee ``FrameForce`` de ``frame`` para ``combos`` con las unidades de SAP2000 fijadas a tonf-m.

    Las unidades originales se restauran siempre, aunque la lectura falle a mitad de camino.
    """
    try:
        original_units = model.GetPresentUnits()
    except Exception:
        original_units = None

    try:
        model.SetPresentUnits(_EUNITS_TONF_M)
    except Exception as exc:
        raise SapHormigonError(f"No se pudo fijar las unidades de SAP2000 a tonf-m: {exc}") from exc

    try:
        try:
            return _frame_forces(model, frame, list(combos))
        except SapHormigonError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise SapHormigonError(str(exc)) from exc
        except Exception as exc:
            raise SapHormigonError(f"Results.FrameForce('{frame}') falló: {exc}") from exc
    finally:
        if original_units is not None:
            try:
                model.SetPresentUnits(original_units)
            except Exception:
                pass


def get_frame_forces(model: Any, frame: str, combos: list[str]) -> dict[str, Any]:
    """Esfuerzos crudos de ``frame`` para ``combos``, en tonf, tonf·m y m (M3 sin invertir).

    Fuerza las unidades presentes de SAP2000 a tonf-m antes de leer ``Results.FrameForce`` y
    restaura las originales al terminar, incluso si la lectura falla. No calcula envolventes:
    devuelve columnas paralelas ``filas`` para que el servidor construya la demanda.
    """
    pedidos = list(dict.fromkeys(combos))
    filas = _leer_en_tonf_m(model, frame, pedidos)
    presentes = set(filas["caso"])
    faltan = [c for c in pedidos if c not in presentes]
    if faltan:
        raise SapHormigonError(f"Casos inexistentes en {frame}: {faltan}")
    estaciones = filas["estacion_m"]
    return _clean(
        {
            "frame": frame,
            "longitud_m": max(estaciones) if estaciones else None,
            "unidades": {"fuerza": "tonf", "longitud": "m"},
            "combos": pedidos,
            "filas": filas,
        }
    )


_TIPO_FRAME = 2  # eObjectType.Frame en SelectObj.GetSelected


def _selected_frame_names(model: Any) -> list[str]:
    """Nombres de los frames seleccionados en SAP2000 (``SelectObj.GetSelected``).

    Misma llamada y mismo tratamiento de la respuesta que ``bridge.nomogramas``: sin selección
    (o con selección sin frames) devuelve ``[]``, nunca un error por eso.
    """
    try:
        raw = model.SelectObj.GetSelected(0, [], [])
    except TypeError:
        raw = model.SelectObj.GetSelected()
    codigo, salidas = split_return(raw)
    if codigo != 0:
        raise SapHormigonError(f"SAP2000 SelectObj.GetSelected devolvió {codigo}")
    arrays = [list(x) for x in salidas if isinstance(x, (list, tuple))]
    if not arrays or all(len(a) == 0 for a in arrays):
        return []
    if len(arrays) < 2:
        raise SapHormigonError("Respuesta inesperada de SAP2000 SelectObj.GetSelected.")
    tipos, nombres = arrays[-2], arrays[-1]
    if len(tipos) != len(nombres):
        raise SapHormigonError("SAP2000 SelectObj.GetSelected devolvió arrays de distinto tamaño.")
    return [str(n) for t, n in zip(tipos, nombres) if int(t) == _TIPO_FRAME]


def _frame_ends(model: Any, name: str) -> tuple[str | None, str | None]:
    """Nudos inicial y final de un frame; ``(None, None)`` si SAP2000 no los entrega."""
    try:
        p1, p2 = _unpack(model.FrameObj.GetPoints(name, "", ""), "FrameObj.GetPoints")
        return str(p1), str(p2)
    except Exception:
        return None, None


def get_selected_frames(model: Any) -> dict[str, Any]:
    """Frames seleccionados en SAP2000 con longitud, sección y nudos ``nudo_i``/``nudo_j``.

    Sólo lee. Los nudos permiten a la web ordenar los elementos en cadena y apoyar la viga
    en cada nudo. Sin selección devuelve ``frames: []``.
    """
    nombres = _selected_frame_names(model)
    try:
        _force, length_to_m = _present_units(model)
    except Exception:
        length_to_m = 1.0
    frames = []
    for name in dict.fromkeys(nombres):
        length, section = _frame_length_and_section(model, name, length_to_m)
        nudo_i, nudo_j = _frame_ends(model, name)
        frames.append(
            {
                "nombre": name,
                "longitud_m": length,
                "seccion": section,
                "nudo_i": nudo_i,
                "nudo_j": nudo_j,
            }
        )
    return _clean({"frames": frames})


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


def _short_name(value: str, label: str) -> str:
    value = _valid_name(value, label)
    if len(value) > _MAX_NAME:
        raise ValueError(f"{label} must be at most {_MAX_NAME} characters")
    return value


class FrameForcesBody(BaseModel):
    frame: str
    combos: list[str]

    @field_validator("frame")
    @classmethod
    def frame_name(cls, value: str) -> str:
        return _short_name(value, "frame")

    @field_validator("combos")
    @classmethod
    def bounded_combos(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_COMBOS:
            raise ValueError(f"combos debe contener entre 1 y {_MAX_COMBOS} combinaciones")
        return [_nombre_combo(combo) for combo in value]


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/hormigon/*`` con el mismo
    envoltorio ``call`` (actor STA, token y mapeo de errores a HTTP) que
    ``bridge.diagrama``."""
    prefix = "/v1/sap/actions/hormigon"

    @app.get(f"{prefix}/frames")
    def hormigon_frames():
        return call(lambda model: {"frames": get_frames(model)})

    @app.get(f"{prefix}/combos")
    def hormigon_combos():
        return call(lambda model: {"combos": list_combos(model)})

    @app.get(f"{prefix}/selected-frames")
    def hormigon_selected_frames():
        return call(get_selected_frames)

    @app.post(f"{prefix}/frame-forces")
    def hormigon_frame_forces(body: FrameForcesBody):
        return call(lambda model: get_frame_forces(model, body.frame, body.combos))
