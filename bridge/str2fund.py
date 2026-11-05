"""SAP2000 source/target operations for Str2Fund.

The module is intentionally independent of the web UI.  Plans are validated
before any write, previews are read-only, and every target write is guarded by
an explicit confirmation plus the target path/fingerprint shown in preview.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any

from pydantic import BaseModel, Field

from bridge.actions import _call_get_coord_cartesian, _get_name_list, _ret


class Str2FundError(RuntimeError):
    """An invalid, stale, or unsuccessful Str2Fund SAP operation."""


class Str2FundPartialError(Str2FundError):
    """A target write failed after SAP had accepted one or more changes."""

    def __init__(self, detail: dict[str, Any]):
        super().__init__(str(detail.get("message", "Partial Str2Fund apply")))
        self.detail = detail


_LOAD_FIELDS = ("f1", "f2", "f3", "m1", "m2", "m3")
_UNITS_TON_M_C = 12
_COORD_TOL = 1e-4
_NO_RESULTS_MESSAGE = (
    "El modelo no tiene resultados para los casos pedidos; ejecute el análisis en SAP2000 e intente de nuevo."
)


def _path(model: Any) -> str:
    raw = model.GetModelFilename()
    if isinstance(raw, (tuple, list)):
        strings = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
        numeric = [item for item in (raw[0], raw[-1]) if isinstance(item, int)] if raw else []
        if numeric and any(code != 0 for code in numeric):
            raise Str2FundError(f"GetModelFilename returned {numeric[-1]}")
        return strings[0] if strings else ""
    return str(raw or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(value))))


def _with_ton_m_c(model: Any, operation):
    """Run geometry/load operations in the plan's declared units and restore SAP."""
    previous = model.GetPresentUnits()
    if isinstance(previous, (tuple, list)):
        values = [value for value in previous if isinstance(value, int) and not isinstance(value, bool)]
        if not values:
            raise Str2FundError("Could not identify SAP2000 present units")
        previous = values[0]
    changed = int(previous) != _UNITS_TON_M_C
    if changed:
        _ret(model.SetPresentUnits(_UNITS_TON_M_C), "SetPresentUnits(Ton_m_C)")
    try:
        return operation()
    finally:
        if changed:
            _ret(model.SetPresentUnits(previous), "SetPresentUnits(restore)")


def _result_items(raw: Any, operation: str, expected_outputs: int | None = None) -> list[Any]:
    """Normalize one SAP out-parameter call; require a successful return code."""
    if not isinstance(raw, (tuple, list)):
        raise Str2FundError(f"Unexpected SAP2000 response from {operation}")
    _ret(tuple(raw), operation)
    values = list(raw)
    # Drop one return code at either edge. SAP wrappers most commonly return
    # output arrays followed by ret, but a few place ret first.
    if values and isinstance(values[-1], int) and not isinstance(values[-1], bool):
        values.pop()
    elif values and isinstance(values[0], int) and not isinstance(values[0], bool):
        values.pop(0)
    if expected_outputs is not None and len(values) < expected_outputs:
        raise Str2FundError(f"Unexpected SAP2000 response from {operation}")
    return values


def _com(getter: Any, *args: Any, outs: tuple[Any, ...] = ()) -> Any:
    """Call an OAPI getter, retrying with ByRef output placeholders.

    Typed wrappers return the outputs when they are omitted; late-bound
    (pywin32 dynamic) dispatch needs a placeholder per output parameter.
    """
    try:
        raw = getter(*args)
        if isinstance(raw, (tuple, list)):
            return raw
    except Exception:
        if not outs:
            raise
    return getter(*args, *outs)


def _probe_ok(raw: Any) -> bool:
    """True when an OAPI Get* result carries a zero return code.

    Typed wrappers end with the code, late binding starts with it; two ints at
    both ends can only be the typed layout (count first, code last).
    """
    if not isinstance(raw, (tuple, list)) or not raw:
        return False
    first, last = raw[0], raw[-1]
    first_int = isinstance(first, int) and not isinstance(first, bool)
    last_int = isinstance(last, int) and not isinstance(last, bool)
    if last_int:
        return last == 0
    return first_int and first == 0


def _probe_case_kind(model: Any, name: str) -> str | None:
    """Classify a case by asking each case-type object for its loads.

    A getter on the wrong case type returns a non-zero code (or raises), so the
    first one that answers identifies the type without GetTypeOAPI.
    """
    probes = (
        ("response_spectrum", "ResponseSpectrum", (0, [], [], [], [], [])),
        ("linear_static", "StaticLinear", (0, [], [], [])),
    )
    for kind, member, outs in probes:
        owner = getattr(model.LoadCases, member, None)
        getter = getattr(owner, "GetLoads", None)
        if not callable(getter):
            continue
        try:
            if _probe_ok(_com(getter, name, outs=outs)):
                return kind
        except Exception:
            continue
    return None


def _case_type(model: Any, name: str) -> dict[str, Any]:
    """Return conservative LoadCases type metadata (GetTypeOAPI_1 / GetTypeOAPI / GetType)."""
    getters = [
        getattr(model.LoadCases, method, None) for method in ("GetTypeOAPI_1", "GetTypeOAPI", "GetType")
    ]
    getters = [g for g in getters if callable(g)]
    last_error = "LoadCases.GetTypeOAPI_1 unavailable" if not getters else "unexpected case metadata"
    candidates: list[int] = []
    for getter in getters:
        try:
            raw = _com(getter, name, outs=(0, 0))
            if not isinstance(raw, (tuple, list)):
                continue
            ints = [int(v) for v in raw if isinstance(v, int) and not isinstance(v, bool)]
            if not ints:
                continue
            # (CaseType, SubType, ret) with typed wrappers, (ret, CaseType, SubType)
            # with late binding. A case type is never 0, so a leading 0 is the ret.
            if len(ints) >= 3:
                ret, case_type = (ints[0], ints[1]) if ints[0] == 0 else (ints[-1], ints[0])
            elif len(ints) == 2:
                ret, case_type = (ints[0], ints[1]) if ints[0] == 0 else (ints[1], ints[0])
            else:
                continue
            if ret != 0:
                last_error = f"{getter.__name__ if hasattr(getter, '__name__') else 'GetType'} returned {ret}"
                continue
            candidates = [case_type]
            break
        except Exception as exc:
            last_error = str(exc)
    probed = None if candidates else _probe_case_kind(model, name)
    if probed:
        candidates = [1 if probed == "linear_static" else 4]
    if not candidates:
        return {"name": name, "kind": "unknown", "eligible": False, "reason": last_error}
    try:
        # OAPI eLoadCaseType: 1=LinearStatic, 4=ResponseSpectrum. Spectrum
        # results can provide a magnitude while a static case provides its
        # sign; callers that need a particular role use the role flags.
        kind = (
            "linear_static" if candidates and candidates[0] == 1
            else "response_spectrum" if candidates and candidates[0] == 4
            else f"oapi_type_{candidates[0]}" if candidates
            else "unknown"
        )
        eligible_for_sign = kind == "linear_static"
        eligible_for_magnitude = kind in {"linear_static", "response_spectrum"}
        eligible = eligible_for_sign or eligible_for_magnitude
        return {
            "name": name,
            "kind": kind,
            "eligible": eligible,
            "eligible_for_sign": eligible_for_sign,
            "eligible_for_magnitude": eligible_for_magnitude,
            "reason": None if eligible else "only linear static and response-spectrum cases are supported",
        }
    except Exception as exc:
        return {"name": name, "kind": "unknown", "eligible": False, "reason": str(exc)}


def _frame_endpoints(model: Any, frame: str) -> tuple[str, str]:
    raw = _com(model.FrameObj.GetPoints, frame, outs=("", ""))
    values = _result_items(raw, f"FrameObj.GetPoints({frame!r})")
    names = [str(value) for value in values if isinstance(value, str) and value]
    if len(names) < 2:
        raise Str2FundError(f"SAP2000 returned no end joints for frame {frame!r}")
    return names[0], names[1]


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_section_values(values: list[Any], n_dims: int) -> tuple[str, list[float]] | None:
    """Parse PropFrame.Get* output (ret already stripped).

    Real OAPI order is FileName, MatProp, <dims...>, Color, Notes, GUID. When the
    first two items are strings and the next ones are numbers we use the
    positional MatProp (FileName may be empty or not). Otherwise fall back to the
    legacy heuristic: first non-empty string and first positive numbers.
    """
    if (
        len(values) >= 2 + n_dims
        and isinstance(values[0], str)
        and isinstance(values[1], str)
        and all(_is_num(v) for v in values[2 : 2 + n_dims])
    ):
        material = values[1].strip()
        dims = [float(v) for v in values[2 : 2 + n_dims]]
    else:
        material = next((str(v).strip() for v in values if isinstance(v, str) and v.strip()), "")
        dims = [float(v) for v in values if _is_num(v) and math.isfinite(float(v)) and float(v) > 0][:n_dims]
    if not material or len(dims) < n_dims or any(not math.isfinite(d) or d <= 0 for d in dims):
        return None
    return material, dims


