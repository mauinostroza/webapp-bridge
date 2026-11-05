"""Transport snapshot from existing SAP readers, independent of UI/rendering."""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from datetime import datetime, timezone
import math
from time import perf_counter
from typing import Any
from uuid import uuid4

from bridge.viewer.core.combinations import expand_combinations
from bridge.viewer.core.load_direction import load_direction_vector
from bridge.viewer.sap2000.reader_metadata import load_combination_definitions
from bridge.viewer.sap2000.units import UNIT_CODES, extract_result_units, unit_label
from bridge.viewer.core.sections import area_property, read_sections

MAX_LOAD_ENTRIES = 200_000
_AXES = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
_FORCE_COMPONENTS = ('Fx', 'Fy', 'Fz')
_MOMENT_COMPONENTS = ('Mx', 'My', 'Mz')


def _number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Valor no finito en datos SAP2000.')
    return number


def _groups(value: Any) -> list[str]:
    return [x.strip() for x in str(value or '').split(';') if x.strip()]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, '0', 'No', 'False', 'false'):
        return False
    if value in (1, '1', 'Yes', 'True', 'true'):
        return True
    raise ValueError(f'Restricción no interpretable: {value!r}')


def build_snapshot(reader: Any, source: str, model_name: str, unit_system: str) -> dict:
    """All reader/COM calls execute on the caller's dedicated STA thread.

    Geometry and section dimensions are meters; loads retain source force
    units. Loads come from every pattern (no pattern filter) and are emitted
    twice: legacy entries without ``component`` (one per load and sample,
    unchanged) and one entry per non-null global component with ``component``
    Fx..Mz, ``direction`` = unit axis and signed ``value``. Combinations list
    only load cases (``pattern``), nested ones expanded by factor product.
    Snapshot construction never writes units or results. The only write to the
    selection is the temporary, always-restored display selection of load
    patterns made by the batch load readers (``all_load_patterns_displayed``).
    """
    started = perf_counter()
    units = extract_result_units(unit_system)
    if unit_system not in UNIT_CODES or not units:
        raise ValueError('Unidades SAP2000 desconocidas; no se puede afirmar que la geometría esté en metros.')
    if source not in ('demo', 'sap2000'):
        raise ValueError('Fuente de datos no soportada.')
    model = reader.model
    before_filename = str(model.GetModelFilename())
    before_units = unit_label(model.GetPresentUnits())
    if before_units != unit_system:
        raise ValueError('Las unidades SAP2000 cambiaron antes de la lectura; vuelve a recargar.')
    warnings: list[str] = []

    def rows(method: str) -> list:
        try:
            table = getattr(reader, method)(limit=None)
        except Exception as exc:
            warnings.append(f'{method}: {exc}')
            return []
        warnings.extend(f'{method}: {e}' for e in table.get('metadata', {}).get('errors', []) or [])
        return [row for row in table.get('rows', []) if row and str(row[0]).strip() and str(row[0]).lower() not in ('total', '(sin datos)')]

    joint_table, frame_table = reader.joint_coordinates_and_frame_geometry(limit=None)
    geometry_errors = [str(e) for t in (joint_table, frame_table) for e in t.get('metadata', {}).get('errors', []) or []]
    if not joint_table.get('rows') and geometry_errors:
        raise ValueError('No se pudo leer la geometría SAP2000: ' + '; '.join(geometry_errors[:5]))
    warnings.extend(geometry_errors)
    ids: list[str] = []
    seen_ids: set[str] = set()
    positions: list[float] = []
    groups = []
    skipped = {'nodes': 0, 'frames': 0, 'areas': 0}
    skipped_detail: list[str] = []
    node_total = 0

    def skip(kind: str, text: str) -> None:
        skipped[kind] += 1
        if len(skipped_detail) < 50:
            skipped_detail.append(text)

    for row in joint_table.get('rows', []):
        if not row or not str(row[0]).strip():
            continue
        name = str(row[0])
        if name in seen_ids:
            raise ValueError(f'Identificador de nodo duplicado: {name}')
        seen_ids.add(name)
        node_total += 1
        try:
            if len(row[1:4]) != 3:
                raise ValueError('coordenadas incompletas')
            xyz = [_number(x) for x in row[1:4]]
        except (ValueError, TypeError) as exc:
            skip('nodes', f'Nodo {name}: {exc}; omitido.')
            continue
        ids.append(name)
        positions.extend(xyz)
        groups.append(_groups(row[4] if len(row) > 4 else ''))
    if node_total and not ids:
        raise ValueError('Ningún nodo tiene coordenadas finitas; no hay geometría que mostrar.')
    node_index = {name: i for i, name in enumerate(ids)}
    assigned = {str(r[0]): str(r[1]) for r in rows('frame_assigned_section') if len(r) >= 2}
    section_stats: dict = {}
    local_axes = {str(r[0]): r[1:3] for r in rows('frame_local_axes') if len(r) >= 3}
    section_names = set(assigned.values()) - {''}
    if source == 'demo':
        table = reader.frame_section_properties(limit=None)
        sections = table.get('sections', {})
        warnings.extend(str(x) for x in table.get('metadata', {}).get('errors', []) or [])
    else:
        sections = read_sections(model, section_names, unit_system, warnings, section_stats)
    frames = []
    frame_index = {}
    frame_total = 0
    for row in frame_table.get('rows', []):
        if len(row) < 4 or not str(row[1]).strip():
            continue
        name, i, j = map(str, row[1:4])
        if name in frame_index:
            raise ValueError(f'Identificador de barra duplicado: {name}')
        frame_total += 1
        if i not in node_index or j not in node_index:
            skip('frames', f'Barra {name}: nodos extremos no disponibles; omitida.')
            continue
        start, end = node_index[i], node_index[j]
        p, q = positions[start*3:start*3+3], positions[end*3:end*3+3]
        axis = [b-a for a, b in zip(p, q)]
        if math.hypot(*axis) <= 1e-12:
            warnings.append(f'Barra {name}: longitud cero; omitida.')
            continue
        angle, advanced = local_axes.get(name, (None, True))
        angle = None if advanced else angle
        axis2 = axis3 = None
        try:
            axis2 = list(load_direction_vector(2, axis, angle, 'Local'))
            axis3 = list(load_direction_vector(3, axis, angle, 'Local'))
        except ValueError:
            warnings.append(f'Barra {name}: orientación local no disponible; se muestra alámbrica.')
        section = assigned.get(name, '')
        frame = {'id': name, 'start': start, 'end': end, 'section': section,
                 'material': str(sections.get(section, {}).get('material', '')),
                 'groups': _groups(row[10] if len(row) > 10 else ''), 'axis2': axis2, 'axis3': axis3}
        frames.append(frame)
        frame_index[name] = (frame, p, axis, angle)
    areas = []
    area_cache: dict = {}
    for row in rows('area_geometry'):
        if len(row) < 3:
            continue
        name = str(row[0])
        points = _groups(row[2])
        if any(x not in node_index for x in points) or len(set(points)) < 3:
            skip('areas', f'Área {name}: conectividad incompleta; omitida.')
            continue
        section, thickness = area_property(model, name, unit_system, area_cache)
        if source == 'demo' and section:
            thickness = reader.area_shell_thickness(limit=None).get('thicknesses', {}).get(section)
        if thickness is None:
            warnings.append(f'Área {name}: espesor no disponible; superficie media sin espesor.')
        areas.append({'id': name, 'nodes': [node_index[x] for x in points], 'section': section, 'thickness': thickness})
    supports = []
    for row in rows('joint_restraints'):
        if len(row) >= 7 and str(row[0]) in node_index:
            dofs = [_bool(x) for x in row[1:7]]
            if any(dofs):
                supports.append({'node': node_index[str(row[0])], 'dofs': dofs})
    loads: list[dict] = []
    loads_truncated = False

    def add_load(object_type, name, pattern, point, direction, value, kind, component=None, unit=None):
        """Sin ``component`` añade la entrada antigua (una por carga y muestra); con él, una entrada por componente."""
        nonlocal loads_truncated
        if abs(value) <= 1e-12:
            return
        if len(loads) >= MAX_LOAD_ENTRIES:
            loads_truncated = True
            return
        entry = {'id': f'load:{len(loads)}', 'objectType': object_type, 'objectId': name,
                 'pattern': str(pattern), 'position': list(point), 'direction': list(direction),
                 'value': value, 'kind': kind,
                 'unit': unit if unit is not None else units['force'] + ('/'+units['length'] if kind == 'distributed' else '')}
        if component is not None:
            entry['component'] = component
        loads.append(entry)

    def component_unit(distributed: bool, moment: bool) -> str:
        if distributed:
            return units['force'] if moment else units['force'] + '/' + units['length']
        return f"{units['force']}·{units['length']}" if moment else units['force']

    for method, distributed in (('frame_loads_list', True), ('frame_point_loads_list', False)):
        for row in rows(method):
            name = str(row[0])
            try:
                required = 8 if distributed else 6
                if len(row) < required:
                    raise ValueError('Asignación incompleta.')
                if name not in frame_index:
                    raise ValueError('Barra de carga no disponible.')
                kind_text = str(row[2]).strip().casefold()
                if kind_text in ('force', '1', '1.0'):
                    moment = False
                elif kind_text in ('moment', '2', '2.0'):
                    moment = True
                else:
                    raise ValueError(f'tipo de carga no interpretable: {row[2]!r}.')
                component_names = _MOMENT_COMPONENTS if moment else _FORCE_COMPONENTS
                kind = 'distributed' if distributed else 'point'
                _, p, axis, angle = frame_index[name]
                # Reduced historic tables omit CSys. CSI local codes imply
                # Local, global/projected/gravity imply Global; named systems
                # are explicitly refused by the shared direction resolver.
                try:
                    code = int(row[3])
                except (ValueError, TypeError):
                    code = 0
                csys = row[required] if len(row) > required and row[required] else ('Local' if 1 <= code <= 3 else 'Global')
                direction = load_direction_vector(row[3], axis, angle, csys)
                if distributed:
                    d1, d2, v1, v2 = map(_number, row[4:8])
                    if not 0 <= d1 <= d2 <= 1:
                        raise ValueError('Distancias relativas fuera de 0–1 o invertidas.')
                    count = 7 if d2 > d1 else 1
                    samples = [(d1+(d2-d1)*t/max(1,count-1), v1+(v2-v1)*t/max(1,count-1)) for t in range(count)]
                else:
                    dist, value = map(_number, row[4:6])
                    if not 0 <= dist <= 1:
                        raise ValueError('Distancia relativa fuera de 0–1.')
                    samples = [(dist, value)]
                for dist, value in samples:
                    point = [a+dist*b for a,b in zip(p,axis)]
                    if not moment:
                        signed = [x*(1 if value >= 0 else -1) for x in direction]
                        add_load('frame', name, row[1], point, signed, value, kind)
                    for index, factor in enumerate(direction):
                        add_load('frame', name, row[1], point, _AXES[index], value*factor, kind,
                                 component=component_names[index], unit=component_unit(distributed, moment))
            except (ValueError, TypeError, OverflowError) as exc:
                warnings.append(f'{method} {name}: {exc}')
    for row in rows('joint_loads_list'):
        name = str(row[0])
        try:
            if len(row) < 8 or name not in node_index:
                raise ValueError('Carga nodal incompleta o nodo no disponible.')
            if len(row) > 8 and str(row[8]).strip().casefold() != 'global':
                raise ValueError('Sistema de coordenadas nodal no soportado.')
            forces = list(map(_number, row[2:8]))
            n = node_index[name]
            point = positions[n*3:n*3+3]
            force = forces[:3]
            magnitude = math.hypot(*force)
            if magnitude > 1e-12:
                add_load('joint', name, row[1], point, [x/magnitude for x in force], magnitude, 'point')
            for k, value in enumerate(forces):
                moment = k >= 3
                add_load('joint', name, row[1], point, _AXES[k % 3], value, 'point',
                         component=(_MOMENT_COMPONENTS if moment else _FORCE_COMPONENTS)[k % 3],
                         unit=component_unit(False, moment))
        except (ValueError, TypeError, OverflowError) as exc:
            warnings.append(f'joint_loads_list {name}: {exc}')
    if loads_truncated:
        warnings.append(f'Cargas: se muestran {MAX_LOAD_ENTRIES} entradas como máximo (loadsTruncated).')
    combination_definitions = load_combination_definitions(model, warnings)
    combinations, combinations_truncated, combination_warnings = expand_combinations(combination_definitions)
    warnings.extend(combination_warnings)
    patterns = {str(r[0]) for r in rows('load_patterns')}
    patterns.update(x['pattern'] for x in loads)
    if unit_label(model.GetPresentUnits()) != before_units:
        raise ValueError('Las unidades SAP2000 cambiaron durante la lectura; snapshot descartado.')
    if str(model.GetModelFilename()) != before_filename:
        raise ValueError('El modelo SAP2000 cambió durante la lectura; snapshot descartado.')
    totals = {'nodes': node_total, 'frames': frame_total, 'areas': len(areas) + skipped['areas']}
    labels = {'nodes': 'nodos', 'frames': 'barras', 'areas': 'áreas'}
    summary = [f'{skipped[k]} de {totals[k]} {labels[k]} omitidas (datos no válidos de SAP2000).'
               for k in ('nodes', 'frames', 'areas') if skipped[k]]
    warnings = summary + skipped_detail + warnings
    return {'schemaVersion': 1, 'id': uuid4().hex, 'source': source, 'modelName': model_name,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'units': {'geometry': 'm', 'force': units['force'], 'source': unit_system},
            'nodes': {'ids': ids, 'positions': positions, 'groups': groups}, 'frames': frames,
            'sections': sections, 'areas': areas, 'supports': supports, 'loads': loads,
            'loadsTruncated': loads_truncated, 'combinations': combinations,
            'combinationsTruncated': combinations_truncated,
            'patterns': sorted(patterns), 'warnings': list(dict.fromkeys(warnings)),
            'stats': {'readMs': round((perf_counter()-started)*1000, 2),
                      'skippedNodes': skipped['nodes'], 'skippedFrames': skipped['frames'],
                      'skippedAreas': skipped['areas'], **section_stats}}
