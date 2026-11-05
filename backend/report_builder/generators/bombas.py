"""
Generador de la seccion "Vibración de bombas y fundación" a partir del JSON
`bombas.json` que el módulo "Vibración de bombas" envía al proyecto junto a los
PNG de sus gráficos (`imagenes[].ruta` es relativa a la carpeta del JSON).

Contrato de entrada: generators/schemas/bombas.v1.schema.json.
Este modulo no importa nada del motor de calculo: solo conoce la forma del
JSON y reproduce los resultados ya calculados.
"""

from __future__ import annotations

import math

from backend.report_builder.generators.base import (
    add_centered_image,
    add_key_value_table,
    add_table_caption,
    resolve_image_path,
    shade_table_header,
)

MODULE_ID = "bombas"

TITULO_DEFECTO = "Vibración de bombas y fundación"

REFERENCIAS_DEFECTO = (
    "Arya, C., O'Neill, M. y Pincus, G. Design of Structures and Foundations for Vibrating Machines.",
    "NCh 2369 — Diseño sísmico de estructuras e instalaciones industriales.",
    "ACI 318-08, Apéndice D — Anclajes al hormigón.",
    "ISO 10816-3 — Evaluación de la vibración de máquinas (Grupo 2, apoyo rígido o flexible).",
)

NOTAS_DEFECTO = (
    "Las frecuencias características (1X, 2X, paso de álabes, BPFO, BPFI, BSF y FTF) son valores de "
    "referencia para comparar con un espectro medido en terreno; el programa no realiza análisis espectral.",
    "Los límites de zona de severidad ISO 10816-3 son editables: la clase y el tipo de apoyo de cada bomba "
    "pueden exigir otros valores según el estándar aplicable.",
    "Todo resultado debe ser verificado por un ingeniero estructural antes de usarse en un proyecto real.",
)

_MODOS = (("V", "Vertical"), ("H", "Horizontal"), ("R", "Rocking"))
_ESTADO_ISO = {"A": "A (buena)", "B": "B (aceptable)", "C": "C (insatisfactoria)", "D": "D (inaceptable)"}


def _n(valor, decimales: int = 4, unidad: str = "") -> str:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return "—"
    return f"{valor:.{decimales}f}{(' ' + unidad) if unidad else ''}"


def _tabla(doc, titulo: str, encabezados: tuple, filas: list, style_map: dict) -> None:
    add_table_caption(doc, titulo, style_map)
    table = doc.add_table(rows=1, cols=len(encabezados))
    table.style = style_map["table_reference"]
    for i, h in enumerate(encabezados):
        table.rows[0].cells[i].text = h
    shade_table_header(table)
    for fila in filas:
        celdas = table.add_row().cells
        for i, valor in enumerate(fila):
            celdas[i].text = str(valor)


def build(doc, data: dict, style_map: dict) -> None:
    for clave in ("entrada", "dinamico", "resumen", "sismico", "pernos", "vibracion"):
        if not isinstance(data.get(clave), dict) or not data[clave]:
            raise ValueError(f"Se requiere «{clave}» con resultados del módulo de bombas")
    doc.add_heading(data.get("titulo") or TITULO_DEFECTO, level=1).style = style_map["heading_1"]
    doc.add_paragraph(
        "Se analiza la fundación de la bomba centrífuga (modos vertical, horizontal y de balanceo) según "
        "Arya, O'Neill y Pincus, se verifica su estabilidad sísmica y los pernos de anclaje, y se entregan "
        "las frecuencias características y la severidad de vibración de la bomba.",
        style=style_map["body"],
    )
    _add_entrada(doc, data["entrada"], data["pernos"], style_map)
    _add_dinamico(doc, data["dinamico"], style_map)
    _add_verificaciones(doc, data, style_map)
    _add_vibracion(doc, data["vibracion"], style_map)
    _add_graficos(doc, data.get("imagenes") or [], style_map, data.get("_json_dir"))
    _add_notas(doc, style_map)


