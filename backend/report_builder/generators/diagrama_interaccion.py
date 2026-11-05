"""
Generador de la seccion "Diagrama de interaccion" a partir del JSON que arma
`POST /diagrama/reporte/bundle` (el programa "Diagrama de interaccion" lo
envia al proyecto como ZIP junto con los PNG de los graficos; cada
`imagenes[].ruta` es relativa a la carpeta del JSON).

Contrato de entrada: generators/schemas/diagrama_interaccion.v1.schema.json.
Este modulo no importa nada del motor de calculo: solo conoce la forma del
JSON.
"""

from __future__ import annotations

import math
from pathlib import Path

from backend.report_builder.generators.base import (
    add_centered_image,
    add_results_table,
    add_spacer,
    add_table_caption,
    resolve_image_path,
)
from backend.report_builder.generators.formato import fmt_num, normalizar_unidad

MODULE_ID = "diagrama_interaccion"

ADVERTENCIA_DEFECTO = (
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real."
)

NOTAS_DEFECTO = (
    "Diseño a flexocompresión según ACI 318-25: la capacidad de diseño es φPn y φMn, con el factor φ "
    "dependiente de la deformación neta de tracción.",
    "El diagrama se construye con compresión positiva. La utilización de cada caso es la razón entre la "
    "demanda (Mx, My) y la capacidad en el corte horizontal del mismo nivel de carga axial N.",
    "Un caso queda «dentro» cuando su punto (Mx, My) cae dentro del contorno de capacidad de diseño.",
)

# Con más casos que esto, la tabla muestra los de mayor utilización y lo avisa.
MAX_FILAS_CASOS = 40

COLOR_OK = "3B6D11"
COLOR_ERROR = "A32D2D"

_TIPO_COLUMNA = {"tied": "Con estribos", "spiral": "Con zuncho (espiral)"}


