"""SAP2000 acción para Acero AISC 360-22: enviar un perfil como sección y asignarlo a barras.

Rutas ``/v1/sap/actions/acero-aisc360/enviar/{preview,apply,revertir}``:

* ``preview`` solo lee: existencia de la sección y del material, barras resueltas con su
  sección actual, bloqueo del modelo y avisos. No escribe.
* ``apply`` valida igual que ``preview`` y después escribe: crea la sección (o la sobrescribe
  con ``sobrescribir=true``), asigna cada barra con ``FrameObj.SetSection``, relee y compara.
* ``revertir`` reasigna las secciones previas que devolvió ``apply`` (solo ``SetSection``).

Con un perfil I, ``apply`` relee la geometría con ``PropFrame.GetISection`` (en mm, porque la
relectura va dentro del bloque N_mm_C) y la compara con lo enviado. Canal, ángulo y tubos solo
comprueban que la sección existe.

A diferencia de ``bridge.actions.assign_section``, ante un fallo a mitad no se continúa ni se
revierte automáticamente: la respuesta es ``partial`` (409) con las barras aplicadas, la sección
previa de cada una y ``revertir`` para deshacer a mano. Las secciones se escriben con
``bridge.actions.create_section`` (que ya fuerza y restaura las unidades N_mm_C). La geometría
solo se relee para la sección I (``GetISection``).

Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14): creación por tipo, asignación, relectura,
sobrescritura, nombres con ``*``, desbloqueo. Desbloquear BORRA los resultados del análisis.
La sonda v4 (2026-10-16) comprobó la geometría de los cinco tipos con ``PropFrame.Get*``, posición a
posición (D4-1). Esta acción solo relee la geometría de la I.

Mismo patrón que ``bridge/acero_aisc360_fuerzas.py``: sin conexión COM propia; las rutas se
instalan desde ``bridge.actions.install_routes`` con el envoltorio ``call``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from bridge.acero_aisc360_fuerzas import (
    VALIDADO_CON,
    VALIDADO_FECHA,
    _nombres_barras,
    _restaurar_unidades,
    _salidas,
    _seccion,
    _valor_unico,
)
from bridge.actions import (
    ActionValidationError,
    PartialApplyError,
    SectionExistsError,
    _get_name_list,
    _ret,
    _valid_name,
    create_section,
    selected_frames,
)

logger = logging.getLogger(__name__)

EUNITS_N_MM_C = 9  # eUnits.N_mm_C, como create_section
MAX_BARRAS = 500
PREFIJO = "/v1/sap/actions/acero-aisc360/enviar"

# Tipo de la web -> familia de SAP2000 que usa create_section.
TIPO_A_SAP = {
    "I": "I",
    "canal": "Channel",
    "angulo": "Angle",
    "tubo_rect": "Tube",
    "tubo_circ": "Pipe",
    "pipe": "Pipe",
}
TIPOS_TUBO = frozenset({"tubo_circ", "pipe"})
TIPOS_TEE = frozenset({"tee", "tees", "t"})

# Tipos (nombres de SAP2000, ``tipo_sap``) cuya geometría se comparó posición a posición con SAP2000 23.3.1:
# la I en la sonda v3 (2026-10-14, docs/SONDA_ACERO_AISC360.md) y los cinco tipos en la sonda v4 (2026-10-16,
# docs/SONDA_ACERO_AISC360_V4.md, D4-1-posicional-*: diferencia 0,000 mm en cada dimensión; ids de prueba
# D4-1-posicional-I, -Channel, -Angle, -Tube y -Pipe). Lista blanca: un tipo nuevo nace no validado.
TIPOS_VALIDADOS_POSICIONAL = frozenset({"I", "Channel", "Angle", "Tube", "Pipe"})

# ``validado_con_sap`` depende de la operación y, en apply, del tipo (``validacion``). No se añaden claves a
# las respuestas: origin/main fija su contrato exacto.


def validacion(operacion: str, tipo_sap: str | None = None) -> dict[str, Any]:
    """Campos de validación de una respuesta que toca SAP2000 (ver docs/SONDA_ACERO_AISC360.md).

    * ``preview``: True para todos los tipos (solo lecturas COM, validado por la sonda v3).
    * ``apply`` y apply parcial: True si ``tipo_sap`` está en ``TIPOS_VALIDADOS_POSICIONAL`` (I, canal,
      ángulo, tubo y pipe). Cualquier tipo fuera de la lista, o sin ``tipo_sap``, da False.
    * ``revertir`` y cualquier otra operación: False. La sonda v4 (D4-2) probó revertir con una sección I;
      la operación no recibe el tipo, así que no se da por validada para ninguno.
    """
    if operacion == "preview":
        validado = True
    elif operacion == "apply":
        validado = tipo_sap in TIPOS_VALIDADOS_POSICIONAL
    else:
        validado = False
    return {
        "validado_con_sap": validado,
        "validado_con": VALIDADO_CON,
        "validado_fecha": VALIDADO_FECHA,
    }


ADVERTENCIA_GEOMETRIA = (
    "Geometría de un tipo sin validar con SAP2000: la lectura posterior no se ha comparado posición a "
    "posición (no se relee en esta acción); revise las dimensiones en SAP2000 tras aplicar."
)
ADVERTENCIA_GEOMETRIA_I = (
    "La geometría de la sección I se relee con PropFrame.GetISection y se compara con lo enviado."
)
ADVERTENCIA_GEOMETRIA_SONDA = (
    "Geometría de canal/ángulo/tubo/pipe: esta acción no se relee; la sonda v4 la comparó posición a posición "
    "con SAP2000 23.3.1 (2026-10-16). Verifique en su versión."
)
ADVERTENCIA_REVERTIR = (
    "Revertir las barras no restaura una sección sobrescrita ni borra una sección nueva: revísela a mano."
)
ADVERTENCIA_PARCIAL = (
    "Las barras ya aplicadas no se revierten automáticamente: use revertir con la lista de la respuesta."
)
ADVERTENCIA_DESBLOQUEO = (
    "El modelo quedó desbloqueado: SAP2000 BORRA los resultados del análisis (FrameForce deja de devolver "
    "filas). Hay que volver a analizar el modelo para tener resultados."
)


class SapAceroEnviarError(RuntimeError):
    """Error de SAP2000 o de estado del modelo. ``detail`` es el cuerpo del 409."""

    def __init__(self, message: str, code: str = "sap_error"):
        super().__init__(message)
        self.detail = {"code": code, "message": message}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


class DimensionesMm(BaseModel):
    """Dimensiones en mm. Qué claves usa cada tipo lo decide ``_geometria``.

    Rectangulares (I, canal, angulo, tubo_rect): ``d``, ``bf``, ``tf``, ``tw``. En tubo_rect
    se aceptan ``t3`` y ``t2`` como alias de ``d`` y ``bf``. Circulares (tubo_circ, pipe): ``D``, ``t``.
    """

    model_config = ConfigDict(extra="forbid")

    d: float | None = None
    bf: float | None = None
    tf: float | None = None
    tw: float | None = None
    t3: float | None = None
    t2: float | None = None
    D: float | None = None
    t: float | None = None


class PerfilBody(BaseModel):
    nombre: str
    tipo: str
    dimensiones_mm: DimensionesMm

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, value: str) -> str:
        return _valid_name(value, "perfil.nombre", allow_star=True)

    @field_validator("tipo")
    @classmethod
    def tipo_soportado(cls, value: str) -> str:
        if value in TIPO_A_SAP:
            return value
        if value.strip().lower() in TIPOS_TEE:
            raise ValueError(
                "Las tees no se pueden enviar con esta acción. Tipos válidos: "
                "I, canal, angulo, tubo_rect, tubo_circ, pipe."
            )
        raise ValueError(
            f"tipo {value!r} no soportado. Tipos válidos: I, canal, angulo, tubo_rect, tubo_circ, pipe."
        )


class EnviarBody(BaseModel):
    perfil: PerfilBody
    material: str
    barras: list[str] | Literal["selected"]
    sobrescribir: bool = False
    desbloquear: bool = False

    @field_validator("material")
    @classmethod
    def material_valido(cls, value: str) -> str:
        return _valid_name(value, "material")

    @field_validator("barras")
    @classmethod
    def barras_acotadas(cls, value: list[str] | str) -> list[str] | str:
        if isinstance(value, str):
            return value
        if not value or len(value) > MAX_BARRAS:
            raise ValueError(f"barras debe contener entre 1 y {MAX_BARRAS} nombres, o 'selected'")
        return list(dict.fromkeys(_valid_name(barra, "barra") for barra in value))


class RevertirBody(BaseModel):
    revertir: dict[str, str]
    desbloquear: bool = False

    @field_validator("revertir")
    @classmethod
    def revertir_acotado(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or len(value) > MAX_BARRAS:
            raise ValueError(f"revertir debe contener entre 1 y {MAX_BARRAS} barras")
        return {
            _valid_name(barra, "barra"): _valid_name(seccion, "sección previa", allow_star=True)
            for barra, seccion in value.items()
        }


# ---------------------------------------------------------------------------
# Geometría por tipo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Geometria:
    """Argumentos de ``create_section``: ``depth``/``width``/``tw``/``tf`` en mm."""

    tipo_sap: str
    depth: float
    width: float
    tf: float
    tw: float


def _geometria(perfil: PerfilBody) -> Geometria:
    tipo = perfil.tipo
    dims = perfil.dimensiones_mm.model_dump(exclude_none=True)
    if tipo in TIPOS_TUBO:
        permitidas = {"D", "t"}
    else:
        permitidas = {"d", "bf", "tf", "tw"} | ({"t3", "t2"} if tipo == "tubo_rect" else set())
    no_aplicables = sorted(set(dims) - permitidas)
    if no_aplicables:
        raise ActionValidationError(f"Dimensiones no aplicables al tipo {tipo}: {', '.join(no_aplicables)}.")

    def valor(principal: str, alias: str | None = None) -> float | None:
        a = dims.get(principal)
        b = dims.get(alias) if alias else None
        if a is not None and b is not None and a != b:
            raise ActionValidationError(f"{principal} y {alias} son alias del mismo valor y no coinciden.")
        return a if a is not None else b

    if tipo in TIPOS_TUBO:
        diametro, espesor = valor("D"), valor("t")
        requeridas = {"D": diametro, "t": espesor}
        geo = Geometria(TIPO_A_SAP[tipo], diametro or 0.0, diametro or 0.0, espesor or 0.0, espesor or 0.0)
    else:
        alias_d, alias_bf = ("t3", "t2") if tipo == "tubo_rect" else (None, None)
        profundidad, ancho = valor("d", alias_d), valor("bf", alias_bf)
        requeridas = {"d": profundidad, "bf": ancho, "tf": dims.get("tf"), "tw": dims.get("tw")}
        geo = Geometria(
            TIPO_A_SAP[tipo],
            profundidad or 0.0,
            ancho or 0.0,
            dims.get("tf") or 0.0,
            dims.get("tw") or 0.0,
        )
    faltan = [nombre for nombre, v in requeridas.items() if v is None]
    if faltan:
        raise ActionValidationError(f"Faltan dimensiones para el tipo {tipo}: {', '.join(faltan)}.")
    if any(not math.isfinite(v) or v <= 0 for v in requeridas.values() if v is not None):
        raise ActionValidationError("Las dimensiones deben ser finitas y mayores que cero (mm).")
    return geo


# ---------------------------------------------------------------------------
# Lecturas y planificación (sin escribir)
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    nombre: str
    geo: Geometria
    material: str
    materiales: list[str]
    existe_material: bool | None
    existe_seccion: bool
    bloqueado: bool
    barras: list[tuple[str, str | None]]
    inexistentes: list[str]
    avisos: list[str] = field(default_factory=list)


def _unidades(model: Any) -> int:
    try:
        return int(_valor_unico(model.GetPresentUnits(), "GetPresentUnits"))
    except Exception as exc:
        raise SapAceroEnviarError(f"No se pudieron leer las unidades de SAP2000: {exc}") from exc


def _bloqueado(model: Any) -> bool:
    """``GetModelIsLocked`` como escalar o como tupla con el código de retorno. Si falla: no bloqueado
    (criterio de ``bridge.actions.model_is_locked``)."""
    try:
        return bool(_valor_unico(model.GetModelIsLocked(), "GetModelIsLocked"))
    except Exception:
        return False


def _dimensiones_i_mm(model: Any, nombre: str) -> dict[str, float] | None:
    """Geometría de una sección I con ``PropFrame.GetISection`` (unidades presentes: mm en apply).

    Forma real (pywin32, SAP2000 23.3.1), código al inicio:
    ``[0, FileName, MatProp, t3, t2, tf, tw, t2b, tfb, Color, Notes, GUID]``.
    Devuelve ``None`` si la lectura falla o no trae los datos.
    """
    try:
        salidas = _salidas(model.PropFrame.GetISection(nombre), "PropFrame.GetISection")
        t3, t2, tf, tw = (float(v) for v in salidas[2:6])
    except Exception:
        return None
    return {"d": t3, "bf": t2, "tf": tf, "tw": tw}


def _secciones(model: Any) -> set[str]:
    return set(_get_name_list(model.PropFrame, "PropFrame.GetNameList"))


def _materiales(model: Any, avisos: list[str]) -> list[str]:
    try:
        return _get_name_list(model.PropMaterial, "PropMaterial.GetNameList", 0, [])
    except Exception:
        avisos.append("No se pudieron leer los materiales del modelo: el material se escribe sin comprobar.")
        return []


def _resolver_barras(model: Any, barras: list[str] | str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Barras con su sección actual y las pedidas que no existen en el modelo."""
    if barras == "selected":
        seleccion = selected_frames(model)
        if not seleccion:
            raise SapAceroEnviarError("No hay barras seleccionadas en SAP2000.")
        resueltas = {item["frame"]: item["section"] or None for item in seleccion}
        if len(resueltas) > MAX_BARRAS:
            raise ActionValidationError(
                f"La selección tiene {len(resueltas)} barras; el máximo es {MAX_BARRAS}."
            )
        return list(resueltas.items()), []
    existentes = set(_nombres_barras(model))
    inexistentes = [barra for barra in barras if barra not in existentes]
    presentes = [barra for barra in barras if barra in existentes]
    if not presentes:
        raise SapAceroEnviarError("Ninguna de las barras pedidas existe en el modelo.")
    return [(barra, _seccion(model, barra)) for barra in presentes], inexistentes


