"""Resultados de análisis y diseño bajo demanda (no engordan la instantánea).

Solo lectura. Un caso o combinación es OBLIGATORIO en lo que depende de uno,
y se valida contra la lista real del modelo: el texto del usuario nunca llega
tal cual al filtro del lector. Lo que no se pudo leer o se resumió se dice en
`warnings`; nunca se inventan valores ni unidades.

OJO: leer resultados selecciona casos/combinaciones "para salida" en SAP2000
(`Results.Setup`), un estado de visualización del programa, no del modelo.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import math
from typing import Any

from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_utils import _is_diagnostic_table
from bridge.viewer.sap2000.results_views import (
    AREA_STRESS_COMPONENTS,
    area_stress_unit,
    build_design_ratios_report,
    reduce_area_forces_to_scalar,
)
from bridge.viewer.sap2000.units import UNIT_CODES, length_factor_to_meters


class ResultsError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _units(table: dict) -> dict:
    return dict((table.get('metadata') or {}).get('result_units') or {})


def _notes(table: dict) -> list[str]:
    meta = table.get('metadata') or {}
    out = [f'Selección de resultados: {e}' for e in meta.get('selection_errors', []) or []]
    if meta.get('truncated'):
        out.append('La lectura se truncó: faltan filas.')
    return out


def _reader(model: Any, result_filter: str | None = None) -> SapModelReader:
    return SapModelReader(model, result_filter=result_filter)


def list_cases(model: Any) -> dict:
    filters = _reader(model).available_result_filters()
    cases = [{'value': f['value'], 'name': f['name'], 'kind': f['kind']} for f in filters
             if f.get('kind') in ('case', 'combo')]
    return {'cases': cases}


def _filter_value(model: Any, case: str) -> str:
    """El valor del filtro sale de la lista real del modelo, no del cliente."""
    for item in list_cases(model)['cases']:
        if case in (item['value'], item['name']):
            return item['value']
    raise ResultsError(422, 'unknown_case', f'El caso o combinación {case!r} no existe en el modelo.')


def _require_rows(table: dict, what: str) -> None:
    if _is_diagnostic_table(table) or not table.get('rows'):
        raise ResultsError(409, 'no_results',
                           f'SAP2000 no devolvió {what}: ¿está analizado el modelo y existe ese caso?')


def design_ratios(model: Any) -> dict:
    report = build_design_ratios_report(_reader(model))
    if not report.ratios:
        raise ResultsError(409, 'no_results',
                           'No hay D/C de diseño. Ejecuta el diseño de acero u hormigón en SAP2000. '
                           + ' '.join(report.warnings))
    finite = {k: v for k, v in report.ratios.items() if math.isfinite(v)}
    return {'ratios': finite, 'max': max(finite.values(), default=0.0), 'warnings': list(report.warnings)}


def area_stress(model: Any, case: str, component: str) -> dict:
    if component not in AREA_STRESS_COMPONENTS:
        raise ResultsError(422, 'bad_component', f'Componente desconocido: {component!r}.')
    value = _filter_value(model, case)
    table = _reader(model, value).area_forces(limit=None)
    _require_rows(table, 'esfuerzos de shell')
    values, vmax = reduce_area_forces_to_scalar(table, component)
    if not values:
        raise ResultsError(409, 'no_results', f'La tabla no trae la columna {component}.')
    units = _units(table)
    return {'case': value, 'component': component, 'values': values, 'max': vmax,
            'unit': area_stress_unit(component, units),
            'warnings': ['Promedio de los valores en las esquinas de cada área: vista rápida, no resultado de diseño.']
                        + _notes(table)}


def displacements(model: Any, case: str) -> dict:
    value = _filter_value(model, case)
    table = _reader(model, value).joint_displacements(limit=None)
    _require_rows(table, 'desplazamientos')
    cols = [str(c).strip().casefold() for c in table['columns']]
    try:
        i_obj = cols.index('obj')
        idx = [cols.index(c) for c in ('u1', 'u2', 'u3', 'r1', 'r2', 'r3')]
    except ValueError as exc:
        raise ResultsError(409, 'no_results', 'La tabla de desplazamientos no trae las columnas esperadas.') from exc
    nodes: dict[str, list[float]] = {}
    multi = 0
    for row in table['rows']:
        vals = [_finite(row[i]) for i in idx]
        if any(v is None for v in vals):
            continue
        name = str(row[i_obj]).strip()
        if name in nodes:
            multi += 1
            if math.hypot(*vals[:3]) <= math.hypot(*nodes[name][:3]):
                continue
        nodes[name] = vals
    warnings = _notes(table)
    if multi:
        warnings.append(f'{multi} filas adicionales (varios pasos o máximos/mínimos): por nodo se muestra el de mayor desplazamiento.')
    units = _units(table)
    length = units.get('length', '')
    factor = length_factor_to_meters(units['label']) if units.get('label') in UNIT_CODES else None
    translations = [v[:3] for v in nodes.values()]
    return {'case': value, 'nodes': nodes, 'unit': length, 'toMeters': factor,
            'max': max((math.hypot(*t) for t in translations), default=0.0), 'warnings': warnings}


def frame_forces(model: Any, case: str, frame: str) -> dict:
    value = _filter_value(model, case)
    table = _reader(model, value).frame_forces(limit=None, frame_name=frame)
    _require_rows(table, f'fuerzas de la barra {frame}')
    cols = [str(c).strip().casefold() for c in table['columns']]
    need = ['objsta', 'steptype', 'p', 'v2', 'v3', 't', 'm2', 'm3']
    if any(c not in cols for c in need):
        raise ResultsError(409, 'no_results', 'La tabla de fuerzas no trae las columnas esperadas.')
    ix = {c: cols.index(c) for c in need}
    stations = []
    for row in table['rows']:
        item = {'x': _finite(row[ix['objsta']]), 'step': str(row[ix['steptype']]),
                **{k.upper(): _finite(row[ix[k]]) for k in ('p', 'v2', 'v3', 't', 'm2', 'm3')}}
        if item['x'] is not None:
            stations.append(item)
    stations.sort(key=lambda s: (s['step'], s['x']))
    units = _units(table)
    return {'case': value, 'frame': frame, 'stations': stations, 'units': units,
            'warnings': _notes(table)}
