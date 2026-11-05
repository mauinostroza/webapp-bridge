"""Modificación de Fundaciones (Fundaciones SAP2000): lectura, vista previa y creación.

Etapa 3 de ``docs/PLAN_MODIFICACION_FUNDACIONES.md``. El módulo lee frames y
catálogos del modelo abierto, revisa un plan ``modif_plan_v1`` contra el modelo
(``preview``, solo lectura) y lo crea (``apply``): sección de área -> puntos
nuevos -> áreas -> restricciones -> grupo -> carga uniforme; después relee todo
y devuelve ``verification.issues``.

Reutiliza las funciones ya probadas con SAP2000 real de ``bridge.str2fund``
(sin modificarlas): ``_with_ton_m_c``, ``_add_point`` (verifica el nombre
devuelto por ``AddCartesian``), ``_add_area``, ``_ensure_area_section``
(ShellType Thin=1/Thick=2, ``SetShell_1`` sobrescribe en silencio, por eso se
compara antes), ``_create_group`` (``GroupDef.SetGroup`` + ``SetGroupAssign``),
``_group_members``, ``_call_set``, ``_read_shell``. Los nombres de SAP2000 no
distinguen mayúsculas, por eso todas las comparaciones de nombres usan casefold.
"""

from __future__ import annotations

import math
from typing import Any

from bridge.actions import _call_get_coord_cartesian, _get_name_list, selected_objects
from bridge.str2fund import (
    _COORD_TOL,
    _LOCKED_MESSAGE,
    Str2FundError,
    _add_area,
    _add_point,
    _area_section_difference,
    _call_set,
    _com,
    _create_group,
    _ensure_area_section,
    _frame_endpoints,
    _frame_rotation,
    _group_members,
    _is_circle_section,
    _model_locked,
    _read_shell,
    _result_items,
    _section_dims,
    _with_ton_m_c,
)

SOURCE_SCHEMA = "modif_source_v1"
PLAN_SCHEMA = "modif_plan_v1"
_LOAD_DIR_GLOBAL_Z = 6
_SHELL_KINDS = {1: "thin", 2: "thick", 3: "membrane"}


class ModifError(Str2FundError):
    """Plan inválido, desactualizado o rechazado por SAP2000."""


class ModifPartialError(ModifError):
    """La creación falló después de que SAP2000 aceptó algunos cambios."""

    def __init__(self, detail: dict[str, Any]):
        super().__init__(str(detail.get("message", "Creación parcial")))
        self.detail = detail


def _names(member: Any, operation: str) -> list[str]:
    return _get_name_list(member, operation)


def _fold(values: list[str]) -> dict[str, str]:
    return {v.casefold(): v for v in values}


# ---------------------------------------------------------------------------
# 1) Lectura de frames
# ---------------------------------------------------------------------------


def _footprint(
    model: Any, section: str, rotation_deg: float, vertical: bool, notes: list[str]
) -> dict[str, Any] | None:
    if not vertical:
        notes.append("El frame no es vertical: no se calculó su huella en planta.")
        return None
    if not section:
        notes.append("No se pudo leer la sección del frame.")
        return None
    if _is_circle_section(model, section):
        dims = _section_dims(model, section)
        if dims is not None:
            return {"shape": "circ", "d": round(dims[1], 9)}
    dims = _section_dims(model, section)
    if dims is None:
        notes.append(
            f"No se pudieron leer las dimensiones de la sección {section!r} (no es rectangular ni circular)."
        )
        return None
    _material, t3, t2 = dims
    # Un frame vertical sin giro tiene T3 a lo largo de X global (ver
    # str2fund._frame_section_spec); el giro rota la sección alrededor del eje del frame.
    angle = math.radians(rotation_deg)
    cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
    lx = t3 * cos + t2 * sin
    ly = t3 * sin + t2 * cos
    if rotation_deg and abs(rotation_deg) > 1e-9:
        notes.append(f"Huella calculada con el giro del frame ({rotation_deg:g}°).")
    return {"shape": "rect", "lx": round(lx, 9), "ly": round(ly, 9)}


def _frame_section_name(model: Any, name: str) -> str:
    try:
        values = _result_items(
            _com(model.FrameObj.GetSection, name, outs=("", "")), f"FrameObj.GetSection({name!r})"
        )
    except Exception:
        return ""
    return next((str(v) for v in values if isinstance(v, str) and v), "")


