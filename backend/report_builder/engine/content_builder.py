"""
Construccion del contenido generado por un modulo, listo para transplantar
dentro de un ancla real.

El "lienzo" de trabajo se arma clonando el documento real objetivo (mismos
estilos, cuerpo vacio) en vez de partir de un Document() en blanco: los
generadores usan nombres de estilo propios del template real (Parrafo,
Table Grid, Normal Table, etc.) que no existen en la plantilla por defecto
de python-docx.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement


def blank_canvas_from(docx_path: str | Path):
    """Documento con los mismos estilos que docx_path pero cuerpo vacio."""
    document = docx.Document(str(docx_path))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    return document


def _clear_canvas_body(canvas) -> None:
    body = canvas.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def build_content_elements(generator_fn, data: dict, style_map: dict, canvas) -> list:
    """
    Ejecuta `generator_fn(doc, data, style_map)` sobre `canvas` (ver
    `blank_canvas_from`) y devuelve una lista de elementos XML (deepcopy,
    listos para insertar en cualquier otro documento) sin el w:sectPr final
    que python-docx agrega siempre al final del body.

    El llamador es responsable de construir `canvas` una sola vez (por
    ejemplo, una vez por corrida de sync) y reutilizarlo para varios modulos:
    este helper siempre lo deja vacio al terminar (incluso si el generador
    lanza una excepcion), listo para el siguiente modulo, sin volver a leer
    el .docx real desde disco en cada llamada.
    """
    try:
        generator_fn(canvas, data, style_map)
        body = canvas.element.body
        return [deepcopy(child) for child in body if child.tag != qn("w:sectPr")]
    finally:
        _clear_canvas_body(canvas)


class HeadingLevelError(Exception):
    """El template no tiene el estilo Heading N necesario para anidar el
    contenido al nivel pedido (ver shift_heading_levels)."""


def shift_heading_levels(elements: list, canvas, offset: int) -> None:
    """Sube el nivel de cada parrafo Heading 1/2/3 de `elements` en
    `offset` niveles (Heading 1 -> Heading (1+offset), etc.), reescribiendo
    w:pStyle/@w:val al styleId real de "Heading N" en `canvas` (mismos
    estilos que el documento real, ver blank_canvas_from). No-op si offset
    es 0. Lanza HeadingLevelError si el template no tiene definido el
    estilo "Heading N" necesario (p. ej. offset empuja a Heading 4 y el
    template no lo tiene) - el llamador (sync_engine._sync_module) ya
    convierte cualquier excepcion de este paso en un STATUS_ERROR por
    modulo, sin abortar el resto de la sincronizacion."""
    if not offset:
        return
    # Los generadores solo usan Heading 1-3 (unicos definidos en
    # REQUIRED_STYLES) - basta mapear esos tres styleId a su nivel.
    level_by_style_id = {}
    for level in (1, 2, 3):
        level_by_style_id[canvas.styles[f"Heading {level}"].style_id] = level

    def _target_style_id(new_level: int) -> str:
        name = f"Heading {new_level}"
        try:
            return canvas.styles[name].style_id
        except KeyError as exc:
            raise HeadingLevelError(
                f"El documento no tiene el estilo '{name}' - no se puede anidar "
                f"como subcapitulo (el contenido generado necesita nivel {new_level})."
            ) from exc

    for el in elements:
        for p in el.iter(qn("w:p")):
            pStyle_el = p.find(qn("w:pPr") + "/" + qn("w:pStyle"))
            if pStyle_el is None:
                continue
            level = level_by_style_id.get(pStyle_el.get(qn("w:val")))
            if level is None:
                continue
            pStyle_el.set(qn("w:val"), _target_style_id(level + offset))


def set_title_heading_text(elements: list, canvas, text: str) -> None:
    """Reemplaza el texto del PRIMER parrafo Heading 1/2/3 de `elements` por
    `text`, sin tocar su nivel ni el resto del contenido. Cada generador
    escribe ese primer heading como su propio titulo hardcodeado (ver p. ej.
    generators/fundaciones.py: `doc.add_heading("Verificacion de
    Fundaciones", level=1)`) - esto lo reemplaza por el nombre de modulo
    para mostrar (engine.generator_registry.MODULE_DISPLAY_NAMES), para que
    el titulo del ancla en el documento sea siempre consistente con el
    nombre del modulo que el usuario ve en el resto de la UI, sin tener que
    tocar cada generador. No-op si `elements` no tiene ningun heading."""
    heading_style_ids = {canvas.styles[f"Heading {level}"].style_id for level in (1, 2, 3)}

    for el in elements:
        for p in el.iter(qn("w:p")):
            pStyle_el = p.find(qn("w:pPr") + "/" + qn("w:pStyle"))
            if pStyle_el is None or pStyle_el.get(qn("w:val")) not in heading_style_ids:
                continue
            runs = list(p.iter(qn("w:r")))
            if not runs:
                return
            first_run, rest = runs[0], runs[1:]
            for extra_run in rest:
                extra_run.getparent().remove(extra_run)
            for t_el in list(first_run.iter(qn("w:t"))):
                first_run.remove(t_el)
            text_el = OxmlElement("w:t")
            text_el.text = text
            first_run.append(text_el)
            return


def relocate_images(elements: list, source_part, target_part) -> None:
    """Reescribe las referencias a imagenes embebidas (a:blip/@r:embed) de
    `elements` para que apunten a relaciones validas en `target_part`.

    `elements` se generan sobre `source_part` (el canvas temporal, ver
    build_content_elements) y se insertan luego como XML crudo dentro de un
    documento real distinto: add_picture() no solo escribe el <a:blip
    r:embed="rIdN"> en el parrafo, tambien registra una relacion OPC en el
    paquete del canvas apuntando a un ImagePart que vive ahi. Sin este paso,
    ese rId no resuelve a nada en el paquete de destino y Word no puede
    mostrar la imagen (no aparece nada, ni un icono roto).
    """
    for el in elements:
        for blip in el.iter(qn("a:blip")):
            r_id = blip.get(qn("r:embed"))
            if not r_id:
                continue
            image_part = source_part.related_parts[r_id]
            new_r_id = target_part.relate_to(image_part, RT.IMAGE)
            blip.set(qn("r:embed"), new_r_id)


def mark_fields_for_update(document) -> None:
    """Fuerza a Word a recalcular todos los campos (w:fldSimple/w:fldChar -
    tabla de contenidos, STYLEREF, SEQ, etc.) la proxima vez que se abra el
    documento, seteando w:updateFields en settings.xml. Sin esto, esos campos
    quedan con el valor cacheado que tenian la ultima vez que alguien los
    actualizo a mano en Word (F9 / "Actualizar campos") - el motor de sync
    nunca los toca directamente porque el contenido de un campo depende de
    todo el documento, no solo del ancla que se acaba de escribir. Idempotente:
    si el elemento ya existe (con cualquier valor), solo se actualiza su
    w:val en vez de duplicarlo."""
    settings = document.settings.element
    update_fields = settings.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        settings.append(update_fields)
    update_fields.set(qn("w:val"), "true")


def drop_image_relationships(elements: list, part) -> None:
    """Libera las relaciones de imagenes embebidas en `elements` dentro de
    `part`. Se usa sobre el contenido saliente de un ancla antes de
    reemplazarlo, para que las imagenes de una sincronizacion anterior no
    queden huerfanas acumulandose en el paquete del .docx en cada sync."""
    for el in elements:
        for blip in el.iter(qn("a:blip")):
            r_id = blip.get(qn("r:embed"))
            if r_id:
                part.drop_rel(r_id)
