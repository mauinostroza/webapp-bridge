"""
Contrato que debe cumplir cada generador de contenido de report_builder.

Un generador es una funcion `build(doc, data, style_map)` que agrega
parrafos/tablas a `doc` (un docx.Document en blanco, ver
engine/content_builder.py) a partir del JSON `data` del modulo, usando
unicamente los nombres de estilo declarados en `style_map` - nunca un nombre
de estilo literal hardcodeado, para que el motor pueda validar de antemano
que el template real los tiene. No hace falta heredar de nada: cualquier
funcion con esa firma sirve como generador.
"""

from __future__ import annotations

import math
from pathlib import Path

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, RGBColor

REQUIRED_STYLE_KEYS = (
    "heading_1",
    "heading_2",
    "heading_3",
    "body",
    "caption",
    "table_reference",
    "table_results",
    "bullet_list",
)

TABLE_HEADER_FILL = "CAEDFB"


def _run_with_text(text: str) -> OxmlElement:
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    return run


def _field_run(instr: str, cached_text: str) -> OxmlElement:
    """Campo `fldSimple` (STYLEREF/SEQ) con un valor cacheado - Word lo
    recalcula solo al abrir el documento (F9 / "Actualizar campos"), igual
    que cualquier TOC generado fuera de Word."""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rPr.append(OxmlElement("w:noProof"))
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = cached_text
    run.append(t)
    fld.append(run)
    return fld


def add_table_caption(doc, text: str, style_map: dict, kind: str = "Tabla", container=None) -> None:
    """Parrafo de caption con la misma estructura de campos de Word que usan
    las memorias de calculo reales de la empresa: "Tabla 4-1. <texto>."
    (o "Figura N-M." con kind="Figura"), numerado con STYLEREF (numero de
    seccion Heading 1 vigente) + SEQ (contador reiniciado por seccion).
    Se inserta antes de la tabla/figura que describe, con keepNext activo
    para que no quede separado de ella al paginar (para figuras, va
    despues de la imagen - ver convencion en README.md).

    `container` (opcional) es donde se agrega el parrafo - por defecto
    `doc`, pero puede ser una celda de tabla (`cell.add_paragraph`) para
    captions dentro de una grilla de imagenes."""
    paragraph = (container or doc).add_paragraph(style=style_map["caption"])
    paragraph.paragraph_format.keep_with_next = True

    p = paragraph._p
    p.append(_run_with_text(f"{kind} "))
    p.append(_field_run(" STYLEREF 1 \\s ", "1"))
    hyphen_run = OxmlElement("w:r")
    hyphen_run.append(OxmlElement("w:noBreakHyphen"))
    p.append(hyphen_run)
    p.append(_field_run(f" SEQ {kind} \\* ARABIC \\s 1 ", "1"))
    p.append(_run_with_text(f". {text}."))


def shade_cell(cell, fill: str) -> None:
    """Fondo de color solido en una celda de tabla (`w:shd`)."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def shade_row(row, fill: str) -> None:
    """Fondo de color en todas las celdas de una fila."""
    for cell in row.cells:
        shade_cell(cell, fill)


def shade_table_header(table, fill: str = TABLE_HEADER_FILL) -> None:
    """Fondo de color + negrita en la fila de encabezado, igual al patron
    de las memorias de calculo reales de la empresa (ver EJEMPLO.docx)."""
    shade_row(table.rows[0], fill)
    for cell in table.rows[0].cells:
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True


def lighten_hex(hex_color: str, factor: float = 0.75) -> str:
    """Aclara un color hex hacia blanco en `factor` (0 = sin cambio,
    1 = blanco). Uso: fondos de fila con colores de severidad sin quedar
    demasiado saturados/planos al imprimir."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * factor)
    g = round(g + (255 - g) * factor)
    b = round(b + (255 - b) * factor)
    return f"{r:02X}{g:02X}{b:02X}"


def ratio_fill_color(ratio, escala_ratio) -> str | None:
    """Color de fondo (hex, ya aclarado) para una fila de 'filas_criticas'
    segun su `ratio`, usando los 4 cortes documentados: <0.7, <0.9, <=1.0,
    >1.0. Devuelve None si `ratio` es None (no colorear) o si no hay
    `escala_ratio` disponible (4 colores)."""
    if ratio is None or not escala_ratio or len(escala_ratio) < 4:
        return None
    if ratio < 0.7:
        base = escala_ratio[0]
    elif ratio < 0.9:
        base = escala_ratio[1]
    elif ratio <= 1.0:
        base = escala_ratio[2]
    else:
        base = escala_ratio[3]
    return lighten_hex(base)


