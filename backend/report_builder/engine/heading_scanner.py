"""
Escaneo de la estructura de titulos (Heading 1/2/3) de un documento Word ya
cargado con python-docx, para presentar posiciones candidatas de insercion de
anclas en el Anchor Wizard. No incluye los titulos de la Tabla de Contenido
(usan estilos "toc 1/2/3", distintos de "Heading 1/2/3").

Este modulo solo detecta estructura ya existente - nunca decide donde va el
contenido de un modulo. La eleccion final siempre la hace el ingeniero en el
wizard.
"""

from __future__ import annotations

from dataclasses import dataclass

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from backend.report_builder.engine.anchor_locator import REPORT_TAG_PREFIX, get_tag

HEADING_STYLE_LEVELS = {
    "Heading 1": 1,
    "Heading 2": 2,
    "Heading 3": 3,
}

# w:outlineLvl es 0-based (0 = nivel 1 ... 8 = nivel 9). Solo nos interesan
# los niveles 1-3, igual que HEADING_STYLE_LEVELS.
MAX_OUTLINE_LEVEL = 2

PREVIEW_TEXT_MAX_LEN = 70


@dataclass
class HeadingCandidate:
    level: int
    text: str
    paragraph_index: int
    element: object  # el w:p real (lxml), referencia de insercion para anchor_writer


@dataclass
class PositionOption:
    label: str
    preview: str = ""  # descripcion de donde queda exactamente esta posicion
    # Insercion real (lxml): a lo sumo uno de los dos esta seteado. Si ambos
    # son None, se inserta al final del documento (antes del w:sectPr).
    insert_after: object | None = None  # insertar como siguiente hermano de este w:p
    insert_before: object | None = None  # insertar como hermano anterior a este w:p
    # Titulo del que viene esta opcion (None para "Al final del documento").
    # Permite recalcular la variante "al inicio de la seccion" (ver
    # start_of_section_option) sin tener que re-escanear el documento.
    heading: HeadingCandidate | None = None


def _is_inside_existing_sdt(p_element) -> bool:
    """
    True si este w:p ya vive dentro de un Content Control (w:sdt) - propio de
    report_builder o no. No se ofrecen estas posiciones porque una nueva
    ancla instalada ahi quedaria anidada dentro de contenido que otro
    modulo puede vaciar y regenerar por completo en cada sync (ver
    sync_engine._sync_module), lo que borraria la ancla anidada sin aviso.
    """
    return any(ancestor.tag == qn("w:sdt") for ancestor in p_element.iterancestors())


def _is_inside_report_sdt(p_element) -> bool:
    """
    True si este w:p vive dentro de un ancla administrada por report_builder
    (w:sdt con tag report:...). Su contenido es generado y lo reemplaza cada
    sync: sus titulos no son estructura del documento ni fronteras de seccion.
    """
    for ancestor in p_element.iterancestors(qn("w:sdt")):
        tag = get_tag(ancestor)
        if tag is not None and tag.startswith(REPORT_TAG_PREFIX):
            return True
    return False


def _style_outline_level(document, style_id: str | None) -> int | None:
    """outlineLvl definido en el propio estilo (styles.xml), no en el parrafo."""
    if not style_id:
        return None
    for style_el in document.styles.element.findall(qn("w:style")):
        if style_el.get(qn("w:styleId")) == style_id:
            outline_el = style_el.find(qn("w:pPr") + "/" + qn("w:outlineLvl"))
            if outline_el is None:
                return None
            val = outline_el.get(qn("w:val"))
            return int(val) if val is not None else None
    return None


def _outline_fallback_level(p_element, document) -> int | None:
    """
    Nivel de titulo (1-3) a partir de w:outlineLvl, directo en el parrafo o
    heredado de su estilo. Cubre templates que renombran/personalizan sus
    estilos de titulo: Word igual los reconoce como "titulo de nivel N" por
    outlineLvl, independiente del nombre visible del estilo.
    """
    outline_el = p_element.find(qn("w:pPr") + "/" + qn("w:outlineLvl"))
    if outline_el is not None:
        val = outline_el.get(qn("w:val"))
        outline_val = int(val) if val is not None else None
    else:
        pStyle_el = p_element.find(qn("w:pPr") + "/" + qn("w:pStyle"))
        style_id = pStyle_el.get(qn("w:val")) if pStyle_el is not None else None
        outline_val = _style_outline_level(document, style_id)

    if outline_val is not None and 0 <= outline_val <= MAX_OUTLINE_LEVEL:
        return outline_val + 1
    return None


