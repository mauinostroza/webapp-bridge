"""Readable slenderness report from the API result contract; no recalculation."""

from __future__ import annotations

from .base import center_table, shade_table_header

MODULE_ID = 'esbelteces'


def build(doc, data: dict, style_map: dict) -> None:
    rows = data.get('results')
    if rows is None and isinstance(data.get('result'), dict):
        rows = [data['result']]
    if not isinstance(rows, list) or not rows:
        raise ValueError('Se requiere results con resultados del módulo de esbelteces')
    doc.add_heading('Verificación de esbelteces', level=1).style = style_map['heading_1']
    doc.add_paragraph(
        'Dimensiones en mm y esfuerzos en MPa. Se reproducen los resultados y advertencias del cálculo; las verificaciones no evaluadas no se consideran aprobadas.',
        style=style_map['body'],
    )
    for result in rows:
        if not isinstance(result, dict):
            raise ValueError('Cada resultado debe ser un objeto')
        name = result.get('frame_name', 'Elemento')
        section = result.get('section_name', 'Sección sin nombre')
        doc.add_heading(f'{name} — {section}', level=2).style = style_map['heading_2']
        columns = [
            ('Norma seleccionada', 'norma'),
            ('Esbeltez ala', 'lambda_flange'),
            ('Esbeltez alma', 'lambda_web'),
            ('Esbeltez global X', 'lambda_x'),
            ('Esbeltez global Y', 'lambda_y'),
            ('Ala / flexión', 'clasif_flange_flexion'),
            ('Alma / flexión', 'clasif_web_flexion'),
            ('Ala / compresión', 'estado_flange'),
            ('Alma / compresión', 'estado_web'),
        ]
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = 'Verificación'
        table.rows[0].cells[1].text = 'Resultado'
        for label, key in columns:
            value = result.get(key)
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = (
                'No evaluado'
                if value is None
                else (f'{value:.4g}' if isinstance(value, float) else str(value))
            )
        center_table(table)
        shade_table_header(table, 'D9EAF7')
        for warning in result.get('warnings', []):
            doc.add_paragraph(f'Observación: {warning}', style=style_map['body'])
