"""Expansión de combinaciones de respuesta para el snapshot del Viewer (sin COM).

Recibe las definiciones crudas de ``reader_metadata.load_combination_definitions``
y devuelve, por combinación, los casos de carga con su factor total. Las
combinaciones anidadas se expanden multiplicando factores; los ciclos se cortan
y se anotan, y los límites truncan con aviso en lugar de fallar.
"""

from __future__ import annotations

from typing import Any

MAX_COMBINATIONS = 5000
MAX_ITEMS = 500
_EPSILON = 1e-12


def expand_combinations(
    definitions: list[dict[str, Any]],
    max_combinations: int = MAX_COMBINATIONS,
    max_items: int = MAX_ITEMS,
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Devuelve ``(combinations, truncated, warnings)``.

    Cada combinación sale como ``{"name", "items": [{"pattern", "factor"}]}`` con
    un único ítem por patrón (los repetidos se suman) y sin factores nulos. Un
    término ``combo`` se sustituye por los casos de esa combinación, multiplicados
    por su factor. Un ciclo o una referencia inexistente se omite y se anota.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        by_name.setdefault(definition["name"], definition)
    memo: dict[str, tuple[dict[str, float], bool]] = {}
    warnings: list[str] = []

    def expand(name: str, stack: tuple[str, ...]) -> tuple[dict[str, float], bool]:
        """Casos de ``name`` con factor 1 y si su expansión encontró un ciclo."""
        if name in memo:
            return memo[name]
        totals: dict[str, float] = {}
        cyclic = False
        for term in by_name[name]["items"]:
            factor = term["factor"]
            if not term["combo"]:
                totals[term["case"]] = totals.get(term["case"], 0.0) + factor
                continue
            child = term["case"]
            if child not in by_name:
                warnings.append(f"Combinación {name}: referencia a «{child}», que no existe; omitida.")
                continue
            if child == name or child in stack:
                cyclic = True
                warnings.append(f"Combinación {name}: referencia circular a «{child}»; omitida.")
                continue
            sub, sub_cyclic = expand(child, (*stack, name))
            cyclic = cyclic or sub_cyclic
            for case, value in sub.items():
                totals[case] = totals.get(case, 0.0) + factor * value
        result = (totals, cyclic)
        if not cyclic:
            memo[name] = result
        return result

    combinations: list[dict[str, Any]] = []
    truncated = False
    for definition in definitions:
        if len(combinations) >= max_combinations:
            truncated = True
            warnings.append(
                f"Se muestran {max_combinations} combinaciones; el resto se omite (combinationsTruncated)."
            )
            break
        name = definition["name"]
        totals, _ = expand(name, ())
        items = [
            {"pattern": case, "factor": factor} for case, factor in totals.items() if abs(factor) > _EPSILON
        ]
        if len(items) > max_items:
            truncated = True
            warnings.append(
                f"Combinación {name}: más de {max_items} patrones; se truncan (combinationsTruncated)."
            )
            items = items[:max_items]
        combinations.append({"name": name, "items": items})
    return combinations, truncated, list(dict.fromkeys(warnings))
