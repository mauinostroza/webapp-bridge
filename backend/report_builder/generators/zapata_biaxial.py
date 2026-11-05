"""
Generador del informe de Zapatas biaxiales.

Contrato de entrada: generators/schemas/zapata_biaxial.v1.schema.json (numeros
crudos; el formato lo aplica este modulo con generators/formato.py). Por cada
fundacion: datos de entrada (geometria, materiales y factores), tabla de
resultados por combinacion con su estado, y la verificacion gobernante.
Criterio de presentacion en docs/formato_reporte_docx.md.
"""

from __future__ import annotations

from pathlib import Path

from backend.report_builder.generators.base import (
    add_centered_image,
    add_results_table,
    add_spacer,
    add_table_caption,
    resolve_image_path,
)
from backend.report_builder.generators.formato import (
    NBSP,
    VACIO,
    estado_ratio,
    fmt_cantidad,
    fmt_num,
    fmt_porcentaje,
)

MODULE_ID = "zapata_biaxial"

# Tope de filas de la tabla por combinacion: con mas, se muestran las de mayor
# razon de tension y se avisa en el texto.
MAX_FILAS_COMBINACIONES = 40

COLOR_OK = "3B6D11"
COLOR_ERROR = "A32D2D"

# (clave, etiqueta, unidad)
GEOMETRIA = (
    ("Lx", "Largo de la zapata, Lx", "m"),
    ("Ly", "Ancho de la zapata, Ly", "m"),
    ("hz", "Espesor de la zapata, hz", "m"),
    ("hr", "Altura de relleno, hr", "m"),
    ("Lpx", "Pedestal en x, Lpx", "m"),
    ("Lpy", "Pedestal en y, Lpy", "m"),
    ("hp", "Altura del pedestal, hp", "m"),
    ("eix", "Excentricidad del pedestal en x, eix", "m"),
    ("eiy", "Excentricidad del pedestal en y, eiy", "m"),
)
MATERIALES = (
    ("gamma_hormigon", "Peso unitario del hormigón, γh", "tonf/m³"),
    ("gamma_suelo", "Peso unitario del suelo, γs", "tonf/m³"),
    ("phi", "Ángulo de fricción, φ", "°"),
    ("sigma_adm_e", "Tensión admisible estática, σadm", "tonf/m²"),
    ("sigma_adm_d", "Tensión admisible dinámica, σadm", "tonf/m²"),
)
FACTORES = (
    ("FS_deslizamiento_e", "FS deslizamiento mínimo, estático", ""),
    ("FS_deslizamiento_d", "FS deslizamiento mínimo, dinámico", ""),
    ("FS_volcamiento_e", "FS volcamiento mínimo, estático", ""),
    ("FS_volcamiento_d", "FS volcamiento mínimo, dinámico", ""),
)
PORCENTAJES = (
    ("porc_comp_estatico", "Área comprimida mínima, estático"),
    ("porc_comp_dinamico", "Área comprimida mínima, dinámico"),
)


def _filas_datos(origen: dict, definiciones, extras=()) -> list:
    filas = []
    for clave, etiqueta, unidad in definiciones:
        if origen.get(clave) is None:
            continue
        filas.append([etiqueta, fmt_num(origen[clave], 2), unidad or VACIO])
    for clave, etiqueta in extras:
        if origen.get(clave) is not None:
            filas.append([etiqueta, fmt_porcentaje(origen[clave], 0), VACIO])
    return filas


def _tabla_datos(doc, titulo: str, filas: list, style_map: dict) -> None:
    if not filas:
        return
    add_table_caption(doc, titulo, style_map)
    add_results_table(
        doc,
        ["Parámetro", "Valor", "Unidad"],
        filas,
        style_map,
        widths=[4.0, 1.3, 1.0],
        numeric_cols=(1,),
    )
    add_spacer(doc)


def _razon_max(c: dict) -> float:
    return c.get("ratio_sigma") if isinstance(c.get("ratio_sigma"), (int, float)) else -1.0


