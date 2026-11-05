"""SAP2000 acciones para Espectro: envío de curvas y lectura de resultados
(reacciones de base, participación modal) usadas por "OBT. SAP" / "ENV. SAP".

Puerto literal de ``sap2000_bridge.py`` + las funciones ``_calcular_ps``,
``_calcular_qxqy``, ``_calcular_modal`` y ``_enviar_curvas`` de
``sap2000_dialogs.py`` (proyecto de escritorio original,
``subprograms/espectro_diseno/``) al patrón de este repositorio: sin conexión
COM propia (nada de ``GetActiveObject``/``SapConnection``), cada operación
recibe el ``SapModel`` ya conectado a través del actor STA de la sesión (ver
``bridge/sap2000_agent.py``) y las rutas se instalan desde
``bridge.actions.install_routes`` con el mismo envoltorio ``call`` y la misma
autenticación por token, igual que ``bridge/esbelteces.py`` y
``bridge/combinaciones.py``.

Mismas llamadas COM, mismos argumentos, mismo indexado de resultados
(``raw[4]``/``raw[5]``/``raw[6]``) y mismos mensajes de error literales que el
original. Excepción deliberada: el peso sísmico (Ps) y los cortes basales
(Qx, Qy) se leen siempre en tonf (unidades presentes Ton_m_C) y se
restauran las unidades del modelo al terminar; el original los devolvía en las
unidades del modelo, de modo que un modelo en kgf mostraba kg como toneladas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, field_validator

from bridge.actions import _valid_name, split_return

DAMP_RATIO_DEFAULT = 0.05
FUNC_TYPE_RESPONSE_SPECTRUM = 1
CASE_TYPE_MODAL = 3

UNITS_TON_M_C = 12  # eUnits.Ton_m_C: fuerza en tonf

NO_RESULTS_HINT = "Corra el análisis en SAP2000 primero (Analyze > Run Analysis)."

_MAX_ITEMS = 500


class SapEspectroError(RuntimeError):
    """Espejo de los dicts ``{"success": False, "error": ...}`` del original.

    ``detail`` sigue el mismo criterio estructurado que
    ``bridge.combinaciones.Sap2000ComError``: el envoltorio ``call`` de
    ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


def _outputs(raw: Any, ret_first: bool | None = None) -> tuple[int, list[Any]]:
    """``(código, salidas)`` con el código de retorno al inicio o al final."""
    try:
        return split_return(raw, ret_first)
    except RuntimeError as exc:
        raise SapEspectroError(str(exc)) from exc


# ---------------------------------------------------------------------------
# sap2000_bridge.py — port literal (``conn.sap_model`` -> ``model``)
# ---------------------------------------------------------------------------


def list_response_spectrum_functions(model: Any) -> list[str]:
    """Lista las funciones de espectro de respuesta ya definidas en SAP2000.

    Port literal de ``list_response_spectrum_functions``:
    ``Func.GetNameList(0, [], FUNC_TYPE_RESPONSE_SPECTRUM)``.
    """
    try:
        raw = model.Func.GetNameList(0, [], FUNC_TYPE_RESPONSE_SPECTRUM)
    except Exception as exc:
        raise SapEspectroError(f"Error consultando funciones en SAP2000: {exc}") from exc

    code, outs = _outputs(raw)
    if code != 0 or len(outs) < 2:
        raise SapEspectroError("SAP2000 no pudo listar las funciones de espectro.")

    n = outs[0]
    return list(outs[1])[:n] if n else []


