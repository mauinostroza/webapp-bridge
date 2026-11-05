"""
Generador de la seccion "Diseno de Placas Base" a partir del JSON de salida
del modulo de placas base (columna de acero, AISC 360 / ACI 318).

Contrato de entrada: generators/schemas/placas_base.v1.schema.json. Este
modulo no importa nada del subprograma que produce ese JSON - solo conoce su
forma, tal como la documenta el schema.
"""

from __future__ import annotations

from backend.report_builder.generators.base import add_key_value_table, add_table_caption, shade_table_header

MODULE_ID = "placas_base"


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def build(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Diseño de Placas Base", level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "A continuación se presenta la verificación de placas base de columna "
        "según AISC 360 / ACI 318 para los casos de diseño analizados.",
        style=style_map["body"],
    )

    disenos = data.get("disenos", [])
    for i, caso in enumerate(disenos, start=1):
        _add_caso(doc, i, caso, style_map)


def _add_caso(doc, index: int, caso: dict, style_map: dict) -> None:
    nombre = caso.get("registro", {}).get("nombre_diseno", f"Caso {index}")
    doc.add_heading(f"Caso {index}: {nombre}", level=2).style = style_map["heading_2"]

    notas = caso.get("registro", {}).get("notas")
    if notas:
        doc.add_paragraph(notas, style=style_map["body"])

    _add_material_table(doc, caso.get("material", {}), style_map)
    _add_columna_table(doc, caso.get("columna", {}), style_map)
    _add_placa_base_table(doc, caso.get("placa_base", {}), style_map)
    _add_perno_table(doc, caso.get("perno", {}), style_map)
    _add_pedestal_table(doc, caso.get("pedestal", {}), style_map)

    anclaje = caso.get("anclaje_hormigon")
    if anclaje:
        _add_anclaje_hormigon_table(doc, anclaje, style_map)

    combinaciones = caso.get("combinaciones", [])
    if combinaciones:
        _add_load_combinations_table(doc, combinaciones, style_map)

    if caso.get("atiesador", {}).get("incluir"):
        _add_atiesador_table(doc, caso["atiesador"], style_map)
    if caso.get("placa_silla", {}).get("incluir"):
        _add_placa_silla_table(doc, caso["placa_silla"], style_map)
    if caso.get("llave_corte", {}).get("incluir"):
        _add_llave_corte_table(doc, caso["llave_corte"], style_map)


