"""
Generador del capitulo "Hormigon simple" (ACI 318-25) a partir del JSON simple
`hormigon_simple.json` que el programa "Hormigon simple" sube al proyecto
(sin imagenes). El servidor calcula el contenido a partir de las entradas
(POST /hormigon/reporte); este modulo solo reproduce lo ya calculado.

Contrato de entrada: generators/schemas/hormigon_simple.v1.schema.json.
Este modulo no importa nada del motor de calculo.
"""

from __future__ import annotations

import math

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement

from backend.report_builder.generators.base import (
    add_key_value_table,
    add_table_caption,
    center_table,
    shade_table_header,
)

MODULE_ID = "hormigon_simple"

TITULO_DEFECTO = "Hormigón simple (ACI 318-25)"

ADVERTENCIA_DEFECTO = (
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real."
)

AVISO_PREDIMENSIONAMIENTO = (
    "Las demás rutas deben considerarse herramientas de predimensionamiento hasta completar "
    "los casos de referencia y pruebas de regresión."
)

NOTAS_DEFECTO = (
    "Normativa: ACI 318-25 (Requisitos de Reglamento para Concreto Estructural).",
    "Unidades: tensiones en kgf/cm², fuerzas en tonf, momentos en tonf·m y dimensiones de la sección en cm "
    "(posiciones a lo largo de la viga en m).",
    "Los resultados distinguen dimensionamiento de verificación: sin acero o Av/s provisto el resultado es "
    "de dimensionamiento, no una verificación.",
    "Punzamiento sólo está validado para columna interior.",
    AVISO_PREDIMENSIONAMIENTO,
)

_ESTADO_TEXTO = {
    "OK": "OK (cumple la verificación)",
    "REVISAR": "REVISAR (no cumple alguna verificación)",
    "DIMENSIONAR": "DIMENSIONAR (resultado de dimensionamiento, no es una verificación)",
    "NO DISPONIBLE": "NO DISPONIBLE (el motor no valida esta configuración)",
}

_ETIQUETAS_MATERIALES = (
    ("fc", "f'c (kgf/cm²)"),
    ("fy", "fy (kgf/cm²)"),
    ("sistema_barras", "Sistema de barras"),
    ("phi_flexion", "φ flexión"),
    ("phi_corte", "φ corte"),
    ("phi_compresion", "φ compresión"),
    ("phi_corte_capacidad", "φ corte por capacidad"),
    ("lambda_factor", "λ (hormigón liviano)"),
)

_ETIQUETAS_ENTRADA = {
    "h": "Altura h (cm)",
    "b": "Ancho b (cm)",
    "d_prima": "Distancia al borde d' (cm)",
    "d": "Altura útil d (cm)",
    "Mu": "Momento Mu (tonf·m)",
    "considera_Vc": "Considera Vc",
    "As_traccion": "As en tracción (cm²)",
    "Vu": "Vu (tonf)",
    "Pu": "Pu - carga axial (tonf)",
    "tipo_elemento": "Tipo de elemento",
    "ln": "Luz libre ln (cm)",
    "As": "As (cm²)",
    "Vgr_u": "Vgr,u (tonf)",
    "tipo": "Tipo de marco",
    "c1": "c1 (cm)",
    "c2": "c2 (cm)",
    "tipo_columna": "Tipo de columna",
    "beta": "β",
    "phi": "φ",
    "Mv_transfer": "Momento transferido (tonf·m)",
    "n_puntos": "Número de puntos",
}


def _txt(valor) -> str:
    """Texto de un escalar del JSON; nunca emite nan, inf ni None."""
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        if not math.isfinite(valor):
            return "—"
        if valor == 0:
            return "0"
        decimales = 5 if abs(valor) < 0.1 else 3
        return f"{valor:.{decimales}f}".rstrip("0").rstrip(".")
    return str(valor)


def _tabla(doc, encabezados, filas, style_map: dict, titulo: str | None = None):
    if titulo:
        add_table_caption(doc, titulo, style_map)
    table = doc.add_table(rows=1, cols=len(encabezados))
    table.style = style_map["table_reference"]
    for i, h in enumerate(encabezados):
        table.rows[0].cells[i].text = str(h)
    shade_table_header(table)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))  # se repite en cada página
    numericas = {
        i
        for i in range(len(encabezados))
        if filas
        and all(
            isinstance(f[i], (int, float)) and not isinstance(f[i], bool)
            for f in filas
            if i < len(f) and f[i] is not None
        )
        and any(i < len(f) and f[i] is not None for f in filas)
    }
    for fila in filas:
        cells = table.add_row().cells
        for i, v in enumerate(fila):
            cells[i].text = _txt(v)
            if i in numericas:
                cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for i in numericas:
        table.rows[0].cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    center_table(table)
    return table


