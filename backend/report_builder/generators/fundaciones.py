"""
Generador del informe de fundaciones a partir del JSON de salida del programa
"Fundaciones SAP2000".

Contrato de entrada: generators/schemas/fundaciones.v1.schema.json. Este
modulo depende SOLO de la seccion resumen_reporte.modulos del JSON (los 5
modulos de verificacion resumidos: deslizamiento, nodos levantados, base
reactions, asentamientos y presion de contacto). No importa nada del
programa SAP2000 que produce el JSON - solo conoce su forma.

Severidad (paleta_estado, valores.estado) sigue el contrato descrito en
formato_reporte_docx.md: los colores exactos vienen del JSON (para no
divergir con la app que los define), report_builder solo los aplica al
texto de los callouts - las tablas de resultados ya no se colorean.
"""

from __future__ import annotations

import math
import re

from backend.report_builder.generators.base import (
    add_image_grid,
    add_results_table,
    add_spacer,
    add_table_caption,
    resolve_image_path,
    set_run_color,
)
from backend.report_builder.generators.formato import estado_ratio, fmt_num

MODULE_ID = "fundaciones"

# Orden fijo de presentacion de los 5 modulos, segun formato_reporte_docx.md.
# Base Reactions esta integrado al flujo normal (ya no aparte al final).
MODULOS_INFORME = (
    ("deslizamiento", "Deslizamiento"),
    ("nodos_levantados", "Porcentaje comprimido"),
    ("base_reactions", "Reacciones basales"),
    ("asentamientos", "Asentamientos (totales y diferenciales)"),
    ("presion_contacto", "Tensiones admisibles"),
)

# Modulos que se anuncian en la introduccion (todos salvo Base Reactions,
# que nunca fue una verificacion en si - un resumen de reacciones basales).
MODULOS_VERIFICACION = tuple((key, titulo) for key, titulo in MODULOS_INFORME if key != "base_reactions")

# Modulos que pueden traer imagenes de diagrama (visor de mapa de color).
MODULOS_CON_IMAGENES = {"nodos_levantados", "asentamientos", "presion_contacto"}

# Renombres de titulo de capitulo respecto del "titulo" que trae el JSON
# (que sigue usando los nombres historicos del modulo SAP2000).
TITULOS_OVERRIDE = {module_key: titulo for module_key, titulo in MODULOS_INFORME}

# Paleta de severidad por defecto, para JSON generados antes de que
# "Fundaciones SAP2000" empezara a mandar paleta_estado - mismos colores que
# ya usa la app (verde/ambar/rojo).
DEFAULT_PALETA = {
    "ok_texto": "3B6D11",
    "advertencia_texto": "854F0B",
    "error_texto": "A32D2D",
    "escala_ratio": ["639922", "8FB33A", "D69E2E", "E24B4A"],
}

# Texto introductorio de la seccion "Parametros" de cada modulo: explica
# brevemente que verifica ese capitulo antes de listar los parametros/
# valores considerados como vinetas.
PARAMETROS_INTRO = {
    "deslizamiento": (
        "En este capítulo se verifica que las fundaciones cumplan con los "
        "factores de seguridad al deslizamiento mínimo establecidos, "
        "considerando los siguientes parámetros:"
    ),
    "nodos_levantados": (
        "En este capítulo se verifica que el área de contacto entre las "
        "fundaciones y el suelo se mantenga dentro de los límites "
        "admisibles para todas las combinaciones de carga analizadas. Se "
        "consideran los siguientes parámetros:"
    ),
    "base_reactions": (
        "En este capítulo se presentan las reacciones basales obtenidas "
        "del modelo, utilizadas como base para las verificaciones de "
        "deslizamiento, asentamientos y presión de contacto, considerando "
        "los siguientes parámetros:"
    ),
    "asentamientos": (
        "En este capítulo se presentan los valores de asentamiento vertical obtenidos para las fundaciones."
    ),
    "presion_contacto": (
        "En este capítulo se presentan los valores de presión de contacto "
        "obtenidos entre la fundación y el suelo."
    ),
}

# Frase que se agrega a la introduccion solo cuando el JSON trae el grupo
# «Grupos que no cumplen», es decir, cuando existe un limite contra el cual
# comparar (ver criterio en docs/formato_reporte_docx.md, seccion 7).
COMPARACION_LIMITE = {
    "asentamientos": " Los valores se comparan con los límites admisibles del proyecto.",
    "presion_contacto": " Los valores se comparan con la capacidad admisible del terreno.",
}

# Renombres puntuales de etiquetas de `valores` que trae el JSON, para que
# el texto final no repita la jerga interna del modulo SAP2000.
LABEL_OVERRIDES = {
    "nodos_levantados": {
        "% mínimo comprimido": "Porcentaje mínimo comprimido",
        "Grupos evaluados": "Fundaciones evaluadas",
    },
}

