"""Acciones SAP2000 para perfiles, combinaciones y espectros.

Este módulo no inicia COM ni conserva referencias a ``SapModel``.  Las rutas se
instalan en el agente local y cada operación se envía al actor STA de la sesión.
Las mutaciones requieren una vista previa y ``confirmed=true``.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,128}$")
_MAX_ITEMS = 500
# Cargas nodales: nodos × patrones de carga; un modelo real supera con holgura los 500.
_MAX_LOAD_ROWS = 20000


class ActionValidationError(ValueError):
    """Payload inválido antes de efectuar una llamada a SAP2000."""


class PartialApplyError(RuntimeError):
    """SAP applied an earlier item before a later one failed."""

    def __init__(self, result: dict[str, Any]):
        super().__init__(result["error"])
        self.result = result


class SectionExistsError(RuntimeError):
    """A frame-property name already exists and ``overwrite`` was not set."""

    def __init__(self, name: str):
        message = f"La sección {name} ya existe en el modelo."
        super().__init__(message)
        self.detail = {"code": "section_exists", "name": name, "message": message}


# Nombres de sección: el catálogo H (JIS G3192) marca con "*" las variaciones
# comerciales (p. ej. "H414x405x232.8*") y el escritorio envía el nombre tal
# cual a SAP2000, que decide si lo acepta (ver el hint de create_section).
_SECTION_NAME = re.compile(r"^[^\\/:?\"<>|\x00-\x1f]{1,128}$")


def _valid_name(value: str, label: str = "name", allow_star: bool = False) -> str:
    value = str(value).strip()
    if not (_SECTION_NAME if allow_star else _NAME).fullmatch(value):
        raise ActionValidationError(f"Invalid {label}; use 1–128 printable SAP2000 name characters")
    return value


# Nombres de combinaciones de carga: SAP2000 admite «:» (los nombres por defecto
# son «ASD1: D + L»); se siguen rechazando los demás caracteres prohibidos.
_COMBO_NAME = re.compile(r"^[^\\/*?\"<>|\x00-\x1f]{1,128}$")


def _valid_combo_name(value: str, label: str = "combination name") -> str:
    value = str(value).strip()
    if not _COMBO_NAME.fullmatch(value):
        raise ActionValidationError(f"Invalid {label}; use 1–128 printable SAP2000 name characters")
    return value


def _ret_code(value: Any, operation: str) -> int:
    """Extract a SAP OAPI return code placed first or last by the binding.

    Unlike ``_ret`` this does not raise on a nonzero code: it is used where a
    nonzero return is a warning to report, not a failure to abort on.
    """
    if isinstance(value, tuple):
        candidates = [item for item in (value[-1], value[0]) if isinstance(item, int)]
        if not candidates:
            raise RuntimeError(f"Unexpected SAP2000 response from {operation}")
        code = candidates[0]
    else:
        code = value
    try:
        return int(code)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Unexpected SAP2000 response from {operation}") from exc


def _ret(value: Any, operation: str) -> None:
    """Raise on SAP OAPI return codes placed first or last by the binding."""
    code = _ret_code(value, operation)
    if code != 0:
        raise RuntimeError(f"SAP2000 {operation} returned {code}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def split_return(raw: Any, ret_first: bool | None = None) -> tuple[int, list[Any]]:
    """Separate a COM call result into ``(return_code, outputs)``.

    SAP2000's OAPI puts the return code last with comtypes/typed wrappers
    (``(out1, ..., ret)``) and first with pywin32 late binding (``(ret, out1, ...)``).
    The side is inferred from the types: the code is the only ``int`` at an end when
    the other end is not an ``int``. When both ends are ints (e.g. ``GetTypeOAPI``)
    the end holding ``0`` wins, ret-last by default; callers that already know the
    side from an unambiguous call in the same session pass ``ret_first``.
    """
    if not isinstance(raw, (tuple, list)):
        try:
            return int(raw), []
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Unexpected SAP2000 response (no return code)") from exc
    items = list(raw)
    if not items:
        raise RuntimeError("Unexpected SAP2000 response (empty)")
    if ret_first is None:
        first, last = _is_int(items[0]), _is_int(items[-1])
        if first and not last:
            ret_first = True
        elif last and not first:
            ret_first = False
        elif first and last:
            ret_first = items[0] == 0 and items[-1] != 0
        else:
            raise RuntimeError("Unexpected SAP2000 response (no return code)")
    if ret_first:
        return int(items[0]), items[1:]
    return int(items[-1]), items[:-1]


def _name_list(response: Any, operation: str) -> list[str]:
    """Normalize GetNameList returns from pywin32/comtypes.

    The original bridges receive both ``(ret, count, names)`` and
    ``(count, names, ret)`` shapes, depending on the generated COM wrapper.
    """
    if not isinstance(response, tuple) or len(response) < 2:
        raise RuntimeError(f"Unexpected SAP2000 response from {operation}")
    _ret(response, operation)
    names = next(
        (
            item
            for item in response
            if isinstance(item, (list, tuple)) and all(isinstance(name, str) for name in item)
        ),
        None,
    )
    if names is None:
        return []
    return [str(name) for name in names if str(name)]


def _get_name_list(member: Any, operation: str, *preferred_args: Any) -> list[str]:
    """Call GetNameList across the short and ByRef-friendly COM signatures.

    Mirrors ``reacciones.py::_get_name_list``: tries ``GetNameList()`` bare,
    then with ``(0, [])`` ByRef placeholders, and finally the legacy
    ``GetNameList_1(0, [])`` some older SAP2000 builds expose instead.
    """
    attempts = (preferred_args, (), (0, [])) if preferred_args else ((), (0, []))
    failures: list[Exception] = []
    for args in attempts:
        try:
            return _name_list(member.GetNameList(*args), operation)
        except TypeError as exc:
            failures.append(exc)
    legacy = getattr(member, "GetNameList_1", None)
    if callable(legacy):
        try:
            return _name_list(legacy(0, []), f"{operation}_1")
        except TypeError as exc:
            failures.append(exc)
    raise RuntimeError(f"SAP2000 rejected supported arguments for {operation}: {failures[-1]}")


def _call_get_coord_cartesian(model: Any, name: str) -> tuple[float, float, float]:
    """Read a joint's cartesian coordinates tolerating SAP2000's known signatures.

    Literal port of ``coordenadas.py::_call_get_coord_cartesian`` (see
    ``app/sap2000/coordenadas.py`` in engineering_suite, lines ~48-98): tries
    the direct signature first (``GetCoordCartesian(name) -> (ret, x, y, z)``),
    then VARIANT ByRef output parameters for bindings that pass arguments by
    reference, then a ret-less 3-tuple for old signatures. ``pythoncom`` and
    ``win32com.client`` are imported lazily, only inside the VARIANT branch,
    so this module still imports on Linux.

    Raises ``RuntimeError`` when none of the three known signatures succeeds:
    the original does not return a ``(0, 0, 0)`` sentinel, which would be
    indistinguishable from a real point at the origin and would silently
    corrupt mesh geometry or the moment lever arms that consume this value.
    """
    name = str(name)

    # Intento 1: firma directa (v21+), espera (ret, x, y, z)
    try:
        ret = model.PointObj.GetCoordCartesian(name)
        if isinstance(ret, tuple) and len(ret) >= 4:
            ret_code = int(ret[0])
            if ret_code == 0:
                return (float(ret[1]), float(ret[2]), float(ret[3]))
    except TypeError:
        pass
    except Exception:
        pass

    errores: list[Exception] = []

    # Intento 2: argumentos por referencia (COM out params con VARIANT)
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore

        x = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        y = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        z = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        ret_code = model.PointObj.GetCoordCartesian(name, x, y, z)
        if ret_code == 0 or ret_code == (0,):
            return (float(x.value), float(y.value), float(z.value))
    except Exception as e:
        errores.append(e)

    # Intento 3: tupla con len=3 (version antigua sin ret_code al inicio)
    try:
        ret = model.PointObj.GetCoordCartesian(name)
        if isinstance(ret, tuple) and len(ret) >= 3:
            return (float(ret[0]), float(ret[1]), float(ret[2]))
    except Exception as e:
        errores.append(e)

    logger.error(
        "bridge.actions._call_get_coord_cartesian: no se pudo leer la "
        "coordenada del nodo %r con ninguna de las firmas conocidas "
        "(errores: %s)",
        name,
        [str(e) for e in errores],
    )
    raise RuntimeError(f"No se pudo obtener la coordenada del nodo '{name}' desde SAP2000.")


def model_is_locked(model: Any) -> bool:
    """Whether SAP2000 has locked the model (i.e. it has been analyzed).

    Literal port of ``estado_modelo._modelo_bloqueado`` (see
    ``app/sap2000/estado_modelo.py`` in engineering_suite): SAP2000 locks a
    model automatically after a successful analysis, so an unlocked model is
    the original's direct signal that no results exist yet for any case or
    combination (``modelo_no_corrido`` is exactly ``not model_is_locked``).
    Tolerates whatever shape ``GetModelIsLocked`` returns (a bare bool, or a
    ByRef-style tuple with the bool anywhere in it); any failure to call it
    is treated as "not locked", same as the original.
    """
    try:
        ret = model.GetModelIsLocked()
    except Exception:
        return False
    if isinstance(ret, (list, tuple)):
        for v in ret:
            if isinstance(v, bool):
                return v
        return bool(ret[0]) if ret else False
    return bool(ret)


def profile_catalog(model: Any) -> dict[str, list[str]]:
    """Read material and frame-property names using the source OAPI signatures."""
    return {
        # The original profile sender calls PropMaterial.GetNameList(0, []).
        "materials": _get_name_list(model.PropMaterial, "PropMaterial.GetNameList", 0, []),
        "sections": _get_name_list(model.PropFrame, "PropFrame.GetNameList"),
    }


def profile_diagnostics(model: Any) -> list[str]:
    """Raw COM diagnostics (port of ``SAP2000Connection.diagnostics``).

    Lines with what SAP2000 returns *uninterpreted* through COM, to tell
    whether the problem is how data is read/sent over COM or SAP2000/the model
    itself. Each probe is isolated: an exception becomes a line, never aborts.
    """
    out: list[str] = []

    def probe(label: str, fn: Any) -> None:
        try:
            out.append(f"{label}: {fn()!r}")
        except Exception as exc:
            out.append(f"{label}: EXCEPCIÓN {_clean_com_error(exc)}")

    probe("SapModel.GetModelFilename", lambda: model.GetModelFilename())
    probe("SapModel.GetPresentUnits", lambda: model.GetPresentUnits())
    probe("SapModel.GetModelIsLocked", lambda: model.GetModelIsLocked())
    probe("PropMaterial.GetNameList(0, [])", lambda: model.PropMaterial.GetNameList(0, []))
    probe("PropMaterial.GetNameList()", lambda: model.PropMaterial.GetNameList())
    probe("PropFrame.GetNameList(0, [])", lambda: model.PropFrame.GetNameList(0, []))
    return out


def _clean_com_error(exc: Exception) -> str:
    """Readable COM error text (literal port of ``SAP2000Connection._clean_com_error``)."""
    if getattr(exc, "args", None):
        args = exc.args[0]
        if isinstance(args, tuple) and len(args) >= 3:
            return f"{args[1]} (hresult={args[0]})"
        if isinstance(args, str):
            return args
    return str(exc)


def log_profile_diagnostics_if_no_materials(model: Any, materials: list[str]) -> None:
    """Log raw COM diagnostics (logging module, not UI) when no materials came back."""
    if materials:
        return
    try:
        for line in profile_diagnostics(model):
            logger.warning("[DIAG] %s", line)
    except Exception as exc:
        logger.warning("[DIAG] no se pudo diagnosticar: %s", exc)


def selected_frames(model: Any) -> list[dict[str, str]]:
    """Return only selected frame objects and their assigned section.

    ``SelectObj.GetSelected`` supplies object-type/name arrays; eObjectType.Frame
    is 2 in the SAP2000 OAPI.  Every selected frame is resolved with the
    documented ``FrameObj.GetSection`` call.
    """
    raw = model.SelectObj.GetSelected()
    if not isinstance(raw, tuple) or len(raw) < 4:
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    _ret(raw, "SelectObj.GetSelected")
    # Se miran todos los elementos: con el retorno al inicio (ret, n, types, names) el último es ``names``.
    arrays = [item for item in raw if isinstance(item, (list, tuple))]
    if len(arrays) < 2:
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    types, names = arrays[-2], arrays[-1]
    if len(types) != len(names):
        raise RuntimeError("SAP2000 SelectObj.GetSelected returned mismatched arrays")
    result: list[dict[str, str]] = []
    for object_type, name in zip(types, names):
        if int(object_type) != 2:  # SAP2000 eObjectType.Frame
            continue
        section_response = model.FrameObj.GetSection(str(name))
        if not isinstance(section_response, tuple) or len(section_response) < 2:
            raise RuntimeError(f"Unexpected SAP2000 response from FrameObj.GetSection({name!r})")
        _ret(section_response, f"FrameObj.GetSection({name!r})")
        _code, outs = split_return(section_response)
        section = next((item for item in outs if isinstance(item, str) and item), "")
        result.append({"frame": str(name), "section": section})
    return result


def _frame_length_mm(model: Any, frame: str) -> float:
    """Length of a frame from the coordinates of its two end points.

    Both calls are VERIFIED against the OAPI reference: ``FrameObj.GetPoints``
    (frame name in, the two point names out) and ``PointObj.GetCoordCartesian``
    (point name in, x/y/z out).  The caller is responsible for present units
    being N_mm_C so the result is in millimeters.
    """
    points = model.FrameObj.GetPoints(frame)
    if not isinstance(points, tuple) or len(points) < 3:
        raise RuntimeError(f"Unexpected SAP2000 response from FrameObj.GetPoints({frame!r})")
    _ret(points, f"FrameObj.GetPoints({frame!r})")
    names = [item for item in points if isinstance(item, str) and item]
    if len(names) < 2:
        raise RuntimeError(f"Unexpected SAP2000 response from FrameObj.GetPoints({frame!r})")
    point_i, point_j = names[0], names[1]
    coordinates: list[tuple[float, float, float]] = []
    for point in (point_i, point_j):
        response = model.PointObj.GetCoordCartesian(point)
        if not isinstance(response, tuple) or len(response) < 4:
            raise RuntimeError(f"Unexpected SAP2000 response from PointObj.GetCoordCartesian({point!r})")
        _ret(response, f"PointObj.GetCoordCartesian({point!r})")
        numbers = [item for item in response if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if len(numbers) < 3:
            raise RuntimeError(f"Unexpected SAP2000 response from PointObj.GetCoordCartesian({point!r})")
        coordinates.append((float(numbers[0]), float(numbers[1]), float(numbers[2])))
    return math.dist(coordinates[0], coordinates[1])


# Getters DERIVED from create_section's Set* calls, positionally: every one of
# these Set* calls takes (name, material, depth, width, tf, tw, ...), so its
# paired Get*(name) is assumed to return (material, depth, width, tf, tw, ...,
# ret) through ByRef output parameters.  This has NOT been exercised against a
# running SAP2000 instance — only the write side (create_section) has a
# documented signature in this repository.  SAP2000 returns a nonzero code
# when a section is queried with the wrong Get* (e.g. GetChannel on an
# I-section), so trying each call in turn and keeping the first success is how
# a selected frame's section family is identified here; this bridge does not
# call PropFrame.GetTypeOAPI or any other function without a signature already
# used elsewhere in this module.
_DIMENSION_GETTERS = (
    ("I", "GetISection"),
    ("Channel", "GetChannel"),
    ("Angle", "GetAngle"),
    ("Tube", "GetTube"),
)


def _read_dimensions(model: Any, section: str) -> tuple[str, dict[str, float]] | None:
    """Try each derived Get* call for ``section``; return the first that matches.

    Returns ``None`` when no known getter accepts the section (an unsupported
    section family, e.g. a plate girder or a database section), so the caller
    can report the frame with empty dimensions instead of failing outright.
    """
    for section_type, method in _DIMENSION_GETTERS:
        getter = getattr(model.PropFrame, method, None)
        if getter is None:
            continue
        try:
            response = getter(section)
        except Exception:
            continue
        if not isinstance(response, tuple) or len(response) < 6:
            continue
        try:
            _ret(response, f"PropFrame.{method}({section!r})")
        except RuntimeError:
            continue
        try:
            depth, width, tf, tw = (float(response[i]) for i in (1, 2, 3, 4))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in (depth, width, tf, tw)):
            continue
        return section_type, {"h_mm": depth, "b_mm": width, "tf_mm": tf, "tw_mm": tw}
    return None


_UNSUPPORTED_SECTION_NOTE = (
    "Tipo de sección no reconocido (no I/Canal/Ángulo/Tubo); complete las dimensiones manualmente."
)


def selected_frame_properties(model: Any) -> list[dict[str, Any]]:
    """Section geometry and length in mm for the frames selected in SAP2000.

    Feeds Esbelteces directly: for each selected frame this returns the frame
    and section names, the section family when it could be identified, its
    dimensions in mm, and the frame length in mm from its end-point
    coordinates.  See ``_frame_length_mm`` (verified OAPI calls) and
    ``_read_dimensions`` (derived, unverified calls) for what backs each
    field.  Fy is always ``None``: no call already used in this repository
    reads a material's yield stress, and this bridge does not guess a
    structural value — the web UI must let the user fill it in by hand.

    A section whose family ``_read_dimensions`` cannot identify does not fail
    the whole request: that frame comes back with empty dimensions and a
    ``note`` explaining why, so the user sees exactly what could and could not
    be imported.  A genuine SAP2000 error while reading the selection or a
    frame's length (a return code that is not 0) is not caught here — it
    propagates so ``install_routes`` turns it into a 409 instead of a silently
    wrong length.

    This only reads the model: units are switched to N_mm_C for the read and
    restored in a ``finally``, exactly like ``create_section``, and nothing is
    written to SAP2000.
    """
    frames = selected_frames(model)
    if not frames:
        return []
    previous = model.GetPresentUnits()
    changed_units = int(previous) != 9  # eUnits.N_mm_C
    try:
        if changed_units:
            _ret(model.SetPresentUnits(9), "SetPresentUnits(N_mm_C)")
        results: list[dict[str, Any]] = []
        for item in frames:
            frame, section = item["frame"], item["section"]
            length_mm = _frame_length_mm(model, frame)
            dims = _read_dimensions(model, section)
            section_type, dimensions = dims if dims else (None, {})
            results.append(
                {
                    "frame": frame,
                    "section": section,
                    "section_type": section_type,
                    "h_mm": dimensions.get("h_mm"),
                    "b_mm": dimensions.get("b_mm"),
                    "tf_mm": dimensions.get("tf_mm"),
                    "tw_mm": dimensions.get("tw_mm"),
                    "fy_mpa": None,
                    "length_mm": length_mm,
                    "note": None if dims else _UNSUPPORTED_SECTION_NOTE,
                }
            )
        return results
    finally:
        if changed_units:
            _ret(model.SetPresentUnits(previous), "SetPresentUnits(restore)")


def _existing(model: Any, member: str) -> set[str]:
    return set(_name_list(getattr(model, member).GetNameList(), f"{member}.GetNameList"))


# ---------------------------------------------------------------------------
# Groups (shared by the SAP2000 snapshot and the Foundations "import groups"
# dialog): GroupDef.GetAssignments is the group-definition membership call,
# not PointObj/AreaObj.GetGroupAssign, which queries a single object's own
# group assignments.  eObjectType: 1=Point, 5=Area.
# ---------------------------------------------------------------------------


def _call_group_assignments(model: Any, group_name: str) -> Any:
    """Call GroupDef.GetAssignments tolerating short and long COM signatures.

    Mirrors ``section_cuts.py::_llamar_get_assignments`` from the verified
    source: try the ByRef-friendly signature with empty output-parameter
    arrays first, and fall back to the short signature if SAP2000 rejects it.
    """
    try:
        return model.GroupDef.GetAssignments(str(group_name), 0, [], [])
    except Exception:
        return model.GroupDef.GetAssignments(str(group_name))


def _parse_group_assignments(raw: Any) -> tuple[list[int], list[str]]:
    """Normalize GetAssignments to (object_types, object_names).

    win32com returns ``(NumberItems, ObjectType[], ObjectName[], ret)``, but
    the pair of parallel arrays of matching length is located instead of
    assuming a fixed position, mirroring
    ``section_cuts.py::_parsear_get_assignments``.
    """
    if not isinstance(raw, (list, tuple)):
        return [], []
    for i in range(len(raw) - 1):
        types, names = raw[i], raw[i + 1]
        if (
            isinstance(types, (list, tuple))
            and isinstance(names, (list, tuple))
            and len(types) == len(names)
            and len(types) > 0
        ):
            try:
                return [int(t) for t in types], [str(n) for n in names]
            except Exception:
                continue
    return [], []


def group_members(model: Any, group_name: str, object_type: int) -> list[str]:
    """Members of ``group_name`` of the given eObjectType (1=Point, 5=Area)."""
    try:
        raw = _call_group_assignments(model, group_name)
    except Exception:
        return []
    types, names = _parse_group_assignments(raw)
    return [names[i] for i in range(len(names)) if types[i] == object_type]


def _group_name_list(model: Any) -> list[str]:
    """GroupDef.GetNameList, tolerating a raw name tuple or a (ret, count, names) response."""
    try:
        raw = model.GroupDef.GetNameList()
    except Exception:
        return []
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(x, str) for x in raw):
        return [str(x) for x in raw]
    try:
        return _name_list(raw, "GroupDef.GetNameList")
    except Exception:
        return []


def groups_from_model(model: Any) -> list[dict[str, Any]]:
    """Every SAP2000 group with at least one point or area member.

    Shared by the snapshot's default group resolution
    (``bridge/sap2000_agent.py``) and the Foundations "import groups" dialog
    (``/v1/sap/actions/foundations/groups``): one source of truth for
    GroupDef.GetNameList + GetAssignments instead of two.
    """
    groups = []
    for name in _group_name_list(model):
        nodes = group_members(model, name, 1)  # eObjectType.Point
        shells = group_members(model, name, 5)  # eObjectType.Area
        if nodes or shells:
            groups.append({"id": str(name), "name": str(name), "nodes": nodes, "shells": shells})
    return groups


def assign_section(model: Any, frames: list[str], section: str) -> dict[str, Any]:
    section = _valid_name(section, "section", allow_star=True)
    catalog = profile_catalog(model)
    if section not in catalog["sections"]:
        raise ActionValidationError(f"Section {section!r} is not in the SAP2000 frame-property catalog")
    if not frames or len(frames) > _MAX_ITEMS:
        raise ActionValidationError("frames must contain 1–500 entries")
    unique = list(dict.fromkeys(_valid_name(frame, "frame") for frame in frames))
    changed: list[str] = []
    for frame in unique:
        try:
            _ret(model.FrameObj.SetSection(frame, section), f"FrameObj.SetSection({frame!r})")
            changed.append(frame)
        except Exception as exc:
            # SAP has no transaction for SetSection.  Report the precise partial
            # state instead of claiming a rollback that could destroy Auto Select.
            raise PartialApplyError(
                {
                    "section": section,
                    "assigned_frames": changed,
                    "failed_frame": frame,
                    "error": str(exc),
                    "partial": bool(changed),
                }
            ) from exc
    return {"section": section, "assigned_frames": changed, "partial": False}


def create_combinations(model: Any, combinations: list[dict[str, Any]]) -> dict[str, Any]:
    existing = _existing(model, "RespCombo")
    names = [str(combo["name"]) for combo in combinations]
    duplicate = sorted({name for name in names if names.count(name) > 1})
    collisions = sorted(set(names) & existing)
    if duplicate or collisions:
        problem = (
            f"duplicate requested names: {duplicate}"
            if duplicate
            else f"existing SAP2000 combinations: {collisions}"
        )
        raise ActionValidationError(problem)
    created: list[str] = []
    try:
        for combo in combinations:
            name = combo["name"]
            _ret(model.RespCombo.Add(name, combo["combo_type"]), f"RespCombo.Add({name!r})")
            created.append(name)
            for item in combo["items"]:
                # eCNameType: LoadCase=0, LoadCombo=1.  These are the same
                # calls used by the original combination import bridge.
                _ret(
                    model.RespCombo.SetCaseList(name, item["kind"], item["name"], item["factor"]),
                    f"RespCombo.SetCaseList({name!r})",
                )
    except Exception:
        delete = getattr(model.RespCombo, "Delete", None)
        if callable(delete):
            for name in reversed(created):
                try:
                    _ret(delete(name), f"RespCombo.Delete({name!r})")
                except Exception:
                    pass
        raise
    return {"created": created}


def create_response_spectrum(
    model: Any, name: str, points: list[dict[str, float]], damping: float
) -> dict[str, Any]:
    name = _valid_name(name, "spectrum name")
    existing = set(_name_list(model.Func.GetNameList(0, [], 1), "Func.GetNameList(response-spectrum)"))
    if name in existing:
        raise ActionValidationError(f"SAP2000 response-spectrum function already exists: {name!r}")
    periods = [point["period"] for point in points]
    values = [point["value"] for point in points]
    created = False
    try:
        _ret(
            model.Func.FuncRS.SetUser(name, len(points), periods, values, damping),
            f"Func.FuncRS.SetUser({name!r})",
        )
        created = True
    except Exception:
        delete = getattr(model.Func.FuncRS, "Delete", None)
        if created and callable(delete):
            try:
                _ret(delete(name), f"Func.FuncRS.Delete({name!r})")
            except Exception:
                pass
        raise
    return {"created": name, "point_count": len(points), "damping": damping}


_SECTION_TYPES = {"I", "Channel", "Angle", "Tube", "Pipe"}


def create_section(
    model: Any,
    name: str,
    section_type: str,
    material: str,
    depth: float,
    width: float,
    tw: float,
    tf: float,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create or overwrite one geometric frame property, matching the original sender.

    Geometry is expressed in mm; units are forced to N_mm_C for the call and
    restored in a ``finally``, exactly like the original ``send_section``. A
    name that already exists in the model is refused with
    :class:`SectionExistsError` unless ``overwrite`` is set — the original
    desktop tool overwrote silently, but the web asks first.
    """
    name = _valid_name(name, "section name", allow_star=True)
    material = _valid_name(material, "material")
    if section_type not in _SECTION_TYPES:
        raise ActionValidationError("section_type must be I, Channel, Angle, Tube, or Pipe")
    dimensions = {"depth": depth, "width": width, "tw": tw, "tf": tf}
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in dimensions.values()):
        raise ActionValidationError("depth, width, tw, and tf must be finite values greater than zero (mm)")
    # Como el escritorio (sap2000_connection.py::send_section): si el modelo tiene
    # materiales y el elegido no está entre ellos, SAP2000 falla con un críptico
    # "Referencia a objeto no establecida...": se avisa antes. Si no se pueden
    # leer los materiales (o no hay), el material se escribe libre (el valor por
    # defecto de la UI es "A36Acero").
    try:
        model_materials = _get_name_list(model.PropMaterial, "PropMaterial.GetNameList", 0, [])
    except Exception:
        model_materials = []
    if model_materials and material not in model_materials:
        raise ActionValidationError(
            f"El material '{material}' no existe en el modelo de SAP2000. "
            f"Materiales disponibles: {', '.join(model_materials)}"
        )
    sections = _get_name_list(model.PropFrame, "PropFrame.GetNameList")
    overwritten = name in sections
    if overwritten and not overwrite:
        raise SectionExistsError(name)
    previous = model.GetPresentUnits()
    changed_units = int(previous) != 9  # eUnits.N_mm_C
    try:
        if changed_units:
            _ret(model.SetPresentUnits(9), "SetPresentUnits(N_mm_C)")
        # Set*(Name, Material, t3, t2, tf, tw, [t2b, tfb,] Color, Notes, GUID) — same
        # positional order as the original sap2000_connection.py::send_section.
        calls = {
            "I": ("SetISection", (name, material, depth, width, tf, tw, width, tf, -1, "", "")),
            "Channel": ("SetChannel", (name, material, depth, width, tf, tw, -1, "", "")),
            "Angle": ("SetAngle", (name, material, depth, width, tf, tw, -1, "", "")),
            "Tube": ("SetTube", (name, material, depth, width, tf, tw, -1, "", "")),
            # Tubo circular: SetPipe(Name, MatProp, T3 = diámetro exterior, Tw = espesor, ...).
            "Pipe": ("SetPipe", (name, material, depth, tw, -1, "", "")),
        }
        method, args = calls[section_type]
        try:
            code = _ret_code(getattr(model.PropFrame, method)(*args), f"PropFrame.{method}({name!r})")
        except Exception as exc:
            hint = ""
            if "no establecida como instancia" in str(exc):
                hint = (
                    f"\nPosibles causas: el material '{material}' no está definido en el "
                    "modelo (si no hay materiales, créalo en SAP2000: Define > Materials y "
                    "vuelve a conectar), o el nombre de la sección contiene un carácter "
                    "que SAP2000 no acepta (p. ej. '*')."
                )
            raise RuntimeError(f"Error enviando sección {name}: {exc}{hint}") from exc
    finally:
        if changed_units:
            _ret(model.SetPresentUnits(previous), "SetPresentUnits(restore)")
    if code != 0:
        message = f"[{name}] SAP2000 devolvió código {code} (puede ser warning)."
    else:
        message = f"[{name}] Sección enviada correctamente ({section_type})."
    return {
        "name": name,
        "section_type": section_type,
        "ret": code,
        "message": message,
        "overwritten": overwritten,
    }


