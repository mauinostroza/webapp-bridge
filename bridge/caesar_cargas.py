"""SAP2000 acciones para CAESAR II → SAP2000: transferencia de cargas nodales.

Rutas bajo ``/v1/sap/actions/caesar/``:

* ``GET modelo``: solo lectura. Nodos, patrones de carga, unidades y bloqueo, para que la web avise
  de nodos que no existen en SAP2000 y de patrones que ya están.
* ``POST cargas/preview``: solo lecturas COM. Calcula qué se crearía y qué se aplicaría. No escribe.
* ``POST cargas/apply``: crea los load patterns que faltan y aplica cada carga con
  ``PointObj.SetLoadForce``. Solo FX, FY y FZ: los momentos se envían a cero.
* ``GET seleccion``: solo lectura. Puntos (joints) seleccionados ahora en SAP2000, con los que son
  nº de nodo CAESAR (``nodos``) y los que no (``noNumericos``). No cambia la selección.
* ``POST cargas/existentes``: solo lectura. FX, FY y FZ actuales en SAP2000 de esos nodos y
  patrones, sumando las entradas de un mismo nodo y patrón. No escribe.

Unidades (política set-apply-restore): se guarda ``GetPresentUnits``, se fijan las unidades del envío
(``unidades``) y se restauran siempre en ``finally``. Solo se admiten las combinaciones fuerza-longitud
que existen en la tabla eUnits de SAP2000 (``EUNITS_POR_UNIDADES``); otra combinación es 422.

Modelo bloqueado (tiene resultados): ``apply`` responde 409 ``model_locked`` sin escribir, salvo con
``confirmarBloqueo=true``, que lo desbloquea. Desbloquear BORRA los resultados del análisis.

Un nodo que no existe en el modelo no aborta: va a ``fallidas`` con su motivo y no se llama a
``SetLoadForce`` para él. Una carga que SAP2000 rechaza tampoco aborta. Un error a mitad (un patrón
que no se crea, un error inesperado) responde 409 ``partial`` con lo ya aplicado; no se revierte.

Estado: validado con COM falso (tests/test_bridge_caesar_cargas.py). Pendiente de validar con SAP2000
real: las firmas ``LoadPatterns.Add``, ``LoadPatterns.GetLoadType`` y ``PointObj.SetLoadForce`` usadas
aquí se han tomado del código existente y de la skill, y el tiempo de ``apply`` con muchas cargas.

Mismo patrón que ``bridge/acero_aisc360_enviar.py``: sin conexión COM propia; las rutas se instalan
desde ``bridge.actions.install_routes`` con el envoltorio ``call``.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bridge.acero_aisc360_fuerzas import _restaurar_unidades, _valor_unico
from bridge.actions import (
    ActionValidationError,
    PartialApplyError,
    _existing_loads_no_units,
    _get_name_list,
    _ret,
    _valid_name,
    model_is_locked,
    selected_objects,
    split_return,
)
from bridge.com_tiempo import con_limite
from bridge.graph import UNITS

logger = logging.getLogger(__name__)

PREFIJO = "/v1/sap/actions/caesar"
MAX_CARGAS = 200_000
MAX_PATRONES = 2_000
MAX_PUNTOS = 50_000  # por encima, GET modelo solo devuelve el recuento
MAX_LISTA = 200  # tamaño máximo de ``faltantes`` (preview) y ``fallidas`` (apply)
MAX_NODOS_CARGAS_PREVIAS = 2_000  # por encima no se leen las cargas previas (cargasPrevias = null)
MAX_NO_NUMERICOS = 200  # tamaño máximo de ``noNumericos`` (GET seleccion)
LIMITE_LECTURA_S = 120.0
LIMITE_ESCRITURA_S = 600.0  # apply con muchas cargas; el actor COM por defecto espera 30 s

FUERZAS = ("kgf", "N", "kN", "tonf", "lb", "kip")
LONGITUDES = ("m", "mm", "cm", "ft", "in")

# (fuerza, longitud) -> código eUnits de SAP2000. Son los mismos códigos que ``bridge.graph.UNITS``
# (tests/test_bridge_caesar_cargas.py comprueba la coincidencia). Ej.: kN-m = 6, kgf-m = 8, N-m = 10,
# tonf-m = 12 (Ton_m_C).
EUNITS_POR_UNIDADES: dict[tuple[str, str], int] = {
    ("lb", "in"): 1,
    ("lb", "ft"): 2,
    ("kip", "in"): 3,
    ("kip", "ft"): 4,
    ("kN", "mm"): 5,
    ("kN", "m"): 6,
    ("kgf", "mm"): 7,
    ("kgf", "m"): 8,
    ("N", "mm"): 9,
    ("N", "m"): 10,
    ("tonf", "mm"): 11,
    ("tonf", "m"): 12,
    ("kN", "cm"): 13,
    ("kgf", "cm"): 14,
    ("N", "cm"): 15,
    ("tonf", "cm"): 16,
}

# Tipos de load pattern admitidos y su eLoadPatternType. Solo los dos verificados con la sonda de
# SAP2000 23.3.1 (Dead = 1 en el DEAD por defecto; Other = 8). Otros tipos se añadirán cuando se
# compruebe su código en SAP2000 real: la skill sap2000-programming no coincide con la sonda.
TIPOS_PATRON = {"Dead": 1, "Other": 8}
TIPO_PATRON_POR_CODIGO = {codigo: nombre for nombre, codigo in TIPOS_PATRON.items()}

ADVERTENCIA_MOMENTOS = "Solo se transfieren FX, FY y FZ: los momentos Mx, My y Mz se envían a cero."
ADVERTENCIA_LECTURA_MOMENTOS = "Solo se leen FX, FY y FZ: los momentos Mx, My y Mz no se devuelven."
_NODO_CANONICO = re.compile(r"[1-9][0-9]*")  # entero >= 1 sin ceros a la izquierda: «007» no es el nodo 7
ADVERTENCIA_DESBLOQUEO = (
    "El modelo quedó desbloqueado: SAP2000 BORRA los resultados del análisis. "
    "Hay que volver a analizar el modelo para tener resultados."
)


class CaesarError(RuntimeError):
    """Error de SAP2000 o de estado del modelo. ``detail`` es el cuerpo del 409."""

    def __init__(self, message: str, code: str = "sap_error"):
        super().__init__(message)
        self.detail = {"code": code, "message": message}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


class UnidadesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fuerza: Literal["kgf", "N", "kN", "tonf", "lb", "kip"]
    longitud: Literal["m", "mm", "cm", "ft", "in"]

    @model_validator(mode="after")
    def combinacion_soportada(self) -> UnidadesBody:
        if (self.fuerza, self.longitud) not in EUNITS_POR_UNIDADES:
            soportadas = ", ".join(f"{f}-{l}" for f, l in EUNITS_POR_UNIDADES)
            raise ValueError(
                f"Combinación de unidades no soportada: {self.fuerza}-{self.longitud}. "
                f"Combinaciones soportadas (fuerza-longitud): {soportadas}."
            )
        return self


class PatronBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "Other"

    @field_validator("name")
    @classmethod
    def nombre_valido(cls, value: str) -> str:
        return _valid_name(value, "loadPattern")

    @field_validator("type")
    @classmethod
    def tipo_soportado(cls, value: str) -> str:
        if value not in TIPOS_PATRON:
            raise ValueError(
                f"tipo de patrón {value!r} no soportado. Tipos válidos: {', '.join(TIPOS_PATRON)}."
            )
        return value


class CargaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodo: int = Field(ge=1)
    loadPattern: str
    fx: float
    fy: float
    fz: float

    @field_validator("loadPattern")
    @classmethod
    def patron_valido(cls, value: str) -> str:
        return _valid_name(value, "loadPattern")

    @field_validator("fx", "fy", "fz")
    @classmethod
    def finito(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("las componentes de fuerza deben ser finitas")
        return value


class CargasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unidades: UnidadesBody
    crearLoadPatterns: bool
    loadPatterns: list[PatronBody] = Field(default_factory=list, max_length=MAX_PATRONES)
    cargas: list[CargaBody] = Field(min_length=1, max_length=MAX_CARGAS)
    modo: Literal["reemplazar", "sumar"]

    @model_validator(mode="after")
    def sin_repetidos(self) -> CargasBody:
        nombres = [patron.name.casefold() for patron in self.loadPatterns]
        if len(set(nombres)) != len(nombres):
            raise ValueError("loadPatterns contiene nombres repetidos (SAP2000 no distingue mayúsculas).")
        pares = [(carga.nodo, carga.loadPattern.casefold()) for carga in self.cargas]
        if len(set(pares)) != len(pares):
            raise ValueError("cargas repetidas: el mismo nodo y patrón aparece más de una vez.")
        return self


class CargasApplyBody(CargasBody):
    confirmarBloqueo: bool = False


class CargasExistentesBody(BaseModel):
    """Cuerpo de ``POST cargas/existentes``. Solo lectura: no admite ``loadPattern`` por carga ni momentos."""

    model_config = ConfigDict(extra="forbid")

    unidades: UnidadesBody
    loadPatterns: list[str] = Field(min_length=1, max_length=MAX_PATRONES)
    nodos: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=MAX_CARGAS)

    @field_validator("loadPatterns")
    @classmethod
    def patrones_validos(cls, value: list[str]) -> list[str]:
        nombres = [_valid_name(nombre, "loadPattern") for nombre in value]
        if len({nombre.casefold() for nombre in nombres}) != len(nombres):
            raise ValueError("loadPatterns contiene nombres repetidos (SAP2000 no distingue mayúsculas).")
        return nombres


# ---------------------------------------------------------------------------
# Lecturas (sin escribir)
# ---------------------------------------------------------------------------


def _nombre_unidades(codigo: int) -> str:
    return UNITS.get(codigo, f"Unidades SAP no reconocidas ({codigo})")


def _unidades_actuales(model: Any) -> int:
    """Código eUnits presente. Tolera ``GetPresentUnits`` como escalar o como tupla con el retorno."""
    try:
        return int(_valor_unico(model.GetPresentUnits(), "GetPresentUnits"))
    except Exception as exc:
        raise CaesarError(f"No se pudieron leer las unidades de SAP2000: {exc}") from exc


def _unidades_modelo(model: Any) -> str | None:
    """Nombre de las unidades presentes para las lecturas; ``None`` si no se pueden leer."""
    try:
        return _nombre_unidades(_unidades_actuales(model))
    except CaesarError:
        return None


def _puntos(model: Any) -> list[str]:
    return _get_name_list(model.PointObj, "PointObj.GetNameList")


def _patrones(model: Any) -> list[str]:
    return _get_name_list(model.LoadPatterns, "LoadPatterns.GetNameList")


def _casos(model: Any) -> list[str]:
    try:
        return _get_name_list(model.LoadCases, "LoadCases.GetNameList")
    except Exception:
        return []


def _tipo_patron(model: Any, nombre: str) -> str:
    try:
        codigo, salidas = split_return(model.LoadPatterns.GetLoadType(nombre))
        if codigo != 0 or not salidas:
            return "desconocido"
        tipo = int(salidas[0])
    except Exception:
        return "desconocido"
    return TIPO_PATRON_POR_CODIGO.get(tipo, f"tipo {tipo}")


def _es_nula(carga: CargaBody) -> bool:
    return carga.fx == 0 and carga.fy == 0 and carga.fz == 0


@dataclass
class _Plan:
    unidades: int  # eUnits del envío
    bloqueado: bool
    existentes: list[str]  # nombres pedidos que ya están en el modelo
    a_crear: list[PatronBody]
    faltantes: list[str]  # usados en cargas, no están y no se crean
    nodos: list[int]  # distintos, en orden de aparición
    nodos_faltantes: list[int]  # ordenados
    avisos: list[str] = field(default_factory=list)


def _planificar(model: Any, body: CargasBody) -> _Plan:
    avisos: list[str] = []
    unidades = EUNITS_POR_UNIDADES[(body.unidades.fuerza, body.unidades.longitud)]
    puntos = set(_puntos(model))
    mapa = {nombre.casefold(): nombre for nombre in _patrones(model)}

    if body.crearLoadPatterns:
        nuevos = list(body.loadPatterns)
    else:
        nuevos = []
        if body.loadPatterns:
            avisos.append("loadPatterns se ignora porque crearLoadPatterns es false.")
    a_crear = [patron for patron in nuevos if patron.name.casefold() not in mapa]
    creables = {patron.name.casefold() for patron in a_crear}

    pedidos: dict[str, str] = {}  # casefold -> nombre pedido, en orden de aparición
    for patron in nuevos:
        pedidos.setdefault(patron.name.casefold(), patron.name)
    for carga in body.cargas:
        pedidos.setdefault(carga.loadPattern.casefold(), carga.loadPattern)
    existentes = [nombre for cf, nombre in pedidos.items() if cf in mapa]
    faltantes = [nombre for cf, nombre in pedidos.items() if cf not in mapa and cf not in creables]

    nodos = list(dict.fromkeys(carga.nodo for carga in body.cargas))
    nodos_faltantes = sorted(nodo for nodo in nodos if str(nodo) not in puntos)

    casos = {caso.casefold() for caso in _casos(model)}
    for patron in a_crear:
        if patron.name.casefold() in casos:
            avisos.append(
                f"El patrón {patron.name} tiene el nombre de un caso de carga existente: "
                "SAP2000 dará al caso nuevo otro nombre (con sufijo)."
            )

    return _Plan(
        unidades=unidades,
        bloqueado=model_is_locked(model),
        existentes=existentes,
        a_crear=a_crear,
        faltantes=faltantes,
        nodos=nodos,
        nodos_faltantes=nodos_faltantes,
        avisos=avisos,
    )


def _avisos_envio(body: CargasBody, plan: _Plan) -> list[str]:
    """Avisos comunes a preview y apply."""
    avisos = list(plan.avisos)
    avisos.append(ADVERTENCIA_MOMENTOS)
    avisos.append(
        f"Las unidades del modelo se fijan a {UNITS[plan.unidades]} para escribir y se restauran al terminar."
    )
    if body.modo == "reemplazar":
        avisos.append("modo reemplazar: cada carga sustituye la existente del mismo nodo y patrón.")
    else:
        avisos.append("modo sumar: cada carga se suma a la existente del mismo nodo y patrón.")
    ceros = sum(1 for carga in body.cargas if _es_nula(carga))
    if ceros:
        avisos.append(
            f"{ceros} carga(s) con FX, FY y FZ a cero no se escriben (SAP2000 no admite una carga nula)."
        )
    if plan.nodos_faltantes:
        avisos.append(
            f"{len(plan.nodos_faltantes)} nodo(s) no existen en el modelo y no se escribirán: "
            "se listan en fallidas (apply) o en nodos.faltantes (preview)."
        )
    return avisos


def _cargas_previas(model: Any, body: CargasBody, plan: _Plan, avisos: list[str]) -> int | None:
    """Pares nodo x patrón (existente) que ya tienen carga en SAP2000. ``None`` si es caro o falla."""
    faltantes = set(plan.nodos_faltantes)
    presentes = [nodo for nodo in plan.nodos if nodo not in faltantes]
    existentes = {nombre.casefold() for nombre in plan.existentes}
    if not presentes or not existentes:
        return 0
    if len(presentes) > MAX_NODOS_CARGAS_PREVIAS:
        return None
    presentes_set = set(presentes)
    pedidos = {
        (str(carga.nodo), carga.loadPattern.casefold())
        for carga in body.cargas
        if carga.nodo in presentes_set and carga.loadPattern.casefold() in existentes and not _es_nula(carga)
    }
    try:
        filas = _existing_loads_no_units(model, [str(nodo) for nodo in presentes])
    except Exception:
        avisos.append("No se pudieron leer las cargas previas de SAP2000: cargasPrevias es null.")
        return None
    previas = {(fila["joint"], str(fila["load_pat"]).casefold()) for fila in filas}
    return len(pedidos & previas)


def leer_modelo(model: Any) -> dict[str, Any]:
    """``GET modelo``: nodos, patrones (con tipo), unidades y bloqueo. Solo lectura."""
    puntos = _puntos(model)
    truncado = len(puntos) > MAX_PUNTOS
    return {
        "ok": True,
        "locked": model_is_locked(model),
        "unidadesModelo": _unidades_modelo(model),
        "nPuntos": len(puntos),
        "puntosTruncado": truncado,
        "puntos": [] if truncado else puntos,
        "loadPatterns": [
            {"name": nombre, "type": _tipo_patron(model, nombre)} for nombre in _patrones(model)
        ],
    }


def preview_cargas(model: Any, body: CargasBody) -> dict[str, Any]:
    """Vista previa: solo lecturas COM. No llama a ningún método de escritura."""
    plan = _planificar(model, body)
    avisos = _avisos_envio(body, plan)
    if plan.bloqueado:
        avisos.append(
            "El modelo está bloqueado (tiene resultados). El apply responde 409 model_locked salvo con "
            "confirmarBloqueo=true, que desbloquea y BORRA los resultados del análisis."
        )
    previas = _cargas_previas(model, body, plan, avisos)
    ceros = sum(1 for carga in body.cargas if _es_nula(carga))
    return {
        "ok": not plan.nodos_faltantes and not plan.faltantes,
        "locked": plan.bloqueado,
        "unidadesModelo": _unidades_modelo(model),
        "unidadesEnvio": UNITS[plan.unidades],
        "patterns": {
            "existentes": plan.existentes,
            "aCrear": [patron.name for patron in plan.a_crear],
            "faltantes": plan.faltantes,
        },
        "nodos": {
            "total": len(plan.nodos),
            "encontrados": len(plan.nodos) - len(plan.nodos_faltantes),
            "faltantes": plan.nodos_faltantes[:MAX_LISTA],
            "nFaltantes": len(plan.nodos_faltantes),
        },
        "cargas": {"total": len(body.cargas), "conValor": len(body.cargas) - ceros, "ceros": ceros},
        "cargasPrevias": previas,
        "avisos": avisos,
    }


def _nodo_de_nombre(nombre: str) -> int | None:
    """Nº de nodo CAESAR de un nombre SAP2000, o ``None`` si no es un entero >= 1 sin ceros a la izquierda."""
    return int(nombre) if _NODO_CANONICO.fullmatch(nombre) else None


def leer_seleccion(model: Any) -> dict[str, Any]:
    """``GET seleccion``: puntos seleccionados ahora. Solo lectura: no cambia la selección.

    Sin selección responde ``ok`` con listas vacías. ``nodos`` tiene todos los enteros >= 1 (sin tope);
    ``puntos`` solo los primeros ``MAX_PUNTOS`` nombres.
    """
    nombres = selected_objects(model, "joints")
    nodos: list[int] = []
    no_numericos: list[str] = []
    for nombre in nombres:
        nodo = _nodo_de_nombre(nombre)
        if nodo is None:
            no_numericos.append(nombre)
        else:
            nodos.append(nodo)
    return {
        "ok": True,
        "nPuntos": len(nombres),
        "puntos": nombres[:MAX_PUNTOS],
        "puntosTruncado": len(nombres) > MAX_PUNTOS,
        "nodos": nodos,
        "noNumericos": no_numericos[:MAX_NO_NUMERICOS],
        "nNoNumericos": len(no_numericos),
        "unidadesModelo": _unidades_modelo(model),
    }


def leer_cargas_existentes(model: Any, body: CargasExistentesBody) -> dict[str, Any]:
    """``POST cargas/existentes``: FX, FY y FZ actuales de esos nodos y patrones. No escribe en el modelo.

    Se lee por objeto (``_existing_loads_no_units``: ``PointObj.GetLoadForce`` por nodo) y no por la tabla
    «Joint Loads - Force», que solo ve los patrones mostrados. Así no hace falta cambiar la selección de
    patrones mostrados (``all_load_patterns_displayed``). Las entradas de un mismo nodo y patrón se suman,
    también las de distinto sistema de coordenadas (se avisa). Los momentos se descartan.

    Unidades (set-apply-restore): se fijan las del cuerpo para leer y se restauran SIEMPRE en ``finally``.
    Un fallo de SAP2000 al leer responde 409 ``sap_error``.
    """
    envio = EUNITS_POR_UNIDADES[(body.unidades.fuerza, body.unidades.longitud)]
    avisos = [
        ADVERTENCIA_LECTURA_MOMENTOS,
        f"Las unidades del modelo se fijan a {UNITS[envio]} para leer y se restauran al terminar.",
    ]
    previas = _unidades_actuales(model)
    cambia = previas != envio
    restauradas = True
    try:
        if cambia:
            _ret(model.SetPresentUnits(envio), "SetPresentUnits(lectura)")
        puntos = set(_puntos(model))
        mapa = {nombre.casefold(): nombre for nombre in _patrones(model)}
        nodos = list(dict.fromkeys(body.nodos))
        presentes = [nodo for nodo in nodos if str(nodo) in puntos]
        nodos_inexistentes = [nodo for nodo in nodos if str(nodo) not in puntos]
        patrones_inexistentes = [nombre for nombre in body.loadPatterns if nombre.casefold() not in mapa]
        pedidos = [nombre.casefold() for nombre in body.loadPatterns if nombre.casefold() in mapa]
        filas = (
            _existing_loads_no_units(model, [str(nodo) for nodo in presentes])
            if presentes and pedidos
            else []
        )
    except CaesarError:
        raise
    except Exception as exc:
        raise CaesarError(f"No se pudieron leer las cargas de SAP2000: {exc}") from exc
    finally:
        if cambia:
            aviso = _restaurar_unidades(model, previas)
            restauradas = aviso is None
            if aviso:
                avisos.append(aviso)

    solicitados = set(pedidos)
    sumas: dict[tuple[int, str], list[float]] = {}
    coordenadas_no_globales = False
    for fila in filas:
        patron = fila["load_pat"].casefold()
        if patron not in solicitados:
            continue
        if fila["coord_sys"] != "Global":
            coordenadas_no_globales = True
        acumulado = sumas.setdefault((int(fila["joint"]), patron), [0.0, 0.0, 0.0])
        acumulado[0] += fila["f1"]
        acumulado[1] += fila["f2"]
        acumulado[2] += fila["f3"]

    orden = {patron: indice for indice, patron in enumerate(pedidos)}
    cargas = [
        {"nodo": nodo, "loadPattern": mapa[patron], "fx": fx, "fy": fy, "fz": fz}
        for (nodo, patron), (fx, fy, fz) in sumas.items()
        if fx != 0 or fy != 0 or fz != 0
    ]
    cargas.sort(key=lambda carga: (carga["nodo"], orden[carga["loadPattern"].casefold()]))
    truncado = len(cargas) > MAX_CARGAS

    if coordenadas_no_globales:
        avisos.append("Hay cargas en un sistema de coordenadas distinto de Global: se suman tal cual.")
    if patrones_inexistentes:
        avisos.append(f"{len(patrones_inexistentes)} patrón(es) no existen en el modelo: no se leen.")
    if nodos_inexistentes:
        avisos.append(f"{len(nodos_inexistentes)} nodo(s) no existen en el modelo: no se leen.")
    if truncado:
        avisos.append(f"Hay {len(cargas)} cargas; solo se devuelven las primeras {MAX_CARGAS}.")
    logger.info(
        "CAESAR II cargas existentes: nodos=%d patrones=%d cargas=%d unidades_restauradas=%s",
        len(presentes),
        len(pedidos),
        len(cargas),
        restauradas,
    )
    return {
        "ok": True,
        "unidadesEnvio": UNITS[envio],
        "unidadesModelo": _nombre_unidades(previas),
        "unidadesRestauradas": restauradas,
        "cargas": cargas[:MAX_CARGAS],
        "nCargas": len(cargas),
        "cargasTruncadas": truncado,
        "nodosInexistentes": nodos_inexistentes[:MAX_LISTA],
        "nNodosInexistentes": len(nodos_inexistentes),
        "patternsInexistentes": patrones_inexistentes,
        "avisos": avisos,
    }


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def _registrar_fallida(fallidas: list[dict[str, Any]], carga: CargaBody, motivo: str) -> None:
    if len(fallidas) < MAX_LISTA:
        fallidas.append({"nodo": carga.nodo, "loadPattern": carga.loadPattern, "motivo": motivo})


def apply_cargas(model: Any, body: CargasApplyBody) -> dict[str, Any]:
    """Crea los patrones que faltan y aplica las cargas. Unidades restauradas siempre en ``finally``."""
    plan = _planificar(model, body)
    if plan.faltantes:
        raise ActionValidationError(
            "Patrones de carga que no existen y no se crean: "
            + ", ".join(plan.faltantes)
            + ". Añádalos a loadPatterns con crearLoadPatterns=true o créelos en SAP2000. No se escribió nada."
        )
    if plan.bloqueado and not body.confirmarBloqueo:
        raise CaesarError(
            "El modelo está bloqueado (tiene resultados de análisis). Con confirmarBloqueo=true se desbloquea "
            "antes de escribir, lo que BORRA los resultados del análisis. No se escribió nada.",
            code="model_locked",
        )

    previas = _unidades_actuales(model)
    cambia = previas != plan.unidades
    avisos = _avisos_envio(body, plan)
    replace = body.modo == "reemplazar"
    nodos_faltantes = set(plan.nodos_faltantes)
    creados: list[str] = []
    aplicadas = 0
    fallidas: list[dict[str, Any]] = []
    total_fallidas = 0
    desbloqueado = False
    unidades_restauradas = True
    error: str | None = None
    try:
        if plan.bloqueado:
            _ret(model.SetModelIsLocked(False), "SetModelIsLocked(False)")
            desbloqueado = True
            avisos.append(ADVERTENCIA_DESBLOQUEO)
        if cambia:
            _ret(model.SetPresentUnits(plan.unidades), "SetPresentUnits(envio)")
        for patron in plan.a_crear:
            _ret(
                model.LoadPatterns.Add(patron.name, TIPOS_PATRON[patron.type], 0.0, True),
                f"LoadPatterns.Add({patron.name!r})",
            )
            creados.append(patron.name)
        for carga in body.cargas:
            if _es_nula(carga):
                continue
            if carga.nodo in nodos_faltantes:
                total_fallidas += 1
                _registrar_fallida(fallidas, carga, "nodo no existe en el modelo")
                continue
            # Sin reintento con replace=False: en modo «reemplazar» un fallo debe seguir siendo un fallo.
            try:
                _ret(
                    model.PointObj.SetLoadForce(
                        str(carga.nodo),
                        carga.loadPattern,
                        [carga.fx, carga.fy, carga.fz, 0.0, 0.0, 0.0],
                        replace,
                        "Global",
                    ),
                    "PointObj.SetLoadForce",
                )
                aplicadas += 1
            except Exception as exc:
                total_fallidas += 1
                _registrar_fallida(fallidas, carga, str(exc))
    except Exception as exc:
        error = f"Error a mitad de la escritura: {exc}. Revise las cargas en SAP2000."
    finally:
        if cambia:
            aviso = _restaurar_unidades(model, previas)
            unidades_restauradas = aviso is None
            if aviso:
                avisos.append(aviso)

    ok = error is None and total_fallidas == 0
    resultado = {
        "ok": ok,
        "estado": "ok" if ok else "partial",
        "patternsCreados": creados,
        "aplicadas": aplicadas,
        "fallidas": fallidas,
        "nFallidas": total_fallidas,
        "unidadesRestauradas": unidades_restauradas,
        "unidadesModelo": _nombre_unidades(previas),
        "modeloDesbloqueado": desbloqueado,
        "avisos": avisos,
    }
    logger.info(
        "CAESAR II cargas apply: aplicadas=%d fallidas=%d patrones_creados=%d unidades_restauradas=%s error=%s",
        aplicadas,
        total_fallidas,
        len(creados),
        unidades_restauradas,
        error is not None,
    )
    if not ok:
        raise PartialApplyError(
            {
                **resultado,
                "code": "partial",
                "partial": True,
                "error": error or f"{total_fallidas} carga(s) no se pudieron aplicar.",
            }
        )
    return resultado


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/caesar/*`` con el envoltorio ``call`` de ``bridge.actions``."""

    @app.get(f"{PREFIJO}/modelo")
    def caesar_modelo():
        with con_limite(LIMITE_LECTURA_S):
            return call(leer_modelo)

    @app.get(f"{PREFIJO}/seleccion")
    def caesar_seleccion():
        with con_limite(LIMITE_LECTURA_S):
            return call(leer_seleccion)

    @app.post(f"{PREFIJO}/cargas/existentes")
    def caesar_cargas_existentes(body: CargasExistentesBody):
        with con_limite(LIMITE_LECTURA_S):
            return call(lambda model: leer_cargas_existentes(model, body))

    @app.post(f"{PREFIJO}/cargas/preview")
    def caesar_cargas_preview(body: CargasBody):
        with con_limite(LIMITE_LECTURA_S):
            return call(lambda model: preview_cargas(model, body))

    @app.post(f"{PREFIJO}/cargas/apply")
    def caesar_cargas_apply(body: CargasApplyBody):
        with con_limite(LIMITE_ESCRITURA_S):
            return call(lambda model: apply_cargas(model, body))
