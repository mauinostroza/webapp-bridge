# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import math
from typing import Any

from bridge.viewer.sap2000.table_aliases import aliases_for

from bridge.viewer.sap2000.reader_utils import (
    _table,
    _empty_table,
    _data_values,
    _names,
    _get_names,
    _filter_table_by_object,
    _filter_table_by_group,
    _finalize_result_table,
    _is_diagnostic_table,
)
from bridge.viewer.sap2000.reader_geometry import (
    _joint_coordinates,
    _joint_coords_and_groups,
    _frame_geometry,
    _frame_section,
    _frame_assigned_section_from_database,
    _frame_releases_data,
    _frame_releases_from_database,
    _frame_local_axes_data,
    _frame_local_axes_from_database,
    _format_release_summary,
    _frame_distributed_loads,
    _frame_distributed_loads_from_database,
    _frame_point_loads,
    _frame_point_loads_from_database,
    _joint_force_loads,
    _joint_force_loads_from_database,
    _joint_restraint_data,
    _joint_restraints_from_database,
    _constraint_names_and_types,
    _constraint_joints,
    _area_points,
    _area_points_from_database,
)
from bridge.viewer.sap2000.reader_sections import (
    frame_section_properties as _frame_section_properties,
    area_shell_thickness as _area_shell_thickness,
    attach_section_geometry,
    get_material_for_section,
)
from bridge.viewer.sap2000.reader_results import (
    _select_output_cases,
    _sap_results_table,
    _sap_all_selected_results_table,
    _database_table,
    _result_output_cases,
)
from bridge.viewer.sap2000.reader_metadata import (
    model_info as _model_info,
    diagnostics as _diagnostics,
    materials as _materials,
    sections as _sections,
    groups as _groups,
    load_patterns as _load_patterns,
    load_cases as _load_cases,
    load_combinations as _load_combinations,
    available_result_filters as _available_result_filters,
    database_tables as _database_tables,
    selection_properties as _selection_properties,
)

# DesignSteel.GetSummaryResults' RatioType codes (OAPI reference).
_STEEL_RATIO_TYPE_LABELS = {
    1: "PMM",
    2: "Corte mayor",
    3: "Corte menor",
    4: "Capacidad viga-columna mayor",
    5: "Capacidad viga-columna menor",
    6: "Otro",
}


