"""Carpeta «<documento>_reporte» junto a un Word vinculado en su sitio (sólo app local).

Al subir la exportación de un módulo, sus JSON e imágenes también se guardan (espejo)
en una carpeta creada junto al .docx de la persona:

    <carpeta del Word>/<nombre_del_docx>_reporte/<slug_modulo>/<slug_modulo>.json
    <carpeta del Word>/<nombre_del_docx>_reporte/<slug_modulo>/imagenes/<nombre>.png

Las referencias de imagen del JSON espejo son rutas relativas a la carpeta del módulo
(«imagenes/<nombre>.png»). El almacén del proyecto sigue siendo la fuente de verdad: el
espejo sólo se escribe aquí, y el manifiesto puede usar el JSON espejo como fuente
(`fuente = carpeta_reporte`). Nada se escribe fuera de esta carpeta.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

from backend.report_builder.engine.atomic_write import replace_with
from backend.report_builder.modulos_exportacion import slug_modulo

SUFIJO_CARPETA = '_reporte'
CARPETA_IMAGENES = 'imagenes'
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _componente_seguro(texto: str, maximo: int = 120) -> str:
    limpio = _PROHIBIDOS.sub('_', str(texto)).strip().strip('.').strip()
    return (limpio or 'informe')[:maximo]


def nombre_carpeta_reporte(docx_path: Path) -> str:
    return _componente_seguro(docx_path.stem, 120) + SUFIJO_CARPETA


def carpeta_reporte(docx_path: Path) -> Path:
    """Carpeta hermana del .docx, que lleva su nombre seguido de «_reporte»."""
    return docx_path.parent / nombre_carpeta_reporte(docx_path)


def carpeta_modulo(docx_path: Path, module_id: str) -> Path:
    return carpeta_reporte(docx_path) / slug_modulo(module_id)


def archivo_json_modulo(docx_path: Path, module_id: str) -> Path:
    slug = slug_modulo(module_id)
    return carpeta_modulo(docx_path, module_id) / f'{slug}.json'


def ruta_relativa_al_docx(docx_path: Path, path: Path) -> str:
    """Ruta POSIX de `path` relativa a la carpeta del .docx (la que guarda el manifiesto)."""
    return PurePosixPath(*path.relative_to(docx_path.parent).parts).as_posix()


def dentro_de_carpeta_reporte(docx_path: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(carpeta_reporte(docx_path).resolve())


# Windows limita la ruta completa a 259 caracteres y el Word suele estar en carpetas largas
# (OneDrive, proyectos): las imágenes llevan un nombre corto en el espejo.
MAX_NOMBRE_IMAGEN = 48
LIMITE_RUTA_WINDOWS = 259
# Margen para el temporal de la escritura atómica («.<nombre>.xxxxxxxx.tmp»).
MARGEN_TEMPORAL = 20


def _acortar(nombre: str, original: str) -> str:
    """Recorta un nombre largo conservando la extensión y un sufijo que lo mantiene único."""
    if len(nombre) <= MAX_NOMBRE_IMAGEN:
        return nombre
    stem, dot, ext = nombre.rpartition('.')
    if not dot:
        stem, ext = nombre, ''
    sufijo = hashlib.sha1(str(original).encode('utf-8')).hexdigest()[:6]
    resto = MAX_NOMBRE_IMAGEN - len(ext) - (1 if ext else 0) - len(sufijo) - 1
    return f"{stem[:resto].rstrip('_ .')}_{sufijo}" + (f'.{ext}' if ext else '')


def nombres_unicos(originales: list[str]) -> list[str]:
    """Nombres seguros, cortos y distintos para las imágenes de un paquete («x.png», «x (2).png»...)."""
    usados: set[str] = set()
    salida = []
    for original in originales:
        base = _componente_seguro(PurePosixPath(str(original).replace('\\', '/')).name, 200)
        base = _acortar(base, original)
        candidato, n = base, 2
        stem, dot, ext = base.rpartition('.')
        if not dot:
            stem, ext = base, ''
        while candidato.lower() in usados:
            candidato = f'{stem} ({n}).{ext}' if ext else f'{stem} ({n})'
            n += 1
        usados.add(candidato.lower())
        salida.append(candidato)
    return salida


def reemplazar_cadenas(value, mapping: dict[str, str]):
    """Copia de `value` con cada cadena exactamente igual a una clave de `mapping` sustituida."""
    if isinstance(value, dict):
        return {k: reemplazar_cadenas(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [reemplazar_cadenas(v, mapping) for v in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def _escribir(destino: Path, contenido: bytes) -> None:
    """Escritura atómica: el espejo nunca queda a medias si se corta la escritura."""
    replace_with(destino, lambda temp: temp.write_bytes(contenido))


def escribir_modulo(
    docx_path: Path, module_id: str, json_bytes: bytes, imagenes: list[tuple[str, bytes]]
) -> dict:
    """Escribe (o reemplaza) el espejo de un módulo. `imagenes` son pares (nombre único, bytes).

    Las imágenes que ya no están en el paquete se borran de la carpeta del módulo, que es
    de la aplicación. Lanza ValueError si alguna ruta saldría de la carpeta _reporte.
    """
    raiz = carpeta_reporte(docx_path)
    modulo = carpeta_modulo(docx_path, module_id)
    if not modulo.resolve().is_relative_to(raiz.resolve()):
        raise ValueError('Ruta de espejo fuera de la carpeta del informe')
    carpeta_img = modulo / CARPETA_IMAGENES
    if sys.platform == 'win32':
        mas_larga = max([len(n) for n, _ in imagenes] + [len(f'{slug_modulo(module_id)}.json')])
        largo = len(str(carpeta_img.absolute())) + 1 + mas_larga + MARGEN_TEMPORAL
        if largo > LIMITE_RUTA_WINDOWS:
            raise ValueError(
                f'La ruta de la carpeta del informe es demasiado larga para Windows ({largo} caracteres, '
                f'máximo {LIMITE_RUTA_WINDOWS}). Mueva el Word a una carpeta con una ruta más corta.'
            )
    modulo.mkdir(parents=True, exist_ok=True)
    carpeta_img.mkdir(exist_ok=True)

    nombres = {nombre for nombre, _ in imagenes}
    for nombre, contenido in imagenes:
        destino = carpeta_img / nombre
        if not destino.resolve().is_relative_to(carpeta_img.resolve()):
            raise ValueError('Nombre de imagen inválido')
        _escribir(destino, contenido)
    for viejo in carpeta_img.iterdir():
        if viejo.is_file() and not viejo.name.startswith('.') and viejo.name not in nombres:
            viejo.unlink()

    json_path = archivo_json_modulo(docx_path, module_id)
    _escribir(json_path, json_bytes)
    return {'json': json_path, 'imagenes': sorted(nombres)}