def _read_frames(model: Any, frames: list[str] | None) -> dict[str, Any]:
    existing_frames = _names(model.FrameObj, "FrameObj.GetNameList")
    if frames:
        requested = list(dict.fromkeys(str(f) for f in frames))
    else:
        requested = list(dict.fromkeys(selected_objects(model, "frames")))
        if not requested:
            raise ModifError(
                "No hay frames seleccionados en SAP2000: seleccione al menos uno o indique sus nombres."
            )
    known = _fold(existing_frames)
    out: list[dict[str, Any]] = []
    for requested_name in requested:
        name = known.get(requested_name.casefold())
        if name is None:
            raise ModifError(f"El frame {requested_name!r} no existe en el modelo.")
        notes: list[str] = []
        end_i, end_j = _frame_endpoints(model, name)
        ci = _call_get_coord_cartesian(model, end_i)
        cj = _call_get_coord_cartesian(model, end_j)
        if cj[2] < ci[2]:
            lower_joint, upper_joint, lower, upper = end_j, end_i, cj, ci
        else:
            lower_joint, upper_joint, lower, upper = end_i, end_j, ci, cj
        vertical = abs(lower[0] - upper[0]) <= _COORD_TOL and abs(lower[1] - upper[1]) <= _COORD_TOL
        if math.isclose(lower[2], upper[2], abs_tol=_COORD_TOL):
            vertical = False
            notes.append("El frame es horizontal: el nodo inferior es el primero.")
        section = _frame_section_name(model, name)
        rotation = _frame_rotation(model, name) if vertical else 0.0
        out.append(
            {
                "name": name,
                "lower_joint": lower_joint,
                "upper_joint": upper_joint,
                "lower": {"x": float(lower[0]), "y": float(lower[1]), "z": float(lower[2])},
                "section": section,
                "footprint": _footprint(model, section, rotation, vertical, notes),
                "vertical": vertical,
                "notes": notes,
            }
        )
    return {
        "schema": SOURCE_SCHEMA,
        "units": "Ton_m_C",
        "frames": out,
        "existing": {
            "points": _names(model.PointObj, "PointObj.GetNameList"),
            "areas": _names(model.AreaObj, "AreaObj.GetNameList"),
            "frames": existing_frames,
        },
    }


def read_frames(model: Any, frames: list[str] | None = None) -> dict[str, Any]:
    """``modif_source_v1``: nodo inferior, huella en planta y nombres existentes."""
    return _with_ton_m_c(model, lambda: _read_frames(model, frames))


# ---------------------------------------------------------------------------
# 2) Catálogos
# ---------------------------------------------------------------------------


def _read_catalog(model: Any) -> dict[str, Any]:
    sections = []
    for name in _names(model.PropArea, "PropArea.GetNameList"):
        shell = _read_shell(model, name)
        if shell is None:
            sections.append({"name": name, "thickness": None, "material": None, "shell_type": None})
        else:
            sections.append(
                {
                    "name": name,
                    "thickness": shell["thickness"],
                    "material": shell["material"] or None,
                    "shell_type": _SHELL_KINDS.get(shell["type"]),
                }
            )
    return {
        "area_sections": sections,
        "materials": _names(model.PropMaterial, "PropMaterial.GetNameList"),
        "load_patterns": _names(model.LoadPatterns, "LoadPatterns.GetNameList"),
        "groups": _names(model.GroupDef, "GroupDef.GetNameList"),
    }


def read_catalog(model: Any) -> dict[str, Any]:
    return _with_ton_m_c(model, lambda: _read_catalog(model))


# ---------------------------------------------------------------------------
# Validación del plan y revisión contra el modelo
# ---------------------------------------------------------------------------