def _planificar(model: Any, body: EnviarBody) -> _Plan:
    geo = _geometria(body.perfil)
    nombre = body.perfil.nombre
    avisos: list[str] = []
    materiales = _materiales(model, avisos)
    existe_material = (body.material in materiales) if materiales else None
    existe_seccion = nombre in _secciones(model)
    bloqueado = _bloqueado(model)
    barras, inexistentes = _resolver_barras(model, body.barras)
    plan = _Plan(
        nombre=nombre,
        geo=geo,
        material=body.material,
        materiales=materiales,
        existe_material=existe_material,
        existe_seccion=existe_seccion,
        bloqueado=bloqueado,
        barras=barras,
        inexistentes=inexistentes,
        avisos=avisos,
    )
    if inexistentes:
        avisos.append("Barras que no existen en el modelo (se omiten): " + ", ".join(inexistentes) + ".")
    if existe_seccion:
        if body.sobrescribir:
            avisos.append(f"La sección {nombre} ya existe y se sobrescribirá (sobrescribir=true).")
            pedidas = {barra for barra, _ in barras}
            otras = [b for b in _nombres_barras(model) if b not in pedidas and _seccion(model, b) == nombre]
            if otras:
                avisos.append(
                    f"Sobrescribir {nombre} cambia también {len(otras)} barra(s) que no están en la "
                    "lista y usan esa sección."
                )
        else:
            avisos.append(
                f"La sección {nombre} ya existe. Con sobrescribir=false el apply responde 409 y no escribe nada."
            )
    if existe_material is False:
        avisos.append(f"El material '{body.material}' no existe en el modelo de SAP2000.")
    if bloqueado:
        avisos.append(
            "El modelo está bloqueado (tiene resultados). El apply responde 409 salvo con desbloquear=true, "
            "que borra los resultados del análisis."
        )
    sin_leer = [barra for barra, actual in barras if actual is None]
    if sin_leer:
        avisos.append("No se pudo leer la sección actual de: " + ", ".join(sin_leer) + ".")
    return plan


