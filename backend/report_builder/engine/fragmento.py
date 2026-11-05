"""Prototipo «plan/aplicador»: generar el contenido de un módulo sin el documento real.

Hoy el sincronizador abre el .docx completo del usuario, clona un lienzo con sus estilos, ejecuta el
generador y pega el resultado en el ancla (``sync_engine._sync_module``). Todo ocurre en un solo
proceso, así que el motor tiene que correr donde está el Word.

Este módulo separa esos pasos para que el Word nunca tenga que viajar:

1. ``lienzo_ligero`` (en el PC): paquete con los estilos del documento, el cuerpo vacío y sin imágenes,
   cabeceras ni pies. Pesa decenas de KB y solo cambia cuando cambian los estilos (``huella_estilos``).
2. ``generar_fragmento`` (en el servidor, con el lienzo ligero): ejecuta el generador y devuelve un
   fragmento (un .docx diminuto con el contenido ya hecho) y su ``content_hash``.
3. ``aplicar_fragmento`` (en el PC): reubica las imágenes y reemplaza el contenido del ancla en el
   documento real, igual que ``sync_engine``.

Los generadores no cambian: reciben el mismo ``doc`` con los mismos estilos. Estado: PROTOTIPO, no lo
usa ningún flujo; ver ``backend/report_builder/tests/test_fragmento.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

from backend.report_builder.engine.content_builder import (
    build_content_elements,
    drop_image_relationships,
    set_title_heading_text,
    shift_heading_levels,
)
from backend.report_builder.engine.hasher import hash_content

# Relaciones del documento que no hacen falta para generar contenido y pueden pesar mucho.
_RELACIONES_PESADAS = {RT.IMAGE, RT.HEADER, RT.FOOTER}


@dataclass(frozen=True)
class Fragmento:
    datos: bytes
    content_hash: str


def _vaciar_cuerpo(documento) -> None:
    cuerpo = documento.element.body
    for hijo in list(cuerpo):
        if hijo.tag != qn("w:sectPr"):
            cuerpo.remove(hijo)


def lienzo_ligero(origen: str | Path | bytes) -> bytes:
    """Paquete con los estilos del documento y nada más: cuerpo vacío, sin imágenes, cabeceras ni pies."""
    documento = docx.Document(BytesIO(origen) if isinstance(origen, bytes) else str(origen))
    _vaciar_cuerpo(documento)
    cuerpo = documento.element.body
    for sect in cuerpo.iter(qn("w:sectPr")):
        for ref in list(sect):
            if ref.tag in (qn("w:headerReference"), qn("w:footerReference")):
                sect.remove(ref)
    parte = documento.part
    for r_id, rel in list(parte.rels.items()):
        if rel.reltype in _RELACIONES_PESADAS:
            del parte.rels[r_id]
    salida = BytesIO()
    documento.save(salida)
    return salida.getvalue()


def huella_estilos(origen: str | Path | bytes) -> str:
    """Huella de lo que condiciona el contenido generado (estilos y numeración): clave de caché del lienzo."""
    documento = docx.Document(BytesIO(origen) if isinstance(origen, bytes) else str(origen))
    h = hashlib.sha256()
    for parte in sorted(documento.part.package.iter_parts(), key=lambda p: str(p.partname)):
        nombre = str(parte.partname)
        if nombre.endswith(("/styles.xml", "/numbering.xml")):
            h.update(nombre.encode())
            h.update(parte.blob)
    return h.hexdigest()


def generar_fragmento(
    generador: Callable,
    datos: dict,
    style_map: dict,
    lienzo: bytes,
    *,
    titulo: str | None = None,
    desplazamiento: int = 0,
) -> Fragmento:
    """Ejecuta el generador sobre el lienzo ligero y empaqueta el resultado como fragmento.

    Aplica el título y el desplazamiento de niveles como ``sync_engine._sync_module``; el hash del
    contenido es el mismo que se obtendría con el documento completo (``hash_content`` ignora las
    referencias de relaciones).
    """
    canvas = docx.Document(BytesIO(lienzo))
    elementos = build_content_elements(generador, datos, style_map, canvas)
    if titulo is not None:
        set_title_heading_text(elementos, canvas, titulo)
    if desplazamiento:
        shift_heading_levels(elementos, canvas, desplazamiento)
    contenido = hash_content(elementos)
    cuerpo = canvas.element.body
    sect = cuerpo.find(qn("w:sectPr"))
    for el in elementos:
        if sect is not None:
            sect.addprevious(el)
        else:
            cuerpo.append(el)
    salida = BytesIO()
    canvas.save(salida)
    return Fragmento(salida.getvalue(), contenido)


def _reubicar_imagenes(elementos: list, origen, destino) -> None:
    """Como ``content_builder.relocate_images``, pero renombrando cada imagen en el paquete destino.

    El lienzo ligero no trae las imágenes del documento real, así que sus ``image1.png`` colisionarían con
    las del Word: dos partes con el mismo nombre en el zip. Con el lienzo completo del sincronizador actual
    eso no ocurre porque su numeración ya cuenta las imágenes reales.
    """
    paquete = destino.package
    for el in elementos:
        for blip in el.iter(qn("a:blip")):
            r_id = blip.get(qn("r:embed"))
            if not r_id:
                continue
            parte = origen.related_parts[r_id]
            ext = str(parte.partname).rsplit(".", 1)[-1]
            parte.partname = paquete.next_partname(f"/word/media/image%d.{ext}")
            blip.set(qn("r:embed"), destino.relate_to(parte, RT.IMAGE))


def aplicar_fragmento(documento, sdt_content, fragmento: bytes | Fragmento) -> str:
    """Reemplaza el contenido del ancla por el del fragmento; devuelve el ``content_hash`` aplicado."""
    datos = fragmento.datos if isinstance(fragmento, Fragmento) else fragmento
    frag = docx.Document(BytesIO(datos))
    nuevos = [deepcopy(h) for h in frag.element.body if h.tag != qn("w:sectPr")]
    _reubicar_imagenes(nuevos, frag.part, documento.part)
    drop_image_relationships(list(sdt_content), documento.part)
    for hijo in list(sdt_content):
        sdt_content.remove(hijo)
    for el in nuevos:
        sdt_content.append(el)
    return hash_content(nuevos)