class SectionBody(BaseModel):
    frames: list[str]
    section: str
    confirmed: bool = False

    @field_validator("section")
    @classmethod
    def section_name(cls, value: str) -> str:
        return _valid_name(value, "section", allow_star=True)

    @field_validator("frames")
    @classmethod
    def frame_names(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("frames must contain 1–500 entries")
        return [_valid_name(item, "frame") for item in value]


class CreateSectionBody(BaseModel):
    name: str
    section_type: str
    material: str
    depth: float = Field(gt=0)
    width: float = Field(gt=0)
    tw: float = Field(gt=0)
    tf: float = Field(gt=0)
    confirmed: bool = False
    overwrite: bool = False

    @field_validator("name")
    @classmethod
    def section_name(cls, value: str) -> str:
        return _valid_name(value, "section name", allow_star=True)

    @field_validator("material")
    @classmethod
    def material_name(cls, value: str) -> str:
        return _valid_name(value, "material")

    @field_validator("section_type")
    @classmethod
    def shape(cls, value: str) -> str:
        if value not in _SECTION_TYPES:
            raise ValueError("section_type must be I, Channel, Angle, Tube, or Pipe")
        return value

    @field_validator("depth", "width", "tw", "tf")
    @classmethod
    def finite_dimension(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("dimensions must be finite")
        return value


class ComboItem(BaseModel):
    name: str
    factor: float
    kind: int = Field(ge=0, le=1)

    @field_validator("name")
    @classmethod
    def item_name(cls, value: str) -> str:
        return _valid_combo_name(value, "case/combo name")

    @field_validator("factor")
    @classmethod
    def finite_factor(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("factor must be finite")
        return value


class Combination(BaseModel):
    name: str
    combo_type: int = Field(ge=0, le=1)
    items: list[ComboItem]

    @field_validator("name")
    @classmethod
    def combo_name(cls, value: str) -> str:
        return _valid_combo_name(value, "combination name")

    @field_validator("items")
    @classmethod
    def combo_items(cls, value: list[ComboItem]) -> list[ComboItem]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("items must contain 1–500 entries")
        return value


class CombinationsBody(BaseModel):
    combinations: list[Combination]
    confirmed: bool = False

    @field_validator("combinations")
    @classmethod
    def combos(cls, value: list[Combination]) -> list[Combination]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("combinations must contain 1–500 entries")
        return value


class SpectrumPoint(BaseModel):
    period: float = Field(ge=0)
    value: float = Field(ge=0)

    @field_validator("period", "value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("spectrum values must be finite")
        return value


class SpectrumBody(BaseModel):
    name: str
    points: list[SpectrumPoint]
    damping: float = Field(gt=0, le=1)
    confirmed: bool = False

    @field_validator("name")
    @classmethod
    def spectrum_name(cls, value: str) -> str:
        return _valid_name(value, "spectrum name")

    @field_validator("points")
    @classmethod
    def ordered_points(cls, value: list[SpectrumPoint]) -> list[SpectrumPoint]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("points must contain 1–500 entries")
        if any(right.period <= left.period for left, right in zip(value, value[1:])):
            raise ValueError("periods must be strictly increasing")
        return value


# ---------------------------------------------------------------------------
# Foundations "SAP2000" panel data operations (fondaciones live against SAP,
# ported from the original desktop app's app/sap2000/*.py).
# ---------------------------------------------------------------------------

_SELECTION_TYPES = {"joints": 1, "shells": 5, "frames": 2}
_LOAD_FIELDS = ("f1", "f2", "f3", "m1", "m2", "m3")


def load_cases_and_combos(model: Any) -> dict[str, list[str]]:
    """LoadCases and RespCombo name lists, port of reacciones.py:44-69."""
    return {
        "load_cases": _get_name_list(model.LoadCases, "LoadCases.GetNameList"),
        "combos": _get_name_list(model.RespCombo, "RespCombo.GetNameList"),
    }


def _parse_get_selected(raw: Any) -> tuple[list[Any], list[Any]] | None:
    """Normalize one ``SelectObj.GetSelected()`` response to (types, names).

    Returns ``None`` (never raises) when ``raw`` does not carry a matching
    pair of parallel arrays, so the caller can retry with the ByRef-style
    call, mirroring the tolerant parsing in
    ``coordenadas.py::_obtener_nodos_seleccionados_o_todos`` /
    ``presion_contacto.py::obtener_shells_seleccionados``.
    """
    if not isinstance(raw, tuple) or len(raw) < 4:
        return None
    try:
        _ret(raw, "SelectObj.GetSelected")
    except RuntimeError:
        return None
    # Se miran todos los elementos: con el retorno al inicio (ret, n, types, names) el último es ``names``.
    arrays = [item for item in raw if isinstance(item, (list, tuple))]
    if len(arrays) < 2:
        return None
    types, names = arrays[-2], arrays[-1]
    if len(types) != len(names):
        return None
    return list(types), list(names)


def selected_objects(model: Any, kind: str) -> list[str]:
    """Names of the selected joints, shells or frames, port of coordenadas.py:214-257 /
    presion_contacto.py:64-106 (``SelectObj.GetSelected``; eObjectType 1=Point, 5=Area).

    Tries the direct modern signature first and, if it does not yield a
    usable response, the ByRef-argument signature — same two attempts as
    the original ("Intento 1"/"Intento 2") before it gives up.
    """
    object_type = _SELECTION_TYPES[kind]
    parsed = None
    try:
        parsed = _parse_get_selected(model.SelectObj.GetSelected())
    except Exception:
        parsed = None
    if parsed is None:
        try:
            parsed = _parse_get_selected(model.SelectObj.GetSelected(0, [], []))
        except Exception:
            parsed = None
    if parsed is None:
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    types, names = parsed
    return [str(name) for object_type_i, name in zip(types, names) if int(object_type_i) == object_type]


def load_patterns(model: Any) -> list[str]:
    return _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")


# Código eUnits (documentado por CSI) para "Ton_m_C" — la presión de suelo
# solo tiene sentido en esa unidad, port de
# ``presion_contacto._obtener_presion_suelo_impl``/``diagramas_shell.
# _obtener_resultados_shell_impl`` (``conn.set_units("Ton_m_C")``).
_TON_M_C_UNITS = 12

TABLA_PRESION_SUELO = "Element Soil Pressure - Area Shells"
_CANDIDATOS_COLUMNA_AREA = ("area",)
_CANDIDATOS_COLUMNA_AREAELEM = ("areaelem",)
_CANDIDATOS_COLUMNA_JOINT = ("joint", "pointelm", "point")
_CANDIDATOS_COLUMNA_PRESSURE = ("pressure",)


def _select_case_for_output(model: Any, caso: str) -> None:
    """Selecciona un único caso/combinación para resultados, port de
    ``diagramas_shell.seleccionar_caso_para_output`` /
    ``reacciones._seleccionar_casos_output``: intenta clasificar ``caso``
    contra los nombres conocidos de combos/load cases y llama el
    ``Set*SelectedForOutput`` que corresponda; si no matchea ninguno de los
    dos (nombre desconocido o listado no disponible), prueba ambos — mismo
    criterio tolerante del original, que nunca lanza por estas llamadas de
    Setup (una falla aquí no debe impedir intentar dibujar el diagrama)."""
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception:
        pass
    if not caso:
        return
    try:
        disponibles = load_cases_and_combos(model)
    except Exception:
        disponibles = {"load_cases": [], "combos": []}
    caso_str = str(caso)
    es_combo = caso_str in disponibles.get("combos", [])
    es_case = caso_str in disponibles.get("load_cases", [])
    if es_combo or not es_case:
        try:
            model.Results.Setup.SetComboSelectedForOutput(caso_str, True)
        except Exception:
            pass
    if es_case or not es_combo:
        try:
            model.Results.Setup.SetCaseSelectedForOutput(caso_str, True)
        except Exception:
            pass


def select_all_cases_for_output(model: Any) -> None:
    """Default output-case selection when no cases/combos were requested.

    Literal port of the no-``casos`` branch of
    ``reacciones._seleccionar_casos_output`` (app/sap2000/reacciones.py:112-156):
    deselect everything, then select every load case for output — NOT
    combinations. Every original caller (reactions, displacements, nodal
    loads) runs ``_seleccionar_casos_output`` unconditionally before reading,
    so an unscoped read forces this same selection rather than leaving
    SAP2000's previous output selection untouched.
    """
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception:
        pass
    for caso in _get_name_list(model.LoadCases, "LoadCases"):
        try:
            model.Results.Setup.SetCaseSelectedForOutput(str(caso), True)
        except Exception:
            pass


def _response_arrays(raw: Any) -> list[list]:
    """Filtra los elementos ``list``/``tuple`` de una tupla de retorno COM,
    en orden — descarta el código de retorno y los contadores escalares sin
    depender de en qué posición haya quedado cada uno. Port de
    ``diagramas_shell._arrays_de_la_respuesta``."""
    if not isinstance(raw, (list, tuple)):
        return []
    return [list(x) for x in raw if isinstance(x, (list, tuple))]


def shell_geometry(
    model: Any, area_objects: list[str], joints_z: dict[str, float] | None = None
) -> tuple[dict[str, list[str]], dict[str, list[float]], dict[str, list[str]]]:
    """Geometría de malla de los shells (Area objects) pedidos, port de
    ``diagramas_shell.obtener_geometria_shells``.

    Devuelve ``(elementos, joints, elementos_por_area)``:
      - ``elementos``: ``{elemento_malla: [p1, p2, p3, p4]}`` (p4 = p3 si
        el shell es triangular). El nombre de elemento es el ID global que
        devuelve ``AreaObj.GetElm`` (el mismo que usa
        ``Results.AreaForceShell``/``AreaStressShell``/la tabla nativa de
        presión de suelo).
      - ``joints``: ``{punto: [x, y]}`` de todos los puntos usados (solo
        X/Y — planta, igual que el original solo usa las 2 primeras
        componentes para dibujar).
      - ``elementos_por_area``: ``{area: [elm_1, elm_2, ...]}``, mismo
        orden en que ``AreaObj.GetElm(area)`` los devuelve.

    ``joints_z`` (opcional): si se pasa un dict, se llena con ``{punto: z}``
    (la tabla "Z" de "Ver tabla de valores..." de Diagramas de shells). No
    cambia la forma de ``joints`` ni del contrato de ``shell-diagram``.
    """
    elementos: dict[str, list[str]] = {}
    joints: dict[str, list[float]] = {}
    elementos_por_area: dict[str, list[str]] = {}

    for area in area_objects:
        try:
            raw_elm = model.AreaObj.GetElm(area)
        except Exception:
            continue
        arrays_elm = _response_arrays(raw_elm)
        if not arrays_elm:
            continue
        elm_names = arrays_elm[0]
        if elm_names:
            elementos_por_area[str(area)] = [str(elm) for elm in elm_names]

        for elm in elm_names:
            try:
                raw_pts = model.AreaElm.GetPoints(elm)
            except Exception:
                continue
            arrays_pts = _response_arrays(raw_pts)
            if not arrays_pts:
                continue

            pts = [str(p) for p in arrays_pts[0]]
            if len(pts) < 3:
                continue

            for pt in pts:
                if pt not in joints:
                    try:
                        x, y, z = _call_get_coord_cartesian(model, pt)
                    except Exception:
                        continue
                    joints[pt] = [x, y]
                    if joints_z is not None:
                        joints_z[pt] = z

            p1, p2, p3 = pts[0], pts[1], pts[2]
            p4 = pts[3] if len(pts) >= 4 else pts[2]
            elementos[str(elm)] = [p1, p2, p3, p4]

    return elementos, joints, elementos_por_area


def _parse_generic_table(raw: Any) -> tuple[list[str], list[str]]:
    """Normaliza la salida de ``GetTableForDisplayArray`` a
    ``(columnas, datos_planos)``, port de
    ``presion_contacto._parsear_tabla_generica`` sin pandas: entre las
    listas de puros strings de la respuesta, las columnas son la más corta
    (no vacía) y los datos la más larga que sea múltiplo exacto de la
    cantidad de columnas — evita que ``FieldKeyList`` (vacío, se manda así
    para pedir todas las columnas) le gane a la lista real de columnas."""
    if not isinstance(raw, (list, tuple)):
        return [], []
    listas_str = [
        list(x)
        for x in raw
        if isinstance(x, (list, tuple)) and len(x) > 0 and all(isinstance(v, str) for v in x)
    ]
    if len(listas_str) < 2:
        return [], []
    columnas = min(listas_str, key=len)
    n_cols = len(columnas)
    if n_cols == 0:
        return [], []
    candidatos_datos = [
        lst for lst in listas_str if lst is not columnas and len(lst) % n_cols == 0 and len(lst) > 0
    ]
    if not candidatos_datos:
        return columnas, []
    datos = max(candidatos_datos, key=len)
    return columnas, datos


def _call_display_table(model: Any, table: str) -> Any:
    """``DatabaseTables.GetTableForDisplayArray`` tolerating the signature
    variants CSI's OAPI accepts across SAP2000 builds, port of
    ``presion_contacto._llamar_tabla_presion_suelo`` /
    ``diagramas_shell._llamar_tabla_area_forces_o_stresses``: the same six
    ``(TableVersion type, GroupName)`` combinations are tried in the same
    order. A call that raises no exception but returns zero columns is
    treated the same as a failed attempt and the next variant is tried —
    confirmed against a real SAP2000 instance (see the original's docstring):
    the first variant succeeds without error yet returns an empty
    ``FieldsKeysIncluded``. The first variant that returns any columns wins;
    if none do, the first attempt that did not raise is used as a last
    resort (same as the original, which does not give up on a technically
    successful but columnless response).
    """
    attempts: tuple[Callable[[], Any], ...] = (
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, [], "", 0, [], 0, []),
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, [], "ALL", 0, [], 0, []),
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, [], "", "", [], 0, []),
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, [], "ALL", "", [], 0, []),
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, [], 0, [], 0, []),
        lambda: model.DatabaseTables.GetTableForDisplayArray(table, []),
    )
    first_success: Any = None
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            raw = attempt()
        except Exception as exc:
            last_error = exc
            continue
        if first_success is None:
            first_success = raw
        columnas, _datos = _parse_generic_table(raw)
        if columnas:
            return raw
    if first_success is not None:
        return first_success
    raise RuntimeError(
        f"SAP2000 GetTableForDisplayArray({table!r}) failed with every known signature: {last_error}"
    )