_SECTION_OUTS = {
    "GetRectangle": ("", "", 0.0, 0.0, 0, "", ""),
    "GetCircle": ("", "", 0.0, 0, "", ""),
    "GetTube": ("", "", 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetPipe": ("", "", 0.0, 0.0, 0, "", ""),
    "GetISection": ("", "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetChannel": ("", "", 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetTee": ("", "", 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetAngle": ("", "", 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetDblAngle": ("", "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, "", ""),
    "GetGeneral": ("", "") + (0.0,) * 14 + (0, "", ""),
}


def _section_dims(model: Any, section: str) -> tuple[str, float, float] | None:
    """Return (material, t3, t2) for rectangular, circular, tube or pipe sections."""
    # (method, number of leading dimensions, mapping to (t3, t2))
    kinds = (
        ("GetRectangle", 2, lambda d: (d[0], d[1])),
        ("GetCircle", 1, lambda d: (d[0], d[0])),
        ("GetTube", 2, lambda d: (d[0], d[1])),
        ("GetPipe", 1, lambda d: (d[0], d[0])),
        # Steel/general shapes: first two dimensions are always depth (t3) and width (t2).
        ("GetISection", 2, lambda d: (d[0], d[1])),
        ("GetChannel", 2, lambda d: (d[0], d[1])),
        ("GetTee", 2, lambda d: (d[0], d[1])),
        ("GetAngle", 2, lambda d: (d[0], d[1])),
        ("GetDblAngle", 2, lambda d: (d[0], d[1])),
        ("GetGeneral", 2, lambda d: (d[0], d[1])),
    )
    for method, n_dims, mapper in kinds:
        getter = getattr(model.PropFrame, method, None)
        if not callable(getter):
            continue
        try:
            values = _result_items(_com(getter, section, outs=_SECTION_OUTS[method]), f"PropFrame.{method}({section!r})")
            parsed = _parse_section_values(values, n_dims)
        except Exception:
            continue
        if parsed is not None:
            material, dims = parsed
            t3, t2 = mapper(dims)
            return material, t3, t2
    return None


def _rect_section(model: Any, section: str) -> tuple[str, float, float] | None:
    getter = getattr(model.PropFrame, "GetRectangle", None)
    if not callable(getter):
        return None
    try:
        values = _result_items(_com(getter, section, outs=_SECTION_OUTS["GetRectangle"]), f"PropFrame.GetRectangle({section!r})")
        parsed = _parse_section_values(values, 2)
    except Exception:
        return None
    if parsed is None:
        return None
    return parsed[0], parsed[1][0], parsed[1][1]


def _frame_rotation(model: Any, frame: str) -> float:
    getter = getattr(model.FrameObj, "GetLocalAxes", None)
    if not callable(getter):
        return 0.0
    try:
        values = _result_items(_com(getter, frame, outs=(0.0, 0.0, False)), f"FrameObj.GetLocalAxes({frame!r})")
        numbers = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        return numbers[0] if numbers and math.isfinite(numbers[0]) else 0.0
    except Exception:
        return 0.0


def _is_restrained(model: Any, name: str) -> bool | None:
    """True if any translation is restrained, False if free, None if unreadable."""
    getter = getattr(model.PointObj, "GetRestraint", None)
    if not callable(getter):
        return None
    try:
        values = _result_items(_com(getter, name, outs=([False] * 6,)), f"PointObj.GetRestraint({name!r})")
    except Exception:
        return None
    flags: list[Any] = []
    if values and isinstance(values[0], (tuple, list)):
        flags = list(values[0])
    else:
        flags = [v for v in values if isinstance(v, bool)]
    if len(flags) < 3:
        return None
    return any(bool(v) for v in flags[:3])


def _read_restraints(model: Any, names: list[str]) -> dict[str, Any]:
    """Translation-restraint flag per node (True/False/None), plus ``__source__``.

    Order: the viewer's batch DatabaseTables read, then its per-joint
    GetRestraint reader (both already proven against a real SAP2000), then the
    local reader as a last resort.
    """
    out: dict[str, Any] = {name: None for name in names}
    try:
        from bridge.viewer.sap2000.reader_geometry import (
            _joint_restraint_data,
            _joint_restraints_from_database,
        )
    except Exception:
        _joint_restraint_data = _joint_restraints_from_database = None  # type: ignore[assignment]
    if _joint_restraints_from_database is not None:
        try:
            batch = _joint_restraints_from_database(model, [])
        except Exception:
            batch = None
        if batch:
            for name in names:
                flags = batch.get(name)
                out[name] = any(flags[:3]) if flags else False
            out["__source__"] = "database"
            return out
    source = None
    for name in names:
        flags = None
        if _joint_restraint_data is not None:
            try:
                flags = _joint_restraint_data(model.PointObj, name)
            except Exception:
                flags = None
        if flags:
            out[name] = any(flags[:3])
            source = "getrestraint"
        else:
            out[name] = _is_restrained(model, name)
            if out[name] is not None:
                source = source or "getrestraint_local"
    out["__source__"] = source
    return out


def _support_catalog(
    model: Any,
    names: list[str],
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Supports = restrained nodes that are the lower end of a vertical frame.

    If PointObj.GetRestraint is unavailable on every node, fall back to the
    purely geometric criterion. ``diagnostics`` (optional) is filled with the
    mode and the reason each non-selected node was discarded.
    """
    frames = _get_name_list(model.FrameObj, "FrameObj.GetNameList")
    coords: dict[str, tuple[float, float, float]] = {}
    coord_errors: list[str] = []
    for name in list(names):
        try:
            coords[name] = tuple(float(v) for v in _call_get_coord_cartesian(model, name))
        except Exception as exc:
            coord_errors.append(f"{name}: {exc}")

    restraint = _read_restraints(model, names)
    use_restraint = any(value is True for value in restraint.values())
    if diagnostics is not None:
        diagnostics["restraint_source"] = restraint.pop("__source__", None)

    def candidate(name: str) -> bool:
        return restraint[name] is True if use_restraint else True

    # lower joint -> list of per-frame outcomes
    options: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, list[str]] = {}
    vertical_frames = 0
    for frame in frames:
        try:
            i_joint, j_joint = _frame_endpoints(model, frame)
            ends = []
            for joint in (i_joint, j_joint):
                if joint not in coords:
                    coords[joint] = tuple(float(v) for v in _call_get_coord_cartesian(model, joint))
                ends.append(coords[joint])
            i_coord, j_coord = ends
            dz = abs(i_coord[2] - j_coord[2])
            dxy = math.hypot(i_coord[0] - j_coord[0], i_coord[1] - j_coord[1])
            if dz <= _COORD_TOL or dxy > max(_COORD_TOL, 1e-3 * dz):
                continue
            vertical_frames += 1
            support = i_joint if i_coord[2] < j_coord[2] else j_joint
            if support not in coords or not candidate(support) or support not in restraint:
                continue
            raw = _com(model.FrameObj.GetSection, frame, outs=("", ""))
            values = _result_items(raw, f"FrameObj.GetSection({frame!r})")
            section = next((str(value) for value in values if isinstance(value, str) and value), "")
            if not section:
                failures.setdefault(support, []).append(f"{frame}: sin sección")
                continue
            dims = _section_dims(model, section)
            if dims is None:
                failures.setdefault(support, []).append(f"{frame}: sección '{section}' no soportada")
                continue
            material, t3, t2 = dims
            options.setdefault(support, []).append(
                {"frame": frame, "section": section, "material": material, "t3": t3, "t2": t2, "area": t3 * t2}
            )
        except Exception as exc:
            # Unknown failure: record it instead of hiding it.
            failures.setdefault("*", []).append(f"{frame}: {exc}")

    found: dict[str, dict[str, Any]] = {}
    for support, opts in options.items():
        best = max(opts, key=lambda o: (o["area"], o["frame"]))
        rotation = _frame_rotation(model, best["frame"])
        theta = math.radians(rotation)
        t3, t2 = best["t3"], best["t2"]
        # For a vertical frame SAP local 2 is the default horizontal X axis;
        # return the real global bounding footprint after rotation.
        found[support] = {
            "joint": support,
            "x": coords[support][0],
            "y": coords[support][1],
            "z": coords[support][2],
            "column": {
                "width_x": math.hypot(t2 * math.cos(theta), t3 * math.sin(theta)),
                "width_y": math.hypot(t2 * math.sin(theta), t3 * math.cos(theta)),
                "rotation_deg": rotation,
                "section": best["section"],
                "material": best["material"],
            },
        }

    if diagnostics is not None:
        excluded: list[dict[str, Any]] = []
        for name in names:
            if name in found:
                continue
            if use_restraint and restraint[name] is None:
                excluded.append({"joint": name, "reason": "restraint_unreadable"})
            elif use_restraint and not restraint[name]:
                continue  # free node: not a support candidate, not noise
            elif name in failures:
                excluded.append({"joint": name, "reason": "unsupported_section", "detail": "; ".join(failures[name])})
            else:
                excluded.append({"joint": name, "reason": "no_vertical_frame"})
        diagnostics["mode"] = "restraint" if use_restraint else "geometric"
        diagnostics["restrained_nodes"] = sum(1 for v in restraint.values() if v) if use_restraint else None
        diagnostics["excluded"] = excluded
        diagnostics["counts"] = {
            "nodes": len(names),
            "frames": len(frames),
            "vertical_frames": vertical_frames,
            "supports": len(found),
        }
        diagnostics["coord_errors"] = coord_errors[:5]
        diagnostics["frame_errors"] = failures.get("*", [])
    return [found[name] for name in names if name in found]


def _parse_selection_flag(raw: Any, description: str) -> bool | None:
    """Interpret a Get*SelectedForOutput response.

    Returns the selection flag, or None when SAP reports a nonzero code (or no
    readable flag), meaning that selection is unknown.  Accepts ``(sel, ret)``,
    ``(ret, sel)``, ``(ret,)``, a bare integer code and a bare bool.  Anything
    that is not a tuple/list/int/bool is malformed and fails closed.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return None
    if not isinstance(raw, (tuple, list)):
        raise Str2FundError(f"Unexpected SAP2000 response from {description}")
    items = list(raw)
    if not items or any(not isinstance(item, (bool, int)) for item in items):
        raise Str2FundError(f"Unexpected SAP2000 response from {description}")
    bools = [item for item in items if isinstance(item, bool)]
    if bools:
        codes = [item for item in items if not isinstance(item, bool)]
        code = codes[-1] if codes else 0
        flag: int = bools[0]
    else:
        if len(items) == 1:
            return None
        flag, code = items[0], items[-1]
    if code != 0:
        return None
    if flag not in (0, 1):
        raise Str2FundError(f"Unexpected selection flag from {description}")
    return bool(flag)


def _capture_output_selection(model: Any) -> tuple[list[str], list[str], list[str]]:
    """Capture the current output selection.

    Returns ``(cases, combos, unknown)``.  Names whose selection SAP could not
    report (nonzero code or COM error for that name) go to ``unknown`` instead of
    aborting: the capture is only a convenience to restore the user's selection.
    A malformed response or a missing method still fails closed.
    """
    cases = _get_name_list(model.LoadCases, "LoadCases.GetNameList")
    combos = _get_name_list(model.RespCombo, "RespCombo.GetNameList")
    setup = model.Results.Setup
    unknown: list[str] = []

    def selected(method_name: str, name: str) -> bool:
        method = getattr(setup, method_name, None)
        if not callable(method):
            raise Str2FundError(f"SAP2000 {method_name} is required to preserve output selection")
        try:
            raw = method(name)
        except Exception:
            flag = None
        else:
            flag = _parse_selection_flag(raw, f"{method_name}({name!r})")
        if flag is None:
            if name not in unknown:
                unknown.append(name)
            return False
        return flag

    return (
        [name for name in cases if selected("GetCaseSelectedForOutput", name)],
        [name for name in combos if selected("GetComboSelectedForOutput", name)],
        unknown,
    )


def _selection_warning(unknown: list[str]) -> str:
    shown = ", ".join(unknown[:10])
    if len(unknown) > 10:
        shown += f" y {len(unknown) - 10} más"
    return (
        f"No se pudo leer la selección de salida de: {shown}. Tras la lectura se restauró lo que sí "
        "se pudo leer; revise la selección de salida en SAP2000."
    )


def _set_selection(model: Any, cases: list[str], combos: list[str]) -> None:
    setup = model.Results.Setup
    _ret(setup.DeselectAllCasesAndCombosForOutput(), "Results.Setup.DeselectAllCasesAndCombosForOutput")
    for name in cases:
        _ret(setup.SetCaseSelectedForOutput(name, True), f"SetCaseSelectedForOutput({name!r})")
    for name in combos:
        _ret(setup.SetComboSelectedForOutput(name, True), f"SetComboSelectedForOutput({name!r})")


_MATERIAL_TYPES = {1: "steel", 2: "concrete", 3: "nodesign", 4: "aluminum", 5: "coldformed", 6: "rebar", 7: "tendon", 8: "masonry"}


def _split_outputs(raw: Any, operation: str) -> list[Any]:
    """Outputs of an OAPI call whose last output is not an int.

    Typed wrappers end with the return code, late binding starts with it. The
    code is read from the end when that is an int, else from the start.
    """
    if not isinstance(raw, (tuple, list)) or not raw:
        raise Str2FundError(f"Unexpected SAP2000 response from {operation}")
    items = list(raw)

    def is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    # Ret-first (late binding) or ret-last (typed). With ints at both ends
    # (e.g. [0, type]) the zero is the return code.
    if is_int(items[-1]) and is_int(items[0]):
        ret_last = items[-1] == 0 and items[0] != 0
    else:
        ret_last = is_int(items[-1])
    ret = items[-1] if ret_last else items[0]
    if ret != 0:
        raise Str2FundError(f"SAP2000 returned {ret} from {operation}")
    return items[:-1] if ret_last else items[1:]


def _material_definition(model: Any, name: str) -> dict[str, Any] | None:
    """Properties needed to recreate a material in another model (Ton_m_C)."""
    pm = model.PropMaterial
    try:
        out = _split_outputs(_com(pm.GetMaterial, name, outs=(0, 0, "", "")), f"PropMaterial.GetMaterial({name!r})")
        mat_type = int(out[0])
        definition: dict[str, Any] = {"name": name, "type": mat_type, "kind": _MATERIAL_TYPES.get(mat_type, "other")}
        iso = [float(v) for v in _split_outputs(_com(pm.GetMPIsotropic, name, outs=(0.0, 0.0, 0.0, 0.0)), "PropMaterial.GetMPIsotropic")]
        definition.update({"E": iso[0], "U": iso[1], "A": iso[2]})
    except Exception:
        return None
    try:
        weight = _split_outputs(_com(pm.GetWeightAndMass, name, outs=(0.0, 0.0)), "PropMaterial.GetWeightAndMass")
        definition["weight_per_volume"] = float(weight[0])
    except Exception:
        pass
    design = {2: ("GetOConcrete_1", 11), 1: ("GetOSteel_1", 11)}.get(mat_type)
    if design:
        try:
            method, count = design
            outs = _split_outputs(_com(getattr(pm, method), name, outs=(0.0,) * count), f"PropMaterial.{method}")
            definition["design"] = [v if isinstance(v, bool) else (int(v) if isinstance(v, int) else float(v)) for v in outs]
        except Exception:
            pass
    return definition


def _source_materials(model: Any) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    try:
        names = _get_name_list(model.PropMaterial, "PropMaterial.GetNameList")
    except Exception:
        return found
    for name in names:
        definition = _material_definition(model, name)
        if definition:
            found[name] = definition
    return found


def _create_materials_ton_m_c(model: Any, materials: list[dict[str, Any]]) -> dict[str, Any]:
    existing = {n.casefold() for n in _get_name_list(model.PropMaterial, "PropMaterial.GetNameList")}
    created: list[str] = []
    failed: list[dict[str, str]] = []
    pm = model.PropMaterial
    for item in materials:
        name = str(item.get("name") or "")
        if not name or name.casefold() in existing:
            failed.append({"name": name, "reason": "empty name or already exists in target"})
            continue
        try:
            _ret(pm.SetMaterial(name, int(item["type"])), f"PropMaterial.SetMaterial({name!r})")
            _ret(pm.SetMPIsotropic(name, float(item["E"]), float(item["U"]), float(item["A"])), "PropMaterial.SetMPIsotropic")
            if "weight_per_volume" in item:
                _ret(pm.SetWeightAndMass(name, 1, float(item["weight_per_volume"])), "PropMaterial.SetWeightAndMass")
            design = item.get("design")
            if design and int(item["type"]) in (1, 2):
                method = "SetOConcrete_1" if int(item["type"]) == 2 else "SetOSteel_1"
                _ret(getattr(pm, method)(name, *design), f"PropMaterial.{method}")
            created.append(name)
        except Exception as exc:
            failed.append({"name": name, "reason": str(exc)})
    now = {n.casefold() for n in _get_name_list(pm, "PropMaterial.GetNameList")}
    verified = all(name.casefold() in now for name in created)
    return {"created": created, "failed": failed, "verified": verified and not failed}


def create_materials(
    model: Any, materials: list[dict[str, Any]], confirmed: bool, expected_target_path: str, source_path: str | None = None
) -> dict[str, Any]:
    if not confirmed:
        raise Str2FundError("Material creation requires explicit confirmation")
    if not materials:
        raise Str2FundError("No materials to create")
    path = _path(model)
    if not path or _norm_path(path) != _norm_path(expected_target_path):
        raise Str2FundError("The active SAP2000 model does not match expected_target_path")
    if source_path and _norm_path(source_path) == _norm_path(path):
        raise Str2FundError("The source model is currently active; open the target model first")
    result = _with_ton_m_c(model, lambda: _create_materials_ton_m_c(model, materials))
    return {"target": {"path": path}, **result}


def _case_reactions(model: Any, supports: list[str], cases: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for joint in supports:
        raw = model.Results.JointReact(joint, 0)
        if not isinstance(raw, (tuple, list)):
            raise Str2FundError(f"Unexpected SAP2000 Results.JointReact response for {joint!r}")
        # OAPI shape: count,obj,elm,case,step_type,step_num,F1..M3,ret.
        try:
            _ret(tuple(raw), f"Results.JointReact({joint!r})")
        except RuntimeError as exc:
            raise Str2FundError(_NO_RESULTS_MESSAGE) from exc
        values = _result_items(raw, f"Results.JointReact({joint!r})")
        if len(values) < 12:
            raise Str2FundError(f"Incomplete Results.JointReact response for {joint!r}")
        try:
            count = int(values[0])
            objects, output_cases, step_types, step_nums = values[1], values[3], values[4], values[5]
            components = values[6:12]
            vectors = [objects, output_cases, step_types, step_nums, *components]
            if count < 0 or any(len(vector) != count for vector in vectors):
                raise ValueError("inconsistent result arrays")
            for index in range(count):
                case = str(output_cases[index])
                if case not in cases:
                    continue
                row = {
                    "joint": str(objects[index]),
                    "case": case,
                    "step_type": str(step_types[index]),
                    "step_num": float(step_nums[index]),
                }
                row.update(dict(zip(_LOAD_FIELDS, (float(vector[index]) for vector in components))))
                if not all(math.isfinite(row[key]) for key in _LOAD_FIELDS):
                    raise ValueError("non-finite reaction")
                rows.append(row)
        except (TypeError, ValueError, IndexError) as exc:
            raise Str2FundError(f"Invalid Results.JointReact response for {joint!r}: {exc}") from exc
    if not rows:
        # Nothing at all came back for the requested cases: the model has not
        # been analyzed (or its results were deleted).
        raise Str2FundError(_NO_RESULTS_MESSAGE)
    return rows


def _support_notes(diag: dict[str, Any], found: bool) -> list[str]:
    notes: list[str] = []
    if not found:
        c = diag.get("counts") or {}
        notes.append(
            "Diagnóstico de apoyos: "
            f"{c.get('nodes', '?')} nudos, {c.get('frames', '?')} frames, "
            f"{c.get('vertical_frames', '?')} verticales, "
            f"restricciones leídas vía {diag.get('restraint_source') or 'ninguna'}, "
            f"{diag.get('restrained_nodes') if diag.get('restrained_nodes') is not None else 'sin dato de'} nudos restringidos, "
            f"modo {diag.get('mode', '?')}."
        )
        if diag.get("coord_errors"):
            notes.append("Errores leyendo coordenadas: " + "; ".join(diag["coord_errors"]))
        if diag.get("frame_errors"):
            notes.append("Errores leyendo frames: " + "; ".join(diag["frame_errors"]))
    if diag.get("mode") != "restraint":
        # Geometric fallback is reported in support_diagnostics["mode"]; warnings
        # stay empty so models without GetRestraint behave as before.
        return notes
    excluded = diag.get("excluded") or []
    labels = {
        "no_vertical_frame": "sin columna vertical que arranque en el nudo",
        "unsupported_section": "sección no soportada",
        "restraint_unreadable": "restricción ilegible",
    }
    for reason, label in labels.items():
        joints = [str(e["joint"]) for e in excluded if e.get("reason") == reason]
        if joints:
            details = {str(e["joint"]): e.get("detail") for e in excluded if e.get("reason") == reason}
            shown = ", ".join(
                f"{j} [{details[j]}]" if details.get(j) else j for j in joints[:10]
            ) + (f" (+{len(joints) - 10})" if len(joints) > 10 else "")
            notes.append(f"Nudos restringidos descartados como apoyo ({label}): {shown}.")
    if not diag.get("restrained_nodes"):
        notes.append("El modelo no tiene nudos con restricciones de traslación.")
    return notes


def extract_source(model: Any, supports: list[str], cases: list[str]) -> dict[str, Any]:
    """Capture selected support geometry and reactions without leaving SAP altered."""
    path = _path(model)
    if not path:
        raise Str2FundError("Save the source SAP2000 model before extracting Str2Fund data")
    previous_units = model.GetPresentUnits()
    _ret(model.SetPresentUnits(_UNITS_TON_M_C), "SetPresentUnits(Ton_m_C)")
    previous_cases: list[str] = []
    previous_combos: list[str] = []
    previous_unknown: list[str] = []
    selection_captured = False
    try:
        previous_cases, previous_combos, previous_unknown = _capture_output_selection(model)
        selection_captured = True
        all_nodes = _get_name_list(model.PointObj, "PointObj.GetNameList")
        support_diag: dict[str, Any] = {}
        catalog = _support_catalog(model, all_nodes, support_diag)
        by_name = {item["joint"]: item for item in catalog}
        requested_supports = supports or [item["joint"] for item in catalog]
        missing_supports = sorted(set(requested_supports) - set(by_name))
        if missing_supports:
            raise Str2FundError(f"Unsupported or unknown column supports: {', '.join(missing_supports)}")

        all_cases = _get_name_list(model.LoadCases, "LoadCases.GetNameList")
        metadata = [_case_type(model, name) for name in all_cases]
        case_map = {item["name"]: item for item in metadata}
        requested_cases = cases or []
        missing_cases = sorted(set(requested_cases) - set(case_map))
        if missing_cases:
            raise Str2FundError(f"Unknown load cases: {', '.join(missing_cases)}")
        ineligible = [name for name in requested_cases if not case_map[name]["eligible"]]
        if ineligible:
            reasons = "; ".join(f"{name}: {case_map[name]['reason']}" for name in ineligible)
            raise Str2FundError(f"Unsupported load cases cannot be extracted: {reasons}")

        reactions: list[dict[str, Any]] = []
        if requested_supports and requested_cases:
            _set_selection(model, requested_cases, [])
            reactions = _case_reactions(model, requested_supports, requested_cases)
        version = None
        try:
            raw_version = model.GetVersion()
            if isinstance(raw_version, (tuple, list)):
                version = next((str(v) for v in raw_version if isinstance(v, str) and v.strip()), None)
            elif raw_version:
                version = str(raw_version)
        except Exception:
            pass
        source = {
            "model": {"path": path, "units": "Ton_m_C", "version": version},
            "supports": [by_name[name] for name in requested_supports],
            "cases": [case_map[name] for name in requested_cases] if requested_cases else metadata,
            "reactions": reactions,
            "available_supports": catalog,
            "excluded_supports": sorted(set(all_nodes) - set(by_name)),
            "materials": _source_materials(model),
        }
        source["model"]["hash"] = _stable_hash(source)
        result: dict[str, Any] = {"source_snapshot": source}
        if previous_unknown:
            # Outside source_snapshot so the stable hash does not change.
            result["warnings"] = [_selection_warning(previous_unknown)]
        # Diagnostics live outside source_snapshot so the stable hash is unchanged.
        result["support_diagnostics"] = support_diag
        notes = _support_notes(support_diag, bool(catalog))
        if notes:
            result.setdefault("warnings", []).extend(notes)
        return result
    finally:
        restore_error = None
        if selection_captured:
            try:
                _set_selection(model, previous_cases, previous_combos)
            except Exception as exc:
                restore_error = exc
        try:
            _ret(model.SetPresentUnits(previous_units), "SetPresentUnits(restore)")
        except Exception as exc:
            restore_error = restore_error or exc
        if restore_error:
            raise Str2FundError(f"Could not restore SAP2000 units/output selection: {restore_error}") from restore_error


def _plan_validate(plan: dict[str, Any]) -> list[str]:
    if plan.get("schema_version") != "str2fund_plan_v1":
        raise Str2FundError("Unsupported Str2Fund plan schema_version")
    for key in ("source", "points", "frames", "areas", "patterns", "loads", "manifest"):
        if key not in plan:
            raise Str2FundError(f"Str2Fund plan is missing {key}")
    source_path = str(plan["source"].get("path", ""))
    if not source_path:
        raise Str2FundError("Plan source.path is required")
    if plan["source"].get("units") != "Ton_m_C":
        raise Str2FundError("Str2Fund source units must be Ton_m_C")
    names: list[str] = []
    for item in plan["points"]:
        name = str(item.get("name", ""))
        if not name:
            raise Str2FundError("Every plan point needs a name")
        names.append(name)
        for axis in ("x", "y", "z"):
            if not math.isfinite(float(item[axis])):
                raise Str2FundError(f"Point {name!r} has a non-finite {axis} coordinate")
    point_names = {str(item["name"]) for item in plan["points"]}
    frame_names: list[str] = []
    for frame in plan["frames"]:
        frame_names.append(str(frame["name"]))
        if str(frame.get("i")) not in point_names or str(frame.get("j")) not in point_names:
            raise Str2FundError(f"Frame {frame.get('name')!r} references an undefined point")
        if frame.get("i") == frame.get("j") or not frame.get("section"):
            raise Str2FundError(f"Frame {frame.get('name')!r} has invalid connectivity or section")
    area_names: list[str] = []
    for area in plan["areas"]:
        area_names.append(str(area["name"]))
        vertices = area.get("points", [])
        if len(vertices) != 4 or any(point not in point_names for point in vertices):
            raise Str2FundError(f"Area {area.get('name')!r} must reference four plan points")
        if not area.get("section") or not math.isfinite(float(area.get("thickness", 0))) or float(area["thickness"]) <= 0:
            raise Str2FundError(f"Area {area.get('name')!r} has invalid shell property data")
    pattern_names = [str(item.get("name", "")) for item in plan["patterns"]]
    if not all(pattern_names) or len(set(pattern_names)) != len(pattern_names):
        raise Str2FundError("Load pattern names must be non-empty and unique")
    for pattern in plan["patterns"]:
        if "self_weight" in pattern and "selfwt" in pattern and float(pattern["self_weight"]) != float(pattern["selfwt"]):
            raise Str2FundError(f"Load pattern {pattern['name']!r} has contradictory self-weight values")
    for load in plan["loads"]:
        if str(load.get("joint")) not in point_names or str(load.get("pattern")) not in pattern_names:
            raise Str2FundError("A nodal load references an undefined point or pattern")
        if not all(math.isfinite(float(load.get(field, 0))) for field in _LOAD_FIELDS):
            raise Str2FundError("A nodal load contains a non-finite force or moment")
    load_keys = [(str(item["joint"]), str(item["pattern"])) for item in plan["loads"]]
    if len(set(load_keys)) != len(load_keys):
        raise Str2FundError("Each joint/load-pattern pair may appear only once in the plan")
    frame_set, area_set = set(frame_names), set(area_names)
    for group in plan.get("groups", []):
        gname = str(group.get("name", ""))
        if not gname or gname.casefold() == "all":
            raise Str2FundError("Every plan group needs a name other than ALL")
        if any(str(x) not in point_names for x in group.get("points", [])) or any(str(x) not in frame_set for x in group.get("frames", [])) or any(str(x) not in area_set for x in group.get("areas", [])):
            raise Str2FundError(f"Group {gname!r} references an undefined object")
    group_names = [str(g["name"]).casefold() for g in plan.get("groups", [])]
    if len(set(group_names)) != len(group_names):
        raise Str2FundError("Duplicate group names in Str2Fund plan")
    all_names = names + frame_names + area_names + pattern_names
    if len({name.casefold() for name in all_names}) != len(all_names):
        # SAP objects use independent namespaces, so duplicates between object
        # types are legal. Within each kind, duplicates are not.
        for label, values in (("points", names), ("frames", frame_names), ("areas", area_names), ("patterns", pattern_names)):
            if len({name.casefold() for name in values}) != len(values):
                raise Str2FundError(f"Duplicate {label} names in Str2Fund plan")
    return [source_path]


def _target_fingerprint(model: Any) -> tuple[dict[str, Any], str]:
    path = _path(model)
    if not path:
        raise Str2FundError("Save the target SAP2000 model before preview/apply")
    point_names = _get_name_list(model.PointObj, "PointObj.GetNameList")
    frames = _get_name_list(model.FrameObj, "FrameObj.GetNameList")
    areas = _get_name_list(model.AreaObj, "AreaObj.GetNameList")
    patterns = _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")
    # Coordinates catch target edits made after preview, even when names stay
    # unchanged. This read-only fingerprint is deterministic and low cost for
    # the intended foundation model sizes.
    coords = []
    for name in point_names:
        xyz = _call_get_coord_cartesian(model, name)
        coords.append([name, *[round(float(value), 9) for value in xyz]])
    identity = {
        "path": path,
        "points": sorted(coords),
        "frames": sorted(frames),
        "areas": sorted(areas),
        "patterns": sorted(patterns),
    }
    return identity, _stable_hash(identity)


def _preflight(model: Any, plan: dict[str, Any], expected_target_path: str | None = None) -> dict[str, Any]:
    source_path, = _plan_validate(plan)
    identity, fingerprint = _target_fingerprint(model)
    if _norm_path(source_path) == _norm_path(identity["path"]):
        raise Str2FundError("The source model is currently active; open and save a separate target model first")
    if expected_target_path and _norm_path(expected_target_path) != _norm_path(identity["path"]):
        raise Str2FundError("The active SAP2000 model does not match expected_target_path")
    existing = {
        "points": {n.casefold(): n for n in _get_name_list(model.PointObj, "PointObj.GetNameList")},
        "frames": {n.casefold(): n for n in _get_name_list(model.FrameObj, "FrameObj.GetNameList")},
        "areas": {n.casefold(): n for n in _get_name_list(model.AreaObj, "AreaObj.GetNameList")},
        "patterns": {n.casefold(): n for n in _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")},
        "sections": {n.casefold(): n for n in _get_name_list(model.PropFrame, "PropFrame.GetNameList")},
        "area_sections": {n.casefold(): n for n in _get_name_list(model.PropArea, "PropArea.GetNameList")},
        "materials": {n.casefold(): n for n in _get_name_list(model.PropMaterial, "PropMaterial.GetNameList")},
    }
    conflict_details = []
    cases_existing = {n.casefold(): n for n in _get_name_list(model.LoadCases, "LoadCases.GetNameList")}
    try:
        combos_existing = {n.casefold(): n for n in _get_name_list(model.RespCombo, "RespCombo.GetNameList")}
    except Exception:
        combos_existing = {}
    groups_existing = {n.casefold(): n for n in _get_name_list(model.GroupDef, "GroupDef.GetNameList")}

    # The target's default DEAD may be renamed to the foundation weight pattern.
    setup = plan.get("target_setup") or {}
    rename_to = str(setup.get("rename_default_dead") or "")
    will_rename = False
    if rename_to and "dead" in existing["patterns"]:
        dead = existing["patterns"]["dead"]
        if rename_to.casefold() in existing["patterns"] or rename_to.casefold() in cases_existing:
            conflict_details.append({"kind": "patterns", "name": rename_to, "reason": f"already exists, so the default {dead!r} cannot be renamed to it"})
        elif not _default_dead_is_untouched(model, dead):
            conflict_details.append({"kind": "patterns", "name": dead, "reason": "is not the default weight pattern (type Dead, self weight 1); rename or remove it"})
        else:
            will_rename = True

    for kind, objects in (("points", plan["points"]), ("frames", plan["frames"]), ("areas", plan["areas"])):
        for item in objects:
            name = str(item["name"])
            if name.casefold() in existing[kind]:
                conflict_details.append({"kind": kind, "name": name, "reason": f"already exists as {existing[kind][name.casefold()]!r}"})
    for item in plan["patterns"]:
        name = str(item["name"])
        key = name.casefold()
        if key == "dead" and will_rename:
            continue
        for label, taken in (("load pattern", existing["patterns"]), ("load case", cases_existing), ("combination", combos_existing)):
            if key in taken:
                conflict_details.append({"kind": "patterns", "name": name, "reason": f"already exists as {label} {taken[key]!r}"})
                break
    for group in plan.get("groups", []):
        if str(group["name"]).casefold() in groups_existing:
            conflict_details.append({"kind": "groups", "name": group["name"], "reason": f"already exists as {groups_existing[str(group['name']).casefold()]!r}"})

    for section in _plan_frame_sections(plan):
        if str(section["name"]).casefold() in existing["sections"]:
            difference = _frame_section_difference(model, section)
            if difference:
                conflict_details.append({"kind": "section", "name": section["name"], "reason": difference})
        elif not section.get("material") or str(section["material"]).casefold() not in existing["materials"]:
            conflict_details.append({"kind": "material", "name": section.get("material"), "reason": "missing from target model"})
    for section in _plan_area_sections(plan):
        if str(section["name"]).casefold() in existing["area_sections"]:
            difference = _area_section_difference(model, section)
            if difference:
                conflict_details.append({"kind": "section", "name": section["name"], "reason": difference})
        elif not section.get("material") or str(section["material"]).casefold() not in existing["materials"]:
            conflict_details.append({"kind": "material", "name": section.get("material"), "reason": "missing from target model"})
    if _model_locked(model):
        conflict_details.append({"kind": "model", "name": "destino", "reason": _LOCKED_MESSAGE})
    return {
        "target": identity,
        "fingerprint": fingerprint,
        "conflicts": [f"{item['kind']} {item['name']}: {item['reason']}" for item in conflict_details],
        "conflict_details": conflict_details,
        "creates": {"points": len(plan["points"]), "frames": len(plan["frames"]), "areas": len(plan["areas"]), "groups": len(plan.get("groups", [])), "patterns": len(plan["patterns"]), "loads": len(plan["loads"])},
        "will_rename_default_dead": will_rename,
        "manifest_id": str(plan["manifest"].get("id") or _stable_hash(plan)),
    }


def preview_target(model: Any, plan: dict[str, Any]) -> dict[str, Any]:
    def run():
        result = _preflight(model, plan)
        return {"preview": True, "will_mutate": False, **result}

    return _with_ton_m_c(model, run)


def _created_name(raw: Any, operation: str, requested: str) -> None:
    values = _result_items(raw, operation)
    names = [str(item) for item in values if isinstance(item, str) and item]
    if names and names[0] != requested:
        raise Str2FundError(f"SAP2000 created {names[0]!r} instead of requested name {requested!r}")


def _model_locked(model: Any) -> bool | None:
    """True/False from GetModelIsLocked, None when it cannot be read."""
    getter = getattr(model, "GetModelIsLocked", None)
    if not callable(getter):
        return None
    try:
        raw = getter()
    except Exception:
        return None
    values = list(raw) if isinstance(raw, (tuple, list)) else [raw]
    flags = [v for v in values if isinstance(v, bool)]
    return flags[0] if flags else None


_LOCKED_MESSAGE = (
    "El modelo destino está bloqueado (tiene resultados de análisis): SAP2000 rechaza crear objetos. "
    "Desbloquéelo en SAP2000 (icono del candado) y repita la revisión y la creación."
)


def _add_point(model: Any, point: dict[str, Any]) -> None:
    name = str(point["name"])
    x, y, z = float(point["x"]), float(point["y"]), float(point["z"])
    # CSI signature: (x, y, z, ref Name, UserName, CSys, MergeOff, MergeNumber).
    # Leave the output Name blank and request the stable point label via UserName.
    attempts = (
        (x, y, z, "", name, "Global", True, 0),
        # Fallback that worked on a real SAP2000 when the first form returned 1.
        # MergeOff=False can merge coincident points, but plan nodes never coincide.
        (x, y, z, "", name, "Global", False, 0),
        (x, y, z, "", name),
    )
    errors: list[str] = []
    for args in attempts:
        try:
            _created_name(model.PointObj.AddCartesian(*args), "PointObj.AddCartesian", name)
            return
        except Exception as exc:
            errors.append(str(exc))
    if _model_locked(model):
        raise Str2FundError(f"PointObj.AddCartesian({name!r}) failed: {_LOCKED_MESSAGE}")
    raise Str2FundError(f"PointObj.AddCartesian({name!r}) failed: {'; '.join(dict.fromkeys(errors))}")


def _add_frame(model: Any, frame: dict[str, Any]) -> None:
    name = str(frame["name"])
    try:
        # CSI signature: (Point1, Point2, ref Name, PropName, UserName).
        raw = model.FrameObj.AddByPoint(str(frame["i"]), str(frame["j"]), "", str(frame["section"]), name)
        _created_name(raw, f"FrameObj.AddByPoint({name!r})", name)
    except Exception as exc:
        raise Str2FundError(f"FrameObj.AddByPoint({name!r}) failed: {exc}") from exc
    angle = float(frame.get("rotation_deg") or 0.0)
    if angle:
        # SetLocalAxes(Name, Ang, ItemType): rotation of the local axes about axis 1.
        _call_set(model.FrameObj.SetLocalAxes, f"FrameObj.SetLocalAxes({name!r})", (name, angle), (name, angle, 0))


def _add_area(model: Any, area: dict[str, Any]) -> None:
    name = str(area["name"])
    try:
        # CSI signature: (NumPoints, PointNames, ref Name, PropName, UserName).
        raw = model.AreaObj.AddByPoint(4, [str(v) for v in area["points"]], "", str(area["section"]), name)
        _created_name(raw, f"AreaObj.AddByPoint({name!r})", name)
    except Exception as exc:
        raise Str2FundError(f"AreaObj.AddByPoint({name!r}) failed: {exc}") from exc


def _call_set(method: Any, operation: str, *variants: tuple[Any, ...]) -> None:
    """Call an OAPI Set* with its full signature first, then shorter ones.

    The full positional form (optional Color/Notes/GUID included) is the one
    proven against a real SAP2000 in late binding; the short form can return
    something other than a return code there. A non-numeric result is an error.
    """
    last: Exception | None = None
    for args in variants:
        try:
            raw = method(*args)
        except TypeError as exc:
            last = exc
            continue
        items = list(raw) if isinstance(raw, (tuple, list)) else [raw]
        codes = [v for v in (items[-1], items[0]) if isinstance(v, int) and not isinstance(v, bool)]
        if not codes:
            raise Str2FundError(f"SAP2000 {operation} returned an unexpected result: {raw!r}")
        if codes[0] != 0:
            raise Str2FundError(f"SAP2000 {operation} returned {codes[0]}")
        return
    raise Str2FundError(f"SAP2000 {operation} rejected every known signature: {last}")


def _ensure_frame_section(model: Any, section: dict[str, Any]) -> bool:
    """Create a pedestal section (or accept an identical one); True if created."""
    name = str(section["name"])
    if name in set(_get_name_list(model.PropFrame, "PropFrame.GetNameList")):
        difference = _frame_section_difference(model, section)
        if difference:
            raise Str2FundError(f"Frame section {name!r} {difference}")
        return False
    kind, t3, t2 = _frame_section_spec(section)
    material = str(section["material"])
    if kind == "circle":
        method = getattr(model.PropFrame, "SetCircle", None)
        if not callable(method):
            raise Str2FundError("SAP2000 PropFrame.SetCircle is unavailable")
        # SetCircle(Name, MatProp, T3 = diameter, Color, Notes, GUID).
        _call_set(method, f"PropFrame.SetCircle({name!r})", (name, material, t3, -1, "", ""), (name, material, t3))
        return True
    method = getattr(model.PropFrame, "SetRectangle", None)
    if not callable(method):
        raise Str2FundError("SAP2000 PropFrame.SetRectangle is unavailable")
    # SetRectangle(Name, MatProp, T3, T2, Color, Notes, GUID); Ton_m_C: metres.
    _call_set(
        method,
        f"PropFrame.SetRectangle({name!r})",
        (name, material, t3, t2, -1, "", ""),
        (name, material, t3, t2),
    )
    return True


def _ensure_area_section(model: Any, section: dict[str, Any]) -> bool:
    """Create a footing shell section (or accept an identical one); True if created."""
    name = str(section["name"])
    if name in set(_get_name_list(model.PropArea, "PropArea.GetNameList")):
        difference = _area_section_difference(model, section)
        if difference:
            raise Str2FundError(f"Area section {name!r} {difference}")
        return False
    method = getattr(model.PropArea, "SetShell_1", None)
    if not callable(method):
        raise Str2FundError("SAP2000 PropArea.SetShell_1 is unavailable")
    thickness = float(section["thickness"])
    material = str(section["material"])
    # SetShell_1(Name, ShellType, IncludeDrillingDOF, MatProp, MatAng, Thickness,
    # Bending, Color, Notes, GUID). eShellType: Thin=1, Thick=2 (verified on a real
    # SAP2000). Equal membrane/bending thickness; no soil restraints or springs.
    _call_set(
        method,
        f"PropArea.SetShell_1({name!r})",
        (name, _shell_type_of(section), True, material, 0.0, thickness, thickness, -1, "", ""),
    )
    return True


def _read_joint_loads(getter: Any, joint: str, patterns: set[str]) -> dict[str, list[float]]:
    """PointObj.GetLoadForce -> {pattern: [F1..M3]}, tolerant of either binding.

    The return code sits first with late binding and last with typed wrappers,
    so the item count is not taken from a fixed position: the arrays decide.
    """
    attempts = (
        lambda: getter(joint),
        lambda: getter(joint, 0, [], [], [], [], [], [], [], [], [], []),
    )
    last_error = "no usable response"
    for attempt in attempts:
        try:
            raw = attempt()
        except Exception as exc:
            last_error = str(exc)
            continue
        if not isinstance(raw, (tuple, list)):
            last_error = "unexpected response"
            continue
        arrays = [list(v) for v in raw if isinstance(v, (list, tuple))]
        numeric = [v for v in arrays if v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)]
        strings = [v for v in arrays if v and all(isinstance(x, str) for x in v)]
        if len(numeric) < 6 or not strings:
            last_error = "incomplete load assignment arrays"
            continue
        vectors = numeric[-6:]
        size = len(vectors[0])
        if any(len(v) != size for v in vectors):
            last_error = "inconsistent load assignment arrays"
            continue
        # LoadPat is the one string array holding every expected pattern name
        # (PointName, LCStep and CSys arrays have the same length).
        for names in (v for v in strings if len(v) == size):
            if patterns <= {str(n) for n in names}:
                return {str(names[i]): [float(vec[i]) for vec in vectors] for i in range(size)}
        last_error = "load pattern names not found in response"
    raise ValueError(last_error)


_SHELL_THIN, _SHELL_THICK = 1, 2


def _plan_frame_sections(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Unique pedestal sections of a plan (older plans: one per frame)."""
    listed = plan.get("frame_sections")
    if listed:
        return [dict(item) for item in listed]
    unique: dict[str, dict[str, Any]] = {}
    for frame in plan["frames"]:
        unique.setdefault(
            str(frame["section"]),
            {k: frame[k] for k in ("shape", "length_x", "length_y", "side") if k in frame} | {"name": frame["section"], "material": frame["material"]},
        )
    return list(unique.values())


def _plan_area_sections(plan: dict[str, Any]) -> list[dict[str, Any]]:
    listed = plan.get("area_sections")
    if listed:
        return [dict(item) for item in listed]
    unique: dict[str, dict[str, Any]] = {}
    for area in plan["areas"]:
        unique.setdefault(str(area["section"]), {"name": area["section"], "thickness": area["thickness"], "material": area["material"]})
    return list(unique.values())


def _shell_type_of(section: dict[str, Any]) -> int:
    return _SHELL_THICK if section.get("shell_type") == "thick" else _SHELL_THIN


def _read_shell(model: Any, name: str) -> dict[str, Any] | None:
    """PropArea.GetShell_1 -> {type, material, thickness}; None if unreadable."""
    getter = getattr(model.PropArea, "GetShell_1", None)
    if not callable(getter):
        return None
    try:
        values = _result_items(_com(getter, name, outs=(0, False, "", 0.0, 0.0, 0.0, 0, "", "")), f"PropArea.GetShell_1({name!r})")
    except Exception:
        return None
    ints = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    floats = [float(v) for v in values if isinstance(v, float)]
    material = next((str(v) for v in values if isinstance(v, str) and v), "")
    if not ints or len(floats) < 2:
        return None
    return {"type": ints[0], "material": material, "thickness": floats[1]}


def _frame_section_spec(section: dict[str, Any]) -> tuple[str, float, float]:
    """('circle', D, D) or ('rect', T3, T2).

    Plans store the canonical section: T3 is the longer side. An unrotated
    vertical frame has T3 along global X (a frame whose long side runs along Y
    is rotated 90°). Older plans carry ``length_x``/``length_y`` or ``side``.
    """
    if section.get("shape") == "circle":
        diameter = float(section.get("diameter") or section.get("t3") or section.get("length_x"))
        return "circle", diameter, diameter
    if section.get("t3") is not None:
        return "rect", float(section["t3"]), float(section.get("t2", section["t3"]))
    if section.get("length_x") is not None and section.get("length_y") is not None:
        return "rect", float(section["length_x"]), float(section["length_y"])
    side = float(section["side"])
    return "rect", side, side


def _frame_section_difference(model: Any, section: dict[str, Any]) -> str | None:
    """None if the existing section matches the plan; otherwise why not."""
    kind, t3, t2 = _frame_section_spec(section)
    actual = _section_dims(model, str(section["name"]))
    if actual is None:
        return "exists but is not a readable rectangular or circular section"
    material, a3, a2 = actual
    is_circle_actual = _is_circle_section(model, str(section["name"]))
    if (kind == "circle") != is_circle_actual:
        return "exists with another shape"
    if not (math.isclose(a3, t3, rel_tol=1e-8, abs_tol=1e-8) and math.isclose(a2, t2, rel_tol=1e-8, abs_tol=1e-8)):
        return f"exists with other dimensions ({a3} x {a2})"
    if str(material).casefold() != str(section["material"]).casefold():
        return f"exists with other material ({material})"
    return None


def _is_circle_section(model: Any, name: str) -> bool:
    getter = getattr(model.PropFrame, "GetCircle", None)
    if not callable(getter):
        return False
    try:
        _result_items(_com(getter, name, outs=_SECTION_OUTS["GetCircle"]), f"PropFrame.GetCircle({name!r})")
        return True
    except Exception:
        return False


def _area_section_difference(model: Any, section: dict[str, Any]) -> str | None:
    actual = _read_shell(model, str(section["name"]))
    if actual is None:
        return "exists but its shell properties could not be read"
    if actual["type"] != _shell_type_of(section):
        return f"exists with another shell type ({actual['type']})"
    if not math.isclose(actual["thickness"], float(section["thickness"]), rel_tol=1e-8, abs_tol=1e-8):
        return f"exists with another thickness ({actual['thickness']})"
    if actual["material"].casefold() != str(section["material"]).casefold():
        return f"exists with other material ({actual['material']})"
    return None


def _object_names(plan: dict[str, Any]) -> dict[str, list[str]]:
    names = {
        "points": [p["name"] for p in plan["points"]],
        "frames": [f["name"] for f in plan["frames"]],
        "areas": [a["name"] for a in plan["areas"]],
        "patterns": [p["name"] for p in plan["patterns"]],
    }
    if plan.get("groups"):
        names["groups"] = [g["name"] for g in plan["groups"]]
    return names


def _default_dead_is_untouched(model: Any, name: str) -> bool:
    """The target's own DEAD: type Dead, self weight 1, nothing else loaded on it."""
    try:
        load_type = _split_outputs(_com(model.LoadPatterns.GetLoadType, name, outs=(0,)), "LoadPatterns.GetLoadType")
        weight = _split_outputs(_com(model.LoadPatterns.GetSelfWTMultiplier, name, outs=(0.0,)), "LoadPatterns.GetSelfWTMultiplier")
    except Exception:
        return False
    return int(load_type[0]) == 1 and math.isclose(float(weight[0]), 1.0, abs_tol=1e-9)


def _rename_default_dead(model: Any, new_name: str) -> bool:
    """Rename the default DEAD pattern and its case; True if something was renamed.

    LoadPatterns.ChangeName updates the case's load reference but not the case
    name, so both are renamed. Any failure is rolled back as far as possible.
    """
    patterns = {n.casefold(): n for n in _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")}
    cases = {n.casefold(): n for n in _get_name_list(model.LoadCases, "LoadCases.GetNameList")}
    old = patterns.get("dead")
    if old is None or new_name.casefold() in patterns or new_name.casefold() in cases:
        return False
    if not _default_dead_is_untouched(model, old):
        raise Str2FundError(f"The target's {old} is not the default weight pattern (type Dead, self weight 1); it cannot be renamed to {new_name}")
    _ret(model.LoadPatterns.ChangeName(old, new_name), f"LoadPatterns.ChangeName({old!r}, {new_name!r})")
    case = cases.get("dead")
    if case is not None:
        try:
            _ret(model.LoadCases.ChangeName(case, new_name), f"LoadCases.ChangeName({case!r}, {new_name!r})")
        except Exception:
            try:
                model.LoadPatterns.ChangeName(new_name, old)
            except Exception:
                pass
            raise
    return True


def _create_group(model: Any, group: dict[str, Any]) -> None:
    name = str(group["name"])
    _call_set(model.GroupDef.SetGroup, f"GroupDef.SetGroup({name!r})", (name,))
    for kind, member in (("points", model.PointObj), ("frames", model.FrameObj), ("areas", model.AreaObj)):
        for obj in group.get(kind, []):
            _call_set(member.SetGroupAssign, f"SetGroupAssign({obj!r}, {name!r})", (str(obj), name), (str(obj), name, False, 0))


def _group_members(model: Any, name: str) -> dict[str, set[str]]:
    """GroupDef.GetAssignments -> members by kind (1 point, 2 frame, 5 area)."""
    raw = model.GroupDef.GetAssignments(name)
    values = _result_items(raw, f"GroupDef.GetAssignments({name!r})")
    arrays = [list(v) for v in values if isinstance(v, (list, tuple))]
    types = next((a for a in arrays if a and all(isinstance(x, int) for x in a)), [])
    objects = next((a for a in arrays if a and all(isinstance(x, str) for x in a)), [])
    found: dict[str, set[str]] = {"points": set(), "frames": set(), "areas": set()}
    for kind_code, obj in zip(types, objects):
        label = {1: "points", 2: "frames", 5: "areas"}.get(int(kind_code))
        if label:
            found[label].add(str(obj))
    return found


def _verify_applied(model: Any, plan: dict[str, Any]) -> list[str]:
    """Read every generated object/load back; any mismatch blocks success."""
    failures: list[str] = []
    point_names = set(_get_name_list(model.PointObj, "PointObj.GetNameList"))
    frame_names = set(_get_name_list(model.FrameObj, "FrameObj.GetNameList"))
    area_names = set(_get_name_list(model.AreaObj, "AreaObj.GetNameList"))
    pattern_names = set(_get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList"))
    load_case_names = set(_get_name_list(model.LoadCases, "LoadCases.GetNameList"))
    for point in plan["points"]:
        name = str(point["name"])
        if name not in point_names:
            failures.append(f"point {name} missing")
            continue
        actual = _call_get_coord_cartesian(model, name)
        for axis, expected, got in zip("xyz", (point["x"], point["y"], point["z"]), actual):
            if not math.isclose(float(expected), float(got), rel_tol=1e-9, abs_tol=1e-8):
                failures.append(f"point {name} coordinate {axis} differs")
    plan_frame_sections = _plan_frame_sections(plan)
    checked_sections: dict[str, str | None] = {}
    for frame in plan["frames"]:
        name = str(frame["name"])
        if name not in frame_names:
            failures.append(f"frame {name} missing")
            continue
        try:
            actual_points = _frame_endpoints(model, name)
            if actual_points != (str(frame["i"]), str(frame["j"])):
                failures.append(f"frame {name} connectivity differs: {actual_points}")
            raw = _com(model.FrameObj.GetSection, name, outs=("", ""))
            section_values = _result_items(raw, f"FrameObj.GetSection({name!r})")
            section = next((str(v) for v in section_values if isinstance(v, str) and v), "")
            if section != str(frame["section"]):
                failures.append(f"frame {name} section differs")
            if "rotation_deg" in frame:
                expected_angle = float(frame["rotation_deg"] or 0.0)
                actual_angle = _frame_rotation(model, name)
                if abs((actual_angle - expected_angle + 90.0) % 180.0 - 90.0) > 1e-6:
                    failures.append(f"frame {name} local axes rotation is {actual_angle}, expected {expected_angle}")
            section_name = str(frame["section"])
            if section_name not in checked_sections:
                spec = next((sec for sec in plan_frame_sections if str(sec["name"]) == section_name), None)
                checked_sections[section_name] = _frame_section_difference(model, spec) if spec else "is not in the plan"
            if checked_sections[section_name]:
                failures.append(f"frame section {section_name} {checked_sections[section_name]}")
        except Exception as exc:
            failures.append(f"frame {name} readback failed: {exc}")
    for area in plan["areas"]:
        name = str(area["name"])
        if name not in area_names:
            failures.append(f"area {name} missing")
            continue
        try:
            raw_points = model.AreaObj.GetPoints(name)
            values = _result_items(raw_points, f"AreaObj.GetPoints({name!r})")
            arrays = [list(v) for v in values if isinstance(v, (list, tuple)) and v and all(isinstance(x, str) for x in v)]
            actual = max(arrays, key=len) if arrays else []
            expected = [str(v) for v in area["points"]]
            cyclic = any(actual == expected[i:] + expected[:i] for i in range(4))
            reverse = list(reversed(expected))
            cyclic = cyclic or any(actual == reverse[i:] + reverse[:i] for i in range(4))
            if not cyclic:
                failures.append(f"area {name} connectivity differs: {actual}")
            raw_prop = model.AreaObj.GetProperty(name)
            prop_values = _result_items(raw_prop, f"AreaObj.GetProperty({name!r})")
            prop_name = next((str(v) for v in prop_values if isinstance(v, str) and v), "")
            if prop_name != str(area["section"]):
                failures.append(f"area {name} section differs")
        except Exception as exc:
            failures.append(f"area {name} readback failed: {exc}")
    for section in _plan_area_sections(plan):
        difference = _area_section_difference(model, section)
        if difference:
            failures.append(f"area section {section['name']} {difference}")
    for group in plan.get("groups", []):
        try:
            members = _group_members(model, str(group["name"]))
            for kind in ("points", "frames", "areas"):
                if members[kind] != {str(x) for x in group.get(kind, [])}:
                    failures.append(f"group {group['name']} {kind} differ from the plan")
        except Exception as exc:
            failures.append(f"group {group['name']} readback failed: {exc}")
    setup = plan.get("target_setup") or {}
    if setup.get("rename_default_dead"):
        weight = str(setup["rename_default_dead"])
        if weight not in pattern_names or weight not in load_case_names:
            failures.append(f"weight pattern/case {weight} missing")
    for pattern in plan["patterns"]:
        name = str(pattern["name"])
        if name not in pattern_names or name not in load_case_names:
            failures.append(f"load pattern/static case {name} missing")
            continue
        metadata = _case_type(model, name)
        if metadata["kind"] != "linear_static":
            failures.append(f"load case {name} is not verified linear static")
    expected_by_joint: dict[str, dict[str, list[float]]] = {}
    for load in plan["loads"]:
        expected_by_joint.setdefault(str(load["joint"]), {})[str(load["pattern"])] = [float(load.get(field, 0.0)) for field in _LOAD_FIELDS]
    for joint in list(expected_by_joint):
        # All-zero loads are not assigned (SAP rejects them), so nothing to read back.
        expected_by_joint[joint] = {
            pattern: values for pattern, values in expected_by_joint[joint].items() if any(v != 0.0 for v in values)
        }
        if not expected_by_joint[joint]:
            del expected_by_joint[joint]
    for joint, expected_loads in expected_by_joint.items():
        getter = getattr(model.PointObj, "GetLoadForce", None)
        if not callable(getter):
            failures.append("PointObj.GetLoadForce unavailable; six-component load readback cannot be verified")
            break
        try:
            actual_loads = _read_joint_loads(getter, joint, set(expected_loads))
            for pattern, expected in expected_loads.items():
                if pattern not in actual_loads:
                    raise ValueError(f"pattern {pattern} missing")
                actual = actual_loads[pattern]
                if any(not math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8) for a, b in zip(actual, expected)):
                    failures.append(f"joint {joint} load {pattern} six-component values differ")
        except Exception as exc:
            failures.append(f"joint {joint} load readback failed: {exc}")
    return failures


def _set_load_force(model: Any, load: dict[str, Any]) -> None:
    """Assign one six-component nodal load, trying the known signatures.

    The short form is tried first (it worked for the first load patterns on a
    real SAP2000), then the full form with ItemType, then Replace=False. The
    final error carries the values and whether the pattern/joint/model state
    explains the rejection.
    """
    joint = str(load["joint"])
    pattern = str(load["pattern"])
    csys = str(load.get("coord_sys", "Global"))
    forces = [float(load.get(field, 0.0)) for field in _LOAD_FIELDS]
    if all(value == 0.0 for value in forces):
        # SAP2000 rejects an all-zero joint load (verified: code 1). A zero load
        # is the absence of a load, so clear any previous one and move on.
        remover = getattr(model.PointObj, "DeleteLoadForce", None)
        if callable(remover):
            try:
                remover(joint, pattern)
            except Exception:
                pass
        return
    variants = (
        (joint, pattern, forces, True, csys),
        (joint, pattern, list(forces), True, csys, 0),
        (joint, pattern, list(forces), False, csys, 0),
    )
    codes: list[str] = []
    for args in variants:
        try:
            raw = model.PointObj.SetLoadForce(*args)
        except Exception as exc:
            codes.append(str(exc))
            continue
        items = list(raw) if isinstance(raw, (tuple, list)) else [raw]
        ints = [v for v in (items[-1], items[0]) if isinstance(v, int) and not isinstance(v, bool)]
        if ints and ints[0] == 0:
            return
        codes.append(f"returned {ints[0] if ints else raw!r}")
    try:
        patterns = {n for n in _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")}
    except Exception:
        patterns = set()
    hints = []
    if patterns and pattern not in patterns:
        hints.append("el patrón de carga no existe en el destino")
    if _model_locked(model):
        hints.append("el modelo está bloqueado")
    raise Str2FundError(
        f"PointObj.SetLoadForce({joint!r}, {pattern!r}) failed ({'; '.join(dict.fromkeys(codes))}); "
        f"values={forces}" + (f"; {', '.join(hints)}" if hints else "")
    )


def _apply(model: Any, plan: dict[str, Any]) -> dict[str, Any]:
    identity, fingerprint = _target_fingerprint(model)
    created = {"frame_sections": 0, "area_sections": 0, "points": 0, "frames": 0, "areas": 0, "groups": 0, "patterns": 0, "loads": 0}
    try:
        setup = plan.get("target_setup") or {}
        if setup.get("rename_default_dead"):
            # Fail fast and clean: nothing has been created yet.
            created["renamed_default_dead"] = int(_rename_default_dead(model, str(setup["rename_default_dead"])))
        # Properties are established before geometry, after full preflight.
        for section in _plan_frame_sections(plan):
            created["frame_sections"] += int(_ensure_frame_section(model, section))
        for section in _plan_area_sections(plan):
            created["area_sections"] += int(_ensure_area_section(model, section))
        for point in plan["points"]:
            _add_point(model, point)
            created["points"] += 1
        for frame in plan["frames"]:
            _add_frame(model, frame)
            created["frames"] += 1
        for area in plan["areas"]:
            _add_area(model, area)
            created["areas"] += 1
        for group in plan.get("groups", []):
            _create_group(model, group)
            created["groups"] += 1
        for pattern in plan["patterns"]:
            name = str(pattern["name"])
            self_weight = float(pattern.get("self_weight", pattern.get("selfwt", 0.0)))
            if self_weight != 0.0:
                raise Str2FundError(f"Str2Fund load pattern {name!r} must have selfwt=0")
            # SAP2000 eLoadPatternType.Other = 8. It avoids misclassifying
            # transferred reaction sets as Dead; no combinations are created.
            raw = model.LoadPatterns.Add(name, 8, self_weight, True)
            _ret(raw, f"LoadPatterns.Add({name!r})")
            created["patterns"] += 1
        for load in plan["loads"]:
            _set_load_force(model, load)
            created["loads"] += 1
    except Exception as exc:
        try:
            partial_identity, partial_fingerprint = _target_fingerprint(model)
        except Exception:
            partial_identity, partial_fingerprint = identity, None
        manifest = {
            "schema_version": "str2fund_manifest_v1",
            "id": str(plan["manifest"].get("id") or _stable_hash(plan)),
            "source_path": plan["source"]["path"],
            "source_hash": plan["source"].get("hash"),
            "target_path": identity["path"],
            "pre_fingerprint": fingerprint,
            "post_fingerprint": partial_fingerprint,
            "naming_version": plan.get("naming_version", 1),
            "object_names": _object_names(plan),
            "created": created,
            "status": "partial",
        }
        raise Str2FundPartialError({"ok": False, "partial": True, "created": created, "manifest": manifest, "target": partial_identity, "message": f"SAP2000 accepted partial changes. Created counts: {created}. Error: {exc}"}) from exc
    try:
        failures = _verify_applied(model, plan)
        new_identity, new_fingerprint = _target_fingerprint(model)
    except Exception as exc:
        failures = [f"readback verification failed: {exc}"]
        try:
            new_identity, new_fingerprint = _target_fingerprint(model)
        except Exception:
            new_identity, new_fingerprint = identity, None
    manifest = {
        "schema_version": "str2fund_manifest_v1",
        "id": str(plan["manifest"].get("id") or _stable_hash(plan)),
        "source_path": plan["source"]["path"],
        "source_hash": plan["source"].get("hash"),
        "target_path": identity["path"],
        "pre_fingerprint": fingerprint,
        "post_fingerprint": new_fingerprint,
        "naming_version": plan.get("naming_version", 1),
            "object_names": _object_names(plan),
        "geometry_hash": _geometry_hash(plan),
        "loads": plan["loads"],
        "created": created,
        "status": "verified" if not failures else "verification_failed",
    }
    if failures:
        raise Str2FundPartialError({"ok": False, "partial": True, "verified": False, "failures": failures, "created": created, "manifest": manifest, "target": new_identity, "message": "SAP2000 model was written but one or more readback checks failed"})
    return {"ok": True, "verified": True, "failures": [], "created": created, "manifest": manifest, "target": new_identity, "fingerprint": new_fingerprint}


def _geometry_hash(plan: dict[str, Any]) -> str:
    return _stable_hash({key: plan[key] for key in ("points", "frames", "areas", "patterns")})


def _update_target_loads_ton_m_c(
    model: Any,
    manifest: dict[str, Any],
    plan: dict[str, Any],
    confirmed: bool,
    expected_target_path: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Replace only Str2Fund-managed joint loads after new source reactions.

    Geometry, properties, and load-pattern names must match the saved
    manifest. This prevents an update request from quietly turning into a
    model rebuild or touching user-created SAP objects.
    """
    if confirmed is not True:
        raise Str2FundError("confirmed must be true to update Str2Fund reactions")
    _plan_validate(plan)
    identity, fingerprint = _target_fingerprint(model)
    if _norm_path(identity["path"]) != _norm_path(expected_target_path):
        raise Str2FundError("The active SAP2000 model does not match expected_target_path")
    if expected_fingerprint != fingerprint or expected_fingerprint != manifest.get("post_fingerprint"):
        raise Str2FundError("Target changed after its last verified manifest; preview the model before updating reactions")
    status = manifest.get("status")
    if status not in {"verified", "partial_load_update", "load_update_verification_failed"} or manifest.get("schema_version") != "str2fund_manifest_v1":
        raise Str2FundError("A verified or recoverable Str2Fund target manifest is required")
    if _norm_path(str(manifest.get("target_path", ""))) != _norm_path(identity["path"]):
        raise Str2FundError("Saved manifest belongs to a different target model")
    if manifest.get("geometry_hash") != _geometry_hash(plan):
        raise Str2FundError("Geometry or load-patterns changed; this operation only updates reactions")
    if int(manifest.get("naming_version", 1)) != int(plan.get("naming_version", 1)):
        raise Str2FundError(
            "The target was created with another Str2Fund naming version; its objects have different names. "
            "Open a clean target model and create the foundations again."
        )
    prior_names = manifest.get("object_names", {})
    expected_names = _object_names(plan)
    for key, current in expected_names.items():
        if key == "groups" and key not in prior_names:
            continue
        if prior_names.get(key) != current:
            raise Str2FundError("Plan object names differ from the managed target manifest")
    # A partial previous load update is safe to retry because all writes below
    # use replacement mode. For the normal path, prove current assignments
    # still match the manifest before overwriting them.
    geometry_failures = _verify_applied(model, {**plan, "loads": []})
    if geometry_failures:
        raise Str2FundError(f"Current managed geometry does not match manifest: {geometry_failures}")
    if status == "verified":
        load_failures = _verify_applied(model, {**plan, "loads": manifest.get("loads", [])})
        if load_failures:
            raise Str2FundError(f"Current managed loads do not match manifest: {load_failures}")
    applied = 0
    try:
        for load in plan["loads"]:
            _set_load_force(model, load)
            applied += 1
    except Exception as exc:
        partial_manifest = {**manifest, "status": "partial_load_update", "pending_loads": plan["loads"], "last_update_source_hash": plan["source"].get("hash"), "updated_rows": applied}
        raise Str2FundPartialError({"ok": False, "partial": True, "updated": applied, "manifest": partial_manifest, "target": identity, "message": f"Some managed nodal loads were updated before SAP2000 returned an error: {exc}"}) from exc
    failures = _verify_applied(model, plan)
    if failures:
        partial_manifest = {**manifest, "status": "load_update_verification_failed", "pending_loads": plan["loads"], "last_update_source_hash": plan["source"].get("hash"), "loads": plan["loads"]}
        raise Str2FundPartialError({"ok": False, "partial": True, "verified": False, "updated": applied, "failures": failures, "manifest": partial_manifest, "target": identity, "message": "Updated reactions failed readback verification"})
    updated_manifest = {
        **manifest,
        "source_path": plan["source"]["path"],
        "source_hash": plan["source"].get("hash"),
        "loads": plan["loads"],
        "pending_loads": None,
        "last_update_source_hash": plan["source"].get("hash"),
        "status": "verified",
    }
    return {"ok": True, "verified": True, "failures": [], "updated_loads": applied, "manifest": updated_manifest, "target": identity, "fingerprint": fingerprint}


def _apply_target_ton_m_c(
    model: Any,
    plan: dict[str, Any],
    confirmed: bool,
    expected_target_path: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    if confirmed is not True:
        raise Str2FundError("confirmed must be true to create a Str2Fund model")
    check = _preflight(model, plan, expected_target_path)
    if not expected_fingerprint or check["fingerprint"] != expected_fingerprint:
        already = [c for c in check["conflict_details"] if c.get("kind") in {"points", "frames", "areas", "patterns"}]
        if already:
            raise Str2FundError(
                "Target already contains objects of this plan (e.g. "
                f"{already[0]['kind']} {already[0]['name']}): a previous creation probably went through. "
                "Do not apply again; check the target model, or use 'update loads' to refresh reactions."
            )
        raise Str2FundError("Target changed after preview; preview the plan again before applying")
    if check["conflicts"]:
        raise Str2FundError(f"Target contains conflicting names or missing materials: {check['conflict_details']}")
    if any(float(pattern.get("self_weight", pattern.get("selfwt", 0.0))) != 0.0 for pattern in plan["patterns"]):
        raise Str2FundError("All Str2Fund load patterns must use selfwt=0 to avoid duplicating source weight")
    return _apply(model, plan)


def update_target_loads(
    model: Any,
    manifest: dict[str, Any],
    plan: dict[str, Any],
    confirmed: bool,
    expected_target_path: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    return _with_ton_m_c(
        model,
        lambda: _update_target_loads_ton_m_c(
            model, manifest, plan, confirmed, expected_target_path, expected_fingerprint
        ),
    )


def apply_target(
    model: Any,
    plan: dict[str, Any],
    confirmed: bool,
    expected_target_path: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    return _with_ton_m_c(
        model,
        lambda: _apply_target_ton_m_c(model, plan, confirmed, expected_target_path, expected_fingerprint),
    )


class SourceBody(BaseModel):
    supports: list[str] = Field(default_factory=list, max_length=500)
    cases: list[str] = Field(default_factory=list, max_length=500)


class TargetPreviewBody(BaseModel):
    plan: dict[str, Any]


class TargetApplyBody(BaseModel):
    plan: dict[str, Any]
    confirmed: bool = False
    expected_target_path: str
    expected_fingerprint: str


class CreateMaterialsBody(BaseModel):
    materials: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    confirmed: bool = False
    expected_target_path: str
    source_path: str | None = None


class TargetUpdateLoadsBody(TargetApplyBody):
    manifest: dict[str, Any]


def install_routes(app: Any, session: Any, com_error: type[Exception]) -> None:
    """Install Str2Fund endpoints on the authenticated local SAP agent."""
    from fastapi import HTTPException

    def call(fn):
        try:
            return session.actor.call(lambda: fn(session._model()))
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            if detail is not None:
                raise HTTPException(409, detail) from exc
            if isinstance(exc, com_error):
                raise HTTPException(409, str(exc)) from exc
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/sap/actions/str2fund/source")
    def source(body: SourceBody):
        def run(model):
            return extract_source(model, body.supports, body.cases)

        return call(run)

    @app.post("/v1/sap/actions/str2fund/target/preview")
    def target_preview(body: TargetPreviewBody):
        return call(lambda model: preview_target(model, body.plan))

    @app.post("/v1/sap/actions/str2fund/target/apply")
    def target_apply(body: TargetApplyBody):
        return call(lambda model: apply_target(model, body.plan, body.confirmed, body.expected_target_path, body.expected_fingerprint))

    @app.post("/v1/sap/actions/str2fund/target/materials")
    def target_materials(body: CreateMaterialsBody):
        return call(lambda model: create_materials(model, body.materials, body.confirmed, body.expected_target_path, body.source_path))

    @app.post("/v1/sap/actions/str2fund/target/update-loads")
    def target_update_loads(body: TargetUpdateLoadsBody):
        return call(lambda model: update_target_loads(model, body.manifest, body.plan, body.confirmed, body.expected_target_path, body.expected_fingerprint))
