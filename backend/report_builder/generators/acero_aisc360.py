"""
Generador del informe de Acero AISC 360-22.

Contrato de entrada: generators/schemas/acero_aisc360.v1.schema.json (numeros
crudos; el formato lo aplica este modulo con generators/formato.py). Estructura:
datos del perfil y del material, demandas, una tabla de estados limite por
capitulo (resistencia de diseno, demanda y razon D/C con su estado) y el
resumen con el elemento que gobierna. Criterio en docs/formato_reporte_docx.md.
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
    VACIO,
    estado_ratio,
    fmt_num,
    normalizar_unidad,
)

MODULE_ID = "acero_aisc360"

COLOR_OK = "3B6D11"
COLOR_ERROR = "A32D2D"
ESTADOS_COLOR = {"Cumple": COLOR_OK, "No cumple": COLOR_ERROR}

ETIQUETAS_RESUMEN = {
    "tension": "Tracción",
    "compression": "Compresión",
    "flexure_major": "Flexión en el eje fuerte",
    "flexure_minor": "Flexión en el eje débil",
    "shear": "Cortante",
    "interaction": "Interacción de esfuerzos (H1)",
}

PROPIEDADES = (
    ("d", "Altura, d", "mm", 1),
    ("bf", "Ancho del ala, bf", "mm", 1),
    ("tf", "Espesor del ala, tf", "mm", 1),
    ("tw", "Espesor del alma, tw", "mm", 1),
    ("area_mm2", "Área, A", "cm²", 1e-2),
    ("weight_kg_m", "Peso", "kgf/m", 1),
    ("Ix_mm4", "Inercia, Ix", "cm⁴", 1e-4),
    ("Zx_mm3", "Módulo plástico, Zx", "cm³", 1e-3),
)
LONGITUDES = (
    ("Lx", "Largo de pandeo en x, Lx", "mm"),
    ("Ly", "Largo de pandeo en y, Ly", "mm"),
    ("Lb", "Largo no arriostrado en flexión, Lb", "mm"),
    ("Lz", "Largo torsional, Lz", "mm"),
    ("Kx", "Factor de longitud efectiva, Kx", ""),
    ("Ky", "Factor de longitud efectiva, Ky", ""),
    ("Kz", "Factor de longitud efectiva, Kz", ""),
    ("Cb", "Coeficiente de flexión, Cb", ""),
)


def _fmt_ratio(ratio, con_demanda: bool) -> str:
    if ratio is None:
        return "∞" if con_demanda else VACIO
    return fmt_num(ratio, 2)


def _tabla_clave_valor(doc, titulo: str, filas: list, style_map: dict) -> None:
    if not filas:
        return
    add_table_caption(doc, titulo, style_map)
    add_results_table(
        doc,
        ["Parámetro", "Valor", "Unidad"],
        filas,
        style_map,
        widths=[3.6, 1.4, 1.0],
        numeric_cols=(1,),
    )
    add_spacer(doc)


def _add_datos(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Datos de entrada", level=2).style = style_map["heading_2"]
    perfil = data["perfil"]
    props = perfil.get("propiedades") or {}
    filas = [["Perfil", perfil["designacion"], VACIO]]
    for clave, etiqueta, unidad, factor in PROPIEDADES:
        if clave in props:
            filas.append([etiqueta, fmt_num(props[clave] * factor), unidad])
    _tabla_clave_valor(doc, "Perfil", filas, style_map)
    ruta = resolve_image_path(data.get("_json_dir"), perfil.get("figura"))
    if ruta and Path(ruta).is_file():
        add_centered_image(
            doc,
            ruta,
            style_map,
            caption=f"Sección transversal del perfil {perfil['designacion']}",
            width_in=3.2,
        )

    mat = data.get("material") or {}
    filas = [
        [f"{nombre}", fmt_num(mat[clave]), "MPa"]
        for clave, nombre in (("Fy", "Tensión de fluencia, Fy"), ("Fu", "Tensión de rotura, Fu"))
        if clave in mat
    ]
    filas.append(["Método de diseño", data.get("metodo", VACIO), VACIO])
    _tabla_clave_valor(doc, "Material y método de diseño", filas, style_map)

    lon = data.get("longitudes") or {}
    filas = [
        [etiqueta, fmt_num(lon[clave]), unidad or VACIO]
        for clave, etiqueta, unidad in LONGITUDES
        if lon.get(clave) is not None
    ]
    _tabla_clave_valor(doc, "Longitudes y factores", filas, style_map)

    dem = data.get("demandas") or {}
    uf = normalizar_unidad(data.get("unidad_fuerza") or "tonf")
    nombres = (
        ("Pu", "Compresión, Pu", uf),
        ("Tu_axial", "Tracción, Tu", uf),
        ("Mux", "Momento en el eje fuerte, Mux", f"{uf}·m"),
        ("Muy", "Momento en el eje débil, Muy", f"{uf}·m"),
        ("Vux", "Corte, Vux", uf),
    )
    filas = [[etiqueta, fmt_num(dem[clave]), unidad] for clave, etiqueta, unidad in nombres if dem.get(clave)]
    if filas:
        _tabla_clave_valor(doc, "Demandas (resistencia requerida)", filas, style_map)


def _add_capitulo(doc, cap: dict, style_map: dict) -> None:
    titulo = cap["titulo"] + (f" ({cap['capitulo']})" if cap.get("capitulo") else "")
    doc.add_heading(titulo, level=3).style = style_map["heading_3"]
    unidad = normalizar_unidad(cap["unidad"])
    filas = []
    for e in cap["estados"]:
        con_demanda = bool(e.get("con_demanda"))
        estado = estado_ratio(e.get("ratio")) if con_demanda and e.get("ratio") is not None else VACIO
        if con_demanda and e.get("ratio") is None:
            estado = "No cumple"
        filas.append(
            [
                e["descripcion"],
                e.get("ecuacion") or VACIO,
                fmt_num(e.get("resistencia")),
                fmt_num(e.get("demanda")) if con_demanda else VACIO,
                _fmt_ratio(e.get("ratio"), con_demanda) if con_demanda else VACIO,
                estado,
            ]
        )
    add_table_caption(doc, f"Estados límite de {cap['titulo'].lower()}", style_map)
    add_results_table(
        doc,
        [
            "Estado límite",
            "Ecuación",
            f"Resistencia de diseño [{unidad}]",
            f"Demanda [{unidad}]",
            "D/C",
            "Estado",
        ],
        filas,
        style_map,
        widths=[2.3, 0.8, 1.1, 0.9, 0.5, 0.8],
        numeric_cols=(2, 3, 4),
        status_col=5,
        status_colors=ESTADOS_COLOR,
    )
    for nota in cap.get("notas") or []:
        doc.add_paragraph(nota, style=style_map["bullet_list"])
    add_spacer(doc)


def _add_resumen(doc, data: dict, style_map: dict) -> None:
    r = data["resumen"]
    doc.add_heading("Resumen de la verificación", level=2).style = style_map["heading_2"]
    if data.get("error"):
        doc.add_paragraph(f"No se pudo evaluar el diseño: {data['error']}", style=style_map["body"])
        return
    if r.get("sin_demanda"):
        doc.add_paragraph(
            "No se ingresaron demandas: solo se informan las resistencias de diseño.", style=style_map["body"]
        )
        return
    etiqueta = ETIQUETAS_RESUMEN.get(r.get("gobernante"), r.get("gobernante") or VACIO)
    if r.get("gobernante") == "interaction" and r.get("interaccion_con_traccion"):
        etiqueta = "Interacción de esfuerzos con tracción (H1)"
    veredicto = "cumple" if r.get("cumple") else "no cumple"
    doc.add_paragraph(
        f"La verificación gobernante es {etiqueta}, con una razón demanda/capacidad máxima de "
        f"{fmt_num(r.get('ratio_max'), 2)}. El perfil {data['perfil']['designacion']} {veredicto}.",
        style=style_map["body"],
    )
    filas = [
        [ETIQUETAS_RESUMEN.get(k, k), fmt_num(v, 2), estado_ratio(v)]
        for k, v in (r.get("ratios") or {}).items()
    ]
    if filas:
        add_table_caption(doc, "Razones demanda/capacidad por verificación", style_map)
        add_results_table(
            doc,
            ["Verificación", "D/C", "Estado"],
            filas,
            style_map,
            widths=[3.4, 1.0, 1.0],
            numeric_cols=(1,),
            status_col=2,
            status_colors=ESTADOS_COLOR,
        )


def build(doc, data: dict, style_map: dict) -> None:
    doc.add_heading(data.get("titulo") or "Acero AISC 360-22", level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        f"Se presenta la verificación del perfil {data['perfil']['designacion']} según {data.get('norma', 'AISC 360-22')} "
        f"(método {data.get('metodo', 'LRFD')}): resistencias de diseño por estado límite frente a las demandas "
        "ingresadas.",
        style=style_map["body"],
    )
    for w in data.get("advertencias") or []:
        doc.add_paragraph(w, style=style_map["bullet_list"])

    _add_datos(doc, data, style_map)

    doc.add_heading("Resistencias y verificaciones", level=2).style = style_map["heading_2"]
    for cap in data.get("capitulos") or []:
        _add_capitulo(doc, cap, style_map)

    _add_resumen(doc, data, style_map)