def _add_entrada(doc, e: dict, pernos: dict, style_map: dict) -> None:
    doc.add_heading("Datos de entrada", level=2).style = style_map["heading_2"]
    bomba = [
        ("Velocidad de operación f (rpm)", _n(e.get("f_rpm"), 0)),
        ("Peso total bomba + motor Wm (tonf)", _n(e.get("Wm"), 3)),
        ("Peso del rotor del motor Wi (tonf)", _n(e.get("Wi"), 4)),
        ("Excentricidad del rotor ei (m)", _n(e.get("ei"), 4)),
        ("Peso del impulsor Wii (tonf)", _n(e.get("Wii"), 4)),
        ("Excentricidad del impulsor eii (m)", _n(e.get("eii"), 4)),
        ("N° de álabes del impulsor", _n(e.get("n_vanes"), 0)),
    ]
    add_table_caption(doc, "Propiedades de la bomba", style_map)
    add_key_value_table(doc, bomba, style_map)
    fundacion = [
        ("Distancia terreno–base de fundación Hs (m)", _n(e.get("Hs"), 2)),
        ("Pedestal tp × Lp × Bp (m)", f"{_n(e.get('tp'), 2)} × {_n(e.get('Lp'), 2)} × {_n(e.get('Bp'), 2)}"),
        ("Bloque tf × Lf × Bf (m)", f"{_n(e.get('tf'), 2)} × {_n(e.get('Lf'), 2)} × {_n(e.get('Bf'), 2)}"),
        ("Pernos de anclaje por línea np", _n(e.get("np"), 0)),
        ("Separación transversal st (m)", _n(e.get("st"), 2)),
    ]
    add_table_caption(doc, "Propiedades de la fundación", style_map)
    add_key_value_table(doc, fundacion, style_map)
    suelo = [
        ("Aceleración máxima del suelo Ao/g", _n(e.get("Ao_g"), 2)),
        ("Densidad γ (tonf/m³)", _n(e.get("gamma"), 2)),
        ("Ángulo de fricción Φ (°)", _n(e.get("phi_deg"), 1)),
        ("Módulo de Poisson ν", _n(e.get("nu"), 2)),
        ("Módulo de corte G (tonf/m²)", _n(e.get("G"), 0)),
        ("Presión admisible σadm (tonf/m²)", _n(e.get("sigma_adm"), 1)),
        ("Perno (material, db)", f"{pernos.get('material_name', '—')}, {_n(pernos.get('db_cm'), 3)} cm"),
        ("Coeficientes Kp / Rp", f"{_n(e.get('Kp'), 2)} / {_n(e.get('Rp'), 2)}"),
    ]
    add_table_caption(doc, "Propiedades del suelo y de los anclajes", style_map)
    add_key_value_table(doc, suelo, style_map)


def _add_dinamico(doc, dinamico: dict, style_map: dict) -> None:
    doc.add_heading("Análisis dinámico", level=2).style = style_map["heading_2"]

    def fila(nombre, simbolo, campo, decimales, unidad=("", "", "")):
        return (
            nombre,
            simbolo,
            *(_n(dinamico[k].get(campo), decimales, u) for (k, _), u in zip(_MODOS, unidad, strict=True)),
        )

    def fmr(k):
        v = dinamico[k].get("fmr")
        return "no hay resonancia" if v is None else _n(v, 2, "rpm")

    filas = [
        fila("Radio equivalente", "ro", "ro", 4, ("m", "m", "m")),
        fila("Masa", "m", "m", 4),
        fila("Razón de masa", "B", "B", 4),
        fila("Coeficiente de enterramiento", "η", "eta", 4),
        fila("Factor de enterramiento del amortiguamiento", "α", "alpha", 4),
        fila("Razón de amortiguamiento geométrico", "D", "D", 4),
        fila("Coeficiente del resorte", "β", "beta", 4),
        fila("Constante del resorte equivalente", "k", "k", 1, ("tonf/m", "tonf/m", "tonf·m/rad")),
        fila("Frecuencia natural", "fn", "fn", 2, ("rpm", "rpm", "rpm")),
        ("Frecuencia resonante", "fmr", *(fmr(k) for k, _ in _MODOS)),
        fila("Factor de magnificación", "M", "Mr", 4),
        fila("Fuerza dinámica", "Fo", "Fo", 6, ("tonf", "tonf", "tonf·m")),
        fila("Amplitud de la vibración", "δ", "delta", 6, ("mm", "mm", "rad")),
        fila("Factor de transmisibilidad", "Tr", "Tr", 4),
        fila("Fuerza transmitida", "Po", "Po", 6, ("tonf", "tonf", "tonf·m")),
    ]
    _tabla(
        doc,
        "Resultados del análisis dinámico por modo",
        ("Variable", "Símbolo", "Vertical", "Horizontal", "Rocking"),
        filas,
        style_map,
    )
    totales = [
        (nombre, _n(dinamico[k].get("delta_total"), 6, "mm"))
        for k, nombre in (
            ("V", "Amplitud resultante vertical δTotal"),
            ("H", "Amplitud resultante horizontal δTotal"),
        )
    ]
    add_table_caption(doc, "Amplitud resultante con la vibración impuesta por el balanceo", style_map)
    add_key_value_table(doc, totales, style_map)


def _estado(texto) -> str:
    return str(texto) if texto in ("OK", "ALERTA", "FALLA") else "—"