class SapModelReader:
    def __init__(self, model: Any, result_filter: str | None = None) -> None:
        self.model = model
        self.result_filter = result_filter

    def context(self) -> dict:
        return {
            "materials": _data_values(self.materials(limit=200)),
            "sections": _data_values(self.sections(limit=300)),
            "groups": _data_values(self.groups(limit=200)),
            "load_patterns": _data_values(self.load_patterns(limit=200)),
            "load_cases": _data_values(self.load_cases(limit=200)),
            "load_combinations": _data_values(self.load_combinations(limit=200)),
        }

    def table_for_action(self, action: str, parameters: dict[str, Any] | None = None) -> dict:
        mapping = {
            "list_materials": self.materials,
            "list_sections": self.sections,
            "list_groups": self.groups,
            "list_load_patterns": self.load_patterns,
            "list_load_cases": self.load_cases,
            "list_load_combinations": self.load_combinations,
            "model_diagnostics": self.diagnostics,
            "selection_properties": self.selection_properties,
            "model_info": self.model_info,
            "joint_displacements": self.joint_displacements,
            "frame_forces": self.frame_forces,
            "joint_reactions": self.joint_reactions,
            "joint_coordinates": self.joint_coordinates,
            "frame_geometry": self.frame_geometry,
            "model_geometry": self.frame_geometry,
            "geometry_3d": self.frame_geometry,
            "list_database_tables": self.database_tables,
            "frame_length": self.frame_length,
            "frame_assigned_section": self.frame_assigned_section,
            "frame_releases": self.frame_releases,
            "frame_local_axes": self.frame_local_axes,
            "frame_loads_list": self.frame_loads_list,
            "joint_loads_list": self.joint_loads_list,
            "frame_point_loads_list": self.frame_point_loads_list,
            "joint_restraints": self.joint_restraints,
            "constraints": self.constraints,
            "area_geometry": self.area_geometry,
            "steel_design_results": self.steel_design_results,
            "concrete_design_results": self.concrete_design_results,
            "concrete_beam_design_results": self.concrete_beam_design_results,
            "frame_section_properties": self.frame_section_properties,
            "area_shell_thickness": self.area_shell_thickness,
            "section_cut_forces": self.section_cut_forces,
            "area_forces": self.area_forces,
        }
        if action not in mapping:
            raise ValueError(f"Acción de lectura no soportada: {action}")
        params = parameters or {}
        if action == "joint_displacements" and params.get("joint"):
            table = self.joint_displacements(joint_name=str(params.get("joint")))
        elif action == "joint_reactions" and params.get("joint"):
            table = self.joint_reactions(joint_name=str(params.get("joint")))
        elif action == "frame_forces" and params.get("frame"):
            table = self.frame_forces(frame_name=str(params.get("frame")))
        elif action == "section_cut_forces" and params.get("section_cut"):
            table = self.section_cut_forces(cut_name=str(params.get("section_cut")))
        elif action == "area_forces" and params.get("area"):
            table = self.area_forces(area_name=str(params.get("area")))
        elif action == "joint_coordinates" and params.get("joint"):
            table = _filter_table_by_object(self.joint_coordinates(), str(params.get("joint")), ["Joint", "Obj", "Object"])
        elif action in {"frame_geometry", "model_geometry", "geometry_3d"} and params.get("frame"):
            table = _filter_table_by_object(self.frame_geometry(), str(params.get("frame")), ["Frame", "Obj", "Object", "Element"])
        elif action == "frame_length":
            table = self.frame_length(frame_name=params.get("frame"))
        elif action == "frame_assigned_section":
            table = self.frame_assigned_section(frame_name=params.get("frame"))
        elif action == "frame_releases":
            table = self.frame_releases(frame_name=params.get("frame"))
        elif action == "frame_local_axes":
            table = self.frame_local_axes(frame_name=params.get("frame"))
        elif action == "frame_loads_list":
            table = self.frame_loads_list(frame_name=params.get("frame"), pattern=params.get("load_pattern") or params.get("pattern"))
        elif action == "joint_loads_list":
            table = self.joint_loads_list(joint_name=params.get("joint"), pattern=params.get("load_pattern") or params.get("pattern"))
        elif action == "frame_point_loads_list":
            table = self.frame_point_loads_list(frame_name=params.get("frame"), pattern=params.get("load_pattern") or params.get("pattern"))
        elif action == "joint_restraints":
            table = self.joint_restraints(joint_name=params.get("joint"))
        elif action == "constraints":
            table = self.constraints(constraint_name=params.get("constraint"))
        elif action == "area_geometry":
            table = self.area_geometry(area_name=params.get("area"))
        else:
            table = mapping[action]()
        if params.get("group"):
            table = _filter_table_by_group(table, self.model, str(params.get("group")), action)
        if not table.get("summary_message"):
            if action == "joint_coordinates" and params.get("joint") and table.get("rows"):
                row = table["rows"][0]
                table["summary_message"] = f"El joint {row[0]} esta en X={row[1]}, Y={row[2]}, Z={row[3]}."
            elif action in {"frame_geometry", "model_geometry", "geometry_3d"} and params.get("frame") and table.get("rows"):
                row = table["rows"][0]
                table["summary_message"] = (
                    f"El frame {row[1]} conecta {row[2]} ({row[4]}, {row[5]}, {row[6]}) -> "
                    f"{row[3]} ({row[7]}, {row[8]}, {row[9]})."
                )
        if not table.get("rows"):
            return _empty_table(table.get("name", action), table.get("columns", ["Message"]))
        return table

    # ── Metadata ──────────────────────────────────────────────────

    def model_info(self, limit: int | None = None) -> dict:
        return _model_info(self.model, limit)

    def diagnostics(self, limit: int | None = None) -> dict:
        sections_data = self.sections()
        return _diagnostics(self.model, limit, sections_data=sections_data)

    def materials(self, limit: int | None = None) -> dict:
        return _materials(self.model, limit)

    def sections(self, limit: int | None = None) -> dict:
        return _sections(self.model, limit)

    def groups(self, limit: int | None = None) -> dict:
        return _groups(self.model, limit)

    def load_patterns(self, limit: int | None = None) -> dict:
        return _load_patterns(self.model, limit)

    def load_cases(self, limit: int | None = None) -> dict:
        return _load_cases(self.model, limit)

    def load_combinations(self, limit: int | None = None) -> dict:
        return _load_combinations(self.model, limit)

    def available_result_filters(self) -> list[dict[str, str]]:
        return _available_result_filters(self.model)

    def database_tables(self, limit: int | None = None) -> dict:
        return _database_tables(self.model, limit)

    def selection_properties(self, limit: int | None = None) -> dict:
        return _selection_properties(self.model, limit)

    # ── Geometry ──────────────────────────────────────────────────

    def joint_coordinates(self, limit: int | None = None) -> dict:
        joints, errors = _joint_coordinates(self.model)
        rows = [
            [name, coords[0], coords[1], coords[2], "; ".join(groups)]
            for name, coords, groups in joints
        ]
        if limit:
            rows = rows[:limit]
        table = _table("joint_coordinates", ["Joint", "X", "Y", "Z", "Groups"], rows)
        table["metadata"] = {"joint_count": len(joints), "errors": errors}
        return table

    def frame_geometry(self, limit: int | None = None) -> dict:
        frames, errors = _frame_geometry(self.model)
        rows = [
            [
                index + 1,
                frame["name"],
                frame["i_end"],
                frame["j_end"],
                frame["xi"],
                frame["yi"],
                frame["zi"],
                frame["xj"],
                frame["yj"],
                frame["zj"],
                "; ".join(frame["groups"]),
            ]
            for index, frame in enumerate(frames)
        ]
        if limit:
            rows = rows[:limit]
        table = _table(
            "frame_geometry",
            ["Id", "Frame", "IEnd", "JEnd", "Xi", "Yi", "Zi", "Xj", "Yj", "Zj", "Groups"],
            rows,
        )
        table["metadata"] = {"frame_count": len(frames), "errors": errors}
        return table

    def joint_coordinates_and_frame_geometry(self, limit: int | None = None) -> tuple[dict, dict]:
        """Fetch joint coordinates + frame geometry from a single COM pass.

        Equivalent to calling `joint_coordinates()` and `frame_geometry()`
        separately, but shares one PointObj/GroupDef COM round-trip instead
        of two (each of those methods otherwise re-derives coords + groups
        independently).
        """
        coords, groups, errors = _joint_coords_and_groups(self.model)
        joints = [(name, point, groups.get(name, [])) for name, point in coords.items()]
        joint_rows = [
            [name, point[0], point[1], point[2], "; ".join(g)] for name, point, g in joints
        ]
        if limit:
            joint_rows = joint_rows[:limit]
        joint_table = _table("joint_coordinates", ["Joint", "X", "Y", "Z", "Groups"], joint_rows)
        joint_table["metadata"] = {"joint_count": len(joints), "errors": errors}

        frames, frame_errors = _frame_geometry(self.model, shared=(coords, groups))
        frame_rows = [
            [
                index + 1,
                frame["name"],
                frame["i_end"],
                frame["j_end"],
                frame["xi"],
                frame["yi"],
                frame["zi"],
                frame["xj"],
                frame["yj"],
                frame["zj"],
                "; ".join(frame["groups"]),
            ]
            for index, frame in enumerate(frames)
        ]
        if limit:
            frame_rows = frame_rows[:limit]
        frame_table = _table(
            "frame_geometry",
            ["Id", "Frame", "IEnd", "JEnd", "Xi", "Yi", "Zi", "Xj", "Yj", "Zj", "Groups"],
            frame_rows,
        )
        frame_table["metadata"] = {"frame_count": len(frames), "errors": frame_errors}

        return joint_table, frame_table

    def frame_length(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        frames, errors = _frame_geometry(self.model)
        # _frame_geometry() consume coordenadas ya normalizadas a metros (ver
        # _joint_coords_and_groups en reader_geometry.py), asi que la longitud
        # calculada aqui siempre esta en metros, sin importar las "present
        # units" activas en SAP2000 — la etiqueta debe decir "m", no el token
        # crudo de _length_unit() (que reflejaria mm/cm/ft/in y mentiria).
        unit = "m"
        rows: list[list[Any]] = []
        matched: list[tuple[str, float]] = []
        for frame in frames:
            try:
                xi, yi, zi = float(frame["xi"]), float(frame["yi"]), float(frame["zi"])
                xj, yj, zj = float(frame["xj"]), float(frame["yj"]), float(frame["zj"])
            except (TypeError, ValueError):
                continue
            length = math.sqrt((xj - xi) ** 2 + (yj - yi) ** 2 + (zj - zi) ** 2)
            if frame_name and str(frame.get("name", "")).casefold() != frame_name.casefold():
                continue
            rows.append([frame["name"], round(length, 4), unit, frame["i_end"], frame["j_end"]])
            matched.append((frame["name"], length))
        if limit:
            rows = rows[:limit]
        table = _table("frame_length", ["Frame", "Length", "Unit", "IEnd", "JEnd"], rows)
        table["metadata"] = {"frame_count": len(rows), "errors": errors, "unit": unit}
        if frame_name and matched:
            name, length = matched[0]
            table["summary_message"] = f"El frame {name} tiene {length:.3f} {unit} de longitud.".strip()
        elif frame_name:
            table["summary_message"] = f"No encontre un frame llamado {frame_name}."
        return table

    def frame_assigned_section(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        frame_obj = getattr(self.model, "FrameObj", None)
        if frame_obj is None:
            return _empty_table("frame_assigned_section", ["Frame", "Section", "AutoSelect"])
        try:
            names = _get_names(frame_obj)
        except Exception:
            return _empty_table("frame_assigned_section", ["Frame", "Section", "Aviso"])
        # Fase 17: intenta una lectura batch (una sola llamada COM para todos
        # los frames) antes de caer al loop por-frame (una llamada por frame).
        batch = _frame_assigned_section_from_database(self.model, [])
        rows: list[list[Any]] = []
        matched: tuple[str, str] | None = None
        for name in names:
            if frame_name and name.casefold() != frame_name.casefold():
                continue
            # El limit se aplica dentro del bucle (no solo al final) para no
            # gastar llamadas COM de sobra cuando la tabla batch no esta
            # disponible y se cae al loop por-frame (mismo defecto que
            # area_geometry, ver su comentario).
            if limit and not frame_name and len(rows) >= limit:
                break
            if batch is not None and name in batch:
                section, sauto = batch[name]
            else:
                section, sauto = _frame_section(frame_obj, name)
            rows.append([name, section, sauto])
            if frame_name:
                matched = (name, section)
        if limit:
            rows = rows[:limit]
        table = _table("frame_assigned_section", ["Frame", "Section", "AutoSelect"], rows)
        if matched:
            table["summary_message"] = f"El frame {matched[0]} tiene asignada la seccion {matched[1] or '(sin asignar)'}."
        elif frame_name:
            table["summary_message"] = f"No encontre un frame llamado {frame_name}."
        return table

    def frame_releases(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        frame_obj = getattr(self.model, "FrameObj", None)
        if frame_obj is None:
            return _empty_table("frame_releases", ["Frame", "Side", "U1", "U2", "U3", "R1", "R2", "R3"])
        try:
            names = _get_names(frame_obj)
        except Exception:
            return _empty_table("frame_releases", ["Frame", "Side", "U1", "U2", "U3", "R1", "R2", "R3"])
        # Fase 17: intenta una lectura batch (una sola llamada COM para todos
        # los frames) antes de caer al loop por-frame (una llamada por frame).
        batch = _frame_releases_from_database(self.model, [])
        rows: list[list[Any]] = []
        summary: str | None = None
        for name in names:
            if frame_name and name.casefold() != frame_name.casefold():
                continue
            if limit and not frame_name and len(rows) >= limit:
                break
            if batch is not None and name in batch:
                ii, jj = batch[name]
            else:
                ii, jj = _frame_releases_data(frame_obj, name)
            rows.append([name, "I"] + [bool(v) for v in ii])
            rows.append([name, "J"] + [bool(v) for v in jj])
            if frame_name:
                summary = _format_release_summary(name, ii, jj)
        if limit:
            rows = rows[:limit]
        table = _table("frame_releases", ["Frame", "Side", "U1", "U2", "U3", "R1", "R2", "R3"], rows)
        if summary:
            table["summary_message"] = summary
        elif frame_name:
            table["summary_message"] = f"No encontre un frame llamado {frame_name}."
        return table

    def frame_local_axes(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        """Angulo de rotacion de ejes locales 2/3 (grados, alrededor del eje
        local 1) por frame. Fila = [Frame, Angle, Advanced]. `Angle` es
        `None` cuando el frame usa ejes locales "avanzados" (un solo angulo
        no los describe, ver `_frame_local_axes_data`) o cuando la lectura
        fallo — nunca un valor fabricado. `Advanced` es un bool que distingue
        ambos casos. Este metodo solo expone el dato leido; orientar la
        extrusion del visor 3D con el es tarea de otro lote."""
        frame_obj = getattr(self.model, "FrameObj", None)
        if frame_obj is None:
            return _empty_table("frame_local_axes", ["Frame", "Angle", "Advanced"])
        try:
            names = _get_names(frame_obj)
        except Exception:
            return _empty_table("frame_local_axes", ["Frame", "Angle", "Advanced"])
        # Mismo patron Fase 17 que frame_assigned_section/frame_releases:
        # batch primero (una llamada COM para todos los frames), fallback
        # por-frame solo para los frames que el batch no cubrio.
        batch = _frame_local_axes_from_database(self.model, [])
        rows: list[list[Any]] = []
        matched: tuple[str, float | None, bool] | None = None
        for name in names:
            if frame_name and name.casefold() != frame_name.casefold():
                continue
            if limit and not frame_name and len(rows) >= limit:
                break
            if batch is not None and name in batch:
                angle, advanced = batch[name], False
            else:
                angle, advanced = _frame_local_axes_data(frame_obj, name)
            rows.append([name, angle, advanced])
            if frame_name:
                matched = (name, angle, advanced)
        if limit:
            rows = rows[:limit]
        table = _table("frame_local_axes", ["Frame", "Angle", "Advanced"], rows)
        if matched:
            name, angle, advanced = matched
            if advanced:
                table["summary_message"] = (
                    f"El frame {name} usa ejes locales avanzados; el angulo no esta disponible."
                )
            elif angle is None:
                table["summary_message"] = f"No se pudo leer el angulo de ejes locales del frame {name}."
            else:
                table["summary_message"] = f"El frame {name} tiene un angulo de ejes locales de {angle:.2f} grados."
        elif frame_name:
            table["summary_message"] = f"No encontre un frame llamado {frame_name}."
        return table

    def frame_loads_list(self, limit: int | None = None, frame_name: str | None = None, pattern: str | None = None) -> dict:
        frame_obj = getattr(self.model, "FrameObj", None)
        if frame_obj is None:
            return _empty_table("frame_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist1", "Dist2", "Val1", "Val2"])
        try:
            names = _get_names(frame_obj)
        except Exception as exc:
            table = _empty_table("frame_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist1", "Dist2", "Val1", "Val2"])
            table["metadata"] = {"errors": [f"GetNameList: {exc}"]}
            return table
        # Fase 17: batch read (una llamada COM) antes de caer al loop por-frame.
        errors: list[str] = []
        batch = _frame_distributed_loads_from_database(self.model, errors)
        rows: list[list[Any]] = []
        target_pattern = (pattern or "").strip()
        for name in names:
            if frame_name and name.casefold() != frame_name.casefold():
                continue
            if limit and not frame_name and len(rows) >= limit:
                break
            entries = batch.get(name, []) if batch is not None else _frame_distributed_loads(frame_obj, name, errors)
            for entry in entries:
                if target_pattern and str(entry.get("pattern", "")).casefold() != target_pattern.casefold():
                    continue
                rows.append([
                    entry.get("frame", name),
                    entry.get("pattern", ""),
                    entry.get("type", ""),
                    entry.get("dir", ""),
                    entry.get("dist1", ""),
                    entry.get("dist2", ""),
                    entry.get("val1", ""),
                    entry.get("val2", ""),
                    entry.get("csys", ""),
                ])
        if limit:
            rows = rows[:limit]
        csys_available = any(row[-1] != "" for row in rows)
        if not csys_available:
            rows = [row[:-1] for row in rows]
        table = _table("frame_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist1", "Dist2", "Val1", "Val2"], rows)
        if csys_available:
            table["columns"].append("CSys")
            table["rows_flat"] = [str(row[-2]) for row in rows]
        if errors:
            table.setdefault("metadata", {})["errors"] = errors
        if frame_name and not rows:
            table["summary_message"] = f"El frame {frame_name} no tiene cargas distribuidas asignadas."
        elif frame_name:
            table["summary_message"] = f"El frame {frame_name} tiene {len(rows)} carga(s) distribuida(s)."
        if errors:
            table["summary_message"] = "Lectura de cargas incompleta: " + "; ".join(errors[:3])
        return table

    def joint_loads_list(self, limit: int | None = None, joint_name: str | None = None, pattern: str | None = None) -> dict:
        point_obj = getattr(self.model, "PointObj", None)
        if point_obj is None:
            return _empty_table("joint_loads_list", ["Joint", "Pattern", "F1", "F2", "F3", "M1", "M2", "M3"])
        try:
            names = _get_names(point_obj)
        except Exception as exc:
            table = _empty_table("joint_loads_list", ["Joint", "Pattern", "F1", "F2", "F3", "M1", "M2", "M3"])
            table["metadata"] = {"errors": [f"GetNameList: {exc}"]}
            return table
        # Fase 17: batch read (una llamada COM) antes de caer al loop por-joint.
        errors: list[str] = []
        batch = _joint_force_loads_from_database(self.model, errors)
        rows: list[list[Any]] = []
        target_pattern = (pattern or "").strip()
        for name in names:
            if joint_name and name.casefold() != joint_name.casefold():
                continue
            if limit and not joint_name and len(rows) >= limit:
                break
            entries = batch.get(name, []) if batch is not None else _joint_force_loads(point_obj, name, errors)
            for entry in entries:
                if target_pattern and str(entry.get("pattern", "")).casefold() != target_pattern.casefold():
                    continue
                forces = entry.get("forces", [0.0] * 6)
                rows.append([entry.get("joint", name), entry.get("pattern", ""), *forces, entry.get("csys", "")])
        if limit:
            rows = rows[:limit]
        csys_available = any(row[-1] != "" for row in rows)
        if not csys_available:
            rows = [row[:-1] for row in rows]
        table = _table("joint_loads_list", ["Joint", "Pattern", "F1", "F2", "F3", "M1", "M2", "M3"], rows)
        if csys_available:
            table["columns"].append("CSys")
            table["rows_flat"] = [str(row[-2]) for row in rows]
        if errors:
            table.setdefault("metadata", {})["errors"] = errors
        if joint_name and not rows:
            table["summary_message"] = f"El joint {joint_name} no tiene cargas puntuales asignadas."
        elif joint_name:
            table["summary_message"] = f"El joint {joint_name} tiene {len(rows)} carga(s) puntual(es)."
        if errors:
            table["summary_message"] = "Lectura de cargas incompleta: " + "; ".join(errors[:3])
        return table

    def frame_point_loads_list(self, limit: int | None = None, frame_name: str | None = None, pattern: str | None = None) -> dict:
        """Cargas puntuales en barras (FrameObj.GetLoadPoint).

        Wrapper sobre _frame_point_loads de reader_geometry. Devuelve una
        tabla con columnas [Frame, Pattern, Type, Dir, Dist, Val].
        """
        frame_obj = getattr(self.model, "FrameObj", None)
        if frame_obj is None:
            return _empty_table("frame_point_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist", "Val"])
        try:
            names = _get_names(frame_obj)
        except Exception as exc:
            table = _empty_table("frame_point_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist", "Val"])
            table["metadata"] = {"errors": [f"GetNameList: {exc}"]}
            return table
        # Fase 17: batch read (una llamada COM) antes de caer al loop por-frame.
        errors: list[str] = []
        batch = _frame_point_loads_from_database(self.model, errors)
        rows: list[list[Any]] = []
        target_pattern = (pattern or "").strip()
        for name in names:
            if frame_name and name.casefold() != frame_name.casefold():
                continue
            if limit and not frame_name and len(rows) >= limit:
                break
            entries = batch.get(name, []) if batch is not None else _frame_point_loads(frame_obj, name, errors)
            for entry in entries:
                if target_pattern and str(entry.get("pattern", "")).casefold() != target_pattern.casefold():
                    continue
                rows.append([
                    entry.get("frame", name),
                    entry.get("pattern", ""),
                    entry.get("type", ""),
                    entry.get("dir", ""),
                    entry.get("dist", ""),
                    entry.get("val", ""),
                    entry.get("csys", ""),
                ])
        if limit:
            rows = rows[:limit]
        csys_available = any(row[-1] != "" for row in rows)
        if not csys_available:
            rows = [row[:-1] for row in rows]
        table = _table("frame_point_loads_list", ["Frame", "Pattern", "Type", "Dir", "Dist", "Val"], rows)
        if csys_available:
            table["columns"].append("CSys")
            table["rows_flat"] = [str(row[-2]) for row in rows]
        if errors:
            table.setdefault("metadata", {})["errors"] = errors
        if frame_name and not rows:
            table["summary_message"] = f"El frame {frame_name} no tiene cargas puntuales asignadas."
        elif frame_name:
            table["summary_message"] = f"El frame {frame_name} tiene {len(rows)} carga(s) puntual(es)."
        if errors:
            table["summary_message"] = "Lectura de cargas incompleta: " + "; ".join(errors[:3])
        return table

    def joint_restraints(self, limit: int | None = None, joint_name: str | None = None) -> dict:
        """Restricciones de apoyo en joints (PointObj.GetRestraint).

        Wrapper sobre _joint_restraint_data de reader_geometry. Devuelve una
        tabla con columnas [Joint, U1, U2, U3, R1, R2, R3]. Solo incluye
        joints con al menos un DOF restringido (omite joints libres).
        """
        point_obj = getattr(self.model, "PointObj", None)
        if point_obj is None:
            return _empty_table("joint_restraints", ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"])
        try:
            names = _get_names(point_obj)
        except Exception:
            return _empty_table("joint_restraints", ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"])
        # Fase 17: batch read (una llamada COM) antes de caer al loop por-joint.
        batch = _joint_restraints_from_database(self.model, [])
        rows: list[list[Any]] = []
        for name in names:
            if joint_name and name.casefold() != joint_name.casefold():
                continue
            if limit and not joint_name and len(rows) >= limit:
                break
            if batch is not None:
                dofs = batch.get(name)
            else:
                dofs = _joint_restraint_data(point_obj, name)
            if dofs is None:
                continue  # joint sin restricción (libre)
            if not any(dofs):
                continue  # joint libre (todos False)
            rows.append([name] + [bool(v) for v in dofs])
        if limit:
            rows = rows[:limit]
        table = _table("joint_restraints", ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"], rows)
        if joint_name and not rows:
            table["summary_message"] = f"El joint {joint_name} no tiene restriccion de apoyo asignada."
        elif joint_name:
            table["summary_message"] = f"El joint {joint_name} tiene restriccion de apoyo asignada."
        return table

    def constraints(self, limit: int | None = None, constraint_name: str | None = None) -> dict:
        """Constraints de definicion (Body/Diaphragm/Equal/Weld/Plate/Rod/
        Beam) via ConstraintDef.GetNameList/GetConstraint/GetJoints. No lee
        restraints de apoyo por nodo (eso es joint_restraints, otra API).
        """
        columns = ["Constraint", "Type", "Joints"]
        constraint_def = getattr(self.model, "ConstraintDef", None)
        if constraint_def is None:
            return _empty_table("constraints", columns)
        entries = _constraint_names_and_types(self.model)
        rows: list[list[Any]] = []
        for name, c_type in entries:
            if constraint_name and name.casefold() != constraint_name.casefold():
                continue
            if limit and not constraint_name and len(rows) >= limit:
                break
            joints = _constraint_joints(self.model, name)
            rows.append([name, c_type, ", ".join(joints)])
        if limit:
            rows = rows[:limit]
        table = _table("constraints", columns, rows)
        if constraint_name and not rows:
            table["summary_message"] = f"No existe el constraint {constraint_name}."
        elif constraint_name and rows:
            table["summary_message"] = f"El constraint {constraint_name} ({rows[0][1]}) incluye: {rows[0][2]}."
        return table

    def area_geometry(self, limit: int | None = None, area_name: str | None = None) -> dict:
        area_obj = getattr(self.model, "AreaObj", None)
        if area_obj is None:
            return _empty_table("area_geometry", ["Area", "NumPoints", "Points"])
        try:
            names = _get_names(area_obj)
        except Exception:
            return _empty_table("area_geometry", ["Area", "NumPoints", "Points"])
        # Batch read (una sola llamada COM para todas las areas) antes de
        # caer al loop por-area (una o dos llamadas COM por area). El limit
        # se aplica DENTRO del bucle (no despues, como antes) para no gastar
        # llamadas COM de sobra cuando el fallback esta activo: con 5000
        # areas y limit=10, el bug anterior hacia 5000-10000 llamadas para
        # despues tirar 4990 filas.
        batch = _area_points_from_database(self.model, [])
        rows: list[list[Any]] = []
        matched_points: list[str] | None = None
        for name in names:
            if area_name and name.casefold() != area_name.casefold():
                continue
            if limit and not area_name and len(rows) >= limit:
                break
            if batch is not None and name in batch:
                points = batch[name]
            else:
                points = _area_points(area_obj, name)
            rows.append([name, len(points), "; ".join(points)])
            if area_name:
                matched_points = points
        if limit:
            rows = rows[:limit]
        table = _table("area_geometry", ["Area", "NumPoints", "Points"], rows)
        if area_name and matched_points is not None:
            table["summary_message"] = f"El area {area_name} tiene {len(matched_points)} puntos: {', '.join(matched_points)}."
        elif area_name:
            table["summary_message"] = f"No encontre un area llamada {area_name}."
        return table

    # ── Section geometry for 3D extrusion ─────────────────────────

    def frame_section_properties(self, limit: int | None = None) -> dict:
        return _frame_section_properties(self.model, limit)

    def area_shell_thickness(self, limit: int | None = None) -> dict:
        return _area_shell_thickness(self.model, limit)

    def frame_section_materials(self, limit: int | None = None) -> dict:
        """Return section->material mapping (and table) for use by 3D pro viewer 'Material' mode."""
        frame_prop = getattr(self.model, "PropFrame", None)
        try:
            names = _get_names(frame_prop) if frame_prop else []
        except Exception:
            names = []
        rows: list[list[Any]] = []
        mats: dict[str, str] = {}
        for i, nm in enumerate(names):
            if limit is not None and i >= limit:
                break
            m = get_material_for_section(frame_prop, nm) if frame_prop else ""
            mats[nm] = m
            rows.append([nm, m])
        tbl = _table("frame_section_materials", ["Section", "Material"], rows)
        tbl["materials"] = mats
        return tbl

    def joint_displacement(self, joint_name: str, limit: int | None = None) -> dict:
        return self.joint_displacements(limit=limit, joint_name=joint_name)

    def frame_force(self, frame_name: str, limit: int | None = None) -> dict:
        return self.frame_forces(limit=limit, frame_name=frame_name)

    def joint_reaction(self, joint_name: str, limit: int | None = None) -> dict:
        return self.joint_reactions(limit=limit, joint_name=joint_name)

    # ── Results ───────────────────────────────────────────────────

    def joint_displacements(self, limit: int | None = None, joint_name: str | None = None) -> dict:
        setup = _select_output_cases(self.model, self.result_filter)
        read_limit = None if joint_name else limit
        table = _sap_results_table(
            "joint_displacements",
            self.model.Results.JointDispl,
            joint_name or "ALL",
            labels=["Obj", "Elm", "LoadCase", "StepType", "StepNum", "U1", "U2", "U3", "R1", "R2", "R3"],
            setup=setup,
            limit=read_limit,
            prefer_object=bool(joint_name),
        )
        if _is_diagnostic_table(table):
            db_table = _database_table(
                self.model,
                "joint_displacements",
                aliases_for("joint_displacements"),
                setup,
                read_limit,
            )
            if db_table:
                return _finalize_result_table(
                    db_table,
                    self.result_filter,
                    joint_name,
                    ["Joint", "Obj", "Object", "Elm", "Element"],
                    limit,
                )
        return _finalize_result_table(table, self.result_filter, joint_name, ["Joint", "Obj", "Object", "Elm", "Element"], limit)

    def frame_forces(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        setup = _select_output_cases(self.model, self.result_filter)
        read_limit = None if frame_name else limit
        table = _sap_results_table(
            "frame_forces",
            self.model.Results.FrameForce,
            frame_name or "ALL",
            labels=[
                "Obj", "ObjSta", "Elm", "ElmSta", "LoadCase",
                "StepType", "StepNum", "P", "V2", "V3", "T", "M2", "M3",
            ],
            setup=setup,
            limit=read_limit,
            prefer_object=bool(frame_name),
        )
        if _is_diagnostic_table(table):
            db_table = _database_table(
                self.model,
                "frame_forces",
                aliases_for("frame_forces"),
                setup,
                read_limit,
            )
            if db_table:
                return _finalize_result_table(
                    db_table,
                    self.result_filter,
                    frame_name,
                    ["Frame", "Obj", "Object", "Elm", "Element"],
                    limit,
                )
        return _finalize_result_table(table, self.result_filter, frame_name, ["Frame", "Obj", "Object", "Elm", "Element"], limit)

    def section_cut_forces(self, limit: int | None = None, cut_name: str | None = None) -> dict:
        """Fuerzas/momentos totales de un section cut (SapModel.Results.SectionCutAnalysis).

        A diferencia de frame_forces/joint_displacements, esta función COM no
        recibe (Name, ItemTypeElm) — reporta todos los section cuts que estén
        seleccionados para output, así que primero hay que seleccionarlos
        explícitamente (uno solo si se pide `cut_name`, todos si no)."""
        setup = _select_output_cases(self.model, self.result_filter)
        setup_api = getattr(self.model.Results, "Setup", None)
        try:
            if cut_name and setup_api is not None:
                setup_api.SetSectionCutSelectedForOutput(cut_name, True)
            elif setup_api is not None:
                setup_api.SelectAllSectionCutsForOutput(True)
        except Exception as exc:
            setup.setdefault("selection_errors", []).append(f"SectionCut selection: {exc}")

        read_limit = None if cut_name else limit
        table = _sap_all_selected_results_table(
            "section_cut_forces",
            self.model.Results.SectionCutAnalysis,
            labels=["SCut", "LoadCase", "StepType", "StepNum", "F1", "F2", "F3", "M1", "M2", "M3"],
            setup=setup,
            limit=read_limit,
        )
        return _finalize_result_table(table, self.result_filter, cut_name, ["SCut", "SectionCut", "Name"], limit)

    def area_forces(self, limit: int | None = None, area_name: str | None = None) -> dict:
        """Fuerzas/momentos de shell por punto de esquina (SapModel.Results.AreaForceShell):
        F11/F22/F12, M11/M22/M12, V13/V23. Calcado de frame_forces() — misma
        selección de casos y mismo formateo de tabla. API confirmada contra
        SAP2000 real (ver plan Fase 13). Nota: los valores vienen por
        PointElm (esquina de cada elemento de área), no un valor único por
        área — el consumidor (p.ej. el visor 3D) debe reducir/promediar."""
        setup = _select_output_cases(self.model, self.result_filter)
        read_limit = None if area_name else limit
        table = _sap_results_table(
            "area_forces",
            self.model.Results.AreaForceShell,
            area_name or "ALL",
            labels=[
                "Obj", "Elm", "PointElm", "LoadCase", "StepType", "StepNum",
                "F11", "F22", "F12", "FMax", "FMin", "FAngle", "FVM",
                "M11", "M22", "M12", "MMax", "MMin", "MAngle",
                "V13", "V23", "VMax", "VAngle",
            ],
            setup=setup,
            limit=read_limit,
            prefer_object=bool(area_name),
        )
        return _finalize_result_table(table, self.result_filter, area_name, ["Area", "Obj", "Object", "Elm", "Element"], limit)

    def joint_reactions(self, limit: int | None = None, joint_name: str | None = None) -> dict:
        setup = _select_output_cases(self.model, self.result_filter)
        read_limit = None if joint_name else limit
        func = getattr(self.model.Results, "JointReact", None)
        if callable(func):
            table = _sap_results_table(
                "joint_reactions",
                func,
                joint_name or "ALL",
                labels=["Obj", "Elm", "LoadCase", "StepType", "StepNum", "F1", "F2", "F3", "M1", "M2", "M3"],
                setup=setup,
                limit=read_limit,
                prefer_object=bool(joint_name),
            )
            if not _is_diagnostic_table(table):
                return _finalize_result_table(
                    table,
                    self.result_filter,
                    joint_name,
                    ["Joint", "Obj", "Object", "Elm", "Element"],
                    limit,
                )
        db_table = _database_table(
            self.model,
            "joint_reactions",
            aliases_for("joint_reactions"),
            setup,
            read_limit,
        )
        if db_table:
            return _finalize_result_table(
                db_table,
                self.result_filter,
                joint_name,
                ["Joint", "Obj", "Object", "Elm", "Element"],
                limit,
            )
        from bridge.viewer.sap2000.reader_utils import _diagnostic_result_table
        return _diagnostic_result_table(
            "joint_reactions",
            "",
            setup,
            "SAP2000 no devolvio reacciones por API directa ni por tablas internas.",
        )

    # ── Design Results ──────────────────────────────────────────────

    def steel_design_results(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        """Get steel design D/C ratios via DesignSteel.GetSummaryResults.

        Fixed bug: this previously read `design_obj.summary_results` as a
        plain Python attribute, which only exists on the test double
        (`sap2000/fake_model.py`) — the real SAP2000 COM object has no such
        attribute, only the `GetSummaryResults(...)` method (confirmed
        against the OAPI reference), so this always silently returned an
        empty table against real SAP2000 (caught by the old bare
        `except Exception`). Now calls the real method."""
        columns = ["Frame", "D/C", "Type", "Location", "Status"]
        design = getattr(self.model, "DesignSteel", None)
        if design is None:
            return _empty_table("steel_design_results", columns)
        return self._read_steel_design_summary(design, columns, frame_name, limit)

    def _read_steel_design_summary(
        self, design: Any, columns: list[str], frame_name: str | None, limit: int | None
    ) -> dict:
        name = frame_name or "All"
        # ItemType: 0=Object (a specific frame), 1=Group (Name="All" = every frame).
        item_type = 0 if frame_name else 1
        raw = None
        for call in (
            lambda: design.GetSummaryResults(name, 0, [], [], [], [], [], [], [], item_type),
            lambda: design.GetSummaryResults(name, 0, [], [], [], [], [], [], []),
        ):
            try:
                result = call()
            except Exception:
                continue
            if isinstance(result, (tuple, list)):
                raw = result
                break
        if raw is None:
            return _empty_table("steel_design_results", columns)

        values = list(raw)
        arrays = [list(v) for v in values if isinstance(v, (list, tuple))]
        string_arrays = [a for a in arrays if a and all(isinstance(x, str) for x in a)]
        numeric_arrays = [
            a for a in arrays
            if a and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in a)
        ]
        if not string_arrays or not numeric_arrays:
            return _empty_table("steel_design_results", columns)

        frame_names = string_arrays[0]
        count = len(frame_names)
        ratios = numeric_arrays[0] if len(numeric_arrays) > 0 else [0.0] * count
        ratio_types = numeric_arrays[1] if len(numeric_arrays) > 1 else [0] * count
        locations = numeric_arrays[2] if len(numeric_arrays) > 2 else [""] * count

        rows: list[list[Any]] = []
        for i, fname in enumerate(frame_names):
            if i >= len(ratios):
                break
            if frame_name and str(fname).casefold() != frame_name.casefold():
                continue
            ratio = ratios[i]
            rtype = _STEEL_RATIO_TYPE_LABELS.get(int(ratio_types[i]) if i < len(ratio_types) else -1, "")
            loc = locations[i] if i < len(locations) else ""
            status = "OK" if isinstance(ratio, (int, float)) and ratio <= 1.0 else "FAIL"
            rows.append([fname, f"{ratio:.3f}" if isinstance(ratio, float) else ratio, rtype, loc, status])
        if limit:
            rows = rows[:limit]
        table = _table("steel_design_results", columns, rows)
        if frame_name and rows:
            table["summary_message"] = f"El frame {frame_name} tiene D/C={rows[0][1]} ({rows[0][4]})."
        return table

    def concrete_design_results(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        """Get concrete column design D/C ratios via DesignConcrete.GetSummaryResultsColumn.

        Fixed bug (same class as steel_design_results, Fase 18): this
        previously read `design_obj.summary_results` as a plain Python
        attribute, which only exists on the test double — real SAP2000 has
        no such attribute, always silently returning an empty table.

        Scope: columns only. DesignConcrete.GetSummaryResultsBeam has no
        ratio field at all (only required rebar areas), so beams cannot
        feed this D/C-shaped table without fabricating a number — left
        uncovered here, same as before this fix. Within columns, MyOption=2
        ("Design" mode, as opposed to "Check") also has no meaningful D/C
        ratio, so those rows get Status="N/A (modo Diseño)" and no numeric
        ratio instead of a fabricated value.
        """
        columns = ["Frame", "D/C", "Type", "Location", "Status"]
        design = getattr(self.model, "DesignConcrete", None)
        if design is None:
            return _empty_table("concrete_design_results", columns)
        return self._read_concrete_column_design_summary(design, columns, frame_name, limit)

    def _read_concrete_column_design_summary(
        self, design: Any, columns: list[str], frame_name: str | None, limit: int | None
    ) -> dict:
        name = frame_name or "All"
        item_type = 0 if frame_name else 1
        raw = None
        for call in (
            lambda: design.GetSummaryResultsColumn(
                name, 0, [], [], [], [], [], [], [], [], [], [], [], [], item_type
            ),
            lambda: design.GetSummaryResultsColumn(name, 0, [], [], [], [], [], [], [], [], [], [], [], []),
        ):
            try:
                result = call()
            except Exception:
                continue
            if isinstance(result, (tuple, list)):
                raw = result
                break
        if raw is None:
            return _empty_table("concrete_design_results", columns)

        # Fixed positional layout, matching the declared ByRef order of
        # GetSummaryResultsColumn(Name, NumberItems, FrameName(), MyOption(),
        # Location(), PMMCombo(), PMMArea(), PMMRatio(), VmajorCombo(),
        # AVmajor(), VminorCombo(), AVminor(), ErrorSummary(),
        # WarningSummary(), ItemType) — confirmed against the OAPI reference.
        # Positional indexing (not a string/numeric-shape heuristic) because
        # Location, PMMArea and PMMRatio are all numeric arrays and would be
        # ambiguous to distinguish by shape alone; this mirrors the fixed
        # ByRef-order convention already used across reader_results.py.
        values = list(raw)
        if len(values) < 14:
            return _empty_table("concrete_design_results", columns)
        frame_names = values[1] if isinstance(values[1], (list, tuple)) else []
        options = values[2] if isinstance(values[2], (list, tuple)) else []
        locations = values[3] if isinstance(values[3], (list, tuple)) else []
        ratios = values[6] if isinstance(values[6], (list, tuple)) else []
        if not frame_names or not ratios:
            return _empty_table("concrete_design_results", columns)

        rows: list[list[Any]] = []
        for i, fname in enumerate(frame_names):
            if i >= len(ratios):
                break
            if frame_name and str(fname).casefold() != frame_name.casefold():
                continue
            is_check_mode = int(options[i]) == 1 if i < len(options) else True
            loc = locations[i] if i < len(locations) else ""
            if is_check_mode:
                ratio = ratios[i]
                status = "OK" if isinstance(ratio, (int, float)) and ratio <= 1.0 else "FAIL"
                ratio_display: Any = f"{ratio:.3f}" if isinstance(ratio, float) else ratio
            else:
                ratio_display = ""
                status = "N/A (modo Diseño)"
            rows.append([fname, ratio_display, "PMM", loc, status])
        if limit:
            rows = rows[:limit]
        table = _table("concrete_design_results", columns, rows)
        if frame_name and rows:
            table["summary_message"] = f"El frame {frame_name} tiene D/C={rows[0][1]} ({rows[0][4]})."
        return table

    def concrete_beam_design_results(self, limit: int | None = None, frame_name: str | None = None) -> dict:
        """Get concrete beam design results (required rebar areas) via
        DesignConcrete.GetSummaryResultsBeam.

        Fase 23: distinta de concrete_design_results (columnas) a propósito
        — SAP2000 no reporta un ratio D/C para vigas, solo áreas de acero
        de refuerzo requeridas (top/bot/corte), así que esta tabla no
        alimenta build_design_ratios ni el modo de color "D/C Diseño" del
        visor 3D (no hay un ratio comparable que mostrar ahí); es solo
        tabla/consulta por chat, igual que se decidió para las columnas en
        modo "Design" en la Fase 19.
        """
        columns = ["Frame", "Location", "Top Area", "Bot Area", "Vmajor Area"]
        design = getattr(self.model, "DesignConcrete", None)
        if design is None:
            return _empty_table("concrete_beam_design_results", columns)
        return self._read_concrete_beam_design_summary(design, columns, frame_name, limit)

    def _read_concrete_beam_design_summary(
        self, design: Any, columns: list[str], frame_name: str | None, limit: int | None
    ) -> dict:
        name = frame_name or "All"
        item_type = 0 if frame_name else 1
        raw = None
        for call in (
            lambda: design.GetSummaryResultsBeam(
                name, 0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], item_type
            ),
            lambda: design.GetSummaryResultsBeam(
                name, 0, [], [], [], [], [], [], [], [], [], [], [], [], [], []
            ),
        ):
            try:
                result = call()
            except Exception:
                continue
            if isinstance(result, (tuple, list)):
                raw = result
                break
        if raw is None:
            return _empty_table("concrete_beam_design_results", columns)

        # Fixed positional layout, matching the declared ByRef order of
        # GetSummaryResultsBeam(Name, NumberItems, FrameName(), Location(),
        # TopCombo(), TopArea(), BotCombo(), BotArea(), VmajorCombo(),
        # VmajorArea(), TLCombo(), TLArea(), TTCombo(), TTArea(),
        # ErrorSummary(), WarningSummary(), ItemType) — confirmed against
        # the OAPI reference. Positional indexing (not a string/numeric-shape
        # heuristic), same reasoning as _read_concrete_column_design_summary:
        # Location/TopArea/BotArea/VmajorArea/TLArea/TTArea are all numeric
        # arrays and would be ambiguous to distinguish by shape alone.
        values = list(raw)
        if len(values) < 14:
            return _empty_table("concrete_beam_design_results", columns)
        frame_names = values[1] if isinstance(values[1], (list, tuple)) else []
        locations = values[2] if isinstance(values[2], (list, tuple)) else []
        top_areas = values[4] if isinstance(values[4], (list, tuple)) else []
        bot_areas = values[6] if isinstance(values[6], (list, tuple)) else []
        vmajor_areas = values[8] if isinstance(values[8], (list, tuple)) else []
        if not frame_names or not top_areas:
            return _empty_table("concrete_beam_design_results", columns)

        rows: list[list[Any]] = []
        for i, fname in enumerate(frame_names):
            if i >= len(top_areas):
                break
            if frame_name and str(fname).casefold() != frame_name.casefold():
                continue
            loc = locations[i] if i < len(locations) else ""
            top = top_areas[i]
            bot = bot_areas[i] if i < len(bot_areas) else ""
            vmajor = vmajor_areas[i] if i < len(vmajor_areas) else ""
            rows.append([
                fname,
                loc,
                f"{top:.4f}" if isinstance(top, float) else top,
                f"{bot:.4f}" if isinstance(bot, float) else bot,
                f"{vmajor:.4f}" if isinstance(vmajor, float) else vmajor,
            ])
        if limit:
            rows = rows[:limit]
        table = _table("concrete_beam_design_results", columns, rows)
        if frame_name and rows:
            table["summary_message"] = (
                f"El frame {frame_name}: área sup.={rows[0][2]}, área inf.={rows[0][3]}."
            )
        return table

