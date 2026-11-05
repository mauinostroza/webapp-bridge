"""SAP2000 acciones para Combinaciones: matriz de Response Combinations.

Puerto literal de ``combinaciones/io/sap2000_com.py`` (proyecto de escritorio
original) al patrón de este repositorio: sin conexión COM propia, cada
operación recibe el ``SapModel`` ya conectado a través del actor STA de la
sesión (ver ``bridge/sap2000_agent.py``) y las rutas se instalan desde
``bridge.actions.install_routes`` con la misma autenticación por token, igual
que ``bridge/esbelteces.py``.

Los helpers de aquí abajo (``_call_get_name_list``, ``_normalize_com_return``,
``_extract_names_from_result``, ...) y las funciones públicas
(``get_load_cases_from_sap2000``, ``send_combinations_to_sap2000``, ...) son
un port línea por línea del original — mismos mensajes literales en español,
mismo orden de reintentos por firma COM, mismo criterio de fallo total vs.
advertencias no bloqueantes. La única diferencia estructural es que cada
función pública toma ``model`` como primer argumento en lugar de llamar a su
propio ``_get_sap_model()``: la conexión ya vive en la sesión del agente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bridge.actions import _valid_combo_name

_MAX_ITEMS = 500


class Sap2000ComError(RuntimeError):
    """Espejo de ``combinaciones.io.sap2000_com.Sap2000ComError``.

    ``detail``, cuando está presente, es el cuerpo JSON estructurado que el
    envoltorio ``call`` de ``bridge.actions.install_routes`` usa para el 409
    (ver ``ComponentsBody``/``send`` más abajo); si es ``None`` el envoltorio
    cae al caso genérico y usa ``str(exc)`` como cuerpo.
    """

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail


@dataclass
class SendCombinationsResult:
    """Resultado del envío de combinaciones a SAP2000."""

    created: int
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers COM: múltiples firmas por llamada, port literal.
# ---------------------------------------------------------------------------


def _call_get_name_list(load_patterns: Any) -> Any:
    attempts: tuple[tuple[Any, ...], ...] = (
        tuple(),
        (0, []),
        (0, ()),
        (0, None),
    )
    errors: list[str] = []
    for args in attempts:
        try:
            return load_patterns.GetNameList(*args)
        except TypeError as exc:
            errors.append(str(exc))
        except Exception:
            raise
    raise Sap2000ComError(
        "No se pudo leer la lista de patrones de carga desde SAP2000. "
        "La API COM rechazó los argumentos esperados para GetNameList.\n"
        f"Detalle: {' | '.join(errors)}"
    )


def _call_get_combo_name_list(resp_combo: Any) -> Any:
    attempts: tuple[tuple[Any, ...], ...] = (
        tuple(),
        (0, []),
        (0, ()),
        (0, None),
    )
    errors: list[str] = []
    for args in attempts:
        try:
            return resp_combo.GetNameList(*args)
        except TypeError as exc:
            errors.append(str(exc))
        except Exception:
            raise
    raise Sap2000ComError(
        "No se pudo leer la lista de combinaciones desde SAP2000. "
        "La API COM rechazó los argumentos esperados para GetNameList.\n"
        f"Detalle: {' | '.join(errors)}"
    )


def _call_get_combo_case_list(resp_combo: Any, name: str) -> Any:
    attempts: tuple[tuple[Any, ...], ...] = (
        (name,),
        (name, 0, [], [], []),
        (name, 0, (), (), ()),
        (name, 0, None, None, None),
    )
    errors: list[str] = []
    for args in attempts:
        try:
            return resp_combo.GetCaseList(*args)
        except TypeError as exc:
            errors.append(str(exc))
        except Exception:
            raise
    raise Sap2000ComError(
        f"No se pudo leer la definición de la combinación '{name}' desde SAP2000. "
        "La API COM rechazó los argumentos esperados para GetCaseList.\n"
        f"Detalle: {' | '.join(errors)}"
    )


def _normalize_com_return(ret: object) -> int:
    """Normaliza el valor de retorno crudo de una llamada COM de un solo
    código (Add/Delete/SetCaseList). Port literal de ``_normalize_com_return``."""
    if isinstance(ret, (tuple, list)):
        if not ret:
            return 0
        last = ret[-1]
        return last if isinstance(last, int) else 0
    return ret if isinstance(ret, int) else 0


def _extract_names_from_result(result: Any) -> list[str]:
    if not isinstance(result, tuple) or not result:
        raise Sap2000ComError("SAP2000 devolvió una respuesta inesperada al listar patrones de carga.")

    ret_index: int | None = None
    if isinstance(result[-1], int):
        ret_index = len(result) - 1
    elif isinstance(result[0], int):
        ret_index = 0

    if ret_index is None:
        raise Sap2000ComError("SAP2000 devolvió una respuesta inesperada al listar patrones de carga.")
    if result[ret_index] != 0:
        raise Sap2000ComError(
            "Error al obtener la lista de patrones de carga de SAP2000. "
            f"Código de retorno: {result[ret_index]}"
        )

    candidates = [value for index, value in enumerate(result) if index != ret_index]
    raw_names_candidate = next(
        (
            value
            for value in candidates
            if isinstance(value, str)
            or (isinstance(value, (list, tuple)) and all(isinstance(name, str) for name in value))
        ),
        None,
    )
    if raw_names_candidate is None:
        return []
    if isinstance(raw_names_candidate, str):
        return [raw_names_candidate] if raw_names_candidate else []
    return [name for name in raw_names_candidate if name]


def _extract_combo_terms_from_result(result: Any) -> list[dict[str, object]]:
    if not isinstance(result, tuple) or not result:
        raise Sap2000ComError(
            "SAP2000 devolvió una respuesta inesperada al consultar los componentes de la combinación."
        )

    ret_index: int | None = None
    if isinstance(result[-1], int):
        ret_index = len(result) - 1
    elif isinstance(result[0], int):
        ret_index = 0

    values = [value for index, value in enumerate(result) if index != ret_index]
    if ret_index is not None and result[ret_index] != 0:
        raise Sap2000ComError(
            "Error al obtener los componentes de una combinación de SAP2000. "
            f"Código de retorno: {result[ret_index]}"
        )

    item_types: list[int] = []
    names: list[str] = []
    factors: list[float] = []
    for value in values:
        if isinstance(value, (list, tuple)) and value and all(isinstance(item, int) for item in value):
            if not item_types:
                item_types = [int(item) for item in value]
            continue
        if isinstance(value, (list, tuple)) and value and all(isinstance(item, str) for item in value):
            if not names:
                names = [item for item in value if item]
            continue
        if (
            isinstance(value, (list, tuple))
            and value
            and all(isinstance(item, (int, float)) for item in value)
        ):
            if not factors:
                factors = [float(item) for item in value]

    count = min(len(item_types), len(names), len(factors))
    if count == 0:
        return []

    return [
        {"item_type": item_types[index], "name": names[index], "factor": factors[index]}
        for index in range(count)
        if names[index]
    ]


# ---------------------------------------------------------------------------
# Funciones públicas: port literal, ``model`` ya conectado por la sesión.
# ---------------------------------------------------------------------------


def get_load_cases_from_sap2000(model: Any) -> list[dict[str, object]]:
    """Los Load Cases del modelo activo. ``sap_type`` siempre es ``None``:
    ``LoadCases`` no expone un tipo consultable equivalente a
    ``LoadPatterns.GetLoadType``, port literal de
    ``get_load_cases_from_sap2000``."""
    result = _call_get_name_list(model.LoadCases)
    raw_names = _extract_names_from_result(result)
    return [{"name": name, "sap_type": None} for name in raw_names if name]


def get_response_combinations_from_sap2000(model: Any) -> list[dict[str, object]]:
    """Combinaciones de respuesta del modelo activo, port literal de
    ``get_response_combinations_from_sap2000``."""
    result = _call_get_combo_name_list(model.RespCombo)
    raw_names = _extract_names_from_result(result)
    combinations: list[dict[str, object]] = []
    for name in raw_names:
        if not name:
            continue
        items = _extract_combo_terms_from_result(_call_get_combo_case_list(model.RespCombo, name))
        combinations.append({"name": name, "items": items})
    return combinations


def delete_response_combinations_from_sap2000(model: Any, names: list[str]) -> list[str]:
    """Elimina combinaciones por nombre; retorna las que no se pudieron
    eliminar. Port literal de ``delete_response_combinations_from_sap2000``."""
    failed: list[str] = []
    for name in names:
        ret = _normalize_com_return(model.RespCombo.Delete(name))
        if ret != 0:
            failed.append(name)
    return failed


def get_model_snapshot_from_sap2000(model: Any) -> dict[str, list[dict[str, object]]]:
    """Port literal de ``get_model_snapshot_from_sap2000``: Load Cases +
    combinaciones de respuesta, releídas inline (igual que el original, que
    no reutiliza ``get_response_combinations_from_sap2000`` aquí)."""
    load_cases = get_load_cases_from_sap2000(model)

    combos_result = _call_get_combo_name_list(model.RespCombo)
    combo_names = _extract_names_from_result(combos_result)
    combinations: list[dict[str, object]] = []
    for name in combo_names:
        if not name:
            continue
        combinations.append(
            {
                "name": name,
                "items": _extract_combo_terms_from_result(_call_get_combo_case_list(model.RespCombo, name)),
            }
        )

    return {"load_cases": load_cases, "combinations": combinations}


def send_combinations_to_sap2000(model: Any, matrix: dict[str, Any]) -> SendCombinationsResult:
    """Envía la matriz de combinaciones al modelo activo como Response
    Combinations. Port literal de ``send_combinations_to_sap2000``:

    - Todas las combinaciones se procesan aunque alguna tenga problemas
      puntuales; solo se lanza cuando NINGUNA combinación pudo crearse
      (fallo total).
    - Nombres duplicados dentro de la matriz generan una advertencia previa
      (la última definición pisa a las anteriores, vía Add/Delete/Add).
    - ``combo_type``: 0 = Linear Add, 1 = Envelope (``envelope_members`` no
      es ``None``).
    - Miembros de envolvente: ``SetCaseList(name, 1, member, 1.0)`` — sin
      factor real, SAP2000 siempre usa 1.0 para CNameType=1 (LoadCombo).
    - Casos normales: ``SetCaseList(name, 0, case, float(factor))`` —
      CNameType=0 (LoadCase); factores cero se omiten sin llamar a SAP2000;
      un caso que no está en ``model.LoadCases`` genera advertencia sin
      llamar a SAP2000.
    """
    existing_load_cases = set(_extract_names_from_result(_call_get_name_list(model.LoadCases)))
    created = 0
    warnings: list[str] = []

    combinations = matrix.get("combinations", [])

    # Nombres repetidos dentro de la matriz: el Add()+Delete()+Add() de abajo
    # hace que la última definición con ese nombre pise en silencio los
    # factores de las anteriores. Advertir antes de enviar nada.
    name_counts: dict[str, int] = {}
    for combination in combinations:
        name_counts[combination["name"]] = name_counts.get(combination["name"], 0) + 1
    for name in sorted(name for name, count in name_counts.items() if count > 1):
        warnings.append(
            f"'{name}' aparece {name_counts[name]} veces en la matriz - solo se envía la última "
            "definición, las anteriores con ese mismo nombre se pisan."
        )

    for combination in combinations:
        combo_name = combination["name"]
        envelope_members = combination.get("envelope_members")
        is_envelope = envelope_members is not None
        # ComboType: 0 = Linear Add, 1 = Envelope
        combo_type = 1 if is_envelope else 0
        ret = _normalize_com_return(model.RespCombo.Add(combo_name, combo_type))
        if ret != 0:
            # La combinación puede ya existir; eliminar y volver a agregar.
            model.RespCombo.Delete(combo_name)
            ret = _normalize_com_return(model.RespCombo.Add(combo_name, combo_type))
            if ret != 0:
                warnings.append(f"No se pudo crear la combinación '{combo_name}'.")
                continue

        # La combinación ya quedó creada en SAP2000 en este punto, aunque
        # algún término individual falle más abajo.
        created += 1

        if is_envelope:
            # Los miembros de una envolvente son OTRAS combinaciones
            # (CNameType=1=LoadCombo), sin factor real (SAP2000 usa 1.0).
            for member_name in envelope_members:
                ret = _normalize_com_return(model.RespCombo.SetCaseList(combo_name, 1, member_name, 1.0))
                if ret != 0:
                    warnings.append(
                        f"'{combo_name}' -> '{member_name}': error al asignar la combinación miembro "
                        f"a la envolvente (código SAP2000 {ret}); revisar que se haya enviado antes."
                    )
            continue

        # Agregar cada caso con su factor (omitir factores cero).
        for case_name, factor in combination.get("factors_by_case", {}).items():
            factor = float(factor)
            if factor == 0:
                continue
            if case_name not in existing_load_cases:
                warnings.append(
                    f"'{combo_name}' -> '{case_name}': revisar que el estado de carga se encuentre creado en SAP2000."
                )
                continue
            # CNameType=0 (LoadCase): un Response Combination solo puede
            # referenciar Load Cases.
            ret = _normalize_com_return(model.RespCombo.SetCaseList(combo_name, 0, case_name, factor))
            if ret != 0:
                warnings.append(
                    f"'{combo_name}' -> '{case_name}': error al asignar el caso de carga a la combinación "
                    f"(código SAP2000 {ret}); revisar que el estado de carga se encuentre creado."
                )

    if created == 0 and warnings:
        message = "No se pudo enviar ninguna combinación a SAP2000:\n- " + "\n- ".join(warnings)
        raise Sap2000ComError(message, detail={"code": "send_failed", "message": message})

    return SendCombinationsResult(created=created, warnings=warnings)


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


class DeleteBody(BaseModel):
    names: list[str]
    confirmed: bool = False

    @field_validator("names")
    @classmethod
    def name_list(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("names must contain 1-500 entries")
        return [_valid_combo_name(item, "combination name") for item in value]


class MatrixCombination(BaseModel):
    """Una fila de la matriz (ver ``comb_contract.md``: "Matrix JSON").

    ``factors_by_case`` llega como strings decimales (fieles al ``proj``
    de escritorio, que guarda ``Decimal`` como texto); se convierten a
    ``float`` recién en ``send_combinations_to_sap2000``. ``envelope_members``
    distinto de ``None`` marca una envolvente (combo_type 1); una combinación
    normal lo deja en ``None``.
    """

    name: str
    rule_id: str | None = None
    norm: str | None = None
    family: str | None = None
    canonical_description: str | None = None
    factors_by_case: dict[str, str] = Field(default_factory=dict)
    envelope_members: list[str] | None = None
    envelope_member_factors: dict[str, str] | None = None
    envelope_quick_labels: list[str] | None = None

    @field_validator("name")
    @classmethod
    def combo_name(cls, value: str) -> str:
        return _valid_combo_name(value, "combination name")


class MatrixBody(BaseModel):
    headers: list[str] = Field(default_factory=list)
    combinations: list[MatrixCombination]

    @field_validator("combinations")
    @classmethod
    def bounded_combinations(cls, value: list[MatrixCombination]) -> list[MatrixCombination]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("combinations must contain 1-500 entries")
        return value


class SendBody(BaseModel):
    matrix: MatrixBody
    confirmed: bool = False


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/combinaciones/*``.

    ``call`` es el mismo envoltorio de ``bridge.actions.install_routes`` que
    ejecuta la función dada en el actor STA de la sesión y traduce las
    excepciones a respuestas HTTP (mismo criterio de errores y autenticación
    que el resto del agente; ver ``Sap2000ComError.detail`` arriba para el
    cuerpo 409 del fallo total de ``send``).
    """
    from fastapi import HTTPException

    prefix = "/v1/sap/actions/combinaciones"

    @app.get(f"{prefix}/snapshot")
    def combinaciones_snapshot():
        return call(get_model_snapshot_from_sap2000)

    @app.get(f"{prefix}/existing")
    def combinaciones_existing():
        return call(lambda model: {"combinations": get_response_combinations_from_sap2000(model)})

    @app.post(f"{prefix}/delete")
    def combinaciones_delete(body: DeleteBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to delete combinations from SAP2000")
        return call(lambda model: {"failed": delete_response_combinations_from_sap2000(model, body.names)})

    @app.post(f"{prefix}/send")
    def combinaciones_send(body: SendBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to send combinations to SAP2000")
        matrix = body.matrix.model_dump()

        def run(model: Any) -> dict[str, Any]:
            result = send_combinations_to_sap2000(model, matrix)
            return {"created": result.created, "warnings": result.warnings}

        return call(run)