def set_run_color(run, hex_color: str) -> None:
    """Color de fuente de un run a partir de un hex (sin '#')."""
    run.font.color.rgb = RGBColor.from_string(hex_color.lstrip("#"))


def set_column_widths(table, widths_inches: list) -> None:
    """Fija el ancho de cada columna en pulgadas. Word suele ignorar
    `table.columns[i].width` solo si la tabla sigue en autofit, asi que
    se desactiva autofit y ademas se fija el ancho en cada celda de cada
    fila (no solo en la fila de encabezado), que es lo que Word realmente
    respeta al abrir el documento."""
    table.autofit = False
    table.allow_autofit = False
    for i, width in enumerate(widths_inches):
        table.columns[i].width = Inches(width)
        for row in table.rows:
            row.cells[i].width = Inches(width)


def autosize_column_widths(
    table,
    column_texts: list,
    *,
    min_width_in: float = 0.5,
    char_width_in: float = 0.1,
    padding_in: float = 0.2,
    emphasize_indices: set | None = None,
    emphasize_padding_in: float = 0.3,
) -> None:
    """Ajusta el ancho de cada columna al contenido mas largo que tiene
    (encabezado incluido), en vez de dejarlas repartidas en partes iguales
    (comportamiento default de `doc.add_table()` - ver `set_column_widths`).

    python-docx no puede medir el ancho real que Word va a renderizar para
    un texto (dependeria de la fuente, el tamano, kerning, etc.) - esto
    aproxima con un ancho fijo por caracter, suficiente para que una
    columna de valores cortos ("D", "1.4") no quede tan ancha como una de
    texto largo, sin tener que fijar anchos a mano por tabla.

    `column_texts` es una lista con un elemento por columna, cada uno una
    lista de todos los textos que va a tener esa columna (encabezado +
    cada fila, ya formateados). Reusa `set_column_widths` para fijarlos
    (mismo motivo: Word solo respeta un ancho explicito con autofit
    desactivado).

    `emphasize_indices` (opcional) marca columnas que reciben un margen
    extra (`emphasize_padding_in`) ademas del calculo por caracter - util
    para una columna de formulas ("1.2D+1.6L+0.5Lr") donde el ancho por
    caracter estimado queda mas ajustado que el resto."""
    emphasize_indices = emphasize_indices or set()
    widths = []
    for i, texts in enumerate(column_texts):
        width = max(min_width_in, max((len(str(t)) for t in texts), default=0) * char_width_in + padding_in)
        if i in emphasize_indices:
            width += emphasize_padding_in
        widths.append(width)
    set_column_widths(table, widths)


def center_table(table) -> None:
    """Centra la tabla en la pagina (alineacion horizontal de la tabla
    completa, no del texto dentro de las celdas)."""
    table.alignment = WD_TABLE_ALIGNMENT.CENTER


def add_key_value_table(doc, rows, style_map: dict, headers=("Parámetro", "Valor")):
    """Tabla generica de 2 columnas (parametro/valor), reutilizada por varios generadores."""
    table = doc.add_table(rows=1, cols=2)
    table.style = style_map["table_reference"]
    header_cells = table.rows[0].cells
    header_cells[0].text, header_cells[1].text = headers
    shade_table_header(table)
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = str(label)
        cells[1].text = str(value)
    return table