def _seccion_accion(plan: _Plan, sobrescribir: bool) -> str:
    if not plan.existe_seccion:
        return "crear"
    return "sobrescribir" if sobrescribir else "existe_sin_sobrescribir"


def preview_envio(model: Any, body: EnviarBody) -> dict[str, Any]:
    """Vista previa: solo lecturas COM. No llama a ningún método de escritura."""
    plan = _planificar(model, body)
    cambian = [barra for barra, actual in plan.barras if actual != plan.nombre]
    return {
        "preview": True,
        "will_mutate": False,
        **validacion("preview"),
        "seccion": {
            "nombre": plan.nombre,
            "tipo_sap": plan.geo.tipo_sap,
            "accion": _seccion_accion(plan, body.sobrescribir),
        },
        "material": {"nombre": body.material, "existe": plan.existe_material},
        "barras": [
            {"frame": barra, "seccion_actual": actual, "cambia": actual != plan.nombre}
            for barra, actual in plan.barras
        ],
        "cambios": len(cambian),
        "sin_cambio": len(plan.barras) - len(cambian),
        "modelo_bloqueado": plan.bloqueado,
        "warnings": plan.avisos,
    }


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def _preparar_bloqueo(model: Any, bloqueado: bool, desbloquear: bool) -> bool:
    """Desbloquea el modelo si hace falta. Devuelve ``True`` si lo desbloqueó."""
    if not bloqueado:
        return False
    if not desbloquear:
        raise SapAceroEnviarError(
            "El modelo está bloqueado (tiene resultados de análisis). Con desbloquear=true se desbloquea "
            "antes de escribir, lo que BORRA los resultados del análisis. No se escribió nada.",
            code="modelo_bloqueado",
        )
    try:
        _ret(model.SetModelIsLocked(False), "SetModelIsLocked(False)")
    except Exception as exc:
        raise SapAceroEnviarError(f"No se pudo desbloquear el modelo: {exc}. No se escribió nada.") from exc
    return True