def _fila_combinacion(c: dict) -> list:
    nombre = c.get("output_case") or c.get("caso", VACIO)
    fsd = c.get("fsd")
    return [
        nombre,
        fmt_num(c.get("P"), 1),
        fmt_num(c.get("Mx"), 1),
        fmt_num(c.get("My"), 1),
        fmt_num(c.get("sigma_max"), 2),
        fmt_porcentaje(c.get("area_comprimida"), 0),
        fmt_num(fsd, 2) if fsd is not None else VACIO,
        "Cumple" if c.get("cumple") else "No cumple",
    ]


def _add_fundacion(doc, f: dict, style_map: dict, json_dir=None) -> None:
    doc.add_heading(f["nombre"], level=2).style = style_map["heading_2"]

    doc.add_heading("Datos de entrada", level=3).style = style_map["heading_3"]
    _tabla_datos(
        doc, f"Geometría de {f['nombre']}", _filas_datos(f.get("geometria") or {}, GEOMETRIA), style_map
    )
    filas_mat = _filas_datos(f.get("materiales") or {}, MATERIALES)
    factores = f.get("factores") or {}
    filas_mat += _filas_datos(factores, FACTORES, PORCENTAJES)
    if factores.get("considerar_empuje_pasivo"):
        filas_mat.append(
            [
                "Factor de reducción del empuje pasivo",
                fmt_num(factores.get("factor_reduccion_pasivo", 1), 2),
                VACIO,
            ]
        )
    _tabla_datos(doc, f"Suelo, materiales y criterios de {f['nombre']}", filas_mat, style_map)

    cargas = f.get("cargas") or []
    if not f.get("analizada", True) or not cargas:
        doc.add_paragraph("La fundación no tiene resultados de análisis vigentes.", style=style_map["body"])
        return

    doc.add_heading("Resultados por combinación", level=3).style = style_map["heading_3"]
    ordenadas = sorted(cargas, key=_razon_max, reverse=True)
    mostradas = ordenadas[:MAX_FILAS_COMBINACIONES]
    if len(cargas) > len(mostradas):
        doc.add_paragraph(
            f"Se muestran las {len(mostradas)} combinaciones con mayor razón de tensión, de {len(cargas)} analizadas.",
            style=style_map["body"],
        )
    add_table_caption(doc, f"Resultados por combinación de {f['nombre']}", style_map)
    add_results_table(
        doc,
        [
            "Combinación",
            "P [tonf]",
            "Mx [tonf·m]",
            "My [tonf·m]",
            "σmáx [tonf/m²]",
            "Área comp.",
            "FSD",
            "Estado",
        ],
        [_fila_combinacion(c) for c in mostradas],
        style_map,
        widths=[1.3, 0.7, 0.8, 0.8, 0.9, 0.7, 0.5, 0.8],
        numeric_cols=(1, 2, 3, 4, 5, 6),
        status_col=7,
        status_colors={"Cumple": COLOR_OK, "No cumple": COLOR_ERROR},
    )
    add_spacer(doc)
    _add_gobernante(doc, f, cargas, style_map)
    _add_figuras(doc, f, cargas, style_map, json_dir)


def _add_figuras(doc, f: dict, cargas: list, style_map: dict, json_dir) -> None:
    figuras = f.get("figuras") or {}
    idx = f.get("gobernante")
    comb = ""
    if idx is not None and 0 <= idx < len(cargas):
        comb = cargas[idx].get("output_case") or cargas[idx].get("caso", "")
    sufijo = f" — {comb}" if comb else ""
    imagenes = []
    for clave, titulo in (("planta", "Planta"), ("elevacion", "Elevación")):
        ruta = resolve_image_path(json_dir, figuras.get(clave))
        if ruta and Path(ruta).is_file():
            imagenes.append((ruta, f"{titulo} de {f['nombre']}{sufijo}"))
    if not imagenes:
        return
    doc.add_heading("Esquemas", level=3).style = style_map["heading_3"]
    for ruta, descripcion in imagenes:
        add_centered_image(doc, ruta, style_map, caption=descripcion, width_in=4.8)


