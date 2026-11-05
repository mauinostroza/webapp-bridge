"""
Generador del capitulo "Equilibrio plastico" (diagonales de arriostramiento, NCh2369) a partir
del JSON simple `equilibrio_plastico.json` que el programa "Equilibrio plastico" sube al proyecto
(sin imagenes). El servidor calcula el contenido a partir de las entradas
(POST /equilibrio/reporte); este modulo solo reproduce lo ya calculado. Reemplaza a la memoria de
calculo en PDF del programa de escritorio.

Contrato de entrada: generators/schemas/equilibrio_plastico.v1.schema.json.
Este modulo no importa nada del motor de calculo.
"""

from __future__ import annotations

import math

from backend.report_builder.generators.base import (
    add_key_value_table,
    add_table_caption,
    shade_row,
    shade_table_header,
)

MODULE_ID = "equilibrio_plastico"

TITULO_DEFECTO = "Equilibrio plástico (NCh2369)"

ADVERTENCIA_DEFECTO = (
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real."
)

NOTAS_DEFECTO = (
    "Normativa: NCh2369 (diseño sísmico de estructuras e instalaciones industriales), equilibrio plástico "
    "de diagonales de arriostramiento en X o en V.",
    "Unidades: tensiones en kgf/cm², fuerzas en tonf, momentos en tonf·m, áreas en cm², longitudes en m y "
    "ángulos en grados.",
    "Esta memoria de cálculo reemplaza al PDF del programa de escritorio.",
    "El esquema del marco no se incluye en esta versión del informe.",
)

_FILL_CRITICO = "FDECEA"
_ROTULO_SEVERIDAD = {"error": "Error", "advertencia": "Advertencia", "info": "Información"}
_ORDEN_SEVERIDAD = ("error", "advertencia", "info")

_FORMULAS_COMUNES = (
    "Fye = Fy·Ry",
    "Fcre = 0.658^(Fye/Fe)·Fye",
    "Pne = 1.14·Fcre·Ag / 1000;  Tye = Fye·Ag / 1000",
)
_FORMULAS_INELASTICO = (
    "Método inelástico (Z = 1): Pn = 0.3·Pne / Ry;  Tn = MIN(Tye / Ry, Tdis) (si no hay Tdis, Tn = Tye / Ry)",
)
_FORMULAS_NOMINAL = ("Método nominal (Z = 0): Pn = Pne / Ry;  Tn = Tye / Ry",)
_FORMULAS_X = (
    "Marco en X: θ = atan(H / L);  α = atan(L / H)",
    "Pa (nivel cima) = MAX(Pn, Tn)·cos θ;  Pa (otros niveles) = (Tn − Pn del nivel superior)·cos θ",
    "Pa_col = Σ (Tn + Pn)·cos α desde el nivel hasta la cima",
)
_FORMULAS_V = (
    "Marco en V: θ = atan(H / (L/2));  α = atan((L/2) / H)",
    "Pbr = 0.02·1.14·Fcre·Ag / 1000 / Ry",
    "Pa = (Pn + Tn)·cos θ;  May = Pbr·L / 4;  Max_unb = (Tn − Pn)·sin θ·L / 4",
    "Mgrav = qgrav·L² / 8;  Max = Max_unb + Mgrav",
    "Pa_col = Σ Tn·sin θ desde el nivel hasta la cima",
)
_FORMULA_TOTAL = "Pa_col + Pgrav = Pa_col + Pgrav_model (demanda total en la columna)"


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


def _grados(rad) -> float | None:
    if isinstance(rad, bool) or not isinstance(rad, (int, float)) or not math.isfinite(rad):
        return None
    return math.degrees(rad)