def _verificar(model: Any, esperados: dict[str, str]) -> list[dict[str, Any]]:
    """Relee ``FrameObj.GetSection`` de cada barra y compara con la sección esperada."""
    resultado = []
    for barra, esperado in esperados.items():
        leido = _seccion(model, barra)
        resultado.append({"barra": barra, "esperado": esperado, "leido": leido, "ok": leido == esperado})
    return resultado


def _aviso_geometria(geometria: dict[str, Any]) -> str:
    leida = geometria["leida_mm"]
    if leida is None:
        return f"No se pudo releer la geometría de la sección {geometria['seccion']} con GetISection."
    return (
        f"La geometría de la sección {geometria['seccion']} no coincide con lo enviado: "
        f"enviada {geometria['esperada_mm']}, leída {leida} (mm)."
    )


def _verificar_geometria_i(model: Any, plan: _Plan) -> dict[str, Any]:
    """Compara la geometría leída de la sección I con la enviada (mm). ``ok`` es ``False`` si no se pudo leer."""
    esperada = {"d": plan.geo.depth, "bf": plan.geo.width, "tf": plan.geo.tf, "tw": plan.geo.tw}
    leida = _dimensiones_i_mm(model, plan.nombre)
    if leida is None:
        return {"seccion": plan.nombre, "esperada_mm": esperada, "leida_mm": None, "ok": False}
    ok = all(math.isclose(leida[k], esperada[k], rel_tol=1e-6, abs_tol=1e-6) for k in esperada)
    return {"seccion": plan.nombre, "esperada_mm": esperada, "leida_mm": leida, "ok": ok}


