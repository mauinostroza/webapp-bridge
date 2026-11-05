"""
Generador de la seccion "Parametros Sismicos" a partir del JSON de salida del
programa "Espectro" (boton "Exportar informe (JSON)"), que exporta el JSON
junto a un PNG del grafico en la misma carpeta.

Contrato de entrada: generators/schemas/espectro.v1.schema.json. Este modulo
no importa nada del programa "Espectro" - solo conoce la forma del JSON que
ese programa exporta (`nch2369_app.py::_export_report_json`): parametros de
entrada, propiedades del suelo, referencia al grafico (ruta relativa a la
carpeta del JSON) y valores finales (cortes basales, peso sismico, factores
R1).
"""

from __future__ import annotations

from backend.report_builder.generators.base import (
    add_centered_image,
    add_results_table,
    add_spacer,
    add_table_caption,
    resolve_image_path,
)
from backend.report_builder.generators.formato import fmt_num

MODULE_ID = "espectro"


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


# Decimales por magnitud (docs/CONVENCIONES_INTERFAZ.md, Espectro): períodos y factores 2,
# R 1, ξ, T0, T1 y Q 3. Lo que no está aquí usa la regla general (>= 10 → 1; < 10 → 3).
DECIMALES_PARAMETRO = (
    ("Zona", 0),
    ("Factor de reducción R", 1),
    ("Amortiguamiento", 3),
    ("Período", 2),
    ("Factor", 2),
)
DECIMALES_SUELO = {"S": 2, "r": 2, "p": 2, "q": 3, "T0": 3, "T1": 3}
UNIDADES_SUELO = {"T0": "s", "T1": "s"}


def _decimales_parametro(etiqueta: str):
    for prefijo, decimales in DECIMALES_PARAMETRO:
        if str(etiqueta).startswith(prefijo):
            return decimales
    return None


def _valor(valor, decimales=None) -> str:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return _fmt(valor)
    if isinstance(valor, int):
        return str(valor)
    return fmt_num(valor, decimales)


def build(doc, data: dict, style_map: dict) -> None:
    titulo = data.get("titulo") or "Parámetros Sísmicos"
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "A continuación se presentan los parámetros sísmicos considerados "
        "según NCh2369, el espectro de diseño resultante y los valores "
        "finales obtenidos del análisis.",
        style=style_map["body"],
    )

    _add_parametros_entrada(doc, data.get("parametros_entrada") or [], style_map)
    _add_propiedades_suelo(doc, data.get("propiedades_suelo") or {}, style_map)
    _add_grafico(doc, data.get("grafico") or {}, style_map, data.get("_json_dir"))
    _add_valores_finales(doc, data.get("valores_finales") or {}, style_map)


def _add_parametros_entrada(doc, parametros: list, style_map: dict) -> None:
    if not parametros:
        return
    doc.add_heading("Parámetros de Entrada", level=2).style = style_map["heading_2"]
    add_table_caption(doc, "Parámetros de entrada", style_map)
    filas = [
        [
            _fmt(item.get("etiqueta")),
            _valor(item.get("valor"), _decimales_parametro(item.get("etiqueta"))),
            item.get("comentario") or "—",
        ]
        for item in parametros
    ]
    add_results_table(
        doc, ["Parámetro", "Valor", "Unidad"], filas, style_map, widths=[3.6, 1.4, 1.0], numeric_cols=(1,)
    )
    add_spacer(doc)


def _add_propiedades_suelo(doc, suelo: dict, style_map: dict) -> None:
    if not suelo:
        return
    doc.add_heading("Propiedades del Suelo", level=2).style = style_map["heading_2"]
    add_table_caption(doc, "Propiedades del suelo de fundación", style_map)
    nombres = {"tipo": "Tipo de suelo", "S": "S", "r": "r", "T0": "T0", "p": "p", "q": "q", "T1": "T1"}
    filas = [
        [nombre, _valor(suelo.get(clave), DECIMALES_SUELO.get(clave)), UNIDADES_SUELO.get(clave, "—")]
        for clave, nombre in nombres.items()
        if clave in suelo
    ]
    add_results_table(
        doc, ["Parámetro", "Valor", "Unidad"], filas, style_map, widths=[3.6, 1.4, 1.0], numeric_cols=(1,)
    )
    add_spacer(doc)


def _add_grafico(doc, grafico: dict, style_map: dict, json_dir) -> None:
    ruta = grafico.get("ruta")
    ruta_abs = resolve_image_path(json_dir, ruta)
    if not ruta_abs:
        return

    doc.add_heading("Espectro de Diseño", level=2).style = style_map["heading_2"]
    add_centered_image(doc, ruta_abs, style_map, caption=grafico.get("descripcion") or "Espectro de diseño")


def _add_valores_finales(doc, valores: dict, style_map: dict) -> None:
    if not valores:
        return
    doc.add_heading("Valores Finales", level=2).style = style_map["heading_2"]

    filas = []
    if valores.get("peso_sismico_Ps") is not None:
        filas.append(["Peso sísmico Ps", _valor(valores["peso_sismico_Ps"]), "tonf"])

    cortes = valores.get("cortes") or {}
    for clave, etiqueta in (
        ("Qx", "Corte basal Qx"),
        ("Qy", "Corte basal Qy"),
        ("Qmin", "Corte mínimo Qmin"),
        ("Qmax", "Corte máximo Qmax"),
    ):
        if clave in cortes:
            filas.append([etiqueta, _valor(cortes[clave]), "tonf"])

    for grupo, prefijo, claves in (
        ("ajustes_qmin", "Ajuste Qmin", (("X", "dirección X"), ("Y", "dirección Y"))),
        ("ajustes_qmax", "Ajuste Qmax", (("X", "dirección X"), ("Y", "dirección Y"))),
        ("factores_R1", "Factor R1", (("R1_X", "dirección X"), ("R1_Y", "dirección Y"))),
    ):
        origen = valores.get(grupo) or {}
        for clave, direccion in claves:
            if clave in origen:
                filas.append([f"{prefijo} — {direccion}", _valor(origen[clave], 2), "—"])

    if not filas:
        return
    add_table_caption(doc, "Valores finales del análisis sísmico", style_map)
    add_results_table(
        doc, ["Resultado", "Valor", "Unidad"], filas, style_map, widths=[3.6, 1.4, 1.0], numeric_cols=(1,)
    )