def list_load_cases(model: Any, only_modal: bool = False) -> list[str]:
    """Lista los load cases del modelo. Si ``only_modal``, filtra a casos
    tipo Modal. Port literal de ``list_load_cases``:
    ``LoadCases.GetNameList(0, [])`` + (si ``only_modal``)
    ``LoadCases.GetTypeOAPI(nombre, 0, 0) == CASE_TYPE_MODAL`` por nombre.
    """
    try:
        raw = model.LoadCases.GetNameList(0, [])
    except Exception as exc:
        raise SapEspectroError(f"Error consultando load cases en SAP2000: {exc}") from exc

    code, outs = _outputs(raw)
    if code != 0 or len(outs) < 2:
        raise SapEspectroError("SAP2000 no pudo listar los load cases.")
    # GetNameList entrega (n, nombres): el arreglo delata de qué lado va el código.
    ret_first = isinstance(raw[-1], (list, tuple))

    n = outs[0]
    nombres = list(outs[1])[:n] if n else []

    if only_modal:
        modales = []
        for nombre in nombres:
            try:
                raw_tipo = model.LoadCases.GetTypeOAPI(nombre, 0, 0)
            except Exception:
                continue
            try:
                code_tipo, outs_tipo = _outputs(raw_tipo, ret_first)
            except SapEspectroError:
                continue
            if code_tipo == 0 and outs_tipo and outs_tipo[0] == CASE_TYPE_MODAL:
                modales.append(nombre)
        nombres = modales

    return nombres


def push_curve_to_sap2000(
    model: Any,
    periods: list[float],
    values: list[float],
    func_name: str,
    damp_ratio: float = DAMP_RATIO_DEFAULT,
) -> None:
    """Define/actualiza en SAP2000 una función de espectro de respuesta
    definida por usuario. Llamar con el mismo nombre sobreescribe la función
    existente en SAP2000, lo cual sirve tanto para crear como para
    actualizar. Port literal de ``push_curve_to_sap2000``:
    ``Func.FuncRS.SetUser(func_name, len(periods), periods, values,
    damp_ratio)``.
    """
    if not periods:
        raise SapEspectroError("El espectro está vacío — no hay puntos para enviar.")

    try:
        raw = model.Func.FuncRS.SetUser(func_name, len(periods), periods, values, damp_ratio)
    except Exception as exc:
        raise SapEspectroError(f"Error llamando a SAP2000 ({func_name}): {exc}") from exc

    ret_code = _outputs(raw)[0]
    if ret_code != 0:
        raise SapEspectroError(f"SAP2000 rechazó la función '{func_name}' (código {ret_code}).")


def _select_case_for_output(model: Any, case_name: str) -> Any:
    model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    return model.Results.Setup.SetCaseSelectedForOutput(case_name)


def get_modal_periods_at_max_mass(model: Any, modal_case: str) -> tuple[float, float]:
    """Lee la tabla "Modal Participating Mass Ratios" del caso modal indicado
    y retorna el período asociado al máximo UX (Tx) y al máximo UY (Ty).
    Port literal de ``get_modal_periods_at_max_mass``:
    ``Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [],
    [], [], [], [], [], [], [], [], [])`` (17 argumentos por referencia),
    ``raw[4]``=períodos, ``raw[5]``=UX, ``raw[6]``=UY.
    """
    try:
        _select_case_for_output(model, modal_case)
        raw = model.Results.ModalParticipatingMassRatios(
            0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], []
        )
    except Exception as exc:
        raise SapEspectroError(f"Error leyendo resultados modales: {exc}") from exc

    code, outs = _outputs(raw)
    if code != 0 or not outs or not outs[0] or len(outs) < 7:
        raise SapEspectroError(f"No hay resultados modales para '{modal_case}'. {NO_RESULTS_HINT}")

    periods = list(outs[4])
    ux = list(outs[5])
    uy = list(outs[6])

    ix = ux.index(max(ux))
    iy = uy.index(max(uy))

    return periods[ix], periods[iy]