def _asignar(
    model: Any,
    operaciones: list[tuple[str, str, str | None]],
    seccion: dict[str, Any] | None,
    desbloqueado: bool,
    avisos: list[str],
    aplicadas: list[dict[str, Any]],
    validado: dict[str, Any],
) -> None:
    """``SetSection`` barra a barra. Ante un fallo para: ``PartialApplyError`` con lo ya aplicado.

    ``validado`` son los campos de ``validacion`` de la operación que llama (apply o revertir).
    """
    for barra, nueva, previa in operaciones:
        try:
            _ret(model.FrameObj.SetSection(barra, nueva), f"FrameObj.SetSection({barra!r})")
        except Exception as exc:
            avisos.append(ADVERTENCIA_PARCIAL)
            raise PartialApplyError(
                {
                    "code": "partial",
                    "partial": True,
                    "error": (
                        f"Fallo al asignar la sección a la barra {barra}: {exc}. "
                        "No se continuó con las demás barras."
                    ),
                    "fallida": barra,
                    "seccion": seccion,
                    "aplicadas": list(aplicadas),
                    "revertir": {a["frame"]: a["seccion_previa"] for a in aplicadas},
                    "modelo_desbloqueado": desbloqueado,
                    "resultados_invalidados": desbloqueado,
                    **validado,
                    # Misma lista: la restauración de unidades del finally se añade aquí.
                    "warnings": avisos,
                }
            ) from exc
        aplicadas.append({"frame": barra, "seccion_previa": previa})


