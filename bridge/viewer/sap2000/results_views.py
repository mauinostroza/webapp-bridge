"""Datos de resultados listos para pintar, sin ninguna dependencia de interfaz.

Antes vivían dentro del visor de escritorio (`app/model_viewer_3d_pro.py`).
Se extrajeron aquí para que el puente web los reutilice sin arrastrar Qt ni
PyVista. El comportamiento es el mismo; lo único que cambia es que los avisos
ya no se imprimen por consola: se devuelven, para que lleguen a la pantalla.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Componentes de esfuerzo/fuerza de shell que se pueden pintar sobre un área.
AREA_STRESS_COMPONENTS = frozenset({"M11", "M22", "M12", "V13", "V23", "F11", "F22", "F12"})


def reduce_area_forces_to_scalar(
    table: dict[str, Any], component: str
) -> tuple[dict[str, float], float]:
    """Reduce una tabla de area_forces() (un valor por PointElm/esquina) a un
    escalar por área (promedio de sus esquinas) para el componente pedido.

    Devuelve (área -> valor promedio, max(|valor|)) — el segundo sirve para
    escalar el gradiente de color. Es una aproximación razonable para una
    vista rápida de contornos, no un resultado de diseño preciso (SAP2000
    nativo interpola/extrapola con más cuidado en sus propios contornos)."""
    columns = table.get("columns", [])
    rows = table.get("rows", [])

    obj_col = None
    val_col = None
    for i, col_name in enumerate(columns):
        key = str(col_name).strip().casefold()
        if key in ("obj", "object", "area"):
            obj_col = i
        elif key == component.strip().casefold():
            val_col = i
    if obj_col is None or val_col is None:
        return {}, 0.0

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        if obj_col >= len(row) or val_col >= len(row):
            continue
        name = str(row[obj_col]).strip()
        try:
            val = float(row[val_col])
        except Exception:  # noqa: BLE001 - una celda no numérica se salta
            continue
        sums[name] = sums.get(name, 0.0) + val
        counts[name] = counts.get(name, 0) + 1

    values = {name: sums[name] / counts[name] for name in sums}
    vmax = max((abs(v) for v in values.values()), default=0.0)
    return values, vmax


def area_stress_unit(component: str, result_units: dict[str, str] | None) -> str:
    """Unidad a mostrar para un componente de esfuerzo/fuerza de shell, a
    partir de result_units ({force, length, label}) de la tabla area_forces().

    Convención de SAP2000 para resultados de shell (por unidad de ancho):
    - F11/F22/F12, V13/V23: unidad_fuerza/unidad_longitud (p.ej. "tonf/m").
    - M11/M22/M12: momento por unidad de ancho = fuerza·longitud/longitud, que
      se simplifica a unidad_fuerza sola (p.ej. "tonf").

    Si result_units no está disponible devuelve "": nunca se inventa una unidad."""
    if not result_units:
        return ""
    force = result_units.get("force", "")
    length = result_units.get("length", "")
    if not force:
        return ""
    if component.startswith("M"):
        return force
    if length:
        return f"{force}/{length}"
    return ""


@dataclass(frozen=True)
class DesignRatiosReport:
    """D/C por barra, con todo lo que no se pudo leer dicho en voz alta."""

    ratios: dict[str, float] = field(default_factory=dict)
    #: Avisos legibles: lecturas que fallaron o filas descartadas.
    warnings: tuple[str, ...] = ()
    #: Filas cuyo valor no era un número, por método de lectura.
    bad_rows: dict[str, int] = field(default_factory=dict)


def build_design_ratios_report(reader: Any) -> DesignRatiosReport:
    """Combina los D/C de acero y de hormigón en un único `barra -> ratio`.

    El hormigón se carga primero y el acero lo sobrescribe si una misma barra
    aparece en los dos (una barra solo debería tener un tipo de diseño; el
    acero manda por decisión de proyecto). Lo que no se puede leer o parsear
    NO desaparece en silencio: queda en `warnings`."""
    ratios: dict[str, float] = {}
    warnings: list[str] = []
    bad: dict[str, int] = {}
    if reader is None:
        return DesignRatiosReport()
    for method_name in ("concrete_design_results", "steel_design_results"):
        if not hasattr(reader, method_name):
            continue
        try:
            table = getattr(reader, method_name)(limit=None)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{method_name}: la lectura falló ({exc})")
            continue
        bad_rows = 0
        for row in table.get("rows", []):
            if len(row) < 2:
                continue
            try:
                ratios[str(row[0]).strip()] = float(row[1])
            except Exception:  # noqa: BLE001
                bad_rows += 1
        if bad_rows:
            bad[method_name] = bad_rows
            warnings.append(f"{method_name}: {bad_rows} fila(s) con un ratio no numérico, ignoradas")
    return DesignRatiosReport(ratios, tuple(warnings), bad)


def build_design_ratios(reader: Any) -> dict[str, float]:
    """Solo el diccionario `barra -> D/C` (ver `build_design_ratios_report`)."""
    return build_design_ratios_report(reader).ratios
