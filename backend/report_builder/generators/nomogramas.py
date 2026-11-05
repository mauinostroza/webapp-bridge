"""
Generador de la seccion "Factor de longitud efectiva K (nomogramas de
alineacion)" a partir del JSON simple `nomogramas.json` que el programa
"Nomogramas" envia al proyecto (sin imagenes).

Contrato de entrada: generators/schemas/nomogramas.v1.schema.json.
Este modulo no importa nada del motor de calculo: solo conoce la forma del
JSON y reproduce los resultados ya calculados.
"""

from __future__ import annotations

import math

from backend.report_builder.generators.base import (
    add_key_value_table,
    add_table_caption,
    shade_table_header,
)

MODULE_ID = "nomogramas"

TITULO_DEFECTO = "Factor de longitud efectiva K (nomogramas de alineación)"

ADVERTENCIA_DEFECTO = (
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real."
)

FORMULAS_DEFECTO = {
    "arriostrado": "K = (3·GA·GB + 1.4·(GA + GB) + 0.64) / (3·GA·GB + 2·(GA + GB) + 1.28)",
    "no_arriostrado": "K = √[(1.6·GA·GB + 4·(GA + GB) + 7.5) / (GA + GB + 7.5)]",
    "alpha": "K = (1 − α)·K_arriostrado + α·K_no_arriostrado (interpolación lineal exploratoria)",
    "referencia": "Dumonteil, P. (1992). Simple equations for effective length factors. "
    "Engineering Journal, AISC, 29(3), 111-115.",
}

NOTAS_DEFECTO = (
    "G = Σ(Ic/Lc) / Σ(Iv/Lv), con A el extremo superior y B el inferior. Cada eje principal se analiza por separado.",
    "Convenciones de AISC 360 Commentary: G = 0.0 para empotramiento teórico, G = 1.0 para empotramiento "
    "real y G ≈ 10 para un extremo articulado (el límite analítico es ∞).",
    "Supuestos del método de los diagramas de alineación: miembros prismáticos, uniones rígidas y "
    "comportamiento elástico.",
    "Si el diseño emplea el método de análisis directo de AISC 360, debe revisarse si corresponde usar K = 1 "
    "en lugar de los factores obtenidos con diagramas de alineación.",
    "Las columnas con factor α son solo de estudio: no deben aplicarse en SAP2000.",
)

SOLO_ESTUDIO = "SOLO ESTUDIO (no aplicar en SAP2000)"
_ESTADO_TEXTO = {"VALIDA": "VÁLIDA", "SOLO ESTUDIO": SOLO_ESTUDIO, "INVALIDA": "INVÁLIDA"}


def _g(value) -> str:
    if value == "inf":
        return "∞"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:.3f}"


def _k(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:.3f}"


def _es_estudio(col: dict) -> bool:
    return col.get("estado") == "SOLO ESTUDIO" or str(col.get("condicion_lateral", "")).startswith(
        "Con factor α"
    )


def build(doc, data: dict, style_map: dict) -> None:
    columnas = data.get("columnas")
    if not isinstance(columnas, list) or not columnas:
        raise ValueError("Se requiere columnas con resultados del módulo de nomogramas")
    titulo = data.get("titulo") or TITULO_DEFECTO
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "Se determina el factor de longitud efectiva K de las columnas con las ecuaciones de Dumonteil, "
        "que aproximan los nomogramas de alineación de AISC 360.",
        style=style_map["body"],
    )
    _add_datos(doc, data, columnas, style_map)
    _add_resultados(doc, columnas, style_map)
    _add_formulas(doc, data, style_map)
    _add_notas(doc, data, style_map)


def _add_datos(doc, data: dict, columnas: list, style_map: dict) -> None:
    doc.add_heading("Datos de entrada", level=2).style = style_map["heading_2"]
    n_arri = sum(1 for c in columnas if c.get("condicion_lateral") == "Arriostrado")
    n_no = sum(1 for c in columnas if c.get("condicion_lateral") == "No arriostrado")
    n_alpha = sum(1 for c in columnas if _es_estudio(c))
    n_inv = sum(1 for c in columnas if c.get("estado") == "INVALIDA")
    rows = [
        ("Fecha", str(data.get("fecha", "—"))),
        ("Número de columnas", str(len(columnas))),
        ("Columnas arriostradas", str(n_arri)),
        ("Columnas no arriostradas", str(n_no)),
        ("Columnas con factor α (solo estudio)", str(n_alpha)),
        ("Columnas inválidas", str(n_inv)),
        ("Modo estudio", "Sí" if data.get("modo_estudio") else "No"),
    ]
    add_table_caption(doc, "Resumen de los datos de entrada", style_map)
    add_key_value_table(doc, rows, style_map)


def _add_resultados(doc, columnas: list, style_map: dict) -> None:
    doc.add_heading("Resultados", level=2).style = style_map["heading_2"]
    add_table_caption(doc, "Factor K por columna", style_map)
    table = doc.add_table(rows=1, cols=6)
    table.style = style_map["table_reference"]
    headers = ("Columna", "Condición lateral", "GA", "GB", "K a ingresar en SAP2000", "Estado")
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    shade_table_header(table)
    for c in columnas:
        estado = c.get("estado")
        cells = table.add_row().cells
        cells[0].text = str(c.get("etiqueta") or c.get("id") or "")
        cells[1].text = str(c.get("condicion_lateral", "—"))
        cells[2].text = _g(c.get("ga"))
        cells[3].text = _g(c.get("gb"))
        cells[4].text = _k(c.get("k")) if estado == "VALIDA" and not _es_estudio(c) else "—"
        texto = _ESTADO_TEXTO.get(estado, str(estado or "—"))
        if _es_estudio(c) and estado != "INVALIDA":
            texto = SOLO_ESTUDIO
        elif estado == "INVALIDA" and c.get("causa"):
            texto += f": {c['causa']}"
        cells[5].text = texto
    estudio = [c for c in columnas if _es_estudio(c) and c.get("estado") == "SOLO ESTUDIO"]
    if estudio:
        doc.add_paragraph(
            "Columnas con factor α (solo estudio, no aplicar en SAP2000): "
            + "; ".join(f"{c.get('etiqueta') or c.get('id')} (K = {_k(c.get('k'))})" for c in estudio)
            + ".",
            style=style_map["body"],
        )


def _add_formulas(doc, data: dict, style_map: dict) -> None:
    formulas = {**FORMULAS_DEFECTO, **(data.get("formulas") or {})}
    doc.add_heading("Fórmulas empleadas", level=2).style = style_map["heading_2"]
    doc.add_paragraph(
        f"Ecuaciones de Dumonteil (1992), con {formulas['referencia']}", style=style_map["body"]
    )
    doc.add_paragraph(f"Marco arriostrado: {formulas['arriostrado']}", style=style_map["bullet_list"])
    doc.add_paragraph(f"Marco no arriostrado: {formulas['no_arriostrado']}", style=style_map["bullet_list"])
    doc.add_paragraph(f"Factor α (solo estudio): {formulas['alpha']}", style=style_map["bullet_list"])


def _add_notas(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Notas", level=2).style = style_map["heading_2"]
    for nota in NOTAS_DEFECTO:
        doc.add_paragraph(nota, style=style_map["bullet_list"])
    for adv in data.get("advertencias") or []:
        doc.add_paragraph(str(adv), style=style_map["bullet_list"])
    doc.add_paragraph(ADVERTENCIA_DEFECTO, style=style_map["body"])