def apply_envio(model: Any, body: EnviarBody) -> dict[str, Any]:
    plan = _planificar(model, body)
    if plan.existe_material is False:
        raise ActionValidationError(
            f"El material '{body.material}' no existe en el modelo de SAP2000. "
            f"Materiales disponibles: {', '.join(plan.materiales)}"
        )
    if plan.existe_seccion and not body.sobrescribir:
        raise SectionExistsError(plan.nombre)
    sin_leer = [barra for barra, actual in plan.barras if actual is None]
    if sin_leer:
        raise SapAceroEnviarError(
            f"No se pudo leer la sección actual de: {', '.join(sin_leer)}. No se escribió nada."
        )

    previas = _unidades(model)
    desbloqueado = _preparar_bloqueo(model, plan.bloqueado, body.desbloquear)
    avisos = list(plan.avisos)
    if desbloqueado:
        avisos.append(ADVERTENCIA_DESBLOQUEO)
    es_i = plan.geo.tipo_sap == "I"
    if es_i:
        avisos.append(ADVERTENCIA_GEOMETRIA_I)
    elif plan.geo.tipo_sap in TIPOS_VALIDADOS_POSICIONAL:
        avisos.append(ADVERTENCIA_GEOMETRIA_SONDA)
    else:
        avisos.append(ADVERTENCIA_GEOMETRIA)
    if plan.existe_seccion:
        avisos.append(ADVERTENCIA_REVERTIR)
    seccion = {
        "nombre": plan.nombre,
        "tipo_sap": plan.geo.tipo_sap,
        "accion": "sobrescrita" if plan.existe_seccion else "creada",
    }
    cambia_unidades = previas != EUNITS_N_MM_C
    cambios = [(barra, plan.nombre, actual) for barra, actual in plan.barras if actual != plan.nombre]
    aplicadas: list[dict[str, Any]] = []
    try:
        if cambia_unidades:
            _ret(model.SetPresentUnits(EUNITS_N_MM_C), "SetPresentUnits(N_mm_C)")
        # create_section ya comprueba material, existencia y unidades (vía N_mm_C) y avisa si ret != 0.
        creada = create_section(
            model,
            plan.nombre,
            plan.geo.tipo_sap,
            body.material,
            plan.geo.depth,
            plan.geo.width,
            plan.geo.tw,
            plan.geo.tf,
            overwrite=body.sobrescribir,
        )
        if creada["ret"] != 0:
            avisos.append(creada["message"])
        if plan.nombre not in _secciones(model):
            raise SapAceroEnviarError(f"La sección {plan.nombre} no aparece en el modelo tras enviarla.")
        _asignar(
            model,
            cambios,
            seccion,
            desbloqueado,
            avisos,
            aplicadas,
            validacion("apply", plan.geo.tipo_sap),
        )
        verificacion = _verificar(model, {barra: plan.nombre for barra, _ in plan.barras})
        geometria = _verificar_geometria_i(model, plan) if es_i else None
    finally:
        if cambia_unidades:
            aviso = _restaurar_unidades(model, previas)
            if aviso:
                avisos.append(aviso)

    ok = all(item["ok"] for item in verificacion)
    if not ok:
        malas = [item["barra"] for item in verificacion if not item["ok"]]
        avisos.append(
            "La relectura no coincide con lo escrito en: " + ", ".join(malas) + ". Revise verificacion."
        )
    if geometria is not None and not geometria["ok"]:
        ok = False
        avisos.append(_aviso_geometria(geometria))
    return {
        "ok": ok,
        "partial": False,
        **validacion("apply", plan.geo.tipo_sap),
        "seccion": seccion,
        "modelo_desbloqueado": desbloqueado,
        "resultados_invalidados": desbloqueado,
        "cambios": len(aplicadas),
        "sin_cambio": len(plan.barras) - len(cambios),
        "aplicadas": aplicadas,
        "revertir": {a["frame"]: a["seccion_previa"] for a in aplicadas},
        "verificacion": verificacion,
        "warnings": avisos,
    }


