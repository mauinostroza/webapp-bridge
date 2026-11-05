"""
Resumen legible (texto, no XML) de un conjunto de elementos generados. Se usa
en el modo --dry-run para mostrarle al ingeniero que va a cambiar sin que
tenga que leer XML crudo.
"""

from __future__ import annotations

from docx.oxml.ns import qn

MAX_ROWS_PER_TABLE = 3


def _text_of(element) -> str:
    return "".join(t.text or "" for t in element.iter(qn("w:t")))


def summarize_elements(elements, max_lines: int = 60) -> str:
    lines = []
    for el in elements:
        local_tag = el.tag.split("}")[-1]
        if local_tag == "p":
            text = _text_of(el).strip()
            if text:
                lines.append(text)
        elif local_tag == "tbl":
            rows = el.findall(qn("w:tr"))
            lines.append(f"[tabla de {len(rows)} filas]")
            for row in rows[:MAX_ROWS_PER_TABLE]:
                cells = row.findall(qn("w:tc"))
                cell_texts = [_text_of(c).strip() for c in cells]
                lines.append("  | " + " | ".join(cell_texts))
            if len(rows) > MAX_ROWS_PER_TABLE:
                lines.append(f"  ... ({len(rows) - MAX_ROWS_PER_TABLE} filas mas)")

    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... ({omitted} lineas mas)"]

    return "\n".join(lines)
