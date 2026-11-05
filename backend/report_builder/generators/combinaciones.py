"""
Generador de la seccion "Combinaciones de Carga" a partir del JSON de salida
del programa "Combinaciones" (Exportar > Exportar informacion para reporte
(.docx)...).

Contrato de entrada: generators/schemas/combinaciones.v1.schema.json. Este
modulo no importa nada del programa "Combinaciones" - solo conoce la forma
del JSON que ese programa exporta (`combinaciones/io/report_export.py`,
`build_docx_report_data`): las tablas ASD y LRFD vigentes (mismas columnas
que la hoja Excel) y la lista de estados de carga con su definicion.
"""

from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement

from backend.report_builder.generators.base import (
    add_table_caption,
    autosize_column_widths,
    center_table,
    shade_table_header,
)

MODULE_ID = "combinaciones"

DEFAULT_HEADER_COLOR = "CAEDFB"


def _fmt(value) -> str:
    """Texto de una celda: coeficientes sin basura de coma flotante (1.2000000000000002 → 1.2)."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _es_numerico(columna: list) -> bool:
    valores = [v for v in columna if v not in (None, "")]
    return bool(valores) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valores)


def build(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Combinaciones de Carga", level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "A continuación se presentan las combinaciones de carga (ASD y LRFD) "
        "consideradas en el análisis, junto con los estados de carga que las "
        "componen.",
        style=style_map["body"],
    )

    header_color = (data.get("estilo") or {}).get("color_encabezado", "")
    header_fill = header_color.lstrip("#") if header_color else DEFAULT_HEADER_COLOR

    estados_carga = data.get("estados_carga") or []
    if estados_carga:
        _add_estados_carga(doc, estados_carga, style_map, header_fill)

    tabla_asd = data.get("tabla_asd")
    if tabla_asd and tabla_asd.get("filas"):
        doc.add_heading("Combinaciones ASD", level=2).style = style_map["heading_2"]
        _add_tabla(doc, "Combinaciones de carga — ASD", tabla_asd, style_map, header_fill)

    tabla_lrfd = data.get("tabla_lrfd")
    if tabla_lrfd and tabla_lrfd.get("filas"):
        doc.add_heading("Combinaciones LRFD", level=2).style = style_map["heading_2"]
        _add_tabla(doc, "Combinaciones de carga — LRFD", tabla_lrfd, style_map, header_fill)


def _add_estados_carga(doc, estados_carga: list, style_map: dict, header_fill: str) -> None:
    doc.add_heading("Estados de Carga", level=2).style = style_map["heading_2"]
    add_table_caption(doc, "Estados de carga considerados", style_map)

    column_headers = ("Estado de carga", "Definición")
    table = doc.add_table(rows=1, cols=len(column_headers))
    table.style = style_map["table_reference"]
    headers = table.rows[0].cells
    for i, h in enumerate(column_headers):
        headers[i].text = h
    shade_table_header(table, fill=header_fill)

    for estado in estados_carga:
        cells = table.add_row().cells
        cells[0].text = _fmt(estado.get("nombre"))
        cells[1].text = _fmt(estado.get("definicion"))

    autosize_column_widths(
        table,
        [
            [column_headers[0]] + [_fmt(e.get("nombre")) for e in estados_carga],
            [column_headers[1]] + [_fmt(e.get("definicion")) for e in estados_carga],
        ],
    )
    center_table(table)


def _add_tabla(doc, caption: str, tabla: dict, style_map: dict, header_fill: str):
    columnas = tabla.get("columnas") or []
    filas = tabla.get("filas") or []
    if not columnas:
        return None

    add_table_caption(doc, caption, style_map)
    table = doc.add_table(rows=1, cols=len(columnas))
    table.style = style_map["table_reference"]
    headers = table.rows[0].cells
    for i, h in enumerate(columnas):
        headers[i].text = str(h)
    shade_table_header(table, fill=header_fill)

    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:tblHeader"))  # el encabezado se repite en cada página

    numericas = {i for i in range(len(columnas)) if _es_numerico([f[i] for f in filas if i < len(f)])}
    for fila in filas:
        cells = table.add_row().cells
        for i, val in enumerate(fila):
            if i < len(cells):
                cells[i].text = _fmt(val)
                if i in numericas:
                    cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    combinacion_indices = {i for i, h in enumerate(columnas) if str(h).strip().lower() == "combinación"}
    autosize_column_widths(
        table,
        [
            [str(columnas[i])] + [_fmt(fila[i]) for fila in filas if i < len(fila)]
            for i in range(len(columnas))
        ],
        emphasize_indices=combinacion_indices,
    )
    center_table(table)
    return table