def shell_soil_pressure(
    model: Any,
    elementos: dict[str, list[str]],
    elementos_por_area: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, float]]:
    """Presión de suelo (``Pressure``) por elemento de malla y punto, port
    de ``diagramas_shell.obtener_resultados_shell(..., "soil_pressure",
    "Pressure", ...)`` vía la tabla nativa "Element Soil Pressure - Area
    Shells" (no hay un ``Results.*`` dedicado en el OAPI para esto).

    Devuelve ``{elemento_malla: {punto: presion}}``, restringido a
    ``elementos``. La columna ``AreaElem`` de la tabla es un índice 1-based
    LOCAL a cada Area, no el ID global de ``elementos`` — se intenta primero
    el join directo por ID (por si alguna versión de SAP2000 sí lo trae
    así) y, si no matchea nada, se reconstruye vía ``Area`` +
    ``elementos_por_area`` (mismo orden que ``AreaObj.GetElm``), igual que
    ``diagramas_shell._extraer_resultados_por_area_e_indice``."""
    previous = model.GetPresentUnits()
    if int(model.SetPresentUnits(_TON_M_C_UNITS)) != 0:
        raise RuntimeError("SetPresentUnits(Ton_m_C) failed")
    try:
        raw = _call_display_table(model, TABLA_PRESION_SUELO)
        columnas, datos = _parse_generic_table(raw)
        if not columnas or not datos:
            return {}
        lower = {c.lower(): i for i, c in enumerate(columnas)}
        idx_area = next((lower[c] for c in _CANDIDATOS_COLUMNA_AREA if c in lower), None)
        idx_areaelem = next((lower[c] for c in _CANDIDATOS_COLUMNA_AREAELEM if c in lower), None)
        idx_joint = next((lower[c] for c in _CANDIDATOS_COLUMNA_JOINT if c in lower), None)
        idx_pressure = next((lower[c] for c in _CANDIDATOS_COLUMNA_PRESSURE if c in lower), None)
        if idx_joint is None or idx_pressure is None:
            return {}

        n_cols = len(columnas)
        elementos_pedidos = set(elementos)
        resultados: dict[str, dict[str, float]] = {}
        pendientes_por_area: list[tuple[str, int, str, float]] = []

        for f in range(len(datos) // n_cols):
            fila = datos[f * n_cols : f * n_cols + n_cols]
            try:
                presion = float(fila[idx_pressure])
            except (TypeError, ValueError):
                continue
            joint = str(fila[idx_joint])

            if idx_areaelem is not None:
                elm_directo = str(fila[idx_areaelem])
                if elm_directo in elementos_pedidos:
                    resultados.setdefault(elm_directo, {})[joint] = presion
                    continue

            if idx_area is not None and idx_areaelem is not None:
                pendientes_por_area.append(
                    (str(fila[idx_area]), int(float(fila[idx_areaelem])), joint, presion)
                )

        if not resultados and pendientes_por_area and elementos_por_area:
            for area, indice_1based, joint, presion in pendientes_por_area:
                elms_area = elementos_por_area.get(area)
                if not elms_area:
                    continue
                posicion = indice_1based - 1
                if posicion < 0 or posicion >= len(elms_area):
                    continue
                elm = elms_area[posicion]
                if elm not in elementos_pedidos:
                    continue
                resultados.setdefault(elm, {})[joint] = presion

        return resultados
    finally:
        if int(model.SetPresentUnits(previous)) != 0:
            raise RuntimeError("SAP2000 present units could not be restored")


def _parse_load_force(response: Any) -> list[dict[str, Any]]:
    """Normalize PointObj.GetLoadForce, port of cargas_nodales.py:91-110.

    ``GetLoadForce`` returns 10 parallel output arrays (PointName, LoadPat,
    LCStep, CSys, F1, F2, F3, M1, M2, M3); the last 10 list/tuple items of the
    response are those arrays regardless of where the return code sits.
    """
    arrays = [list(x) if isinstance(x, tuple) else x for x in response if isinstance(x, (list, tuple))]
    if len(arrays) < 10:
        return []
    _point_name, load_pat, _lc_step, csys, f1, f2, f3, m1, m2, m3 = arrays[-10:]
    n = min(len(load_pat), len(csys), *(len(v) for v in (f1, f2, f3, m1, m2, m3)))
    return [
        {
            "load_pat": str(load_pat[i]),
            "coord_sys": str(csys[i]),
            "f1": float(f1[i]),
            "f2": float(f2[i]),
            "f3": float(f3[i]),
            "m1": float(m1[i]),
            "m2": float(m2[i]),
            "m3": float(m3[i]),
        }
        for i in range(n)
    ]


def _with_ton_m_c(model: Any, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with SAP present units forced to Ton_m_C, restoring after.

    Port of the ``conn.set_units("Ton_m_C")`` / ``conn.restore_units()``
    pair that wraps every read/write in ``cargas_nodales.py``
    (``_obtener_cargas_existentes_impl``, ``_aplicar_cargas_en_nodos_impl``):
    both force units unconditionally and restore them in a ``finally``, even
    on failure. eUnits code 12 = Ton_m_C (see ``connection.py::UNIDADES``).
    """
    previous = model.GetPresentUnits()
    if int(model.SetPresentUnits(_TON_M_C_UNITS)) != 0:
        raise RuntimeError("SetPresentUnits(Ton_m_C) failed")
    try:
        return fn()
    finally:
        if int(model.SetPresentUnits(previous)) != 0:
            raise RuntimeError("SAP2000 present units could not be restored")


def existing_loads(model: Any, joints: list[str]) -> list[dict[str, Any]]:
    """Loads already applied to ``joints``, port of cargas_nodales.py:31-71.

    Several ``SetLoadForce`` calls without ``Replace`` can stack multiple
    loads on the same joint/pattern/coordinate-system; those are summed, one
    row per (joint, load_pat, coord_sys), same as the original loop. Units
    are forced to Ton_m_C for the read, same as the original
    ``_obtener_cargas_existentes_impl``.
    """

    def read() -> list[dict[str, Any]]:
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        for joint in dict.fromkeys(str(j) for j in joints):
            response = model.PointObj.GetLoadForce(joint, 0, [], [], [], [], [], [], [], [], [], [], 0)
            for item in _parse_load_force(response):
                key = (joint, item["load_pat"], item["coord_sys"])
                row = rows.setdefault(
                    key,
                    {"joint": joint, "load_pat": item["load_pat"], "coord_sys": item["coord_sys"]}
                    | {field: 0.0 for field in _LOAD_FIELDS},
                )
                for field in _LOAD_FIELDS:
                    row[field] += item[field]
        return list(rows.values())

    return _with_ton_m_c(model, read)


def _existing_loads_no_units(model: Any, joints: list[str]) -> list[dict[str, Any]]:
    """``existing_loads`` body without its own unit forcing/restoring.

    Used by ``preview_loads`` so a preview of many rows sets/restores
    Ton_m_C once instead of once per row.
    """
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for joint in dict.fromkeys(str(j) for j in joints):
        response = model.PointObj.GetLoadForce(joint, 0, [], [], [], [], [], [], [], [], [], [], 0)
        for item in _parse_load_force(response):
            key = (joint, item["load_pat"], item["coord_sys"])
            row = rows.setdefault(
                key,
                {"joint": joint, "load_pat": item["load_pat"], "coord_sys": item["coord_sys"]}
                | {field: 0.0 for field in _LOAD_FIELDS},
            )
            for field in _LOAD_FIELDS:
                row[field] += item[field]
    return list(rows.values())


def preview_loads(model: Any, rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """Compute before/after without writing to SAP2000.

    Reads the "before" state in Ton_m_C, same units as the values ``rows``
    already carry from the web UI (see ``cargasNodales.ts``), matching the
    original ``obtener_cargas_existentes`` unit forcing.
    """

    def build() -> dict[str, Any]:
        changes = []
        for row in rows:
            current = next(
                (
                    item
                    for item in _existing_loads_no_units(model, [row["joint"]])
                    if item["load_pat"] == row["load_pat"] and item["coord_sys"] == row["coord_sys"]
                ),
                None,
            )
            before = {field: current[field] for field in _LOAD_FIELDS} if current else None
            if mode == "replace":
                after = {field: row[field] for field in _LOAD_FIELDS}
            else:
                base = before or dict.fromkeys(_LOAD_FIELDS, 0.0)
                after = {field: base[field] + row[field] for field in _LOAD_FIELDS}
            changes.append(
                {"joint": row["joint"], "load_pat": row["load_pat"], "before": before, "after": after}
            )
        return {"changes": changes}

    return _with_ton_m_c(model, build)


def _try_set_load_force(
    model: Any, joint: str, load_pat: str, forces: list[float], replace: bool, csys: str
) -> bool:
    """One ``PointObj.SetLoadForce`` attempt; ``True`` only on a 0 return code.

    Tolerates the call raising (treated as failure, same as a nonzero
    return code) so the caller can retry with ``replace=False`` either way.
    """
    try:
        ret = model.PointObj.SetLoadForce(joint, load_pat, forces, replace, csys)
    except Exception:
        return False
    try:
        return _ret_code(ret, "PointObj.SetLoadForce") == 0
    except RuntimeError:
        return False


def apply_loads(model: Any, rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """Write loads with ``PointObj.SetLoadForce``, port of cargas_nodales.py's
    current ``_aplicar_cargas_en_nodos_impl`` (superseding the old
    SetLoadForce/AddLoadForce fallback: SAP2000 has no separate
    ``AddLoadForce`` method — "add without replacing" is the same
    ``SetLoadForce`` called with ``replace=False``).

    Units are forced to Ton_m_C for the write and restored after, same as
    the original.

    Before writing anything, every row's joint is checked against
    ``PointObj.GetNameList`` (``_get_name_list``, mirrors
    ``obtener_nombres_nodos_del_modelo``): a joint absent from the
    connected model can't be told apart from any other ``SetLoadForce``
    failure by its return code alone, so those rows are reported up front
    as ``missing_joints`` and each gets the literal failure entry
    ``f"{joint} / {load_pat} (nodo no existe en el modelo)"`` in ``failed``
    without ever calling ``SetLoadForce`` for it. If the name list itself
    can't be read, validation is skipped (same tolerance as the original,
    which logs a warning and proceeds without it) rather than failing the
    whole request.

    For a row whose joint exists, success requires a 0 numeric return code
    (``_ret_code``) from ``SetLoadForce`` with ``replace`` from ``mode``;
    an exception or a nonzero code retries the same call with
    ``replace=False`` — succeeding there is enough. Only when both attempts
    fail does the row's ``f"{joint} / {load_pat}"`` land in ``failed``, and
    the loop moves on to the next row, never raising for a single row.
    Returns ``{"applied": n, "failed": [...], "missing_joints": [...]}``.
    """

    def write() -> dict[str, Any]:
        try:
            joint_names = set(_get_name_list(model.PointObj, "PointObj.GetNameList"))
        except Exception as exc:
            logger.warning(
                "bridge.actions.apply_loads: no se pudo listar los nodos del "
                "modelo para validar antes de aplicar (%s); se sigue sin esa "
                "validación previa",
                exc,
            )
            joint_names = None

        missing_joints: list[str] = []
        if joint_names is not None:
            missing_joints = sorted({str(row["joint"]) for row in rows} - joint_names)
        missing_set = set(missing_joints)

        replace = mode == "replace"
        applied = 0
        failed: list[str] = []
        for row in rows:
            joint = str(row["joint"])
            load_pat = str(row["load_pat"])
            if joint in missing_set:
                failed.append(f"{joint} / {load_pat} (nodo no existe en el modelo)")
                continue
            forces = [row[field] for field in _LOAD_FIELDS]
            csys = row["coord_sys"]
            if _try_set_load_force(model, joint, load_pat, forces, replace, csys):
                applied += 1
            elif _try_set_load_force(model, joint, load_pat, forces, False, csys):
                applied += 1
            else:
                failed.append(f"{joint} / {load_pat}")
        return {"applied": applied, "failed": failed, "missing_joints": missing_joints}

    return _with_ton_m_c(model, write)


class LoadRow(BaseModel):
    joint: str
    load_pat: str
    coord_sys: str = "Global"
    f1: float = 0.0
    f2: float = 0.0
    f3: float = 0.0
    m1: float = 0.0
    m2: float = 0.0
    m3: float = 0.0

    @field_validator("joint")
    @classmethod
    def joint_name(cls, value: str) -> str:
        return _valid_name(value, "joint")

    @field_validator("load_pat")
    @classmethod
    def load_pat_name(cls, value: str) -> str:
        return _valid_name(value, "load pattern")

    @field_validator("coord_sys")
    @classmethod
    def coord_sys_name(cls, value: str) -> str:
        return _valid_name(value, "coordinate system")

    @field_validator("f1", "f2", "f3", "m1", "m2", "m3")
    @classmethod
    def finite_component(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("load components must be finite")
        return value


class LoadsBody(BaseModel):
    rows: list[LoadRow]
    mode: str
    confirmed: bool = False

    @field_validator("rows")
    @classmethod
    def rows_bounds(cls, value: list[LoadRow]) -> list[LoadRow]:
        if not value or len(value) > _MAX_LOAD_ROWS:
            raise ValueError(f"rows must contain 1–{_MAX_LOAD_ROWS} entries")
        return value

    @field_validator("mode")
    @classmethod
    def mode_choice(cls, value: str) -> str:
        if value not in ("replace", "add"):
            raise ValueError("mode must be replace or add")
        return value


class ShellDiagramBody(BaseModel):
    shells: list[str]
    combinacion: str

    @field_validator("shells")
    @classmethod
    def shells_bounds(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("shells must contain 1–500 entries")
        return value


def install_routes(app: Any, session: Any, com_error: type[Exception]) -> None:
    """Install authenticated local-agent routes.  Call after agent middleware."""
    from fastapi import HTTPException

    def call(fn: Callable[[Any], Any]) -> Any:
        try:
            return session.actor.call(lambda: fn(session._model()))
        except SectionExistsError as exc:
            raise HTTPException(409, exc.detail) from exc
        except PartialApplyError as exc:
            raise HTTPException(409, exc.result) from exc
        except com_error as exc:
            # A structured ``.detail`` (e.g. bridge.esbelteces.DesignCodeError,
            # which subclasses RuntimeError so it can also match ``com_error``)
            # becomes the JSON body instead of being flattened to str(exc).
            detail = getattr(exc, "detail", None)
            raise HTTPException(409, detail if detail is not None else str(exc)) from exc
        except ActionValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            # A structured ``.detail`` (SectionExistsError, PartialApplyError,
            # bridge.esbelteces.DesignCodeError, ...) becomes the JSON body of
            # the 409 instead of being flattened to str(exc).
            detail = getattr(exc, "detail", None)
            raise HTTPException(409, detail if detail is not None else str(exc)) from exc

    @app.get("/v1/sap/actions/profiles/catalog")
    def profiles_catalog():
        def read(model):
            catalog = profile_catalog(model)
            # Como el escritorio al conectar: sin materiales se registran los
            # diagnósticos COM crudos (aquí en el log del puente, no en la UI).
            log_profile_diagnostics_if_no_materials(model, catalog["materials"])
            return catalog

        return call(read)

    @app.get("/v1/sap/actions/profiles/selected-frames")
    def profiles_selected_frames():
        return call(lambda model: {"frames": selected_frames(model)})

    @app.get("/v1/sap/actions/profiles/selected-frame-properties")
    def profiles_selected_frame_properties():
        return call(lambda model: {"frames": selected_frame_properties(model)})

    @app.post("/v1/sap/actions/profiles/create/preview")
    def preview_create_section(body: CreateSectionBody):
        return {
            "preview": True,
            "name": body.name,
            "section_type": body.section_type,
            "material": body.material,
            "dimensions_mm": {"depth": body.depth, "width": body.width, "tw": body.tw, "tf": body.tf},
            "will_mutate": False,
        }

    @app.post("/v1/sap/actions/profiles/create/apply")
    def apply_create_section(body: CreateSectionBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to create a section")
        return call(
            lambda model: create_section(
                model,
                body.name,
                body.section_type,
                body.material,
                body.depth,
                body.width,
                body.tw,
                body.tf,
                body.overwrite,
            )
        )

    @app.post("/v1/sap/actions/profiles/section/preview")
    def preview_section(body: SectionBody):
        return {
            "preview": True,
            "section": body.section,
            "frames": list(dict.fromkeys(body.frames)),
            "will_mutate": False,
        }

    @app.post("/v1/sap/actions/profiles/section/apply")
    def apply_section(body: SectionBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to apply a section assignment")
        return call(lambda model: assign_section(model, body.frames, body.section))

    @app.post("/v1/sap/actions/combinations/preview")
    def preview_combinations(body: CombinationsBody):
        return {"preview": True, "names": [item.name for item in body.combinations], "will_mutate": False}

    @app.post("/v1/sap/actions/combinations/apply")
    def apply_combinations(body: CombinationsBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to create combinations")
        payload = [item.model_dump() for item in body.combinations]
        return call(lambda model: create_combinations(model, payload))

    @app.post("/v1/sap/actions/spectrum/preview")
    def preview_spectrum(body: SpectrumBody):
        return {
            "preview": True,
            "name": body.name,
            "point_count": len(body.points),
            "damping": body.damping,
            "will_mutate": False,
        }

    @app.post("/v1/sap/actions/spectrum/apply")
    def apply_spectrum(body: SpectrumBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to create a response-spectrum function")
        points = [point.model_dump() for point in body.points]
        return call(lambda model: create_response_spectrum(model, body.name, points, body.damping))

    @app.get("/v1/sap/actions/foundations/cases")
    def foundations_cases():
        # ``envolventes`` (Diagramas de shells): subconjunto de ``combos`` de
        # tipo Envelope; aditivo, tolerante (``[]`` si no se puede determinar).
        from bridge.foundations_shells import envelope_combos

        def run(model: Any) -> dict[str, list[str]]:
            data = load_cases_and_combos(model)
            return {**data, "envolventes": envelope_combos(model, data["combos"])}

        return call(run)

    @app.get("/v1/sap/actions/foundations/selection")
    def foundations_selection(kind: str):
        if kind not in _SELECTION_TYPES:
            raise HTTPException(422, "kind must be joints, shells or frames")
        return call(lambda model: {"kind": kind, "names": selected_objects(model, kind)})

    @app.get("/v1/sap/actions/foundations/groups")
    def foundations_groups():
        # Misma forma que el snapshot ({id, name, nodes, shells}), que es la que
        # tipa la web (FoundationGroup) y leen los diálogos de grupos.
        return call(lambda model: {"groups": groups_from_model(model)})

    @app.get("/v1/sap/actions/foundations/load-patterns")
    def foundations_load_patterns():
        return call(lambda model: {"load_patterns": load_patterns(model)})

    @app.get("/v1/sap/actions/foundations/loads/existing")
    def foundations_loads_existing(joints: str):
        names = [item.strip() for item in joints.split(",") if item.strip()]
        return call(lambda model: {"rows": existing_loads(model, names)})

    @app.post("/v1/sap/actions/foundations/loads/preview")
    def foundations_loads_preview(body: LoadsBody):
        rows = [row.model_dump() for row in body.rows]
        return call(lambda model: preview_loads(model, rows, body.mode))

    @app.post("/v1/sap/actions/foundations/loads/apply")
    def foundations_loads_apply(body: LoadsBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to apply nodal loads")
        rows = [row.model_dump() for row in body.rows]
        return call(lambda model: apply_loads(model, rows, body.mode))

    @app.post("/v1/sap/actions/foundations/shell-diagram")
    def foundations_shell_diagram(body: ShellDiagramBody):
        """Geometría de malla + presión de suelo de ``body.shells`` para la
        combinación ``body.combinacion``, port de
        ``diagramas_shell.seleccionar_caso_para_output`` +
        ``obtener_geometria_shells`` + ``obtener_resultados_shell(...,
        "soil_pressure", "Pressure", ...)`` — insumo directo del diagrama
        de "Presión de contacto" que dibuja
        `el motor de cálculo del Workbench`.

        Responde::

            {
              "elementos": {"<elemento_malla>": ["<p1>", "<p2>", "<p3>", "<p4>"], ...},
              "joints": {"<punto>": [x, y], ...},
              "resultados": {"<elemento_malla>": {"<punto>": <presion_float>, ...}, ...}
            }

        ``resultados`` está restringido a los elementos de ``elementos``
        (los de los shells pedidos); un elemento sin presión resuelta para
        ``combinacion`` (combo no corrida, etc.) queda ausente de
        ``resultados`` — el llamador (frontend) lo pasa tal cual como
        ``shell.resultados`` al endpoint de exportación."""

        def run(model: Any) -> dict[str, Any]:
            _select_case_for_output(model, body.combinacion)
            elementos, joints, elementos_por_area = shell_geometry(model, body.shells)
            resultados = shell_soil_pressure(model, elementos, elementos_por_area) if elementos else {}
            return {"elementos": elementos, "joints": joints, "resultados": resultados}

        return call(run)

    # Esbelteces (import/extracción de frames, grupos, secciones, K factors).
    # Módulo separado para no sobrecargar este archivo; comparte el mismo
    # envoltorio ``call`` (mismo actor STA y mismo mapeo de errores a HTTP).
    from bridge.esbelteces import install_routes as install_esbelteces_routes

    install_esbelteces_routes(app, call)

    # Combinaciones (matriz de Response Combinations). Módulo separado,
    # mismo envoltorio ``call``.
    from bridge.combinaciones import install_routes as install_combinaciones_routes

    install_combinaciones_routes(app, call)

    # Espectro (port del escritorio NCh2369): funciones RS, load cases,
    # envío de curvas, Ps/Qx/Qy/T* modal. Módulo separado, mismo envoltorio
    # ``call``. Las rutas /v1/sap/actions/spectrum/* de más arriba (creación
    # de función response-spectrum desde el motor de cálculo del Workbench) quedan
    # intactas y son un mecanismo distinto.
    from bridge.espectro import install_routes as install_espectro_routes

    install_espectro_routes(app, call)

    # Diseño de secciones (section cuts de Fundaciones). Módulo separado,
    # mismo envoltorio ``call``.
    from bridge.foundations_cuts import install_routes as install_foundations_cuts_routes

    install_foundations_cuts_routes(app, call)

    # Diagramas de shells (port del escritorio): shell-results,
    # shell-results-multi y pedestal-candidates. Módulo separado, mismo
    # envoltorio ``call``. /foundations/shell-diagram (arriba) queda intacto.
    from bridge.foundations_shells import install_routes as install_foundations_shells_routes

    install_foundations_shells_routes(app, call)

    # Modificación de Fundaciones (crear losa/zapata bajo frames): frames,
    # catalog, preview y apply. Módulo separado, mismo envoltorio ``call``.
    from bridge.modificacion_fundaciones import install_routes as install_modif_fundaciones_routes

    install_modif_fundaciones_routes(app, call)

    from bridge.diagrama import install_routes as install_diagrama_routes

    install_diagrama_routes(app, call)

    from bridge.hormigon import install_routes as install_hormigon_routes

    install_hormigon_routes(app, call)

    from bridge.equilibrio import install_routes as install_equilibrio_routes

    install_equilibrio_routes(app, call)

    from bridge.cad_drwr import install_routes as install_cad_drwr_routes

    install_cad_drwr_routes(app, call)

    from bridge.graph import install_routes as install_graph_routes

    install_graph_routes(app, call)

    # SAP2000 Viewer (port de web_bridge, ver docs/PUENTE_COMPARTIDO.md, B5): rutas
    # /v1/sap/actions/viewer/*. No usa el envoltorio ``call``: su LocalBridge tiene
    # su propio hilo COM con un solo trabajador.
    from bridge.viewer.routes import install_routes as install_viewer_routes

    install_viewer_routes(app)

    # Placa Base (port de sus acciones de lectura y selección, ver docs/PUENTE_COMPARTIDO.md):
    # rutas /v1/sap/actions/placa-base/*, mismo envoltorio ``call``.
    from bridge.placa_base import install_routes as install_placa_base_routes

    install_placa_base_routes(app, call)

    # Zapatas biaxiales (lectura de nodos/reacciones y creación de la zapata):
    # rutas /v1/sap/actions/zapata-biaxial/*, mismo envoltorio ``call``.
    from bridge.zapata_biaxial import install_routes as install_zapata_biaxial_routes

    install_zapata_biaxial_routes(app, call)

    # Nomogramas (vista «Columna y nudos»): lectura de los frames seleccionados:
    # ruta /v1/sap/actions/nomogramas/selected-frames, mismo envoltorio ``call``.
    from bridge.nomogramas import install_routes as install_nomogramas_routes

    install_nomogramas_routes(app, call)

    # Cargas nodales de Fundaciones: ida y vuelta con Excel de escritorio (sin COM):
    # rutas /v1/sap/actions/excel/open-reactions y read-reactions.
    from bridge.excel_reacciones import install_routes as install_excel_reacciones_routes

    install_excel_reacciones_routes(app)

    # Acero AISC 360-22 (fuerzas de barras de SAP2000 para el diseño): rutas
    # /v1/sap/actions/acero-aisc360/*, mismo envoltorio ``call``.
    from bridge.acero_aisc360_fuerzas import install_routes as install_acero_aisc360_fuerzas_routes

    install_acero_aisc360_fuerzas_routes(app, call)

    # Acero AISC 360-22 (enviar perfiles a SAP2000 y asignarlos): rutas
    # /v1/sap/actions/acero-aisc360/enviar/*, mismo envoltorio ``call``.
    from bridge.acero_aisc360_enviar import install_routes as install_acero_aisc360_enviar_routes

    install_acero_aisc360_enviar_routes(app, call)

    from bridge.caesar_cargas import install_routes as install_caesar_cargas_routes

    install_caesar_cargas_routes(app, call)

    # Documentos Word locales (ayudante de la web alojada): rutas /v1/documentos/*.
    # No son SAP: no usan el envoltorio ``call`` (no tocan COM). Ver
    # docs/AYUDANTE_LOCAL_DOCUMENTOS.md.
    from bridge.documentos_locales import install_routes as install_documentos_routes

    install_documentos_routes(app)