def _effective_heading_level(paragraph: Paragraph, p_element, document) -> int | None:
    style_name = paragraph.style.name if paragraph.style else None
    level = HEADING_STYLE_LEVELS.get(style_name)
    if level is not None:
        return level
    return _outline_fallback_level(p_element, document)


def _scan_all_headings(document) -> list:
    """
    Todos los titulos del documento en orden de aparicion, como pares
    (HeadingCandidate, dentro_de_sdt_ajeno). Incluye los que estan dentro de
    un Content Control ajeno (p. ej. un control de contenido que envuelve
    capitulos enteros): son estructura real del documento y marcan donde
    termina una seccion. Excluye los titulos generados dentro de anclas
    report: (ver _is_inside_report_sdt).
    """
    found = []
    body = document.element.body
    for i, p_element in enumerate(body.iter(qn("w:p"))):
        if _is_inside_report_sdt(p_element):
            continue
        paragraph = Paragraph(p_element, document)
        level = _effective_heading_level(paragraph, p_element, document)
        if level is None:
            continue
        text = paragraph.text.strip()
        if not text:
            continue
        candidate = HeadingCandidate(level=level, text=text, paragraph_index=i, element=p_element)
        found.append((candidate, _is_inside_existing_sdt(p_element)))
    return found


def scan_headings(document) -> list:
    """
    Todos los titulos (Heading 1/2/3 por nombre de estilo, o equivalentes por
    outlineLvl) con texto no vacio, en cualquier parte del cuerpo del
    documento - incluye parrafos dentro de tablas (celdas, incluso tablas
    anidadas), no solo los parrafos de nivel superior del body. Se excluyen
    los que ya estan dentro de un Content Control existente (ver
    _is_inside_existing_sdt). En orden de aparicion en el documento.
    """
    return [heading for heading, en_sdt in _scan_all_headings(document) if not en_sdt]


def _insertion_point_before(boundary_element, section_element):
    """
    Elemento ante el que insertar un ancla de forma que quede justo antes de
    `boundary_element` (la frontera de la seccion de `section_element`), sin
    quedar dentro de un Content Control ajeno que no contenga tambien a la
    seccion. Si la frontera vive dentro de un w:sdt que envuelve capitulos
    enteros, se sube hasta ese w:sdt (hermano en el body); pero si ese mismo
    w:sdt contiene tambien el titulo de la seccion, la ancla se queda dentro
    (subir mas alla dejaria la ancla fuera de su propio capitulo). Sin esta subida, la
    frontera envuelta no contaba como limite y la seccion se iba al final del
    documento (ver build_position_options).
    """
    section_ancestors = set(section_element.iterancestors())
    current = boundary_element
    while True:
        parent = current.getparent()
        if parent is None or parent.tag != qn("w:sdtContent"):
            break
        container = parent.getparent()
        if container in section_ancestors:
            break
        current = container
    return current


def _text_of(element) -> str:
    return "".join(t.text or "" for t in element.iter(qn("w:t")))


def _next_section_boundary(headings: list, index: int) -> HeadingCandidate | None:
    """
    El siguiente titulo (en orden de aparicion) cuyo nivel es igual o mas alto
    en la jerarquia (numero de nivel menor o igual) que headings[index] - es
    decir, el titulo que marca el fin de TODA la seccion de headings[index]
    (incluidas sus subsecciones anidadas de nivel inferior). None si no hay
    ninguno (la seccion llega hasta el final del documento).

    Ejemplo con "DISEÑO DE ACERO" (nivel 1) seguido de 4 subtitulos nivel 2 y
    2 nivel 3, y despues "CONCLUSIÓN" (nivel 1): el limite es "CONCLUSIÓN",
    no el primer subtitulo nivel 2.
    """
    level = headings[index].level
    for candidate in headings[index + 1 :]:
        if candidate.level <= level:
            return candidate
    return None


