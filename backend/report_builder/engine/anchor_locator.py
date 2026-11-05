"""
Localizacion de Content Controls (w:sdt) por su w:tag dentro de un documento
Word ya cargado con python-docx.

Sin heuristica de texto: la unica fuente de verdad es el valor exacto de
w:tag/@w:val. Los Content Controls que este subprograma administra siempre
llevan el prefijo REPORT_TAG_PREFIX ("report:") para poder distinguirlos de
los que ya trae el template (portada, TOC, etc.) sin tocarlos.
"""

from __future__ import annotations

from docx.oxml.ns import qn

REPORT_TAG_PREFIX = "report:"


def iter_sdt(document_element):
    """Todos los w:sdt del documento, a cualquier profundidad."""
    return document_element.findall(f".//{qn('w:sdt')}")


def get_tag(sdt_element) -> str | None:
    """Valor de w:tag/@w:val de un w:sdt, o None si no tiene tag."""
    sdt_pr = sdt_element.find(qn("w:sdtPr"))
    if sdt_pr is None:
        return None
    tag_el = sdt_pr.find(qn("w:tag"))
    if tag_el is None:
        return None
    return tag_el.get(qn("w:val"))


def get_sdt_content(sdt_element):
    """El w:sdtContent de un w:sdt (contenedor de sus parrafos/tablas)."""
    return sdt_element.find(qn("w:sdtContent"))


def find_all_sdt_tags(document_element) -> set[str]:
    """Todos los tags presentes en el documento, de cualquier prefijo."""
    tags = set()
    for sdt in iter_sdt(document_element):
        tag = get_tag(sdt)
        if tag is not None:
            tags.add(tag)
    return tags


def find_all_report_tags(document_element) -> set[str]:
    """Solo los tags administrados por report_builder (prefijo report:)."""
    return {tag for tag in find_all_sdt_tags(document_element) if tag.startswith(REPORT_TAG_PREFIX)}


def find_sdt_by_tag(document_element, tag_value: str) -> list:
    """
    Todos los w:sdt cuyo tag == tag_value.

    Longitud esperada == 1. 0 => ancla no encontrada, >1 => ancla duplicada
    (ambiguedad de destino). Ambos casos los resuelve el caller (sync_engine),
    este localizador no decide nada por su cuenta.
    """
    return [sdt for sdt in iter_sdt(document_element) if get_tag(sdt) == tag_value]