def _suma(a, b) -> float | None:
    try:
        total = float(a) + float(b)
    except (TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _tabla(doc, encabezados, filas, style_map: dict, titulo: str | None = None, resaltar=()):
    if titulo:
        add_table_caption(doc, titulo, style_map)
    table = doc.add_table(rows=1, cols=len(encabezados))
    table.style = style_map["table_reference"]
    for i, h in enumerate(encabezados):
        table.rows[0].cells[i].text = str(h)
    shade_table_header(table)
    for n, fila in enumerate(filas):
        row = table.add_row()
        for i, v in enumerate(fila):
            row.cells[i].text = _txt(v)
        if n in resaltar:
            shade_row(row, _FILL_CRITICO)
    return table


def _h2(doc, texto: str, style_map: dict) -> None:
    doc.add_heading(texto, level=2).style = style_map["heading_2"]


def build(doc, data: dict, style_map: dict) -> None:
    niveles = data.get("niveles")
    if not isinstance(niveles, list) or not niveles:
        raise ValueError("Se requiere niveles con resultados del módulo de Equilibrio plástico")
    titulo = data.get("titulo") or TITULO_DEFECTO
    doc.add_heading(titulo, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "Se presenta la memoria de cálculo del equilibrio plástico de las diagonales de arriostramiento "
        "según NCh2369, calculada con los datos indicados.",
        style=style_map["body"],
    )
    proyecto = data.get("proyecto") or {}
    critico = data.get("nivel_critico")
    es_v = proyecto.get("tipo_marco", "X") != "X"
    _add_proyecto(doc, data, proyecto, style_map)
    _add_entradas(doc, niveles, style_map)
    _add_resultados(doc, data, niveles, critico, es_v, style_map)
    _add_advertencias(doc, data.get("advertencias"), style_map)
    if data.get("grupos"):
        _add_grupos(doc, data["grupos"], style_map)
    _add_formulas(doc, proyecto, es_v, style_map)
    _add_notas(doc, data, style_map)


def _add_proyecto(doc, data: dict, proyecto: dict, style_map: dict) -> None:
    _h2(doc, "Datos del proyecto", style_map)
    filas = [
        ("Proyecto", _txt(proyecto.get("nombre"))),
        ("Tipo de marco", _txt(proyecto.get("tipo_marco"))),
        ("Número de niveles", _txt(proyecto.get("n_levels"))),
        ("Fy (kgf/cm²)", _txt(proyecto.get("Fy"))),
        ("Ry", _txt(proyecto.get("Ry"))),
        ("Fye = Fy·Ry (kgf/cm²)", _txt(proyecto.get("Fye"))),
        ("L, luz de la viga (m)", _txt(proyecto.get("L"))),
        ("Método", _txt(proyecto.get("metodo"))),
        ("Nivel crítico", _txt(data.get("nivel_critico"))),
        ("Fecha", _txt(data.get("fecha"))),
    ]
    if proyecto.get("notas"):
        filas.append(("Notas del proyecto", _txt(proyecto["notas"])))
    add_table_caption(doc, "Datos generales del proyecto", style_map)
    add_key_value_table(doc, filas, style_map, headers=("Variable", "Valor"))


def _add_entradas(doc, niveles: list, style_map: dict) -> None:
    _h2(doc, "Datos por nivel", style_map)
    filas = [
        (
            n.get("nivel"),
            n.get("Ag"),
            n.get("Fe"),
            n.get("H"),
            n.get("Tdis"),
            n.get("qgrav"),
            n.get("Pgrav_model"),
        )
        for n in niveles
    ]
    _tabla(
        doc,
        (
            "Nivel",
            "Ag (cm²)",
            "Fe (kgf/cm²)",
            "H (m)",
            "Tdis (tonf)",
            "qgrav (tonf/m)",
            "Pgrav_model (tonf)",
        ),
        filas,
        style_map,
        "Datos de las diagonales por nivel (de la cima a la base)",
    )


def _add_resultados(doc, data: dict, niveles: list, critico, es_v: bool, style_map: dict) -> None:
    _h2(doc, "Resultados", style_map)
    encabezados = [
        "Nivel",
        "Fcre (kgf/cm²)",
        "θ (°)",
        "α (°)",
        "Pne (tonf)",
        "Pn (tonf)",
        "Tye (tonf)",
        "Tn (tonf)",
    ]
    if es_v:
        encabezados += ["Pbr (tonf)", "May (tonf·m)", "Max_unb (tonf·m)"]
    encabezados += [
        "Pa (tonf)",
        "Mgrav (tonf·m)",
        "Max (tonf·m)",
        "Pa_col (tonf)",
        "Pgrav_model (tonf)",
        "Pa_col+Pgrav (tonf)",
    ]
    filas, resaltar = [], []
    for i, n in enumerate(niveles):
        es_critico = critico is not None and n.get("nivel") == critico
        fila = [
            f"{_txt(n.get('nivel'))} (crítico)" if es_critico else n.get("nivel"),
            n.get("Fcre"),
            _grados(n.get("theta")),
            _grados(n.get("alpha")),
            n.get("Pne"),
            n.get("Pn"),
            n.get("Tye"),
            n.get("Tn"),
        ]
        if es_v:
            fila += [n.get("Pbr"), n.get("May"), n.get("Max_unb")]
        total = n.get("Pa_column_total")
        if total is None:
            total = _suma(n.get("Pa_col"), n.get("Pgrav_model"))
        fila += [n.get("Pa"), n.get("Mgrav"), n.get("Max"), n.get("Pa_col"), n.get("Pgrav_model"), total]
        filas.append(fila)
        if es_critico:
            resaltar.append(i)
    _tabla(doc, encabezados, filas, style_map, "Resultados por nivel (de la cima a la base)", resaltar)
    resumen = data.get("resumen") or {}
    if critico is not None:
        doc.add_paragraph(
            f"Nivel crítico (mayor demanda en columna): nivel {_txt(critico)} "
            f"(Pa_col + Pgrav = {_txt(resumen.get('max_Pa_column_total'))} tonf).",
            style=style_map["body"],
        )


def _add_advertencias(doc, advertencias, style_map: dict) -> None:
    _h2(doc, "Advertencias", style_map)
    validas = [a for a in advertencias or [] if isinstance(a, dict) and a.get("mensaje")]
    if not validas:
        doc.add_paragraph("Sin advertencias.", style=style_map["body"])
        return
    for sev in _ORDEN_SEVERIDAD:
        for a in validas:
            if a.get("severidad") == sev:
                doc.add_paragraph(f"{_ROTULO_SEVERIDAD[sev]}: {a['mensaje']}", style=style_map["bullet_list"])


def _add_grupos(doc, grupos: list, style_map: dict) -> None:
    _h2(doc, "Grupos de diseño", style_map)
    doc.add_paragraph(
        "Cada grupo verifica una sección candidata (Ag, Fe) contra la envolvente de las demandas de sus "
        "miembros, con los mismos Fy, Ry y método del proyecto.",
        style=style_map["body"],
    )
    for g in grupos:
        nombre = g.get("nombre") or "Grupo sin nombre"
        doc.add_heading(nombre, level=3).style = style_map["heading_3"]
        sec = g.get("seccion") or {}
        estado = "Cumple" if g.get("estado") == "cumple" else "No cumple"
        filas = [
            ("Ag (cm²)", _txt(sec.get("Ag"))),
            ("Fe (kgf/cm²)", _txt(sec.get("Fe"))),
            ("Fye (kgf/cm²)", _txt(g.get("Fye"))),
            ("Fcre (kgf/cm²)", _txt(g.get("Fcre"))),
            ("Tn de la sección (tonf)", _txt(g.get("tn_cap"))),
            ("Pn de la sección (tonf)", _txt(g.get("pn_cap"))),
            ("Estado", estado),
            ("Miembro crítico", _txt(g.get("miembro_critico"))),
        ]
        add_table_caption(doc, f"{nombre}: sección candidata", style_map)
        add_key_value_table(doc, filas, style_map, headers=("Variable", "Valor"))
        miembros = [
            (
                m.get("label"),
                m.get("Tn_req"),
                m.get("Pn_req"),
                m.get("H"),
                m.get("L"),
                m.get("margin_Tn"),
                m.get("margin_Pn"),
                "Cumple" if m.get("ok") else "No cumple",
            )
            for m in g.get("miembros") or []
        ]
        if miembros:
            _tabla(
                doc,
                (
                    "Miembro",
                    "Tn req (tonf)",
                    "Pn req (tonf)",
                    "H (m)",
                    "L (m)",
                    "Margen Tn (tonf)",
                    "Margen Pn (tonf)",
                    "Estado",
                ),
                miembros,
                style_map,
                f"{nombre}: verificación de los miembros",
            )
        for a in g.get("advertencias") or []:
            if isinstance(a, dict) and a.get("mensaje"):
                rotulo = _ROTULO_SEVERIDAD.get(a.get("severidad"), "Información")
                doc.add_paragraph(f"{rotulo}: {a['mensaje']}", style=style_map["bullet_list"])


def _add_formulas(doc, proyecto: dict, es_v: bool, style_map: dict) -> None:
    _h2(doc, "Fórmulas empleadas", style_map)
    metodo = (
        _FORMULAS_NOMINAL if str(proyecto.get("metodo", "")).startswith("Nominal") else _FORMULAS_INELASTICO
    )
    for f in (*_FORMULAS_COMUNES, *metodo, *(_FORMULAS_V if es_v else _FORMULAS_X), _FORMULA_TOTAL):
        doc.add_paragraph(f, style=style_map["bullet_list"])


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