def _num(value, decimals: int = 2) -> str:
    """Numero con coma decimal omitida (punto) y sin NaN/infinitos."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "—"
        if isinstance(value, int):
            return str(value)
        return f"{value:.{decimals}f}"
    return str(value)


def build(doc, data: dict, style_map: dict) -> None:
    titulo = data.get("titulo") or "Diagrama de interacción"
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    intro = "A continuación se presentan los datos de la sección de hormigón armado, su armadura, "
    intro += "las demandas verificadas y el diagrama de interacción resultante"
    norma = data.get("norma")
    intro += f", según {norma}." if norma else "."
    doc.add_paragraph(intro, style=style_map["body"])

    unidades = data.get("unidades") or {}
    _add_seccion(doc, data.get("seccion") or {}, unidades, style_map)
    _add_armadura(doc, data.get("armadura") or {}, style_map)
    _add_demandas(doc, data, unidades, style_map)
    _add_corte(doc, data.get("corte") or {}, unidades, style_map)
    _add_diagramas(doc, data.get("imagenes") or [], style_map, data.get("_json_dir"))
    _add_notas(doc, data, style_map)


def _tabla_kv(doc, titulo: str, filas: list, style_map: dict) -> None:
    """Tabla Parámetro / Valor / Unidad; los valores ya vienen formateados."""
    add_table_caption(doc, titulo, style_map)
    add_results_table(
        doc, ["Parámetro", "Valor", "Unidad"], filas, style_map, widths=[3.6, 1.4, 1.0], numeric_cols=(1,)
    )
    add_spacer(doc)


def _add_seccion(doc, seccion: dict, unidades: dict, style_map: dict) -> None:
    if not seccion:
        return
    doc.add_heading("Datos de la sección", level=2).style = style_map["heading_2"]

    rows = []
    if seccion.get("tipo") == "circ":
        rows.append(("Forma", "Circular", "—"))
        rows.append(("Diámetro", _num(seccion.get("diametro_mm"), 0), "mm"))
    else:
        rows.append(("Forma", "Rectangular", "—"))
        rows.append(("Ancho, b", _num(seccion.get("b_mm"), 0), "mm"))
        rows.append(("Alto, d", _num(seccion.get("d_mm"), 0), "mm"))
    rows.append(("Área bruta, Ag", fmt_num(seccion.get("ag_mm2"), 0), "mm²"))
    if seccion.get("recubrimiento_mm") is not None:
        rows.append(("Recubrimiento", _num(seccion.get("recubrimiento_mm"), 0), "mm"))
    if seccion.get("tipo_columna"):
        rows.append(
            ("Tipo de columna", _TIPO_COLUMNA.get(seccion["tipo_columna"], seccion["tipo_columna"]), "—")
        )
    rows.append(("Resistencia del hormigón, f'c", _num(seccion.get("fc_mpa"), 1), "MPa"))
    rows.append(("Fluencia del acero, fy", _num(seccion.get("fy_mpa"), 0), "MPa"))
    _tabla_kv(doc, "Datos de la sección y de los materiales", rows, style_map)


def _add_armadura(doc, armadura: dict, style_map: dict) -> None:
    if not armadura:
        return
    doc.add_heading("Armadura", level=2).style = style_map["heading_2"]
    grupos = armadura.get("grupos") or []
    if grupos:
        add_table_caption(doc, "Armadura longitudinal por diámetro", style_map)
        add_results_table(
            doc,
            ["Diámetro [mm]", "Cantidad", "Área unitaria [mm²]", "Área total [mm²]"],
            [
                [
                    _num(g.get("diametro_mm"), 0),
                    _num(g.get("cantidad")),
                    fmt_num(g.get("area_unitaria_mm2"), 1),
                    fmt_num(g.get("area_total_mm2"), 1),
                ]
                for g in grupos
            ],
            style_map,
            numeric_cols=(0, 1, 2, 3),
        )
        add_spacer(doc)

    rows = [
        ("Número de barras", _num(armadura.get("n_barras")), "—"),
        ("Área total de acero, Ast", fmt_num(armadura.get("ast_mm2"), 1), "mm²"),
        (
            "Cuantía, ρ = Ast/Ag",
            fmt_num(armadura.get("rho") * 100, 2) if armadura.get("rho") is not None else "—",
            "%",
        ),
    ]
    trans = armadura.get("transversal") or {}
    for key, label, decimals, unidad in (
        ("diametro_estribo_mm", "Diámetro de estribos", 0, "mm"),
        ("separacion_mm", "Separación de estribos", 0, "mm"),
        ("fy_estribo_mpa", "Fluencia de estribos", 0, "MPa"),
        ("n_estribos", "Número de estribos", 0, "—"),
        ("n_trabas", "Número de trabas", 0, "—"),
    ):
        if trans.get(key) is not None:
            rows.append((label, _num(trans[key], decimals), unidad))
    _tabla_kv(doc, "Resumen de la armadura", rows, style_map)


def _add_demandas(doc, data: dict, unidades: dict, style_map: dict) -> None:
    casos = data.get("casos") or []
    resultados = data.get("resultados") or {}
    if not casos and not resultados:
        return
    doc.add_heading("Demandas y resultados", level=2).style = style_map["heading_2"]

    if resultados:
        rows = []
        if resultados.get("n_casos") is not None:
            rows.append(("Casos verificados", _num(resultados["n_casos"]), "—"))
        if resultados.get("n_fuera") is not None:
            rows.append(("Casos fuera del diagrama", _num(resultados["n_fuera"]), "—"))
        if resultados.get("gobernante"):
            rows.append(("Caso gobernante", resultados["gobernante"], "—"))
        if resultados.get("utilizacion_max") is not None:
            rows.append(("Utilización máxima", fmt_num(resultados["utilizacion_max"], 2), "—"))
        if rows:
            _tabla_kv(doc, "Resumen de resultados", rows, style_map)

    if not casos:
        return
    fuerza = normalizar_unidad(unidades.get("fuerza") or "kN")
    momento = normalizar_unidad(unidades.get("momento") or "kN·m")
    ordenados = sorted(casos, key=lambda c: -(c.get("utilizacion") or 0.0))
    mostrados = ordenados[:MAX_FILAS_CASOS]
    if len(casos) > len(mostrados):
        doc.add_paragraph(
            f"Se muestran los {len(mostrados)} casos de mayor utilización, de {len(casos)} verificados.",
            style=style_map["body"],
        )
    filas = []
    for c in mostrados:
        nombre = str(c.get("nombre", ""))
        if c.get("gobernante"):
            nombre += " (gobernante)"
        filas.append(
            [
                nombre,
                fmt_num(c.get("n")),
                fmt_num(c.get("mx")),
                fmt_num(c.get("my")),
                fmt_num(c.get("utilizacion"), 2),
                "Cumple" if c.get("estado") == "dentro" else "No cumple",
            ]
        )
    add_table_caption(doc, "Demandas, utilización y estado de cada caso", style_map)
    add_results_table(
        doc,
        ["Caso", f"N [{fuerza}]", f"Mx [{momento}]", f"My [{momento}]", "Utilización", "Estado"],
        filas,
        style_map,
        widths=[1.6, 0.9, 0.9, 0.9, 0.9, 0.8],
        numeric_cols=(1, 2, 3, 4),
        status_col=5,
        status_colors={"Cumple": COLOR_OK, "No cumple": COLOR_ERROR},
    )
    add_spacer(doc)


def _add_corte(doc, corte: dict, unidades: dict, style_map: dict) -> None:
    """Diseño al corte (ACI 318): dimensiones y armadura transversal por dirección y razón
    demanda/capacidad de cada caso, con el biaxial combinado."""
    if not corte:
        return
    doc.add_heading("Diseño al corte", level=2).style = style_map["heading_2"]
    fuerza = normalizar_unidad(unidades.get("fuerza") or "kN")

    filas = [
        ("Ancho del alma en x, bw", fmt_num(corte.get("bw_x_mm"), 0), "mm"),
        ("Altura útil en x, d", fmt_num(corte.get("d_x_mm"), 0), "mm"),
        ("Armadura transversal en x, Av", fmt_num(corte.get("av_x_mm2"), 1), "mm²"),
        ("Ancho del alma en y, bw", fmt_num(corte.get("bw_y_mm"), 0), "mm"),
        ("Altura útil en y, d", fmt_num(corte.get("d_y_mm"), 0), "mm"),
        ("Armadura transversal en y, Av", fmt_num(corte.get("av_y_mm2"), 1), "mm²"),
    ]
    _tabla_kv(doc, "Parámetros del diseño al corte", filas, style_map)
    for nota in corte.get("notas") or []:
        doc.add_paragraph(str(nota), style=style_map["bullet_list"])

    casos = corte.get("casos") or []
    if not casos:
        return
    if corte.get("gobernante"):
        veredicto = "cumple" if corte.get("cumple") else "no cumple"
        doc.add_paragraph(
            f"El caso gobernante es {corte['gobernante']}, con una razón demanda/capacidad máxima de "
            f"{fmt_num(corte.get('ratio_max'), 2)}. La sección {veredicto} al corte.",
            style=style_map["body"],
        )
    ordenados = sorted(
        casos, key=lambda c: -max(c.get("ratio_x") or 0, c.get("ratio_y") or 0, c.get("ratio_combinada") or 0)
    )
    mostrados = ordenados[:MAX_FILAS_CASOS]
    if len(casos) > len(mostrados):
        doc.add_paragraph(
            f"Se muestran los {len(mostrados)} casos de mayor razón, de {len(casos)} verificados.",
            style=style_map["body"],
        )
    filas = [
        [
            c.get("nombre", ""),
            fmt_num(c.get("vu_x")),
            fmt_num(c.get("phi_vn_x")),
            fmt_num(c.get("ratio_x"), 2),
            fmt_num(c.get("vu_y")),
            fmt_num(c.get("phi_vn_y")),
            fmt_num(c.get("ratio_y"), 2),
            fmt_num(c.get("ratio_combinada"), 2),
            "Cumple" if c.get("cumple") else "No cumple",
        ]
        for c in mostrados
    ]
    add_table_caption(doc, "Verificación al corte de cada caso", style_map)
    add_results_table(
        doc,
        [
            "Caso",
            f"Vu,x [{fuerza}]",
            f"φVn,x [{fuerza}]",
            "D/C x",
            f"Vu,y [{fuerza}]",
            f"φVn,y [{fuerza}]",
            "D/C y",
            "D/C comb.",
            "Estado",
        ],
        filas,
        style_map,
        widths=[1.0, 0.7, 0.7, 0.5, 0.7, 0.7, 0.5, 0.6, 0.7],
        numeric_cols=(1, 2, 3, 4, 5, 6, 7),
        status_col=8,
        status_colors={"Cumple": COLOR_OK, "No cumple": COLOR_ERROR},
    )
    add_spacer(doc)


def _add_diagramas(doc, imagenes: list, style_map: dict, json_dir) -> None:
    rutas = []
    for imagen in imagenes:
        ruta = resolve_image_path(json_dir, imagen.get("ruta"))
        if ruta and Path(ruta).is_file():
            rutas.append((ruta, imagen.get("descripcion") or "Gráfico"))
    if not rutas:
        return
    doc.add_heading("Diagrama de interacción", level=2).style = style_map["heading_2"]
    for ruta, descripcion in rutas:
        add_centered_image(doc, ruta, style_map, caption=descripcion)


def _add_notas(doc, data: dict, style_map: dict) -> None:
    notas = data.get("notas") or list(NOTAS_DEFECTO)
    doc.add_heading("Notas", level=2).style = style_map["heading_2"]
    for nota in notas:
        doc.add_paragraph(str(nota), style=style_map["bullet_list"])
    doc.add_paragraph(data.get("advertencia") or ADVERTENCIA_DEFECTO, style=style_map["body"])