def _h2(doc, texto: str, style_map: dict) -> None:
    doc.add_heading(texto, level=2).style = style_map["heading_2"]


def build(doc, data: dict, style_map: dict) -> None:
    secciones = data.get("secciones")
    if not isinstance(secciones, list) or not secciones:
        raise ValueError("Se requiere secciones con resultados del módulo de Hormigón simple")
    titulo = data.get("titulo") or TITULO_DEFECTO
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "Se presentan los resultados del diseño de hormigón armado según ACI 318-25, calculados con las "
        "entradas indicadas en cada sección.",
        style=style_map["body"],
    )
    _add_materiales(doc, data, style_map)
    viga_seccion = None
    for seccion in secciones:
        if seccion.get("panel") == "viga":
            viga_seccion = seccion
        else:
            _add_seccion(doc, seccion, style_map)
    if data.get("viga") or viga_seccion:
        _add_viga(doc, data.get("viga") or {}, viga_seccion or {}, style_map)
    _add_notas(doc, data, style_map)


def _add_materiales(doc, data: dict, style_map: dict) -> None:
    _h2(doc, "Materiales", style_map)
    mat = data.get("materiales") or {}
    filas = [(et, _txt(mat.get(k))) for k, et in _ETIQUETAS_MATERIALES if k in mat]
    filas.append(("Fecha", _txt(data.get("fecha"))))
    add_table_caption(doc, "Materiales y factores de reducción", style_map)
    add_key_value_table(doc, filas, style_map, headers=("Variable", "Valor"))


def _add_advertencias(doc, advertencias, style_map: dict) -> None:
    mensajes = [str(a.get("mensaje", "")) for a in advertencias or [] if a.get("mensaje")]
    if not mensajes:
        return
    doc.add_paragraph("Advertencias", style=style_map["body"])
    for m in mensajes:
        doc.add_paragraph(m, style=style_map["bullet_list"])


def _add_estado(doc, seccion: dict, style_map: dict) -> None:
    estado = seccion.get("estado")
    doc.add_paragraph(f"Estado: {_ESTADO_TEXTO.get(estado, _txt(estado))}", style=style_map["body"])


def _add_seccion(doc, seccion: dict, style_map: dict) -> None:
    titulo = seccion.get("titulo") or str(seccion.get("panel", ""))
    _h2(doc, titulo, style_map)
    entradas = seccion.get("entradas") or {}
    if entradas:
        add_table_caption(doc, f"{titulo}: datos de entrada", style_map)
        filas = [(_ETIQUETAS_ENTRADA.get(k, k), _txt(v)) for k, v in entradas.items()]
        add_key_value_table(doc, filas, style_map, headers=("Variable", "Valor"))
    resultados = seccion.get("resultados") or []
    if resultados:
        filas = [(r.get("variable", ""), r.get("valor"), r.get("unidad", "")) for r in resultados]
        _tabla(doc, ("Variable", "Valor", "Unidad"), filas, style_map, f"{titulo}: resultados")
    for tabla in seccion.get("tablas") or []:
        _tabla(doc, tabla.get("columnas") or [], tabla.get("filas") or [], style_map, tabla.get("titulo"))
    _add_estado(doc, seccion, style_map)
    _add_advertencias(doc, seccion.get("advertencias"), style_map)
    pasos = [str(p) for p in seccion.get("pasos") or [] if str(p).strip()]
    if pasos:
        doc.add_heading("Memoria de cálculo", level=3).style = style_map["heading_3"]
        for paso in pasos:
            doc.add_paragraph(paso, style=style_map["body"])


