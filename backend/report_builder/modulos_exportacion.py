"""Módulo del Report Builder al que pertenece cada exportación de un proyecto.

La web y el backend exportan cada informe con un nombre genérico: «esbelteces.json»,
«fundaciones-reporte.zip» (que dentro lleva «reporte.json») o «reporte.json» a secas.
Este módulo es el único sitio que dice a qué generador (`module_id`) corresponde
cada origen, y el que da nombres legibles a lo que se guarda en el proyecto.

Orden de detección de un archivo subido:

1. Por nombre (`ORIGENES_EXPORTACION`, o la etiqueta legible «<Módulo> — fecha»
   que ya le dimos nosotros).
2. Por la pista del paquete (el nombre del ZIP que lo contiene).
3. Por contenido: el único esquema de generador que valida el JSON. Si ninguno, o
   más de uno, el archivo queda sin módulo.

Los archivos antiguos (sin `module_id` guardado) se infieren sólo por nombre.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath

from backend.report_builder.engine.generator_registry import (
    MODULE_DISPLAY_NAMES,
    REPORT_BUILDER_ROOT,
    catalogo_generadores,
)
from backend.report_builder.engine.schema_validation import validate_against_schema

# Clave normalizada del nombre de origen -> module_id. La clave se obtiene con
# `clave_origen`: sin extensión, minúsculas, sin acentos y con «-» y espacios
# convertidos en «_». Un módulo puede tener varios orígenes (JSON suelto y ZIP).
ORIGENES_EXPORTACION: dict[str, str] = {
    # Fundaciones SAP2000: el ZIP «fundaciones-reporte.zip» trae «reporte.json».
    'fundaciones': 'fundaciones',
    'fundaciones_reporte': 'fundaciones',
    'esbelteces': 'esbelteces',
    'combinaciones': 'combinaciones',
    # Espectro: «espectro-reporte.zip» trae «reporte.json» y «grafico.png».
    'espectro': 'espectro',
    'espectro_reporte': 'espectro',
    'diagrama_interaccion': 'diagrama_interaccion',
    'bombas': 'bombas',
    'nomogramas': 'nomogramas',
    'hormigon_simple': 'hormigon_simple',
    'equilibrio_plastico': 'equilibrio_plastico',
    'sulzberger': 'sulzberger',
    'zapata_biaxial': 'zapata_biaxial',
    'zapatas_biaxiales': 'zapata_biaxial',
    'acero_aisc360': 'acero_aisc360',
    'acero': 'acero_aisc360',
    # Origen previsto: el JSON del módulo de placas base de escritorio (su esquema
    # está en generators/schemas). La web todavía no exporta este informe.
    'placas_base': 'placas_base',
}

# Nombres de contenido genérico: no dicen de qué módulo son, se decide por esquema.
ORIGENES_GENERICOS = frozenset({'reporte'})

_EXTENSIONES = ('.json', '.zip', '.docx', '.png', '.jpg', '.jpeg', '.csv', '.xlsx')
_SEPARADOR_ETIQUETA = ' — '
_CARACTERES_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in descompuesto if not unicodedata.combining(c))


def _nombre_base(nombre: str) -> str:
    """Nombre sin carpetas ni extensión conocida."""
    base = PurePosixPath(str(nombre).replace('\\', '/')).name
    lower = base.lower()
    for extension in _EXTENSIONES:
        if lower.endswith(extension):
            return base[: -len(extension)]
    return base


def clave_origen(texto: str) -> str:
    """Clave comparable de un nombre o etiqueta: «Fundaciones-Reporte.zip» -> «fundaciones_reporte»."""
    base = _sin_acentos(_nombre_base(texto)).lower()
    return re.sub(r'[\s\-]+', '_', base).strip('_')


_ETIQUETA_A_MODULO = {clave_origen(etiqueta): modulo for modulo, etiqueta in MODULE_DISPLAY_NAMES.items()}


def etiqueta_modulo(module_id: str) -> str:
    """Nombre legible del módulo (el mismo que muestra el Report Builder)."""
    return MODULE_DISPLAY_NAMES.get(module_id, module_id)


def modulo_por_nombre(nombre: str) -> str | None:
    """module_id a partir del nombre de un archivo, o None si el nombre no lo dice.

    Acepta también las etiquetas «<Módulo> — fecha» y «<Módulo> — fecha / original.png»,
    donde la barra es parte del texto y no una carpeta.
    """
    if not nombre:
        return None
    texto = str(nombre).replace('\\', '/')
    etiqueta = texto.split(_SEPARADOR_ETIQUETA, 1)[0]
    for candidata in (clave_origen(texto), clave_origen(etiqueta)):
        if candidata in ORIGENES_GENERICOS:
            continue
        if candidata in ORIGENES_EXPORTACION:
            return ORIGENES_EXPORTACION[candidata]
        if candidata in _ETIQUETA_A_MODULO:
            return _ETIQUETA_A_MODULO[candidata]
    return None


@functools.lru_cache(maxsize=1)
def _esquemas_por_modulo() -> dict[str, dict]:
    esquemas = {}
    for generador in catalogo_generadores():
        if generador.schema_path:
            ruta = REPORT_BUILDER_ROOT / generador.schema_path
            esquemas[generador.module_id] = json.loads(ruta.read_text(encoding='utf-8'))
    return esquemas


def modulo_por_contenido(data) -> str | None:
    """El único generador cuyo esquema valida `data`; None si ninguno o varios."""
    if not isinstance(data, dict):
        return None
    candidatos = [
        modulo
        for modulo, esquema in _esquemas_por_modulo().items()
        if not validate_against_schema(data, esquema)
    ]
    return candidatos[0] if len(candidatos) == 1 else None


def detectar_modulo(nombre: str, data=None, pista: str | None = None) -> str | None:
    """Módulo de un archivo subido: nombre, luego pista del paquete, luego contenido."""
    return (
        modulo_por_nombre(nombre)
        or (modulo_por_nombre(pista) if pista else None)
        or (modulo_por_contenido(data) if data is not None else None)
    )


def _limpio(texto: str, maximo: int = 180) -> str:
    limpio = _CARACTERES_PROHIBIDOS.sub('_', str(texto))
    limpio = re.sub(r'\s+', ' ', limpio).strip().strip('.')
    return (limpio or 'archivo')[:maximo]


def nombre_legible(module_id: str, cuando: datetime, kind: str, original: str | None = None) -> str:
    """«<Módulo> — AAAA-MM-DD HH:MM.json» para un JSON; «<Módulo> — fecha / <original>» para una imagen.

    La hora lleva «:» a propósito (así lo pidió la persona); sólo se sanea el nombre del módulo y el
    del archivo original.
    """
    fecha = cuando.strftime('%Y-%m-%d %H:%M')
    cabecera = f'{_limpio(etiqueta_modulo(module_id), 120)}{_SEPARADOR_ETIQUETA}{fecha}'
    if kind == 'json':
        return cabecera + '.json'
    return f'{cabecera} / {_nombre_archivo(original or kind)}'


def _nombre_archivo(texto: str) -> str:
    """Último componente del nombre original, saneado; conserva su extensión."""
    base = PurePosixPath(str(texto).replace('\\', '/')).name
    return _limpio(base, 200)


def slug_modulo(module_id: str) -> str:
    """Nombre de carpeta seguro para un módulo (solo minúsculas, dígitos y «_»)."""
    return re.sub(r'[^a-z0-9_]+', '_', clave_origen(module_id)).strip('_') or 'modulo'


def sin_repetir(nombre: str, existentes: set[str]) -> str:
    """Añade « (2)», « (3)»... antes de la extensión si el nombre ya existe en el proyecto."""
    if nombre not in existentes:
        return nombre
    base, dot, extension = nombre.rpartition('.')
    if not dot:
        base, extension = nombre, ''
    n = 2
    while True:
        candidato = f'{base} ({n}).{extension}' if extension else f'{base} ({n})'
        if candidato not in existentes:
            return candidato
        n += 1


def modulos_con_origen() -> set[str]:
    """Todos los module_id que tienen al menos un origen de exportación."""
    return set(ORIGENES_EXPORTACION.values())