def get_base_reaction_sums(model: Any, case_name: str) -> tuple[float, float, float]:
    """Lee la tabla "Base Reactions" para el load case indicado y suma las
    componentes GLOBAL FX, FY, FZ sobre todas las filas devueltas. Port
    literal de ``get_base_reaction_sums``: ``Results.BaseReact(0, [], [],
    [], [], [], [], [], [], [], 0.0, 0.0, 0.0)``, ``raw[4]``=FX,
    ``raw[5]``=FY, ``raw[6]``=FZ.
    """
    try:
        _select_case_for_output(model, case_name)
        raw = model.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0.0, 0.0, 0.0)
    except Exception as exc:
        raise SapEspectroError(f"Error leyendo reacciones de base: {exc}") from exc

    code, outs = _outputs(raw)
    if code != 0 or not outs or not outs[0] or len(outs) < 7:
        raise SapEspectroError(f"No hay reacciones de base para '{case_name}'. {NO_RESULTS_HINT}")

    fx = sum(outs[4])
    fy = sum(outs[5])
    fz = sum(outs[6])

    return fx, fy, fz


# ---------------------------------------------------------------------------
# sap2000_dialogs.py — _calcular_ps / _calcular_qxqy / _calcular_modal /
# _enviar_curvas, port literal (``conn`` -> ``model``).
# ---------------------------------------------------------------------------


def _present_units(model: Any) -> int:
    raw = model.GetPresentUnits()
    if isinstance(raw, (tuple, list)):
        values = [v for v in raw if isinstance(v, int) and not isinstance(v, bool)]
        if not values:
            raise SapEspectroError("No se pudieron identificar las unidades de SAP2000.")
        return values[0]
    return int(raw)


def _en_tonf(model: Any, operation: Callable[[], Any]) -> Any:
    """Ejecuta ``operation`` con las unidades presentes en Ton_m_C (fuerzas en tonf)
    y restaura las del modelo, aunque falle."""
    previous = _present_units(model)
    changed = previous != UNITS_TON_M_C
    if changed:
        ret = _outputs(model.SetPresentUnits(UNITS_TON_M_C))[0]
        if ret != 0:
            raise SapEspectroError("SAP2000 no permitió cambiar las unidades a Ton_m_C.")
    try:
        return operation()
    finally:
        if changed:
            model.SetPresentUnits(previous)


def calcular_ps(model: Any, filas: list[dict[str, Any]]) -> float:
    """Ps = Σ (factor × ΣFZ Base Reactions) por load case. Port literal de
    ``_calcular_ps``: se detiene en el primer load case sin resultados
    (mismo criterio que el original — no acumula parcialmente)."""

    def run() -> float:
        total = 0.0
        for fila in filas:
            _fx, _fy, fz = get_base_reaction_sums(model, fila["case"])
            total += fila["factor"] * fz
        return total

    return _en_tonf(model, run)


def calcular_qxqy(model: Any, case_x: str, case_y: str) -> tuple[float, float]:
    """Qx = ΣFX del caso Sismo X, Qy = ΣFY del caso Sismo Y (no el mismo
    caso). Port literal de ``_calcular_qxqy``."""

    def run() -> tuple[float, float]:
        qx, _fy_x, _fz_x = get_base_reaction_sums(model, case_x)
        _fx_y, qy, _fz_y = get_base_reaction_sums(model, case_y)
        return qx, qy

    return _en_tonf(model, run)


def calcular_modal(model: Any, modal_case: str) -> tuple[float, float]:
    """Port literal de ``_calcular_modal`` → ``get_modal_periods_at_max_mass``."""
    return get_modal_periods_at_max_mass(model, modal_case)


def enviar_curvas(model: Any, jobs: list[tuple[str, list[float], list[float]]]) -> dict[str, Any]:
    """Envía cada curva (nombre, periods, values) a SAP2000; acumula éxitos y
    fallos sin abortar el resto. Port literal de ``_enviar_curvas``."""
    enviadas: list[str] = []
    errores: list[dict[str, str]] = []
    for func_name, periods, values in jobs:
        try:
            push_curve_to_sap2000(model, periods, values, func_name)
        except SapEspectroError as exc:
            errores.append({"nombre": func_name, "error": str(exc)})
        else:
            enviadas.append(func_name)
    return {"enviadas": enviadas, "errores": errores}


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


