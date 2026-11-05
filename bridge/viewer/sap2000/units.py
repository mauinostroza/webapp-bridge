# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
UNIT_CODES = {
    "lb_in_F": 1,
    "lb_ft_F": 2,
    "kip_in_F": 3,
    "kip_ft_F": 4,
    "kN_mm_C": 5,
    "kN_m_C": 6,
    "kgf_mm_C": 7,
    "kgf_m_C": 8,
    "N_mm_C": 9,
    "N_m_C": 10,
    "tonf_mm_C": 11,
    "tonf_m_C": 12,
    "kN_cm_C": 13,
    "kgf_cm_C": 14,
    "N_cm_C": 15,
    "tonf_cm_C": 16,
}


def unit_label(value: int | str | None) -> str:
    if isinstance(value, str):
        return value
    for name, code in UNIT_CODES.items():
        if code == value:
            return name
    return "unknown"


_LENGTH_UNIT_TO_METERS = {
    "in": 0.0254,
    "ft": 0.3048,
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
}


def length_factor_to_meters(value: int | str | None) -> float:
    """Factor to convert a length in SAP2000's active "present units" (a
    UNIT_CODES key like "kN_mm_C", or its numeric code) to meters.

    Falls back to 1.0 (i.e. assumes meters) if the unit system is unknown,
    matching the previous unconverted behavior rather than raising.
    """
    name = value if isinstance(value, str) else unit_label(value)
    length_token = name.split("_")[1] if name.count("_") >= 2 else ""
    return _LENGTH_UNIT_TO_METERS.get(length_token, 1.0)


def extract_result_units(value: int | str | None) -> dict[str, str]:
    """Extrae unidades legibles de un código de unidades SAP2000.

    SAP2000 devuelve "present units" como un código numérico o una cadena
    con formato FUERZA_LONGITUD_TEMPERATURA (p.ej. "tonf_m_C", "kN_mm_C").

    Devuelve un dict con claves:
    - 'force': unidad de fuerza (p.ej. "tonf", "kN", "kgf", "lb", "kip", "N")
    - 'length': unidad de longitud (p.ej. "m", "cm", "mm", "ft", "in")
    - 'label': nombre completo del sistema (p.ej. "tonf_m_C")

    Si el formato no se reconoce, devuelve un dict vacío para no adivinar.
    Nunca devuelve None, siempre un dict (vacío si no reconocible).
    """
    name = value if isinstance(value, str) else unit_label(value)

    # Si no se puede determinar el nombre, devuelve vacío
    if not name or name == "unknown":
        return {}

    parts = name.split("_")
    if len(parts) < 3:
        # Formato no reconocido (esperamos FUERZA_LONGITUD_TEMPERATURA)
        return {}

    force_token = parts[0]
    length_token = parts[1]

    # Verificamos que los tokens sean válidos
    valid_forces = {"lb", "kip", "kN", "kgf", "N", "tonf"}
    valid_lengths = {"in", "ft", "mm", "cm", "m"}

    # Extraemos solo si son válidos; en caso contrario, devolvemos vacío
    if force_token not in valid_forces or length_token not in valid_lengths:
        return {}

    return {
        "force": force_token,
        "length": length_token,
        "label": name,
    }
