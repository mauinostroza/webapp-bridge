"""
Formato numerico y de unidades comun a todos los generadores del report
builder (criterio en docs/formato_reporte_docx.md, seccion "Numeros y
unidades"). Los generadores reciben numeros crudos en el JSON y los formatean
aqui; asi la regla vive en un solo lugar.
"""

from __future__ import annotations

import math

NBSP = " "
VACIO = "—"

# Nombres historicos de unidades -> convencion de la aplicacion.
_UNIDADES = {
    "Ton": "tonf",
    "ton": "tonf",
    "Ton·m": "tonf·m",
    "Ton-m": "tonf·m",
    "Ton/m": "tonf/m",
    "Ton/m2": "tonf/m²",
    "Ton/m²": "tonf/m²",
    "t/m2": "tonf/m²",
}


def normalizar_unidad(unidad: str | None) -> str:
    if not unidad:
        return ""
    return _UNIDADES.get(unidad.strip(), unidad.strip())


def _es_numero(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def decimales_por_magnitud(value: float) -> int:
    """|valor| >= 10 -> 1 decimal; |valor| < 10 (incluido 0) -> 3."""
    return 1 if abs(value) >= 10 else 3


def fmt_num(value, decimales: int | None = None) -> str:
    """Punto decimal; espacio duro como separador de miles solo desde 5
    digitos enteros; `—` si no hay valor. Un texto se devuelve tal cual."""
    if value is None:
        return VACIO
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if not math.isfinite(value):
        return VACIO
    if decimales is None:
        decimales = decimales_por_magnitud(value)
    texto = f"{abs(value):.{decimales}f}"
    entero, _, frac = texto.partition(".")
    if len(entero) >= 5:
        entero = f"{int(entero):,}".replace(",", NBSP)
    signo = "-" if value < 0 and float(texto) != 0 else ""
    return f"{signo}{entero}.{frac}" if frac else f"{signo}{entero}"


def fmt_cantidad(value, unidad: str | None = None, decimales: int | None = None) -> str:
    """«12.5 tonf» con espacio duro entre numero y unidad."""
    texto = fmt_num(value, decimales)
    u = normalizar_unidad(unidad)
    return f"{texto}{NBSP}{u}" if u and texto != VACIO else texto


def fmt_porcentaje(fraccion, decimales: int = 1) -> str:
    """Una fraccion (0.853) como «85.3 %»."""
    if not _es_numero(fraccion):
        return VACIO
    return f"{fmt_num(fraccion * 100, decimales)}{NBSP}%"


def etiqueta_con_unidad(etiqueta: str, unidad: str | None) -> str:
    """Encabezado de columna: «Presion maxima [tonf/m²]»."""
    u = normalizar_unidad(unidad)
    return f"{etiqueta} [{u}]" if u else etiqueta


def estado_ratio(ratio, limite: float = 1.0) -> str:
    """«Cumple» / «No cumple» para una razon demanda/capacidad."""
    if not _es_numero(ratio):
        return VACIO
    return "Cumple" if ratio <= limite else "No cumple"