def revertir_envio(model: Any, body: RevertirBody) -> dict[str, Any]:
    """Reasigna a cada barra la sección indicada (normalmente la previa de ``apply``)."""
    avisos: list[str] = []
    secciones = _secciones(model)
    faltan = sorted({seccion for seccion in body.revertir.values() if seccion not in secciones})
    if faltan:
        raise ActionValidationError(
            f"Secciones previas que ya no existen en el modelo: {', '.join(faltan)}. No se revirtió nada."
        )
    existentes = set(_nombres_barras(model))
    inexistentes = [barra for barra in body.revertir if barra not in existentes]
    if inexistentes:
        avisos.append("Barras que no existen en el modelo (se omiten): " + ", ".join(inexistentes) + ".")
    pares = [(barra, seccion) for barra, seccion in body.revertir.items() if barra in existentes]
    if not pares:
        raise SapAceroEnviarError("Ninguna de las barras a revertir existe en el modelo.")
    actuales = {barra: _seccion(model, barra) for barra, _ in pares}
    desbloqueado = _preparar_bloqueo(model, _bloqueado(model), body.desbloquear)
    if desbloqueado:
        avisos.append(ADVERTENCIA_DESBLOQUEO)
    aplicadas: list[dict[str, Any]] = []
    _asignar(
        model,
        [(barra, seccion, actuales[barra]) for barra, seccion in pares],
        None,
        desbloqueado,
        avisos,
        aplicadas,
        validacion("revertir"),
    )
    verificacion = _verificar(model, dict(pares))
    ok = all(item["ok"] for item in verificacion)
    if not ok:
        malas = [item["barra"] for item in verificacion if not item["ok"]]
        avisos.append("La relectura no coincide en: " + ", ".join(malas) + ". Revise verificacion.")
    return {
        "ok": ok,
        "partial": False,
        **validacion("revertir"),
        "modelo_desbloqueado": desbloqueado,
        "resultados_invalidados": desbloqueado,
        "aplicadas": aplicadas,
        "revertir": {a["frame"]: a["seccion_previa"] for a in aplicadas},
        "verificacion": verificacion,
        "warnings": avisos,
    }


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/acero-aisc360/enviar/*`` con el envoltorio ``call`` de ``bridge.actions``."""

    @app.post(f"{PREFIJO}/preview")
    def acero_enviar_preview(body: EnviarBody):
        return call(lambda model: preview_envio(model, body))

    @app.post(f"{PREFIJO}/apply")
    def acero_enviar_apply(body: EnviarBody):
        return call(lambda model: apply_envio(model, body))

    @app.post(f"{PREFIJO}/revertir")
    def acero_enviar_revertir(body: RevertirBody):
        return call(lambda model: revertir_envio(model, body))