class SendJob(BaseModel):
    nombre: str
    periods: list[float]
    values: list[float]

    @field_validator("nombre")
    @classmethod
    def job_name(cls, value: str) -> str:
        return _valid_name(value, "function name")


class SendBody(BaseModel):
    jobs: list[SendJob]
    confirmed: bool = False

    @field_validator("jobs")
    @classmethod
    def bounded_jobs(cls, value: list[SendJob]) -> list[SendJob]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("jobs must contain 1-500 entries")
        return value


class PsRow(BaseModel):
    case: str
    factor: float

    @field_validator("case")
    @classmethod
    def case_name(cls, value: str) -> str:
        return _valid_name(value, "load case")


class PsBody(BaseModel):
    rows: list[PsRow]

    @field_validator("rows")
    @classmethod
    def bounded_rows(cls, value: list[PsRow]) -> list[PsRow]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("rows must contain 1-500 entries")
        return value


class QxQyBody(BaseModel):
    case_x: str
    case_y: str

    @field_validator("case_x", "case_y")
    @classmethod
    def case_name(cls, value: str) -> str:
        return _valid_name(value, "load case")


class ModalBody(BaseModel):
    modal_case: str

    @field_validator("modal_case")
    @classmethod
    def case_name(cls, value: str) -> str:
        return _valid_name(value, "modal case")


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/espectro/*``.

    ``call`` es el mismo envoltorio de ``bridge.actions.install_routes`` que
    ejecuta la función dada en el actor STA de la sesión y traduce las
    excepciones a respuestas HTTP (mismo criterio de errores y autenticación
    que el resto del agente; ver ``SapEspectroError.detail`` arriba para el
    cuerpo 409). Las rutas existentes ``/v1/sap/actions/spectrum/*`` (creación
    de función response-spectrum) quedan
    intactas — ``espectro`` aquí es el puente 1:1 del escritorio Espectro
    NCh2369 (funciones RS, load cases, envío de curvas, Ps/Qx/Qy/T* modal).
    """
    from fastapi import HTTPException

    prefix = "/v1/sap/actions/espectro"

    @app.get(f"{prefix}/functions")
    def espectro_functions():
        return call(lambda model: {"nombres": list_response_spectrum_functions(model)})

    @app.get(f"{prefix}/load-cases")
    def espectro_load_cases(only_modal: bool = False):
        return call(lambda model: {"nombres": list_load_cases(model, only_modal)})

    @app.post(f"{prefix}/send")
    def espectro_send(body: SendBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to send curves to SAP2000")
        jobs = [(job.nombre, job.periods, job.values) for job in body.jobs]
        return call(lambda model: enviar_curvas(model, jobs))

    @app.post(f"{prefix}/ps")
    def espectro_ps(body: PsBody):
        filas = [row.model_dump() for row in body.rows]
        return call(lambda model: {"ps": calcular_ps(model, filas)})

    @app.post(f"{prefix}/qxqy")
    def espectro_qxqy(body: QxQyBody):
        def run(model: Any) -> dict[str, float]:
            qx, qy = calcular_qxqy(model, body.case_x, body.case_y)
            return {"qx": qx, "qy": qy}

        return call(run)

    @app.post(f"{prefix}/modal")
    def espectro_modal(body: ModalBody):
        def run(model: Any) -> dict[str, float]:
            tx, ty = calcular_modal(model, body.modal_case)
            return {"Tx": tx, "Ty": ty}

        return call(run)

    # Lectura para «Nuevo / reemplazar» en el envío a SAP2000: nombres de los espectros de respuesta
    # que ya existen en el modelo (misma consulta que /functions, expuesta como acción propia).
    @app.get(f"{prefix}/spectra")
    def espectro_spectra():
        return call(lambda model: {"nombres": list_response_spectrum_functions(model)})