def add_results_table(
    doc,
    headers: list,
    rows: list,
    style_map: dict,
    widths: list | None = None,
    numeric_cols: tuple = (),
    status_col: int | None = None,
    status_colors: dict | None = None,
):
    """Tabla de resultados con el estilo comun de los informes: encabezado
    sombreado y repetido en cada pagina, celdas numericas alineadas a la
    derecha, columna de estado (Cumple / No cumple) centrada y, si se dan
    `status_colors` ({texto: hex}), con el texto coloreado. `rows` ya viene
    formateado a texto (ver generators/formato.py)."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = style_map["table_reference"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = str(text)
    shade_table_header(table)
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:tblHeader"))
    for fila in rows:
        cells = table.add_row().cells
        for i, valor in enumerate(fila):
            cells[i].text = str(valor)
            par = cells[i].paragraphs[0]
            if i in numeric_cols:
                par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if i == status_col:
                par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                color = (status_colors or {}).get(str(valor))
                if color:
                    for run in par.runs:
                        set_run_color(run, color)
    for i in numeric_cols:
        table.rows[0].cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if status_col is not None:
        table.rows[0].cells[status_col].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if widths:
        set_column_widths(table, widths)
    center_table(table)
    return table


def resolve_image_path(json_dir, ruta: str) -> str | None:
    """Resuelve `ruta` (del JSON de datos de un generador, que puede venir
    de un origen no confiable) relativa a `json_dir` de forma segura.

    `Path(json_dir) / ruta` por sí solo NO es seguro: si `ruta` ya es
    absoluta, pathlib descarta silenciosamente `json_dir` y usa la ruta
    absoluta tal cual (comportamiento estándar de `/` con un operando
    absoluto a la derecha) - un JSON de un tercero podría así hacer que el
    reporte incluya cualquier imagen local accesible al proceso, o un
    `../../..` que escape del directorio del proyecto. Aquí se rechaza
    cualquier ruta que no quede efectivamente dentro de `json_dir` una vez
    resuelta, devolviendo `None` (que los llamadores ya tratan igual que
    "imagen faltante": se omite en silencio, no se aborta el capítulo)."""
    if not ruta:
        return None
    if not json_dir:
        return ruta

    base = Path(json_dir).resolve()
    candidate = (base / ruta).resolve()
    if candidate != base and base not in candidate.parents:
        return None
    return str(candidate)


def add_spacer(doc, space_after_in: float = 0.15) -> None:
    """Parrafo vacio usado como separador vertical entre bloques de
    contenido (ej. entre una tabla de resultados y la grilla de imagenes
    que la sigue), para que no queden pegados visualmente."""
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Inches(space_after_in)


def add_centered_image(doc, image_path, style_map: dict, caption: str | None = None, width_in: float = 6.0):
    """Imagen unica centrada (no en grilla), con caption "Figura N-M."
    opcional debajo. Uso: graficos de un solo modulo (ej. el espectro de
    diseno), a diferencia de `add_image_grid` que arma varias imagenes con
    metadata por imagen (fundacion/combinacion/valor).

    Si `image_path` no existe en disco, no agrega nada y devuelve None (una
    imagen faltante no debe tirar toda la sincronizacion del capitulo)."""
    if not Path(image_path).is_file():
        return None

    paragraph = doc.add_paragraph()
    paragraph.alignment = 1  # WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    run.add_picture(str(image_path), width=Inches(width_in))

    if caption:
        paragraph.paragraph_format.keep_with_next = True
        add_table_caption(doc, caption, style_map, kind="Figura")
    return paragraph


def add_image_grid(doc, images: list, style_map: dict, cols: int = 2, width_in: float = 2.8):
    """Grilla de imagenes (`cols` columnas) con caption "Figura N-M." debajo
    de cada una, para las imagenes de diagrama de un modulo (`imagenes` en
    el JSON de resumen_reporte). Cada entrada de `images` es un dict con
    `ruta` (ruta YA resuelta a un archivo absoluto), `fundacion`,
    `combinacion`, `valor` y opcionalmente `alto_contraste` (bool).

    Las entradas cuya `ruta` no exista en disco se omiten en silencio - una
    imagen faltante no debe tirar toda la sincronizacion del capitulo. Si
    ninguna imagen es valida, no agrega nada y devuelve None."""
    validas = [img for img in images if Path(img["ruta"]).is_file()]
    if not validas:
        return None

    n_rows = math.ceil(len(validas) / cols)
    table = doc.add_table(rows=n_rows, cols=cols)
    table.style = style_map["table_reference"]
    table.autofit = False

    for i, img in enumerate(validas):
        row, col = divmod(i, cols)
        cell = table.rows[row].cells[col]
        image_paragraph = cell.paragraphs[0]
        image_paragraph.paragraph_format.keep_with_next = True
        run = image_paragraph.add_run()
        run.add_picture(str(img["ruta"]), width=Inches(width_in))

        caption_texto = f"{img['fundacion']} — {img['combinacion']} — {img['valor']}"
        if img.get("alto_contraste"):
            caption_texto += " (alto contraste)"
        add_table_caption(doc, caption_texto, style_map, kind="Figura", container=cell)
        cell.paragraphs[-1].paragraph_format.keep_with_next = False

    return table