# Anchos de columna (pulgadas) de la tabla "Fundaciones criticas": Fundacion,
# Combinacion (mas ancha), metrica.
FILAS_CRITICAS_COL_WIDTHS = [1.1, 3.3, 1.1]
FILAS_CRITICAS_COL_WIDTHS_RATIO = [0.9, 2.3, 1.1, 0.8, 0.9]

ESTADOS_ADVERTENCIA = {"rev", "warn", "advertencia"}


_TON_RE = re.compile(r"\bTon(?=$|[\s/·\-)])")


def _unidades(texto) -> str:
    """Unidades historicas del programa («Ton», «Ton/m²») a la convención
    de la aplicación (`tonf`, `tonf/m²`)."""
    return _TON_RE.sub("tonf", str(texto))


def _fmt(value) -> str:
    """Formatea un valor para mostrarlo, con sentido de ingenieria:
    floats con 2-3 decimales sin ceros a la derecha, bools como Si/No.

    El contrato de entrada (generators/schemas/fundaciones.v1.schema.json)
    declara `valores[1]` y `filas_criticas[2]` como `string | number`, asi que
    un numero es entrada valida: antes se hacia `str(value)` a secas y el
    informe imprimia la basura de coma flotante tal cual
    (0.8300000000000001). El caso numerico se delega en `_fmt_num`, que ya
    resuelve float/int/bool/None.
    """
    if isinstance(value, (bool, int, float)) or value is None:
        return _fmt_num(value)
    return str(value)


def _fmt_num(value, decimals: int = 3) -> str:
    """Formatea un numero con hasta `decimals` decimales, sin ceros a la
    derecha. Solo aplica a float/int; el resto se devuelve tal cual."""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if value is None:
        return "—"
    return str(value)


def _color_estado(estado, paleta: dict):
    """Color de texto (hex) para un `estado` ('ok'/'error'/'rev'/'warn'/
    'advertencia'/None), o None si no corresponde colorear."""
    if estado == "ok":
        return paleta.get("ok_texto")
    if estado == "error":
        return paleta.get("error_texto")
    if estado in ESTADOS_ADVERTENCIA:
        return paleta.get("advertencia_texto")
    return None