def build_position_options(document) -> list:
    """
    Lista de posiciones candidatas para el Anchor Wizard. Elegir "Despues de:
    <titulo>" inserta al final de TODA esa seccion (despues de sus
    subtitulos y contenido anidado de nivel inferior, no inmediatamente
    despues del propio titulo) - asi un modulo insertado despues de un
    capitulo nivel 1 no corta sus subsecciones nivel 2/3 a la mitad.
    """
    options = [
        PositionOption(
            label="Al final del documento",
            preview="(se agrega como ultima seccion, antes del cierre del documento)",
        )
    ]
    # Las fronteras se buscan entre TODOS los titulos, tambien los que estan
    # dentro de un Content Control ajeno (ver _scan_all_headings).
    marked = _scan_all_headings(document)
    all_headings = [heading for heading, _ in marked]
    for i, (heading, _en_sdt) in enumerate(marked):
        indent = "    " * (heading.level - 1)
        label = f"{indent}Despues de: {heading.text} (seccion completa)"
        boundary = _next_section_boundary(all_headings, i)
        if boundary is None:
            options.append(
                PositionOption(
                    label=label,
                    preview="(al final del documento - no hay otro titulo de igual o mayor jerarquia despues)",
                    heading=heading,
                )
            )
        else:
            preview_text = boundary.text
            if len(preview_text) > PREVIEW_TEXT_MAX_LEN:
                preview_text = preview_text[:PREVIEW_TEXT_MAX_LEN].rstrip() + "..."
            options.append(
                PositionOption(
                    label=label,
                    insert_before=_insertion_point_before(boundary.element, heading.element),
                    preview=f'justo antes de: "{preview_text}"',
                    heading=heading,
                )
            )
    return options


def start_of_section_option(heading: HeadingCandidate) -> PositionOption:
    """
    Variante "al inicio" de una seccion: el ancla queda como primer
    contenido de la seccion, inmediatamente despues del propio parrafo del
    titulo (como si fuera un subtitulo en primera posicion), en vez de al
    final de toda la seccion (comportamiento default de build_position_options).
    """
    indent = "    " * (heading.level - 1)
    return PositionOption(
        label=f"{indent}Al inicio de: {heading.text} (como subtitulo)",
        insert_after=heading.element,
        preview=f'justo despues del titulo "{heading.text}" (primer contenido de la seccion)',
        heading=heading,
    )


def suggest_default_index(options: list, module_id: str) -> int:
    """
    Sugerencia de conveniencia, no autoritativa: si el texto de algun titulo
    contiene una palabra del module_id, se pre-selecciona esa posicion. El
    ingeniero siempre puede elegir otra - el programa nunca inserta sin
    confirmacion explicita del ingeniero.
    """
    keywords = [k for k in module_id.lower().replace("_", " ").split() if len(k) > 3]
    for i, option in enumerate(options):
        label_lower = option.label.lower()
        if any(keyword in label_lower for keyword in keywords):
            return i
    return 0


def anchor_heading_offsets(document) -> dict:
    """Por cada ancla report:* del cuerpo, cuantos titulos (los mismos y en el mismo orden que
    build_position_options / la lista de posiciones) la preceden. Es el indice del primer titulo
    posterior al ancla, para que la UI la pinte en su sitio dentro de la estructura."""
    marked = _scan_all_headings(document)
    orden = {candidate.element: k for k, (candidate, _en_sdt) in enumerate(marked)}
    offsets: dict = {}
    contados = 0
    for el in document.element.body.iter():
        if el.tag == qn("w:p") and el in orden:
            contados = orden[el] + 1
        elif el.tag == qn("w:sdt"):
            tag = get_tag(el)
            if tag and tag.startswith(REPORT_TAG_PREFIX):
                offsets[tag] = contados
    return offsets
