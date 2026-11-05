"""
Hashes usados por el motor de sincronizacion.

Dos hashes con propositos distintos:
- hash_source: del JSON fuente de un modulo -> decide si vale la pena volver
  a ejecutar el generador (si la fuente no cambio, se salta la regeneracion).
- hash_content: del XML ya generado, canonicalizado -> (a) evita reescribir
  el .docx si el contenido generado es identico al anterior, (b) detecta
  ediciones manuales hechas directamente dentro de un ancla comparando contra
  el content_hash guardado la ultima vez.

Nota tecnica: python-docx registra clases custom sobre los elementos lxml
(ej. CT_P.text) que sobrescriben el atributo `.text` como una propiedad de
conveniencia ("texto visible del parrafo") en vez del texto crudo del nodo
XML. Eso corrompe cualquier canonicalizacion que dependa de `.text` -y de
paso el metodo c14n2 de lxml, que internamente pasa por esa propiedad-. Por
eso antes de canonicalizar se vuelve a parsear cada elemento con un parser
lxml "limpio" (sin las clases custom de python-docx), para trabajar siempre
sobre el texto crudo real.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from lxml import etree

HASH_PREFIX = "sha256:"

# Namespace OOXML de relaciones (r:id, r:embed, r:link, ...): son punteros
# relativos al paquete (.docx) que los contiene, no contenido autoral. Su
# valor puede cambiar entre corridas de sync sin que el contenido generado
# haya cambiado en absoluto (ver relocate_images en content_builder.py), asi
# que se excluyen del hash de contenido para no romper la deteccion de
# "sin cambios".
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Artefactos que Word inyecta/actualiza al abrir y guardar el documento (aunque
# el usuario no haya tocado el contenido de un ancla en absoluto - basta con
# abrir el .docx en Word y guardarlo, editando cualquier otra parte del
# documento) y que no son contenido autoral:
# - w:proofErr: marcadores del corrector ortografico/gramatical
#   (spellStart/spellEnd/gramStart/gramEnd) alrededor de palabras que Word
#   marca al re-escanear el documento - muy probable con jerga tecnica de
#   ingenieria. Se excluyen del recorrido de hijos por completo.
# - w:rsid*: IDs de sesion de revision que Word reescribe en cada guardado en
#   los parrafos/runs que toco, sin relacion con el contenido visible.
# Sin excluirlos, cualquiera de los dos dispara un falso "posible edicion
# manual" (STATUS_NEEDS_CONFIRMATION) en el proximo sync, sin que el usuario
# haya editado nada dentro del ancla.
_VOLATILE_WORD_ELEMENTS = {"proofErr"}
_VOLATILE_ATTR_PREFIX = "rsid"


def hash_source(data: dict) -> str:
    """Hash estable de un JSON fuente (independiente de orden de claves)."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def _to_plain_element(element):
    """Re-parsea un elemento con un parser lxml sin clases custom de python-docx."""
    return etree.fromstring(etree.tostring(element))


def _canonical_tuple(element) -> tuple:
    """
    Representacion canonica de un elemento: nombre local (sin namespace,
    para no depender de que namespaces esten declarados en el documento
    ambiente), atributos ordenados por nombre local, texto/tail propios, y
    la misma representacion recursiva de sus hijos.
    """
    tag = etree.QName(element).localname
    attrs = tuple(
        sorted(
            (local_name, v)
            for k, v in element.attrib.items()
            for local_name in [etree.QName(k).localname]
            if not k.startswith(f"{{{_RELATIONSHIPS_NS}}}")
            and not local_name.startswith(_VOLATILE_ATTR_PREFIX)
        )
    )
    text = (element.text or "").strip()
    tail = (element.tail or "").strip()
    children = tuple(
        _canonical_tuple(child)
        for child in element
        if etree.QName(child).localname not in _VOLATILE_WORD_ELEMENTS
    )
    return (tag, attrs, text, tail, children)


def hash_content(elements: Iterable) -> str:
    """Hash estable de una secuencia de elementos XML (w:p, w:tbl, ...)."""
    canonical = tuple(_canonical_tuple(_to_plain_element(el)) for el in elements)
    blob = repr(canonical).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    return f"{HASH_PREFIX}{digest}"
