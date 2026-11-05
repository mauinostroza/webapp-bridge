"""
Generador de la seccion "Fundacion de postes (metodo Sulzberger) y muerto de
anclaje" a partir del JSON simple `sulzberger.json` que el programa
"Sulzberger" envia al proyecto (sin imagenes).

Contrato de entrada: generators/schemas/sulzberger.v1.schema.json.
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

MODULE_ID = "sulzberger"

TITULO_DEFECTO = "Fundación de postes (método Sulzberger) y muerto de anclaje"

ADVERTENCIA_DEFECTO = (
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real."
)

CRITERIOS_DEFECTO = {
    "FS_volcamiento_min": 1.5,
    "tan_xi_max": 0.01,
    "FU_max": 1.0,
    "FS_muerto_min": 2.0,
}

FORMULAS = (
    "Resistencia al volcamiento: Mr = ML + Mb, con ML = b·Kh·tan(ξ)·t³/12·(6α² − 4α + 1) "
    "y Mb = Pu·a·(1/2 − (2/3)·√(Pu / (2·b·a²·Kv·tan ξ))).",
    "Factor de seguridad al volcamiento: FS = Mr / Ms.",
    "Tensiones de trabajo: p1 = (1 − α)²/4·Kh·t·tan(ξ); p2 = α·Kh·t·tan(ξ); p3 = √(2·Kv·Pu·tan(ξ) / b).",
    "Muerto de anclaje: β = αt − φs/2; Kp = (1 + sen φs) / (1 − sen φs); "
    "FSm = Rm / TmaxT, con Rm = √(Rx² + Ry²).",
)

NOTAS = (
    "Método Sulzberger: la fundación se modela como un prisma rígido embebido en un suelo elástico "
    "(coeficiente de balasto) que gira un ángulo ξ alrededor de un centro de giro a la profundidad α·t.",
    "Las cargas se ingresan en kgf y kgf·m y el motor las convierte a tonf y tonf·m; las dimensiones "
    "de la fundación están en m y las del muerto de anclaje en cm.",
    "Criterios de aceptación según CIGRE TB 606 / C1.21 y la práctica chilena (SEC).",
)

_NOMBRES_CARGAS = (
    ("Pu_long", "Pu eje fuerte (longitudinal) [kgf]"),
    ("Vu_long", "Vu eje fuerte [kgf]"),
    ("Mu_long", "Mu eje fuerte [kgf·m]"),
    ("Pu_trans", "Pu eje débil (transversal) [kgf]"),
    ("Vu_trans", "Vu eje débil [kgf]"),
    ("Mu_trans", "Mu eje débil [kgf·m]"),
)
_NOMBRES_POSTE = (
    ("Ht", "Altura total Ht [m]"),
    ("He", "Altura empotrada He [m]"),
    ("Hre", "Altura fundación bajo poste Hre [m]"),
    ("Pp", "Peso del poste Pp [kgf]"),
)
_NOMBRES_SUELO = (
    ("Kv", "Coeficiente de balasto basal Kv [tonf/m³]"),
    ("Kh", "Coeficiente de balasto lateral Kh [tonf/m³]"),
    ("sigma_v", "Tensión admisible vertical σv [tonf/m²]"),
    ("sigma_h_inf", "Tensión admisible lateral inferior σh,inf [tonf/m²]"),
    ("sigma_h_sup", "Tensión admisible lateral superior σh,sup [tonf/m²]"),
    ("phi_s", "Ángulo de fricción del suelo φs [°]"),
    ("gamma_relleno", "Densidad del relleno γ [tonf/m³]"),
)
_NOMBRES_FUNDACION = (
    ("a", "Ancho en dirección fuerte a [m]"),
    ("b", "Ancho en dirección débil b [m]"),
    ("a1", "Dimensión transversal inferior del poste a1 [m]"),
    ("b1", "Dimensión longitudinal inferior del poste b1 [m]"),
)
_NOMBRES_MUERTO = (
    ("TmaxT", "Tensión máxima del tirante TmaxT [kgf]"),
    ("alpha_t", "Ángulo del tirante c/r a la vertical αt [°]"),
    ("phi_s", "Ángulo de fricción del suelo φs [°]"),
    ("c_s", "Cohesión del suelo cs [kgf/cm²]"),
    ("gamma_s", "Densidad del suelo γs [kgf/m³]"),
    ("gamma_h", "Densidad del hormigón γh [kgf/m³]"),
    ("Lmb", "Largo del muerto Lmb [cm]"),
    ("Lmt", "Largo del tope Lmt [cm]"),
    ("bm", "Ancho del muerto bm [cm]"),
    ("hm1", "Altura mínima hm1 [cm]"),
    ("hm2", "Altura máxima hm2 [cm]"),
    ("Hent", "Profundidad de enterramiento Hent [cm]"),
)


def _n(value, decimales: int = 3) -> str:
    if value == "inf":
        return "∞"
    if value == "-inf":
        return "−∞"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:.{decimales}f}"


def _estado(cumple) -> str:
    return "CUMPLE" if cumple else "NO CUMPLE"


def _filas(valores: dict, nombres: tuple) -> list:
    return [(etiqueta, _n(valores.get(clave), 3)) for clave, etiqueta in nombres if clave in valores]


def build(doc, data: dict, style_map: dict) -> None:
    fundacion = data.get("fundacion")
    muerto = data.get("muerto")
    entrada = data.get("entrada")
    if not isinstance(fundacion, dict) or not isinstance(muerto, dict) or not isinstance(entrada, dict):
        raise ValueError("Se requiere entrada, fundacion y muerto con resultados del módulo Sulzberger")
    titulo = data.get("titulo") or TITULO_DEFECTO
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "Se verifica la fundación del poste con el método Sulzberger, que modela la fundación como un "
        "prisma rígido en un suelo elástico, y el muerto de anclaje del tirante con cuña de falla y empuje "
        "pasivo, según CIGRE TB 606 / C1.21.",
        style=style_map["body"],
    )
    criterios = {**CRITERIOS_DEFECTO, **(data.get("criterios") or {})}
    _add_datos(doc, data, entrada, style_map)
    _add_fundacion(doc, fundacion, entrada, criterios, style_map)
    _add_muerto(doc, muerto, style_map)
    _add_conclusion(doc, data, fundacion, muerto, style_map)
    _add_formulas_y_notas(doc, style_map)


def _add_datos(doc, data: dict, entrada: dict, style_map: dict) -> None:
    doc.add_heading("Datos de entrada", level=2).style = style_map["heading_2"]
    doc.add_paragraph(f"Fecha: {data.get('fecha', '—')}", style=style_map["body"])
    bloques = (
        ("Cargas en el nivel de terreno", entrada.get("cargas") or {}, _NOMBRES_CARGAS),
        ("Datos del poste", entrada.get("poste") or {}, _NOMBRES_POSTE),
        ("Parámetros del suelo", entrada.get("suelo") or {}, _NOMBRES_SUELO),
        ("Geometría de la fundación", entrada.get("fundacion") or {}, _NOMBRES_FUNDACION),
    )
    for caption, valores, nombres in bloques:
        add_table_caption(doc, caption, style_map)
        add_key_value_table(doc, _filas(valores, nombres), style_map)
    add_table_caption(doc, "Parámetro del método Sulzberger", style_map)
    add_key_value_table(doc, [("Giro de fundación ξ [°]", _n(entrada.get("xi_grados"), 3))], style_map)
    add_table_caption(doc, "Datos del muerto de anclaje", style_map)
    add_key_value_table(doc, _filas(entrada.get("muerto") or {}, _NOMBRES_MUERTO), style_map)


def _verificaciones(f: dict, d: dict, entrada: dict, criterios: dict) -> list:
    suelo = entrada.get("suelo") or {}
    fu = criterios["FU_max"]

    def sup(clave):
        return _n(suelo.get(clave), 2)

    def par(clave, decimales=3):
        return _n(f.get(clave), decimales), _n(d.get(clave), decimales)

    def ok(clave):
        return f"{_estado(f.get(clave))} / {_estado(d.get(clave))}"

    return [
        ("FS volcamiento", *par("FS_volcamiento"), f"≥ {criterios['FS_volcamiento_min']}", ok("cumple_FS")),
        ("Giro tan(ξ)", *par("tan_xi", 5), f"≤ {criterios['tan_xi_max']}", ok("cumple_xi")),
        ("Tensión lateral superior p1 [tonf/m²]", *par("p1"), f"≤ {sup('sigma_h_sup')}", ok("cumple_p1")),
        ("FU p1", *par("FU_p1"), f"≤ {fu}", ok("cumple_p1")),
        ("Tensión lateral inferior p2 [tonf/m²]", *par("p2"), f"≤ {sup('sigma_h_inf')}", ok("cumple_p2")),
        ("FU p2", *par("FU_p2"), f"≤ {fu}", ok("cumple_p2")),
        ("Tensión vertical p3 [tonf/m²]", *par("p3"), f"≤ {sup('sigma_v')}", ok("cumple_p3")),
        ("FU p3", *par("FU_p3"), f"≤ {fu}", ok("cumple_p3")),
    ]


def _add_fundacion(doc, fundacion: dict, entrada: dict, criterios: dict, style_map: dict) -> None:
    doc.add_heading("Verificación de la fundación (método Sulzberger)", level=2).style = style_map[
        "heading_2"
    ]
    f = fundacion.get("eje_fuerte") or {}
    d = fundacion.get("eje_debil") or {}
    errores = fundacion.get("errores") or []
    if errores:
        doc.add_paragraph("Errores de datos: " + "; ".join(str(e) for e in errores), style=style_map["body"])
    add_table_caption(doc, "Resultados intermedios por eje", style_map)
    table = doc.add_table(rows=1, cols=3)
    table.style = style_map["table_reference"]
    for i, h in enumerate(("Magnitud", "Eje fuerte", "Eje débil")):
        table.rows[0].cells[i].text = h
    shade_table_header(table)
    intermedios = (
        ("Carga vertical Pu [tonf]", "Pu", 3),
        ("Corte Vu [tonf]", "Vu", 3),
        ("Momento Mu [tonf·m]", "Mu", 3),
        ("Altura equivalente Heq [m]", "Heq", 3),
        ("β (parámetro Sulzberger)", "beta", 3),
        ("α (centro de giro / t)", "alpha", 3),
        ("Módulo de sección WL [m⁴]", "WL", 4),
        ("Momento solicitante Ms [tonf·m]", "Ms", 3),
        ("Momento lateral resistente ML [tonf·m]", "ML", 3),
        ("Momento basal resistente Mb [tonf·m]", "Mb", 3),
        ("Momento resistente total Mr [tonf·m]", "Mr", 3),
    )
    for etiqueta, clave, dec in intermedios:
        cells = table.add_row().cells
        cells[0].text = etiqueta
        cells[1].text = _n(f.get(clave), dec)
        cells[2].text = _n(d.get(clave), dec)
    add_table_caption(doc, "Verificaciones frente a los criterios de aceptación", style_map)
    table = doc.add_table(rows=1, cols=5)
    table.style = style_map["table_reference"]
    for i, h in enumerate(("Verificación", "Eje fuerte", "Eje débil", "Criterio", "Estado (fuerte / débil)")):
        table.rows[0].cells[i].text = h
    shade_table_header(table)
    for fila in _verificaciones(f, d, entrada, criterios):
        cells = table.add_row().cells
        for i, texto in enumerate(fila):
            cells[i].text = texto
    add_table_caption(doc, "Volúmenes y peso de la fundación", style_map)
    add_key_value_table(
        doc,
        [
            ("Volumen de excavación [m³]", _n(fundacion.get("Vex"))),
            ("Volumen de fundación [m³]", _n(fundacion.get("Vfund"))),
            ("Peso de la fundación [tonf]", _n(fundacion.get("Wfund"))),
        ],
        style_map,
    )


def _add_muerto(doc, muerto: dict, style_map: dict) -> None:
    doc.add_heading("Verificación del muerto de anclaje", level=2).style = style_map["heading_2"]
    errores = muerto.get("errores") or []
    if errores:
        doc.add_paragraph("Errores de datos: " + "; ".join(str(e) for e in errores), style=style_map["body"])
    filas = [
        ("Fuerza horizontal Fh [kgf]", _n(muerto.get("Fh"), 2)),
        ("Fuerza vertical Fv [kgf]", _n(muerto.get("Fv"), 2)),
        ("Peso del muerto Wm [kgf]", _n(muerto.get("Wm"), 2)),
        ("hm = bm·sen(αt) [cm]", _n(muerto.get("hm"), 2)),
        ("Hm = Hent − hm [cm]", _n(muerto.get("Hm"), 2)),
        ("Ángulo de falla β [°]", _n(muerto.get("beta"), 2)),
        ("Peso de la cuña Wcuña [kgf]", _n(muerto.get("W_cuña"), 2)),
        ("Normal al plano Ncuña [kgf]", _n(muerto.get("N_cuña"), 2)),
        ("Tangente al plano Tcuña [kgf]", _n(muerto.get("T_cuña"), 2)),
        ("Coeficiente de empuje pasivo Kp", _n(muerto.get("Kp"), 4)),
        ("Empuje pasivo Ep [kgf]", _n(muerto.get("Ep"), 2)),
        ("Resultante horizontal Rx [kgf]", _n(muerto.get("Rx"), 2)),
        ("Resultante vertical Ry [kgf]", _n(muerto.get("Ry"), 2)),
        ("Resultante total Rm [kgf]", _n(muerto.get("Rm"), 2)),
        ("Factor de seguridad FSm", _n(muerto.get("FSm"))),
        ("Factor de seguridad mínimo Ω", _n(muerto.get("FS_min"), 2)),
        ("Estado", _estado(muerto.get("cumple"))),
    ]
    add_table_caption(doc, "Resultados del muerto de anclaje", style_map)
    add_key_value_table(doc, filas, style_map)


def _add_conclusion(doc, data: dict, fundacion: dict, muerto: dict, style_map: dict) -> None:
    doc.add_heading("Conclusiones", level=2).style = style_map["heading_2"]
    cumple_f = bool(fundacion.get("cumple_global"))
    cumple_m = bool(muerto.get("cumple"))
    doc.add_paragraph(
        f"Fundación del poste (método Sulzberger): {_estado(cumple_f)}.", style=style_map["bullet_list"]
    )
    doc.add_paragraph(f"Muerto de anclaje: {_estado(cumple_m)}.", style=style_map["bullet_list"])
    doc.add_paragraph(
        f"Estado global: {_estado(data.get('cumple_global', cumple_f and cumple_m))}.",
        style=style_map["body"],
    )


def _add_formulas_y_notas(doc, style_map: dict) -> None:
    doc.add_heading("Fórmulas empleadas", level=2).style = style_map["heading_2"]
    for formula in FORMULAS:
        doc.add_paragraph(formula, style=style_map["bullet_list"])
    doc.add_heading("Notas", level=2).style = style_map["heading_2"]
    for nota in NOTAS:
        doc.add_paragraph(nota, style=style_map["bullet_list"])
    doc.add_paragraph(ADVERTENCIA_DEFECTO, style=style_map["body"])