def build(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Verificación de Fundaciones", level=1).style = style_map["heading_1"]

    resumen = data.get("resumen_reporte", {}) or {}
    modulos = resumen.get("modulos") or {}
    paleta = resumen.get("paleta_estado") or DEFAULT_PALETA
    json_dir = data.get("_json_dir")

    _add_introduccion(doc, modulos, style_map)

    for module_key, _titulo in MODULOS_INFORME:
        modulo = modulos.get(module_key)
        if not modulo:  # None -> el modulo no se evaluo (no se guardo historial): se omite entero
            continue

        titulo = TITULOS_OVERRIDE.get(module_key)
        add_spacer(doc)
        doc.add_heading(titulo, level=2).style = style_map["heading_2"]
        _add_modulo(doc, data, modulo, module_key, titulo, style_map, paleta, json_dir)


# ── Partes del informe ───────────────────────────────────────────────────────


def _add_introduccion(doc, modulos: dict, style_map: dict) -> None:
    doc.add_paragraph(
        "Se presenta a continuación la verificación de fundaciones, la "
        "cual comprende las siguientes verificaciones:",
        style=style_map["body"],
    )
    for module_key, module_title in MODULOS_VERIFICACION:
        if modulos.get(module_key):
            doc.add_paragraph(module_title, style=style_map["bullet_list"])


def _add_modulo(
    doc,
    data: dict,
    modulo: dict,
    module_key: str,
    titulo: str,
    style_map: dict,
    paleta: dict,
    json_dir,
) -> None:
    """Subseccion de un modulo: introduccion, resumen (callouts), parametros
    (solo los de entrada reales), fundaciones criticas e imagenes."""
    valores = modulo.get("valores") or []
    hay_limite = any(len(v) >= 1 and v[0] in VALORES_OMITIDOS for v in valores)
    intro = PARAMETROS_INTRO.get(module_key, f"Resultados de {titulo}.")
    if hay_limite:
        intro += COMPARACION_LIMITE.get(module_key, "")
    doc.add_paragraph(intro, style=style_map["body"])

    _add_valores_callouts(doc, modulo, module_key, paleta, style_map)

    parametros = _parametros_bullets(data, module_key)
    if parametros:
        doc.add_heading("Parámetros", level=3).style = style_map["heading_3"]
        for bullet in parametros:
            doc.add_paragraph(bullet, style=style_map["bullet_list"])

    _add_filas_criticas(doc, modulo, titulo, style_map, paleta)

    if module_key in MODULOS_CON_IMAGENES and modulo.get("imagenes"):
        if modulo.get("filas_criticas"):
            add_spacer(doc)
        _add_imagenes(doc, modulo, style_map, json_dir)


VALORES_OMITIDOS = {"Grupos que no cumplen"}


def _add_valores_callouts(doc, modulo: dict, module_key: str, paleta: dict, style_map: dict) -> None:
    """Resumen del modulo: cada `valores` como «etiqueta: valor» en negrita,
    coloreado por estado, una linea con vineta. `VALORES_OMITIDOS` no se
    muestra (redundante con la tabla de fundaciones criticas)."""
    overrides = LABEL_OVERRIDES.get(module_key, {})
    for v in modulo.get("valores") or []:
        if len(v) < 2:
            continue
        etiqueta, valor = v[0], v[1]
        if etiqueta in VALORES_OMITIDOS:
            continue
        estado = v[2] if len(v) > 2 else None
        paragraph = doc.add_paragraph(style=style_map["bullet_list"])
        label_run = paragraph.add_run(f"{_unidades(overrides.get(etiqueta, etiqueta))}: ")
        label_run.bold = True
        value_run = paragraph.add_run(_unidades(_fmt(valor)))
        value_run.bold = True
        color = _color_estado(estado, paleta)
        if color:
            set_run_color(value_run, color)


def _parametros_bullets(data: dict, module_key: str) -> list:
    """Parametros de ENTRADA de un modulo. Solo el deslizamiento los tiene
    (angulo y coeficiente de friccion, FS minimos, en el bloque de contexto
    `nodos_levantados`). Los `valores` son resultados y se muestran en el
    resumen, no aqui."""
    if module_key != "deslizamiento":
        return []
    ctx = data.get("nodos_levantados", {}) or {}
    phi = ctx.get("phi")
    fs_e = ctx.get("fs_deslizamiento_e")
    fs_d = ctx.get("fs_deslizamiento_d")
    bullets = []
    if phi is not None:
        coef_friccion = math.tan(math.radians(phi) * 2 / 3)
        bullets.append(f"Ángulo de fricción: φ = {fmt_num(phi, 1)}°")
        bullets.append(f"Coeficiente de fricción: μ = tan(2/3·φ) = {fmt_num(coef_friccion, 3)}")
    if fs_e is not None:
        bullets.append(f"Factor de seguridad mínimo (estático): FS = {fmt_num(fs_e, 2)}")
    if fs_d is not None:
        bullets.append(f"Factor de seguridad mínimo (dinámico): FS = {fmt_num(fs_d, 2)}")
    return bullets


def _add_filas_criticas(doc, modulo: dict, titulo: str, style_map: dict, paleta: dict) -> None:
    filas_criticas = [fc for fc in (modulo.get("filas_criticas") or []) if len(fc) >= 3]
    if not filas_criticas:
        return

    doc.add_heading("Fundaciones críticas", level=3).style = style_map["heading_3"]
    add_table_caption(doc, f"{titulo}: fundaciones y combinaciones que gobiernan", style_map)
    metric_label = _unidades(modulo.get("columna_metrica") or "Valor")

    # Razón demanda/capacidad y estado solo si el JSON trae el ratio.
    con_ratio = any(len(fc) > 3 and isinstance(fc[3], (int, float)) for fc in filas_criticas)
    headers = ["Fundación", "Combinación", metric_label]
    widths = list(FILAS_CRITICAS_COL_WIDTHS)
    rows = [[fc[0], fc[1], _unidades(_fmt(fc[2]))] for fc in filas_criticas]
    if con_ratio:
        headers += ["Razón D/C", "Estado"]
        widths = FILAS_CRITICAS_COL_WIDTHS_RATIO
        for row, fc in zip(rows, filas_criticas):
            ratio = fc[3] if len(fc) > 3 else None
            row += [fmt_num(ratio, 2), estado_ratio(ratio)]

    add_results_table(
        doc,
        headers,
        rows,
        style_map,
        widths=widths,
        numeric_cols=(2, 3) if con_ratio else (2,),
        status_col=4 if con_ratio else None,
        status_colors={"Cumple": paleta.get("ok_texto"), "No cumple": paleta.get("error_texto")},
    )


def _add_imagenes(doc, modulo: dict, style_map: dict, json_dir) -> None:
    imagenes = modulo.get("imagenes") or []
    if not imagenes:
        return

    resueltas = []
    for img in imagenes:
        ruta_abs = resolve_image_path(json_dir, img.get("ruta"))
        if not ruta_abs:
            continue
        resueltas.append({**img, "ruta": ruta_abs})

    add_image_grid(doc, resueltas, style_map)