def _add_viga(doc, viga: dict, seccion: dict, style_map: dict) -> None:
    _h2(doc, "Armado de viga", style_map)
    geo = viga.get("geometria") or {}
    if geo:
        dem = geo.get("demanda") or {}
        filas = [
            ("Viga", _txt(geo.get("nombre"))),
            ("Longitud L (m)", _txt(geo.get("L"))),
            ("Ancho b (cm)", _txt(geo.get("b"))),
            ("Altura h (cm)", _txt(geo.get("h"))),
            ("Recubrimiento d' (cm)", _txt(geo.get("d_prima"))),
        ]
        for a in geo.get("apoyos") or []:
            filas.append(
                (f"Apoyo {_txt(a.get('etiqueta'))}", f"{_txt(a.get('tipo'))} en x = {_txt(a.get('x'))} m")
            )
        if dem:
            filas.append(("Fuente de la demanda", _txt(dem.get("fuente") or "—")))
            filas.append(("Casos de carga", ", ".join(dem.get("casos") or []) or "—"))
            filas.append(("Estaciones de demanda", _txt(dem.get("n_estaciones"))))
        add_table_caption(doc, "Geometría de la viga", style_map)
        add_key_value_table(doc, filas, style_map, headers=("Variable", "Valor"))
    armado = viga.get("armado") or {}
    capas = {"bottom": "Inferior", "top": "Superior"}
    if armado.get("grupos"):
        filas = [
            (
                g.get("id"),
                capas.get(g.get("layer"), g.get("layer")),
                g.get("row"),
                g.get("count"),
                g.get("dia_mm"),
                g.get("x_start"),
                g.get("x_end"),
                g.get("role") or "",
            )
            for g in armado["grupos"]
        ]
        _tabla(
            doc,
            ("Grupo", "Capa", "Fila", "Cantidad", "Ø (mm)", "x inicio (m)", "x fin (m)", "Función"),
            filas,
            style_map,
            "Armado longitudinal",
        )
    if armado.get("estribos"):
        filas = [
            (z.get("id"), z.get("dia_mm"), z.get("legs"), z.get("spacing_cm"), z.get("x_ini"), z.get("x_fin"))
            for z in armado["estribos"]
        ]
        _tabla(
            doc,
            ("Zona", "Ø (mm)", "Ramas", "Separación (cm)", "x inicio (m)", "x fin (m)"),
            filas,
            style_map,
            "Zonas de estribos",
        )
    ver = viga.get("verificacion") or {}
    if ver.get("chequeos"):
        filas = []
        for c in ver["chequeos"]:
            obs = []
            for d in c.get("deficits") or []:
                obs.append(f"insuficiente entre x = {_txt(d.get('x_ini'))} y {_txt(d.get('x_fin'))} m")
            obs.extend(c.get("notas") or [])
            filas.append(
                (
                    c.get("nombre"),
                    "Cumple" if c.get("cumple") else "No cumple",
                    c.get("utilizacion"),
                    "; ".join(obs),
                )
            )
        _tabla(
            doc,
            ("Chequeo", "Resultado", "Utilización", "Observaciones"),
            filas,
            style_map,
            "Verificación del armado",
        )
    if ver.get("zonas_corte"):
        filas = [
            (
                z.get("x_ini"),
                z.get("x_fin"),
                z.get("phi_vc"),
                z.get("phi_vs"),
                z.get("phi_vn"),
                z.get("av_s"),
                z.get("av_s_min"),
            )
            for z in ver["zonas_corte"]
        ]
        _tabla(
            doc,
            (
                "x inicio (m)",
                "x fin (m)",
                "φVc (tonf)",
                "φVs (tonf)",
                "φVn (tonf)",
                "Av/s (cm²/m)",
                "Av/s mín (cm²/m)",
            ),
            filas,
            style_map,
            "Capacidad al corte por zona de estribos",
        )
    desp = viga.get("despiece") or {}
    if desp.get("filas"):
        filas = [
            (f.get("marca"), f.get("elemento"), f.get("descripcion"), f.get("longitud"), f.get("peso_kg"))
            for f in desp["filas"]
        ]
        filas.append(("Total", "", "", "", desp.get("total_kg")))
        _tabla(
            doc,
            ("Marca", "Elemento", "Descripción", "Longitud", "Peso (kgf)"),
            filas,
            style_map,
            "Despiece del acero",
        )
        doc.add_paragraph(f"Peso total del acero: {_txt(desp.get('total_kg'))} kgf.", style=style_map["body"])
    if seccion:
        _add_estado(doc, seccion, style_map)
        _add_advertencias(doc, seccion.get("advertencias"), style_map)


def _add_notas(doc, data: dict, style_map: dict) -> None:
    _h2(doc, "Notas", style_map)
    vistos = set(NOTAS_DEFECTO)
    for nota in NOTAS_DEFECTO:
        doc.add_paragraph(nota, style=style_map["bullet_list"])
    for adv in data.get("advertencias_generales") or []:
        mensaje = str(adv.get("mensaje", "")) if isinstance(adv, dict) else str(adv)
        if mensaje and mensaje not in vistos:
            vistos.add(mensaje)
            doc.add_paragraph(mensaje, style=style_map["bullet_list"])
    doc.add_paragraph(ADVERTENCIA_DEFECTO, style=style_map["body"])