def _plan_check(plan: Any) -> list[str]:
    """Errores estructurales del plan (lista vacía = aplicable)."""
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise ModifError(f"El plan debe ser un {PLAN_SCHEMA}.")
    for key in ("points", "areas", "area_sections"):
        if not isinstance(plan.get(key), list):
            raise ModifError(f"El plan no tiene la lista {key!r}.")
    problems = [str(e) for e in (plan.get("errors") or [])]
    names: set[str] = set()
    for point in plan["points"]:
        try:
            [float(point[k]) for k in ("x", "y", "z")]
            name = str(point["name"])
        except (KeyError, TypeError, ValueError):
            raise ModifError(f"Punto del plan inválido: {point!r}") from None
        if name.casefold() in names:
            problems.append(f"Nombre de punto repetido en el plan: {name}")
        names.add(name.casefold())
    area_names: set[str] = set()
    for area in plan["areas"]:
        try:
            name = str(area["name"])
            str(area["section"])
            pts = [str(p) for p in area["points"]]
        except (KeyError, TypeError):
            raise ModifError(f"Área del plan inválida: {area!r}") from None
        if len(pts) != 4:
            problems.append(f"El área {name} no tiene 4 nodos.")
        if name.casefold() in area_names:
            problems.append(f"Nombre de área repetido en el plan: {name}")
        area_names.add(name.casefold())
        for p in pts:
            if p.casefold() not in names:
                problems.append(f"El área {name} usa el nodo {p} que no está en el plan.")
    return problems


def _node_map(plan: dict[str, Any]) -> dict[str, str]:
    """Nombre del plan -> nombre real en SAP2000 (nodos existentes se reutilizan)."""
    return {str(p["name"]): str(p.get("existing") or p["name"]) for p in plan["points"]}


def _plan_load(plan: dict[str, Any]) -> dict[str, Any] | None:
    load = plan.get("load")
    if not isinstance(load, dict) or not load.get("pattern") or load.get("uniform") is None:
        return None
    return load


def _review(model: Any, plan: dict[str, Any]) -> dict[str, Any]:
    conflicts = [f"plan: {e}" for e in _plan_check(plan)]
    points = _fold(_names(model.PointObj, "PointObj.GetNameList"))
    areas = _fold(_names(model.AreaObj, "AreaObj.GetNameList"))
    area_sections = _fold(_names(model.PropArea, "PropArea.GetNameList"))
    materials = _fold(_names(model.PropMaterial, "PropMaterial.GetNameList"))
    patterns = _fold(_names(model.LoadPatterns, "LoadPatterns.GetNameList"))
    groups = _fold(_names(model.GroupDef, "GroupDef.GetNameList"))

    new_points = new_sections = new_groups = 0
    for point in plan["points"]:
        name = str(point["name"])
        coords = (float(point["x"]), float(point["y"]), float(point["z"]))
        if point.get("existing"):
            real = points.get(str(point["existing"]).casefold())
            if real is None:
                conflicts.append(f"El nodo existente {point['existing']} ya no está en el modelo.")
                continue
            actual = _call_get_coord_cartesian(model, real)
            if any(abs(a - b) > _COORD_TOL for a, b in zip(actual, coords)):
                conflicts.append(
                    f"El nodo existente {real} ya no está donde el plan lo espera "
                    f"(plan {coords}, modelo {tuple(round(float(v), 6) for v in actual)})."
                )
        else:
            new_points += 1
            if name.casefold() in points:
                conflicts.append(f"El nombre de punto {name} ya está usado en el modelo.")
    for area in plan["areas"]:
        if str(area["name"]).casefold() in areas:
            conflicts.append(f"El nombre de área {area['name']} ya está usado en el modelo.")
    for section in plan["area_sections"]:
        name = str(section["name"])
        spec = {"shell_type": "thick", **section}
        if name.casefold() in area_sections:
            difference = _area_section_difference(model, {**spec, "name": area_sections[name.casefold()]})
            if difference:
                conflicts.append(f"La sección de área {name} {difference}.")
        else:
            if not section.get("create"):
                conflicts.append(f"La sección de área {name} no existe en el modelo.")
            else:
                new_sections += 1
                if str(section.get("material", "")).casefold() not in materials:
                    conflicts.append(
                        f"El material {section.get('material')!r} de la sección {name} no existe en el modelo."
                    )
    group = plan.get("group")
    if isinstance(group, dict) and group.get("name"):
        exists = str(group["name"]).casefold() in groups
        if group.get("create"):
            new_groups += 1
            if exists:
                conflicts.append(f"El grupo nuevo {group['name']} ya existe en el modelo.")
        elif not exists:
            conflicts.append(f"El grupo {group['name']} no existe en el modelo.")
    load = _plan_load(plan)
    if load is not None and str(load["pattern"]).casefold() not in patterns:
        conflicts.append(f"El patrón de carga {load['pattern']} no existe en el modelo.")
    locked = _model_locked(model)
    if locked:
        conflicts.append(_LOCKED_MESSAGE)
    return {
        "ok": not conflicts,
        "conflicts": conflicts,
        "will_create": {
            "points": new_points,
            "areas": len(plan["areas"]),
            "area_sections": new_sections,
            "groups": new_groups,
        },
        "model_locked": bool(locked),
    }