def _add_material_table(doc, material: dict, style_map: dict) -> None:
    doc.add_heading("Materiales", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Propiedades de los materiales", style_map)
    rows = [
        ("f'c hormigón (MPa)", _fmt(material.get("f_prime_c"))),
        ("fy acero de refuerzo (MPa)", _fmt(material.get("fy_acero"))),
        ("Fy acero estructural (MPa)", _fmt(material.get("Fy_estructural"))),
        ("Fu acero estructural (MPa)", _fmt(material.get("Fu_estructural"))),
        ("E acero (MPa)", _fmt(material.get("E_acero"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_columna_table(doc, columna: dict, style_map: dict) -> None:
    doc.add_heading("Columna", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Propiedades de la columna", style_map)
    rows = [
        ("Perfil", _fmt(columna.get("perfil"))),
        ("Tipo de sección", _fmt(columna.get("tipo_seccion"))),
        ("d (cm)", _fmt(columna.get("d"))),
        ("bf (cm)", _fmt(columna.get("bf"))),
        ("tf (cm)", _fmt(columna.get("tf"))),
        ("tw (cm)", _fmt(columna.get("tw"))),
        ("Ag (cm2)", _fmt(columna.get("Ag"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_placa_base_table(doc, placa: dict, style_map: dict) -> None:
    doc.add_heading("Geometría de la placa base", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Geometría de la placa base", style_map)
    rows = [
        ("N (cm)", _fmt(placa.get("N"))),
        ("B (cm)", _fmt(placa.get("B"))),
        ("tp (cm)", _fmt(placa.get("tp"))),
        ("m (cm)", _fmt(placa.get("m"))),
        ("n (cm)", _fmt(placa.get("n"))),
        ("lambda_n", _fmt(placa.get("lambda_n"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_perno_table(doc, perno: dict, style_map: dict) -> None:
    doc.add_heading("Pernos de anclaje", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Pernos de anclaje", style_map)
    rows = [
        ("Grado", _fmt(perno.get("grado"))),
        ("Diámetro (in)", _fmt(perno.get("db_in"))),
        ("Número de pernos en tracción (nt)", _fmt(perno.get("nt"))),
        ("Número de pernos en corte (nv)", _fmt(perno.get("nv"))),
        ("hef (cm)", _fmt(perno.get("hef"))),
        ("edge (cm)", _fmt(perno.get("edge"))),
        ("Separación (cm)", _fmt(perno.get("sep"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_pedestal_table(doc, pedestal: dict, style_map: dict) -> None:
    doc.add_heading("Pedestal", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Geometría del pedestal", style_map)
    rows = [
        ("N (cm)", _fmt(pedestal.get("N"))),
        ("B (cm)", _fmt(pedestal.get("B"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_anclaje_hormigon_table(doc, anclaje: dict, style_map: dict) -> None:
    doc.add_heading("Anclaje al hormigón", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Parámetros de anclaje al hormigón", style_map)
    rows = [
        ("Tipo de anclaje", _fmt(anclaje.get("anchor_type"))),
        ("Norma de diseño", _fmt(anclaje.get("design_code"))),
        ("Condición de fisuración", _fmt(anclaje.get("crack_condition"))),
        ("Sísmico", _fmt(anclaje.get("seismic"))),
        ("Modelo de presión", _fmt(anclaje.get("pressure_model"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_load_combinations_table(doc, combinaciones: list, style_map: dict) -> None:
    doc.add_heading("Combinaciones de carga analizadas", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Combinaciones de carga analizadas", style_map)
    table = doc.add_table(rows=1, cols=6)
    table.style = style_map["table_results"]
    headers = table.rows[0].cells
    for i, h in enumerate(["Combo", "P", "Vx", "Vy", "Mx", "My"]):
        headers[i].text = h
    shade_table_header(table)
    for combo in combinaciones:
        cells = table.add_row().cells
        cells[0].text = _fmt(combo.get("caso"))
        cells[1].text = _fmt(combo.get("P"))
        cells[2].text = _fmt(combo.get("Vx"))
        cells[3].text = _fmt(combo.get("Vy"))
        cells[4].text = _fmt(combo.get("Mx"))
        cells[5].text = _fmt(combo.get("My"))


def _add_atiesador_table(doc, atiesador: dict, style_map: dict) -> None:
    doc.add_heading("Atiesadores", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Geometría de los atiesadores", style_map)
    rows = [
        ("Espesor (cm)", _fmt(atiesador.get("espesor"))),
        ("Altura (cm)", _fmt(atiesador.get("altura"))),
        ("Ancho (cm)", _fmt(atiesador.get("ancho"))),
        ("Cantidad", _fmt(atiesador.get("num_atiesadores"))),
        ("Fy (MPa)", _fmt(atiesador.get("Fy"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_placa_silla_table(doc, silla: dict, style_map: dict) -> None:
    doc.add_heading("Placa silla", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Geometría de la placa silla", style_map)
    rows = [
        ("Espesor (cm)", _fmt(silla.get("espesor"))),
        ("Longitud (cm)", _fmt(silla.get("longitud"))),
        ("Ancho (cm)", _fmt(silla.get("ancho"))),
    ]
    add_key_value_table(doc, rows, style_map)


def _add_llave_corte_table(doc, llave: dict, style_map: dict) -> None:
    doc.add_heading("Llave de corte", level=3).style = style_map["heading_3"]
    add_table_caption(doc, "Geometría de la llave de corte", style_map)
    rows = [
        ("Tipo", _fmt(llave.get("tipo"))),
        ("bc (cm)", _fmt(llave.get("bc"))),
        ("hc (cm)", _fmt(llave.get("hc"))),
    ]
    add_key_value_table(doc, rows, style_map)