def _add_gobernante(doc, f: dict, cargas: list, style_map: dict) -> None:
    idx = f.get("gobernante")
    if idx is None or not (0 <= idx < len(cargas)):
        return
    c = cargas[idx]
    doc.add_heading("Verificación gobernante", level=3).style = style_map["heading_3"]
    nombre = c.get("output_case") or c.get("caso", VACIO)
    resumen = "cumple" if f.get("cumple") else "no cumple"
    doc.add_paragraph(
        f"La combinación gobernante es {nombre} y la fundación {resumen}.", style=style_map["body"]
    )

    filas = []
    if c.get("sigma_max") is not None and c.get("sigma_adm"):
        filas.append(
            [
                "Tensión máxima de contacto",
                fmt_cantidad(c["sigma_max"], "tonf/m²", 2),
                fmt_cantidad(c["sigma_adm"], "tonf/m²", 2),
                fmt_num(c["sigma_max"] / c["sigma_adm"], 2),
                estado_ratio(c["sigma_max"] / c["sigma_adm"]),
            ]
        )
    if c.get("area_comprimida") is not None and c.get("porc_comp_min") is not None:
        ratio = c["porc_comp_min"] / c["area_comprimida"] if c["area_comprimida"] > 0 else 1.2
        filas.append(
            [
                "Área comprimida",
                fmt_porcentaje(c["area_comprimida"], 0),
                f"≥{NBSP}{fmt_porcentaje(c['porc_comp_min'], 0)}",
                fmt_num(ratio, 2),
                estado_ratio(ratio),
            ]
        )
    if c.get("fsd") is not None and c.get("fsd_req"):
        ratio = c["fsd_req"] / c["fsd"] if c["fsd"] > 0 else 1.2
        filas.append(
            [
                "Deslizamiento (FS)",
                fmt_num(c["fsd"], 2),
                f"≥{NBSP}{fmt_num(c['fsd_req'], 2)}",
                fmt_num(ratio, 2),
                estado_ratio(ratio),
            ]
        )
    for eje, clave in (("x", "fsv_x"), ("y", "fsv_y")):
        if c.get(clave) is not None and c.get("fsv_req"):
            ratio = c["fsv_req"] / c[clave] if c[clave] > 0 else 1.2
            filas.append(
                [
                    f"Volcamiento en {eje} (FS)",
                    fmt_num(c[clave], 2),
                    f"≥{NBSP}{fmt_num(c['fsv_req'], 2)}",
                    fmt_num(ratio, 2),
                    estado_ratio(ratio),
                ]
            )
    if not filas:
        return
    add_table_caption(doc, f"Verificaciones de la combinación gobernante de {f['nombre']}", style_map)
    add_results_table(
        doc,
        ["Verificación", "Demanda", "Límite", "Razón D/C", "Estado"],
        filas,
        style_map,
        widths=[2.4, 1.2, 1.2, 0.8, 0.8],
        numeric_cols=(1, 2, 3),
        status_col=4,
        status_colors={"Cumple": COLOR_OK, "No cumple": COLOR_ERROR},
    )


def build(doc, data: dict, style_map: dict) -> None:
    doc.add_heading(data.get("titulo") or "Zapatas biaxiales", level=1).style = style_map["heading_1"]
    fundaciones = data.get("fundaciones") or []
    doc.add_paragraph(
        "Se presenta la verificación de zapatas rectangulares rígidas bajo flexión biaxial: tensión de contacto, "
        "área comprimida, deslizamiento y volcamiento, para cada combinación de carga analizada.",
        style=style_map["body"],
    )
    for f in fundaciones:
        add_spacer(doc)
        _add_fundacion(doc, f, style_map, data.get("_json_dir"))