def preview(model: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Revisión de solo lectura del plan contra el modelo abierto."""
    return _with_ton_m_c(model, lambda: _review(model, plan))


# ---------------------------------------------------------------------------
# Restricciones y cargas
# ---------------------------------------------------------------------------


def _read_restraint(model: Any, name: str) -> list[bool] | None:
    getter = getattr(model.PointObj, "GetRestraint", None)
    if not callable(getter):
        return None
    try:
        values = _result_items(_com(getter, name, outs=([False] * 6,)), f"PointObj.GetRestraint({name!r})")
    except Exception:
        return None
    flags = (
        list(values[0])
        if values and isinstance(values[0], (tuple, list))
        else [v for v in values if isinstance(v, bool)]
    )
    return [bool(v) for v in flags[:6]] if len(flags) >= 6 else None


def _wanted_restraint(model: Any, name: str, point: dict[str, Any]) -> list[bool]:
    """u1/u2 del plan; los demás grados se conservan (un nodo existente no pierde apoyos)."""
    restraint = point.get("restraint") or {}
    flags = _read_restraint(model, name) or [False] * 6
    if restraint.get("u1"):
        flags[0] = True
    if restraint.get("u2"):
        flags[1] = True
    return flags


def _set_restraint(model: Any, name: str, flags: list[bool]) -> None:
    # SetRestraint(Name, Value[6], ItemType); la forma corta como respaldo.
    _call_set(
        model.PointObj.SetRestraint,
        f"PointObj.SetRestraint({name!r})",
        (name, list(flags), 0),
        (name, list(flags)),
    )


def _set_load_uniform(model: Any, area: str, load: dict[str, Any]) -> None:
    pattern, value = str(load["pattern"]), float(load["uniform"])
    # SetLoadUniform(Name, LoadPat, Value, Dir, Replace, CSys, ItemType); Dir 6 = Z global.
    _call_set(
        model.AreaObj.SetLoadUniform,
        f"AreaObj.SetLoadUniform({area!r}, {pattern!r})",
        (area, pattern, value, _LOAD_DIR_GLOBAL_Z, True, "Global", 0),
        (area, pattern, value, _LOAD_DIR_GLOBAL_Z, True, "Global"),
        (area, pattern, value, _LOAD_DIR_GLOBAL_Z, True),
    )


def _read_uniform_loads(model: Any, area: str) -> list[tuple[str, int | None, float]]:
    """AreaObj.GetLoadUniform -> [(patrón, dirección, valor)] tolerante a ambos bindings."""
    getter = model.AreaObj.GetLoadUniform
    raw = _com(getter, area, outs=(0, [], [], [], [], []))
    _result_items(raw, f"AreaObj.GetLoadUniform({area!r})")
    arrays = [list(v) for v in raw if isinstance(v, (list, tuple)) and v]
    strings = [a for a in arrays if all(isinstance(x, str) for x in a)]
    numbers = [a for a in arrays if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in a)]
    if len(strings) < 2 or len(numbers) < 2:
        return []
    patterns = strings[1]  # [AreaName[], LoadPat[], CSys[]]
    directions = numbers[-2]
    values = numbers[-1]
    return [
        (str(patterns[i]), int(directions[i]), float(values[i]))
        for i in range(min(len(patterns), len(directions), len(values)))
    ]


# ---------------------------------------------------------------------------
# Verificación por relectura
# ---------------------------------------------------------------------------


def _same_cycle(actual: list[str], expected: list[str]) -> bool:
    a = [x.casefold() for x in actual]
    for seq in (expected, list(reversed(expected))):
        s = [x.casefold() for x in seq]
        if any(a == s[i:] + s[:i] for i in range(len(s))):
            return True
    return False


def _verify(model: Any, plan: dict[str, Any], restraints: dict[str, list[bool]]) -> list[str]:
    issues: list[str] = []
    nodes = _node_map(plan)
    point_names = _fold(_names(model.PointObj, "PointObj.GetNameList"))
    area_names = _fold(_names(model.AreaObj, "AreaObj.GetNameList"))
    for point in plan["points"]:
        real = nodes[str(point["name"])]
        if real.casefold() not in point_names:
            issues.append(f"El punto {real} no existe en el modelo.")
            continue
        actual = _call_get_coord_cartesian(model, real)
        for axis, expected, got in zip("xyz", (point["x"], point["y"], point["z"]), actual):
            if not math.isclose(float(expected), float(got), rel_tol=1e-9, abs_tol=1e-6):
                issues.append(
                    f"El punto {real} difiere en {axis}: plan {float(expected)}, modelo {float(got)}."
                )
    for section in plan["area_sections"]:
        difference = _area_section_difference(model, {"shell_type": "thick", **section})
        if difference:
            issues.append(f"La sección de área {section['name']} {difference}.")
    for area in plan["areas"]:
        name = str(area["name"])
        if name.casefold() not in area_names:
            issues.append(f"El área {name} no existe en el modelo.")
            continue
        try:
            values = _result_items(model.AreaObj.GetPoints(name), f"AreaObj.GetPoints({name!r})")
            arrays = [
                list(v)
                for v in values
                if isinstance(v, (list, tuple)) and v and all(isinstance(x, str) for x in v)
            ]
            actual = max(arrays, key=len) if arrays else []
            expected = [nodes[str(p)] for p in area["points"]]
            if not _same_cycle(actual, expected):
                issues.append(f"El área {name} tiene nodos {actual}, se esperaba {expected}.")
            prop = _result_items(model.AreaObj.GetProperty(name), f"AreaObj.GetProperty({name!r})")
            section = next((str(v) for v in prop if isinstance(v, str) and v), "")
            if section.casefold() != str(area["section"]).casefold():
                issues.append(
                    f"El área {name} tiene la sección {section!r}, se esperaba {area['section']!r}."
                )
        except Exception as exc:
            issues.append(f"No se pudo releer el área {name}: {exc}")
    for real, expected_flags in restraints.items():
        actual_flags = _read_restraint(model, real)
        if actual_flags is None:
            issues.append(f"No se pudo releer la restricción del punto {real}.")
        elif actual_flags != expected_flags:
            issues.append(
                f"El punto {real} tiene restricciones {actual_flags}, se esperaba {expected_flags}."
            )
    group = plan.get("group")
    if isinstance(group, dict) and group.get("name"):
        try:
            members = _group_members(model, str(group["name"]))
            members_fold = {k: {m.casefold() for m in v} for k, v in members.items()}
            for kind, mapper in (("points", lambda n: nodes.get(str(n), str(n))), ("areas", str)):
                missing = [
                    str(n) for n in group.get(kind, []) if mapper(n).casefold() not in members_fold[kind]
                ]
                if missing:
                    issues.append(f"Al grupo {group['name']} le faltan {kind}: {missing}.")
        except Exception as exc:
            issues.append(f"No se pudo releer el grupo {group['name']}: {exc}")
    load = _plan_load(plan)
    if load is not None:
        for area_name in load.get("areas", []):
            try:
                found = [
                    (d, v)
                    for p, d, v in _read_uniform_loads(model, str(area_name))
                    if p.casefold() == str(load["pattern"]).casefold()
                ]
                if not found:
                    issues.append(f"El área {area_name} no tiene carga en el patrón {load['pattern']}.")
                    continue
                direction, value = found[-1]
                if direction != _LOAD_DIR_GLOBAL_Z or not math.isclose(
                    value, float(load["uniform"]), rel_tol=1e-8, abs_tol=1e-8
                ):
                    issues.append(
                        f"El área {area_name} tiene carga {value} (dir {direction}) en {load['pattern']}, "
                        f"se esperaba {float(load['uniform'])} (dir {_LOAD_DIR_GLOBAL_Z})."
                    )
            except Exception as exc:
                issues.append(f"No se pudo releer la carga del área {area_name}: {exc}")
    return issues


# ---------------------------------------------------------------------------
# Creación
# ---------------------------------------------------------------------------


def _assign_group(model: Any, group: str, points: list[str], areas: list[str]) -> None:
    for member, objects in ((model.PointObj, points), (model.AreaObj, areas)):
        for obj in objects:
            _call_set(
                member.SetGroupAssign,
                f"SetGroupAssign({obj!r}, {group!r})",
                (obj, group),
                (obj, group, False, 0),
            )


def _apply(model: Any, plan: dict[str, Any], expected_hash: str) -> dict[str, Any]:
    if not expected_hash or expected_hash != plan.get("hash"):
        raise ModifError("El hash esperado no coincide con el del plan: genere la vista previa de nuevo.")
    problems = _plan_check(plan)
    if problems:
        raise ModifError(f"El plan tiene errores y no se puede aplicar: {problems}")
    if _model_locked(model):
        raise ModifError(_LOCKED_MESSAGE)
    review = _review(model, plan)
    if review["conflicts"]:
        raise ModifError(f"El modelo cambió o hay conflictos con el plan: {review['conflicts']}")

    nodes = _node_map(plan)
    created = {"area_sections": 0, "points": 0, "areas": 0, "restraints": 0, "groups": 0, "loads": 0}
    restraints: dict[str, list[bool]] = {}
    try:
        for section in plan["area_sections"]:
            created["area_sections"] += int(_ensure_area_section(model, {"shell_type": "thick", **section}))
        for point in plan["points"]:
            if not point.get("existing"):
                _add_point(model, point)
                created["points"] += 1
        for area in plan["areas"]:
            _add_area(
                model,
                {
                    "name": area["name"],
                    "section": area["section"],
                    "points": [nodes[str(p)] for p in area["points"]],
                },
            )
            created["areas"] += 1
        for point in plan["points"]:
            restraint = point.get("restraint") or {}
            if restraint.get("u1") or restraint.get("u2"):
                real = nodes[str(point["name"])]
                flags = _wanted_restraint(model, real, point)
                _set_restraint(model, real, flags)
                restraints[real] = flags
                created["restraints"] += 1
        group = plan.get("group")
        if isinstance(group, dict) and group.get("name"):
            g_points = [nodes.get(str(n), str(n)) for n in group.get("points", [])]
            g_areas = [str(n) for n in group.get("areas", [])]
            if group.get("create"):
                _create_group(model, {"name": group["name"], "points": g_points, "areas": g_areas})
                created["groups"] += 1
            else:
                _assign_group(model, str(group["name"]), g_points, g_areas)
        load = _plan_load(plan)
        if load is not None:
            for area_name in load.get("areas", []):
                _set_load_uniform(model, str(area_name), load)
                created["loads"] += 1
    except Exception as exc:
        raise ModifPartialError(
            {
                "ok": False,
                "partial": True,
                "created": created,
                "message": f"SAP2000 aceptó cambios parciales ({created}). Error: {exc}",
            }
        ) from exc
    try:
        issues = _verify(model, plan, restraints)
    except Exception as exc:
        issues = [f"Falló la verificación por relectura: {exc}"]
    return {"ok": not issues, "created": created, "verification": {"ok": not issues, "issues": issues}}


def apply(model: Any, plan: dict[str, Any], expected_hash: str) -> dict[str, Any]:
    """Crea la fundación del plan y la verifica releyendo el modelo."""
    return _with_ton_m_c(model, lambda: _apply(model, plan, expected_hash))


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def install_routes(app: Any, call: Any) -> None:
    """Instala ``/v1/sap/actions/foundations/modif/{frames,catalog,preview,apply}``
    con el envoltorio ``call`` de ``bridge.actions.install_routes``."""
    prefix = "/v1/sap/actions/foundations/modif"

    @app.post(f"{prefix}/frames")
    def modif_frames(body: dict[str, Any] | None = None):
        frames = (body or {}).get("frames")
        return call(lambda model: read_frames(model, frames if isinstance(frames, list) else None))

    @app.post(f"{prefix}/catalog")
    def modif_catalog(body: dict[str, Any] | None = None):
        return call(lambda model: read_catalog(model))

    @app.post(f"{prefix}/preview")
    def modif_preview(body: dict[str, Any]):
        return call(lambda model: preview(model, (body or {}).get("plan")))

    @app.post(f"{prefix}/apply")
    def modif_apply(body: dict[str, Any]):
        return call(
            lambda model: apply(model, (body or {}).get("plan"), str((body or {}).get("expected_hash") or ""))
        )
