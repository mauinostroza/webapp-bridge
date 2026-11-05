"""
Construccion e insercion automatica de anclas (Content Controls w:sdt) en un
.docx real, en la posicion que el ingeniero elige en el Anchor Wizard. El
ingeniero nunca escribe un tag a mano ni abre la pestana Programador de
Word: este modulo arma el bloque OOXML e lo inserta.
"""

from __future__ import annotations

import random

from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement

from backend.report_builder.engine.anchor_locator import REPORT_TAG_PREFIX, find_sdt_by_tag
from backend.report_builder.engine.content_builder import drop_image_relationships
from backend.report_builder.engine.hasher import hash_content

PLACEHOLDER_TEXT = "[Se generara automaticamente en la proxima sincronizacion]"


class AnchorAlreadyExistsError(Exception):
    """Ya existe un Content Control con ese tag en el documento."""


def tag_for_module(module_id: str) -> str:
    return f"{REPORT_TAG_PREFIX}{module_id}:seccion"


def next_available_tag(document, module_id: str) -> str:
    """Devuelve el tag base para module_id si esta libre en el documento;
    si ya esta ocupado (el modulo ya tiene una instancia instalada), prueba
    sufijos ":2", ":3"... hasta encontrar uno libre. Permite instalar el
    mismo modulo mas de una vez, cada instancia con su propio tag."""
    base = tag_for_module(module_id)
    if not find_sdt_by_tag(document.element, base):
        return base
    instance = 2
    while True:
        candidate = f"{base}:{instance}"
        if not find_sdt_by_tag(document.element, candidate):
            return candidate
        instance += 1


def alias_for_module(module_id: str) -> str:
    label = module_id.replace("_", " ").title()
    return f"{label} - GENERADO, NO EDITAR"


def _new_sdt_id() -> str:
    return str(random.randint(1, 2**31 - 1))


def _build_placeholder_elements(placeholder_text: str = PLACEHOLDER_TEXT) -> list:
    """Construye los elementos w:p usados como contenido de un ancla recien
    instalada, todavia sin sincronizar. Se comparte entre build_report_sdt
    (que los inserta) y placeholder_content_hash (que el sync engine usa
    para reconocer este estado)."""
    placeholder_p = OxmlElement("w:p")
    run = OxmlElement("w:r")
    text_el = OxmlElement("w:t")
    text_el.text = placeholder_text
    run.append(text_el)
    placeholder_p.append(run)
    return [placeholder_p]


def placeholder_content_hash() -> str:
    """Hash de content_builder de un ancla recien instalada, nunca
    sincronizada. El sync engine lo usa para distinguir "este ancla nunca
    tuvo contenido real" de "este ancla tiene contenido identico al ya
    generado" - son casos con distinta implicancia para needs_confirmation."""
    return hash_content(_build_placeholder_elements())


def build_report_sdt(tag: str, alias: str, placeholder_text: str = PLACEHOLDER_TEXT):
    """Construye un Content Control de bloque valido, sin insertarlo todavia."""
    sdt = OxmlElement("w:sdt")
    sdt_pr = OxmlElement("w:sdtPr")

    alias_el = OxmlElement("w:alias")
    alias_el.set(qn("w:val"), alias)
    sdt_pr.append(alias_el)

    tag_el = OxmlElement("w:tag")
    tag_el.set(qn("w:val"), tag)
    sdt_pr.append(tag_el)

    id_el = OxmlElement("w:id")
    id_el.set(qn("w:val"), _new_sdt_id())
    sdt_pr.append(id_el)

    sdt.append(sdt_pr)

    sdt_content = OxmlElement("w:sdtContent")
    for el in _build_placeholder_elements(placeholder_text):
        sdt_content.append(el)

    sdt.append(sdt_content)
    return sdt


def install_anchor(document, tag: str, alias: str, insert_after=None, insert_before=None):
    """
    Inserta un nuevo w:sdt en el body de `document`.

    A lo sumo uno de insert_after/insert_before debe venir seteado:
    - insert_after: elemento de referencia, se inserta como su siguiente hermano.
    - insert_before: elemento de referencia, se inserta como su hermano anterior
      (usado por heading_scanner para insertar al final de una seccion completa,
      justo antes del siguiente titulo de igual o mayor jerarquia).
    - Si ambos son None: se inserta al final del documento (antes del
      w:sectPr final, que siempre debe seguir siendo el ultimo hijo del body).
    """
    if find_sdt_by_tag(document.element, tag):
        raise AnchorAlreadyExistsError(f"Ya existe un ancla con tag {tag!r} en el documento")

    sdt = build_report_sdt(tag, alias)
    body = document.element.body

    if insert_before is not None:
        insert_before.addprevious(sdt)
    elif insert_after is not None:
        insert_after.addnext(sdt)
    else:
        sect_pr = body.find(qn("w:sectPr"))
        if sect_pr is not None:
            sect_pr.addprevious(sdt)
        else:
            body.append(sdt)

    return sdt


def install_module_anchor(document, module_id: str, insert_after=None, insert_before=None):
    """Atajo: calcula un tag libre para module_id (permite instalar el mismo
    modulo mas de una vez) e instala el ancla."""
    tag = next_available_tag(document, module_id)
    alias = alias_for_module(module_id)
    sdt = install_anchor(document, tag, alias, insert_after=insert_after, insert_before=insert_before)
    return tag, sdt


def remove_anchor(document, tag: str) -> bool:
    """
    Quita el Content Control (w:sdt) con ese tag, con todo su contenido
    generado. Devuelve False si no se encontro ningun w:sdt con ese tag (no
    es un error - el llamador decide si eso es motivo de aviso), True si se
    quito. Si hay mas de uno (tag duplicado), se quitan todos.
    """
    matches = find_sdt_by_tag(document.element, tag)
    if not matches:
        return False
    for sdt in matches:
        # Si el contenido del ancla tenia imagenes embebidas, liberar sus
        # relaciones antes de quitar el w:sdt del body - de lo contrario la
        # relacion y el ImagePart quedan huerfanos en el paquete .docx
        # (nunca referenciados, nunca eliminados), y se acumulan en cada
        # ciclo eliminar/reinstalar/sincronizar hasta duplicar el peso del
        # archivo y corromperlo. Mismo helper que ya usa sync_engine.py al
        # reemplazar el contenido de un ancla en cada sync.
        drop_image_relationships([sdt], document.part)
        sdt.getparent().remove(sdt)
    return True


def remove_module_anchor(document, tag: str) -> bool:
    """Atajo/alias fino de remove_anchor: recibe el tag exacto de la
    instancia a quitar (module_id ya no identifica una instancia unica,
    puede haber varias del mismo modulo con tags distintos)."""
    return remove_anchor(document, tag)