def _add_verificaciones(doc, data: dict, style_map: dict) -> None:
    doc.add_heading("Verificaciones normativas", level=2).style = style_map["heading_2"]
    s, q, a = data["resumen"], data["sismico"], data["pernos"]
    adm = data["entrada"].get("sigma_adm")
    adm_n = adm if isinstance(adm, (int, float)) and math.isfinite(adm) else None
    area = min(
        (v for v in (q.get("Ac_plus"), q.get("Ac_minus")) if isinstance(v, (int, float))), default=None
    )
    filas = [
        ("(Wf+Wp)/Wm", _n(s.get("ratio_W")), "> 3.0", _estado(s.get("ratio_W_check"))),
        (
            "σo estática (tonf/m²)",
            _n(s.get("sigma_o")),
            _lim("< ", adm_n, 0.5),
            _estado(s.get("sigma_o_check")),
        ),
        ("Excentricidad dinámica (m)", _n(s.get("e_dyn")), "< 0.15", _estado(s.get("e_dyn_check"))),
        (
            "σ(e+d) máx (tonf/m²)",
            _n(s.get("sigma_ed_max")),
            _lim("< ", adm_n, 0.75),
            _estado(s.get("sigma_ed_max_check")),
        ),
        ("σ(e+d) mín (tonf/m²)", _n(s.get("sigma_ed_min")), "> 0", _estado(s.get("sigma_ed_min_check"))),
        ("Velocidad máxima de operación (mm/s)", _n(s.get("v_max")), "< 2.54", _estado(s.get("v_max_check"))),
        (
            "σ máx sísmica (tonf/m²)",
            _n(q.get("sigma_max")),
            _lim("< ", adm_n, 1.0),
            _estado(q.get("sigma_max_check")),
        ),
        ("Área comprimida", _n(area), "> 0.90", _estado(q.get("Ac_check"))),
        ("FS al deslizamiento", _n(q.get("FSD")), "> 1.2", _estado(q.get("FSD_check"))),
        ("FS al volcamiento", _n(q.get("FSV")), "> 1.2", _estado(q.get("FSV_check"))),
        (
            "Interacción tracción–corte de pernos",
            _n(a.get("interaction")),
            "< 1.2",
            _estado(a.get("interaction_check")),
        ),
    ]
    _tabla(
        doc,
        "Verificaciones de la fundación y de los anclajes",
        ("Verificación", "Valor", "Límite", "Estado"),
        filas,
        style_map,
    )


def _lim(prefijo: str, adm, factor: float) -> str:
    return "—" if adm is None else f"{prefijo}{_n(adm * factor, 1)}"


def _add_vibracion(doc, v: dict, style_map: dict) -> None:
    doc.add_heading("Diagnóstico de vibración", level=2).style = style_map["heading_2"]
    f1x = v.get("f1x_hz")

    def orden(hz):
        return (
            _n(hz / f1x, 2, "X")
            if isinstance(hz, (int, float)) and isinstance(f1x, (int, float)) and f1x
            else "—"
        )

    filas = [
        ("1X — desbalance", v.get("f1x_hz"), v.get("f1x_cpm")),
        ("2X — desalineamiento", v.get("f2x_hz"), v.get("f2x_cpm")),
        ("Paso de álabes (VPF)", v.get("vpf_hz"), v.get("vpf_cpm")),
        ("BPFO — pista externa", v.get("bpfo_hz"), v.get("bpfo_cpm")),
        ("BPFI — pista interna", v.get("bpfi_hz"), v.get("bpfi_cpm")),
        ("BSF — elemento rodante", v.get("bsf_hz"), v.get("bsf_cpm")),
        ("FTF — jaula", v.get("ftf_hz"), v.get("ftf_cpm")),
    ]
    _tabla(
        doc,
        "Frecuencias características de la bomba",
        ("Frecuencia", "Hz", "cpm", "Orden"),
        [(n, _n(hz, 4), _n(cpm, 1), orden(hz)) for n, hz, cpm in filas],
        style_map,
    )
    medida = v.get("v_meas")
    zona = v.get("iso_zone")
    iso = [
        ("Tipo de apoyo", str(v.get("iso_mount_name", "—"))),
        ("Límite A/B (mm/s RMS)", _n(v.get("iso_AB"), 2)),
        ("Límite B/C (mm/s RMS)", _n(v.get("iso_BC"), 2)),
        ("Límite C/D (mm/s RMS)", _n(v.get("iso_CD"), 2)),
        (
            "Vibración medida (mm/s RMS)",
            _n(medida, 2) if isinstance(medida, (int, float)) and medida > 0 else "sin medir",
        ),
        ("Zona ISO 10816-3", _ESTADO_ISO.get(zona, "—")),
    ]
    add_table_caption(doc, "Severidad de vibración según ISO 10816-3", style_map)
    add_key_value_table(doc, iso, style_map)
    texto = v.get("diagnosis_text")
    if texto:
        doc.add_paragraph(str(texto), style=style_map["body"])


def _add_graficos(doc, imagenes: list, style_map: dict, json_dir) -> None:
    rutas = []
    for imagen in imagenes:
        ruta = resolve_image_path(json_dir, imagen.get("ruta"))
        if ruta:
            rutas.append((ruta, imagen.get("descripcion") or "Gráfico"))
    if not rutas:
        return
    doc.add_heading("Gráficos", level=2).style = style_map["heading_2"]
    for ruta, descripcion in rutas:
        add_centered_image(doc, ruta, style_map, caption=descripcion)


def _add_notas(doc, style_map: dict) -> None:
    doc.add_heading("Referencias y notas", level=2).style = style_map["heading_2"]
    for texto in REFERENCIAS_DEFECTO + NOTAS_DEFECTO:
        doc.add_paragraph(texto, style=style_map["bullet_list"])
