"""Selección de salida de SAP2000 (``Results.Setup``): guardar la del usuario y devolvérsela tras leer resultados.

Leer resultados exige marcar para salida solo los casos y combinaciones pedidos
(``DeselectAllCasesAndCombosForOutput`` + ``Set*SelectedForOutput``). Eso cambia un estado de visualización de SAP2000 que el
usuario no espera ver tocado: al terminar la lectura encontraba desmarcadas las combinaciones que tenía seleccionadas.

``preserve_output_selection`` guarda la selección antes y la restaura al terminar, aunque la lectura falle. Es de mejor
esfuerzo: si no se puede leer o restaurar, deja un aviso en ``warnings`` y nunca impide la lectura. Todas las lecturas que
cambian la selección se envuelven en él (ver docs/SAP_ACTIONS.md, «Selección de salida»); las acciones cuyo fin ES fijar la
selección (``placa-base/seleccionar-cargas``) no lo usan.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _selected_flag(ret: Any) -> bool | None:
    """Lee el booleano de ``Get*SelectedForOutput`` sin depender del orden del retorno."""
    if isinstance(ret, (tuple, list)):
        for value in ret:
            if isinstance(value, bool):
                return value
        return None
    return ret if isinstance(ret, bool) else None


def _capture_selection(model: Any) -> tuple[list[str], list[str]] | None:
    """Casos y combinaciones marcados para salida, o ``None`` si no se puede leer."""
    # Importación diferida: el lector del visor importa módulos del puente que, a su vez, usan este.
    from bridge.viewer.sap2000.reader_results import _load_case_names, _load_combination_names

    setup = model.Results.Setup
    selected: list[list[str]] = []
    for names, getter in (
        (_load_case_names(model), setup.GetCaseSelectedForOutput),
        (_load_combination_names(model), setup.GetComboSelectedForOutput),
    ):
        flags = [(n, _selected_flag(getter(n))) for n in names]
        # Si algún valor no se pudo interpretar, mejor no «restaurar» una selección inventada.
        if any(flag is None for _, flag in flags):
            return None
        selected.append([n for n, flag in flags if flag])
    return selected[0], selected[1]


def _marcar(funcion: Any, nombre: str) -> None:
    """``Set*SelectedForOutput(nombre, True)``; los enlaces que no admiten el segundo argumento reciben solo el nombre."""
    try:
        funcion(nombre, True)
    except TypeError:
        funcion(nombre)


@contextmanager
def preserve_output_selection(model: Any, warnings: list[str] | None = None) -> Iterator[None]:
    """Restaura la selección de salida del usuario al terminar la lectura (incluso si esta falla).

    Los avisos (selección ilegible o no restaurable) se añaden a ``warnings``. Se puede anidar: cada nivel restaura lo
    que encontró al entrar.
    """
    avisos = warnings if warnings is not None else []
    try:
        saved = _capture_selection(model)
    except Exception:  # noqa: BLE001 - guardar es de mejor esfuerzo
        saved = None
    if saved is None:
        avisos.append(
            "No se pudo leer la selección de salida de SAP2000: puede haber cambiado tras la lectura. "
            "Revise los casos y combinaciones marcados para salida."
        )
    try:
        yield
    finally:
        if saved is not None:
            cases, combos = saved
            try:
                setup = model.Results.Setup
                setup.DeselectAllCasesAndCombosForOutput()
                for name in cases:
                    _marcar(setup.SetCaseSelectedForOutput, name)
                for name in combos:
                    _marcar(setup.SetComboSelectedForOutput, name)
            except Exception as exc:  # noqa: BLE001 - restaurar es de mejor esfuerzo
                avisos.append(f"No se pudo restaurar la selección de salida de SAP2000: {exc}")


def con_avisos(resultado: dict[str, Any], avisos: list[str]) -> dict[str, Any]:
    """Añade ``warnings`` al resultado solo si hay avisos (clave opcional: sin avisos la respuesta no cambia)."""
    if avisos:
        resultado["warnings"] = [*resultado.get("warnings", []), *avisos]
    return resultado
