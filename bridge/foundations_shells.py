"""Acciones SAP2000 de "Diagramas de shells (Beta)" (Fundaciones).

Puerto del escritorio (``app/gui/panel_diagramas_shell.py``,
``app/sap2000/diagramas_shell.py``, ``app/sap2000/pedestales_automaticos.py``,
``app/sap2000/estado_modelo.py``) al patrón de este repositorio: sin conexión
COM propia, cada operación recibe el ``SapModel`` ya conectado a través del
actor STA de la sesión y las rutas se instalan desde
``bridge.actions.install_routes`` con el mismo envoltorio ``call`` (igual que
``bridge/esbelteces.py`` y ``bridge/espectro.py``).

Política de estrictez idéntica a la del escritorio (tolerante): cada llamada
COM cuya firma no está verificada contra una instancia real de SAP2000 se
prueba con ``try/except`` y varias firmas, y un fallo puntual no aborta el
resto. Rutas (contrato exacto en ``docs/SAP_ACTIONS.md``, sección "Diagramas
de shells (port del escritorio)"):

* ``GET  /v1/sap/actions/foundations/cases`` gana ``envolventes`` (ver
  ``envelope_combos``; la ruta vive en ``bridge/actions.py``).
* ``POST /v1/sap/actions/foundations/shell-results``
* ``POST /v1/sap/actions/foundations/shell-results-multi``
* ``POST /v1/sap/actions/foundations/pedestal-candidates``
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

from bridge.actions import (
    _MAX_ITEMS,
    _call_display_table,
    _call_get_coord_cartesian,
    _get_name_list,
    _parse_generic_table,
    _response_arrays,
    _select_case_for_output,
    _with_ton_m_c,
    model_is_locked,
    shell_geometry,
    shell_soil_pressure,
)

logger = logging.getLogger(__name__)

COMPONENTES_FORCES = ["F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23"]
COMPONENTES_STRESSES = ["S11", "S22", "S12", "SMax", "SMin", "SVM", "T13", "T23"]
COMPONENTES_SOIL_PRESSURE = ["Pressure"]

TABLA_AREA_FORCES = "Element Forces - Area Shells"
TABLA_AREA_STRESSES = "Element Stresses - Area Shells"

# Arrays de metadata que preceden a los componentes en AreaForceShell /
# AreaStressShell: Obj, Elm, PointElm, LoadCase, StepType, StepNum.
_N_ARRAYS_METADATA = 6
_INDICE_POINT_ELM = 2
_INDICE_STEP_TYPE = 4
_ORDEN_COMPONENTES_FORCES = [
    "F11", "F22", "F12", "FMax", "FMin", "FAngle", "FVM",
    "M11", "M22", "M12", "MMax", "MMin", "MAngle", "V13", "V23",
]  # fmt: skip
_ORDEN_COMPONENTES_STRESSES = [
    "S11", "S22", "S12", "SMax", "SMin", "SAngle", "SVM",
    "T13", "T23", "TMax", "TAngle",
]  # fmt: skip

_CANDIDATOS_COLUMNA_ELM = ("areaelem", "areaelm", "elm", "element")
_CANDIDATOS_COLUMNA_JOINT = ("joint", "pointelm", "point")
_CANDIDATOS_COLUMNA_STEPTYPE = ("steptype",)

# RespCombo.ComboType: 0=Linear Add, 1=Envelope, 2=Absolute Add, 3=SRSS, 4=Range Add.
_CODIGO_TIPO_ENVELOPE = 1

# Pedestales automáticos.
_EPS_VERTICAL = 1e-3
_DIMENSION_PLACEHOLDER = 0.30
MOTIVO_CIRCULAR = "Sección circular, aproximada como cuadrado"
MOTIVO_NO_RECONOCIDA = "Sección no reconocida, dimensión de relleno — revisar manualmente"

MSG_MODELO_NO_CORRIDO = (
    "El modelo de SAP2000 no está corrido: no existen resultados "
    "para ninguna combinación ni caso.\n\nCorre el análisis "
    "(Analyze > Run Analysis) y vuelve a intentar."
)
MSG_CORRIDO_SIN_RESULTADOS = (
    "El modelo está corrido, pero no hay resultados para las "
    "combinaciones/casos elegidos.\n\nRevisa que estén incluidas en el "
    "análisis ya corrido, o que el caso no lineal haya convergido."
)

_MAX_JOINTS = 200_000
_MAX_FRANJAS = 200


# ---------------------------------------------------------------------------
# estado_modelo.mensaje_sin_resultados
# ---------------------------------------------------------------------------


def mensaje_sin_resultados(model: Any) -> str:
    """Texto literal del escritorio: distingue "modelo nunca corrido" de
    "corrido pero sin resultados para lo pedido"."""
    if not model_is_locked(model):
        return MSG_MODELO_NO_CORRIDO
    return MSG_CORRIDO_SIN_RESULTADOS


# ---------------------------------------------------------------------------
# Combos envolventes (diagramas_shell._obtener_tipo_combo / listar_combos_envolventes)
# ---------------------------------------------------------------------------


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def combo_type(model: Any, nombre: str) -> int | None:
    """``RespCombo.ComboType(nombre)`` con las 3 firmas tolerantes del
    escritorio. ``None`` si ninguna devolvió un tipo utilizable (nunca lanza)."""
    errores: list[str] = []

    try:
        ret = model.RespCombo.ComboType(nombre)
        for valor in ret if isinstance(ret, (list, tuple)) else (ret,):
            if _numeric(valor):
                return int(valor)
    except Exception as exc:
        errores.append(f"ComboType(nombre): {type(exc).__name__}: {exc}")

    try:
        ret = model.RespCombo.ComboType(nombre, 0)
        if isinstance(ret, (list, tuple)):
            for valor in ret:
                if _numeric(valor):
                    return int(valor)
    except Exception as exc:
        errores.append(f"ComboType(nombre, 0): {type(exc).__name__}: {exc}")

    try:
        # -1 como centinela: no es un código válido (0-4). El placeholder
        # tiene prioridad sobre ``ret`` (que puede ser solo el código de éxito).
        placeholder = [-1]
        ret = model.RespCombo.ComboType(nombre, placeholder)
        if placeholder[0] != -1:
            return int(placeholder[0])
        for valor in ret if isinstance(ret, (list, tuple)) else (ret,):
            if _numeric(valor):
                return int(valor)
    except Exception as exc:
        errores.append(f"ComboType(nombre, [placeholder]): {type(exc).__name__}: {exc}")

    if errores:
        logger.debug(
            "combo_type: ninguna firma probada devolvió un tipo utilizable para %r — %s",
            nombre,
            " | ".join(errores),
        )
    return None


def envelope_combos(model: Any, nombres_combo: list[str]) -> list[str]:
    """Subconjunto de ``nombres_combo`` (combinaciones, NO load cases) cuyo
    tipo es Envelope, en el mismo orden. Best-effort: un nombre cuyo tipo no
    se pudo determinar se trata como no-envolvente, sin lanzar."""
    envolventes: list[str] = []
    for nombre in nombres_combo:
        try:
            tipo = combo_type(model, nombre)
        except Exception:
            tipo = None
        if tipo == _CODIGO_TIPO_ENVELOPE:
            envolventes.append(nombre)
    return envolventes


# ---------------------------------------------------------------------------
# Resultados de shells (diagramas_shell.obtener_resultados_shell[_multi])
# ---------------------------------------------------------------------------


def _call_area_force_or_stress_shell(model: Any, elm: str, es_stress: bool) -> Any:
    metodo = model.Results.AreaStressShell if es_stress else model.Results.AreaForceShell
    try:
        return metodo(elm, 1)
    except Exception:
        empty: list = []
        args_api = [empty] * 23
        return metodo(elm, 1, 0, empty, empty, empty, empty, empty, empty, *args_api)


def _call_area_table(model: Any, es_stress: bool) -> Any:
    """Tabla completa de fuerzas/tensiones de área en una sola llamada COM
    (vía rápida). Nombre de tabla y columnas NO verificados contra SAP2000
    real: si falla o no trae columnas, el llamador cae al loop por elemento."""
    return _call_display_table(model, TABLA_AREA_STRESSES if es_stress else TABLA_AREA_FORCES)


def _extract_from_table(
    columnas: list[str],
    datos: list[str],
    elementos: dict[str, list[str]],
    componente: str,
    paso: str | None = None,
) -> dict[str, dict[str, float]]:
    """``{elemento: {punto: valor}}`` desde la tabla plana, restringido a
    ``elementos``. ``{}`` (sin lanzar) si faltan columnas — el llamador cae al
    loop por elemento. ``paso`` filtra por ``StepType`` (case-insensitive); si
    se pide y la tabla no tiene esa columna, ``{}``."""
    if not columnas or not datos:
        return {}
    lower = {c.lower(): i for i, c in enumerate(columnas)}
    idx_elm = next((lower[c] for c in _CANDIDATOS_COLUMNA_ELM if c in lower), None)
    idx_joint = next((lower[c] for c in _CANDIDATOS_COLUMNA_JOINT if c in lower), None)
    idx_comp = lower.get(componente.lower())
    if idx_elm is None or idx_joint is None or idx_comp is None:
        return {}
    idx_step = next((lower[c] for c in _CANDIDATOS_COLUMNA_STEPTYPE if c in lower), None)
    if paso is not None and idx_step is None:
        return {}

    n_cols = len(columnas)
    pedidos = set(elementos)
    resultados: dict[str, dict[str, float]] = {}
    for f in range(len(datos) // n_cols):
        fila = datos[f * n_cols : f * n_cols + n_cols]
        elm = str(fila[idx_elm])
        if elm not in pedidos:
            continue
        if paso is not None and idx_step is not None and str(fila[idx_step]).strip().lower() != paso.lower():
            continue
        try:
            valor = float(fila[idx_comp])
        except (TypeError, ValueError):
            continue
        if math.isnan(valor):
            continue
        resultados.setdefault(elm, {})[str(fila[idx_joint])] = valor
    return resultados


def _results_per_element(
    model: Any,
    elementos: dict[str, list[str]],
    es_stress: bool,
    componente: str,
    paso: str | None = None,
) -> dict[str, dict[str, float]]:
    """Respaldo: una llamada COM por elemento (Results.AreaForceShell /
    AreaStressShell). ``paso`` filtra por el array StepType (índice 4)."""
    orden = _ORDEN_COMPONENTES_STRESSES if es_stress else _ORDEN_COMPONENTES_FORCES
    try:
        offset = _N_ARRAYS_METADATA + orden.index(componente)
    except ValueError:
        offset = _N_ARRAYS_METADATA  # componente desconocido: primero de la lista

    resultados: dict[str, dict[str, float]] = {}
    for elm in elementos:
        try:
            raw = _call_area_force_or_stress_shell(model, elm, es_stress)
        except Exception as exc:
            logger.warning(
                "shell results: falló la llamada COM para el elemento %r (¿modelo no corrido?): %s",
                elm,
                exc,
            )
            continue
        arrays = _response_arrays(raw)
        if len(arrays) <= offset:
            logger.warning(
                "shell results: respuesta corta para el elemento %r (%d arreglo(s), se esperaban al menos %d)",
                elm,
                len(arrays),
                offset + 1,
            )
            continue
        pt_elm = arrays[_INDICE_POINT_ELM]
        valores = arrays[offset]
        if not pt_elm or not valores:
            continue
        step_types = arrays[_INDICE_STEP_TYPE] if len(arrays) > _INDICE_STEP_TYPE else []
        try:
            valores_elm: dict[str, float] = {}
            for i in range(min(len(pt_elm), len(valores))):
                if paso is not None:
                    step_type = str(step_types[i]).strip().lower() if i < len(step_types) else ""
                    if step_type != paso.lower():
                        continue
                valores_elm[str(pt_elm[i])] = float(valores[i])
        except (TypeError, ValueError) as exc:
            logger.warning("shell results: no se pudo parsear el elemento %r: %s", elm, exc)
            continue
        if valores_elm:
            resultados[elm] = valores_elm
    return resultados


def read_shell_results(
    model: Any,
    elementos: dict[str, list[str]],
    tipo: str,
    componentes: list[str],
    elementos_por_area: dict[str, list[str]] | None,
    paso: str | None,
) -> dict[str, dict[str, dict[str, float]]]:
    """``{componente: {elemento: {punto: valor}}}`` del caso/combo ya
    seleccionado para output. Port de ``obtener_resultados_shell`` /
    ``obtener_resultados_shell_multi``: la tabla se pide UNA vez y se extraen
    todos los ``componentes`` en memoria; si un componente no matchea por
    tabla cae al loop por elemento solo para ese. ``soil_pressure`` ignora
    ``paso`` (la tabla nativa no expone StepType) y NO exige StepType/StepNum."""
    if tipo == "soil_pressure":
        # shell_soil_pressure fuerza/restaura Ton_m_C por sí mismo.
        return {c: shell_soil_pressure(model, elementos, elementos_por_area) for c in componentes}

    es_stress = tipo == "stresses"

    def run() -> dict[str, dict[str, dict[str, float]]]:
        columnas: list[str] = []
        datos: list[str] = []
        try:
            columnas, datos = _parse_generic_table(_call_area_table(model, es_stress))
        except Exception:
            logger.debug(
                "shell results: la vía de tabla por lote falló; se cae al loop por elemento.", exc_info=True
            )
        salida: dict[str, dict[str, dict[str, float]]] = {}
        for componente in componentes:
            res = _extract_from_table(columnas, datos, elementos, componente, paso=paso)
            if not res:
                res = _results_per_element(model, elementos, es_stress, componente, paso=paso)
            salida[componente] = res
        return salida

    return _with_ton_m_c(model, run)


# ---------------------------------------------------------------------------
# Franjas: estaciones y extremo gobernante (panel_diagramas_shell.py l.143-320)
# ---------------------------------------------------------------------------


def _py_round(x: float) -> int:
    return int(round(x))


def _centroide(pts: list[str], joints: dict[str, list[float]]) -> tuple[float, float] | None:
    xs = [joints[p][0] for p in pts if p in joints]
    ys = [joints[p][1] for p in pts if p in joints]
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _tamano_caracteristico(pts: list[str], joints: dict[str, list[float]]) -> float:
    xs = [joints[p][0] for p in pts if p in joints]
    ys = [joints[p][1] for p in pts if p in joints]
    if not xs:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def tamano_caracteristico_mediano(elementos: dict[str, list[str]], joints: dict[str, list[float]]) -> float:
    tamanos = sorted(t for pts in elementos.values() if (t := _tamano_caracteristico(pts, joints)) > 0)
    return tamanos[len(tamanos) // 2] if tamanos else 0.0


def _punto_en_franja(franja: dict[str, Any], x: float, y: float) -> bool:
    ancho_x = franja["largo"] if franja["largo_en_x"] else franja["ancho"]
    ancho_y = franja["ancho"] if franja["largo_en_x"] else franja["largo"]
    return abs(x - franja["centro_x"]) <= ancho_x / 2 and abs(y - franja["centro_y"]) <= ancho_y / 2


def _extremos_franja(franja: dict[str, Any]) -> tuple[float, float, float, float]:
    cx, cy, largo = franja["centro_x"], franja["centro_y"], franja["largo"]
    if franja["largo_en_x"]:
        return cx - largo / 2, cy, cx + largo / 2, cy
    return cx, cy - largo / 2, cx, cy + largo / 2


def calcular_estaciones_franja(
    elementos: dict[str, list[str]],
    joints: dict[str, list[float]],
    resultados: dict[str, dict[str, float]],
    franja: dict[str, Any],
) -> list[tuple[float, float]]:
    """Port literal de ``_calcular_estaciones_franja``: una estación
    ``(posición, valor)`` por posición ocupada dentro de la banda."""
    x1, y1, _x2, _y2 = _extremos_franja(franja)
    crudas: list[tuple[float, float]] = []
    for elm, pts in elementos.items():
        centroide = _centroide(pts, joints)
        if centroide is None or not _punto_en_franja(franja, *centroide):
            continue
        valores_elm = resultados.get(elm, {})
        if not valores_elm:
            continue
        valor = sum(valores_elm.values()) / len(valores_elm)
        cx, cy = centroide
        s = (cx - x1) if franja["largo_en_x"] else (cy - y1)
        crudas.append((s, valor))
    if not crudas:
        return []

    ancho_bucket = tamano_caracteristico_mediano(elementos, joints)
    if ancho_bucket <= 0:
        crudas.sort(key=lambda t: t[0])
        return crudas

    grupos: dict[int, list[tuple[float, float]]] = {}
    for s, valor in crudas:
        grupos.setdefault(_py_round(s / ancho_bucket), []).append((s, valor))
    estaciones = [(sum(s for s, _v in m) / len(m), sum(v for _s, v in m) / len(m)) for m in grupos.values()]
    estaciones.sort(key=lambda t: t[0])
    return estaciones


def extremo_gobernante(estaciones: list[tuple[float, float]]) -> float | None:
    """``abs(max) >= abs(min) ? max : min`` sobre los valores de las
    estaciones; ``None`` si no hay estaciones."""
    if not estaciones:
        return None
    val_max = max(v for _s, v in estaciones)
    val_min = min(v for _s, v in estaciones)
    return val_max if abs(val_max) >= abs(val_min) else val_min


# ---------------------------------------------------------------------------
# Pedestales automáticos (pedestales_automaticos.py)
# ---------------------------------------------------------------------------


def _frame_names(model: Any) -> list[str]:
    try:
        return _get_name_list(model.FrameObj, "FrameObj.GetNameList")
    except Exception:
        return []


def _frame_points(model: Any, frame: str) -> tuple[str, str]:
    raw = model.FrameObj.GetPoints(frame)
    arrays = _response_arrays(raw)
    if arrays:
        punto_i = str(arrays[0][0]) if arrays[0] else None
        punto_j = str(arrays[1][0]) if len(arrays) > 1 and arrays[1] else None
        if punto_i and punto_j:
            return punto_i, punto_j
    if isinstance(raw, (list, tuple)):
        candidatos = [str(x) for x in raw if isinstance(x, str)]
        if len(candidatos) >= 2:
            return candidatos[0], candidatos[1]
    raise RuntimeError(f"No se pudo interpretar FrameObj.GetPoints({frame!r})")


def _frame_section_name(model: Any, frame: str) -> str:
    ret = model.FrameObj.GetSection(frame)
    if isinstance(ret, (list, tuple)):
        nombres = [x for x in ret if isinstance(x, str)]
        if nombres:
            return nombres[0]
    raise RuntimeError(f"No se pudo interpretar FrameObj.GetSection({frame!r})")


def _dims_rectangle(model: Any, seccion: str) -> tuple[float, float]:
    # GetRectangle(Name, ref FileName, ref MatProp, ref T3, ref T2, ref Color,
    # ref Notes, ref GUID): T3/T2 son Double (float); código de retorno y Color
    # son Long (int) — filtrar por float aísla las dimensiones reales.
    ret = model.PropFrame.GetRectangle(seccion)
    dims = [v for v in ret if isinstance(v, float)] if isinstance(ret, (list, tuple)) else []
    if len(dims) >= 2:
        t3, t2 = dims[0], dims[1]
        if t3 > 0 and t2 > 0:
            return float(t3), float(t2)
    raise RuntimeError(f"PropFrame.GetRectangle({seccion!r}) no devolvió dimensiones válidas")


def _dims_circle(model: Any, seccion: str) -> tuple[float, float]:
    ret = model.PropFrame.GetCircle(seccion)
    dims = [v for v in ret if isinstance(v, float)] if isinstance(ret, (list, tuple)) else []
    if dims:
        diametro = float(dims[0])
        if diametro > 0:
            return diametro, diametro
    raise RuntimeError(f"PropFrame.GetCircle({seccion!r}) no devolvió un diámetro válido")


def _largo_en_x(model: Any, frame: str) -> bool:
    """Ángulo local del frame (``FrameObj.GetLocalAxes``): el último numérico
    de la respuesta, módulo 180; ``True`` si <45 o >=135. Sin lectura, ``True``."""
    try:
        ret = model.FrameObj.GetLocalAxes(frame)
        angulos = (
            [v for v in ret if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if isinstance(ret, (list, tuple))
            else []
        )
        if angulos:
            angulo = float(angulos[-1]) % 180.0
            return angulo < 45.0 or angulo >= 135.0
    except Exception:
        pass
    return True


def pedestal_candidates(model: Any, joints_shells: set[str]) -> list[dict[str, Any]]:
    """Frames verticales cuyo extremo inferior comparte joint con la malla de
    los shells (``joints_shells``). Port literal de
    ``detectar_pedestales_automaticos``; un frame que falle no aborta los demás."""
    candidatos: list[dict[str, Any]] = []
    for frame in _frame_names(model):
        try:
            punto_i, punto_j = _frame_points(model, frame)
            xi, yi, zi = _call_get_coord_cartesian(model, punto_i)
            xj, yj, zj = _call_get_coord_cartesian(model, punto_j)
        except Exception as exc:
            logger.debug(
                "pedestal_candidates: no se pudo leer geometría del frame %r (%s) — se omite.", frame, exc
            )
            continue

        vertical = (
            abs(xi - xj) < _EPS_VERTICAL and abs(yi - yj) < _EPS_VERTICAL and abs(zi - zj) > _EPS_VERTICAL
        )
        if not vertical:
            continue

        if zi <= zj:
            punto_pie, (x_pie, y_pie) = punto_i, (xi, yi)
        else:
            punto_pie, (x_pie, y_pie) = punto_j, (xj, yj)
        if punto_pie not in joints_shells:
            continue

        try:
            seccion = _frame_section_name(model, frame)
        except Exception as exc:
            logger.debug("pedestal_candidates: frame %r sin sección legible (%s) — se omite.", frame, exc)
            continue

        aproximado = False
        motivo = ""
        try:
            largo, ancho = _dims_rectangle(model, seccion)
        except Exception:
            try:
                largo, ancho = _dims_circle(model, seccion)
                aproximado = True
                motivo = MOTIVO_CIRCULAR
            except Exception as exc:
                largo = ancho = _DIMENSION_PLACEHOLDER
                aproximado = True
                motivo = MOTIVO_NO_RECONOCIDA
                logger.debug(
                    "pedestal_candidates: sin dimensiones de la sección %r del frame %r (%s) — placeholder.",
                    seccion,
                    frame,
                    exc,
                )

        candidatos.append(
            {
                "frame": frame,
                "largo": largo,
                "ancho": ancho,
                "largo_en_x": _largo_en_x(model, frame),
                "centro_x": x_pie,
                "centro_y": y_pie,
                "aproximado": aproximado,
                "motivo_aviso": motivo,
            }
        )
    return candidatos


# ---------------------------------------------------------------------------
# Cuerpos de petición
# ---------------------------------------------------------------------------

Tipo = Literal["forces", "stresses", "soil_pressure"]
Paso = Literal["max", "min"]


def _componentes_validos(tipo: str) -> list[str]:
    return {"forces": COMPONENTES_FORCES, "stresses": COMPONENTES_STRESSES}.get(
        tipo, COMPONENTES_SOIL_PRESSURE
    )


def _check_shells(value: list[str]) -> list[str]:
    if not value or len(value) > _MAX_ITEMS:
        raise ValueError("shells must contain 1–500 entries")
    return value


class ShellResultsBody(BaseModel):
    shells: list[str]
    combinacion: str
    tipo: Tipo
    componente: str
    paso: Paso | None = None
    sin_geometria: bool = False

    @field_validator("shells")
    @classmethod
    def shells_bounds(cls, value: list[str]) -> list[str]:
        return _check_shells(value)

    @field_validator("paso", mode="before")
    @classmethod
    def paso_lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def componente_valido(self) -> ShellResultsBody:
        if self.tipo != "soil_pressure" and self.componente not in _componentes_validos(self.tipo):
            raise ValueError(f"componente must be one of {_componentes_validos(self.tipo)}")
        return self


class ComboRef(BaseModel):
    combinacion: str
    paso: Paso | None = None

    @field_validator("paso", mode="before")
    @classmethod
    def paso_lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class FranjaBody(BaseModel):
    centro_x: float
    centro_y: float
    largo: float
    ancho: float
    largo_en_x: bool = True


class ShellResultsMultiBody(BaseModel):
    shells: list[str]
    combos: list[ComboRef]
    tipo: Tipo
    componentes: list[str] | None = None
    franjas: list[FranjaBody]

    @field_validator("shells")
    @classmethod
    def shells_bounds(cls, value: list[str]) -> list[str]:
        return _check_shells(value)

    @field_validator("combos")
    @classmethod
    def combos_bounds(cls, value: list[ComboRef]) -> list[ComboRef]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("combos must contain 1–500 entries")
        return value

    @field_validator("franjas")
    @classmethod
    def franjas_bounds(cls, value: list[FranjaBody]) -> list[FranjaBody]:
        if not value or len(value) > _MAX_FRANJAS:
            raise ValueError(f"franjas must contain 1–{_MAX_FRANJAS} entries")
        return value

    @model_validator(mode="after")
    def componentes_validos(self) -> ShellResultsMultiBody:
        if self.componentes is not None:
            validos = _componentes_validos(self.tipo)
            if not self.componentes or any(c not in validos for c in self.componentes):
                raise ValueError(f"componentes must be a non-empty subset of {validos}")
        return self


class PedestalCandidatesBody(BaseModel):
    joints: list[str]

    @field_validator("joints")
    @classmethod
    def joints_bounds(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_JOINTS:
            raise ValueError(f"joints must contain 1–{_MAX_JOINTS} entries")
        return value


# ---------------------------------------------------------------------------
# Operaciones de ruta
# ---------------------------------------------------------------------------


def shell_results(model: Any, body: ShellResultsBody) -> dict[str, Any]:
    _select_case_for_output(model, body.combinacion)
    joints_z: dict[str, float] = {}
    elementos, joints, elementos_por_area = shell_geometry(model, body.shells, joints_z)
    componente = "Pressure" if body.tipo == "soil_pressure" else body.componente
    resultados = (
        read_shell_results(model, elementos, body.tipo, [componente], elementos_por_area, body.paso)[
            componente
        ]
        if elementos
        else {}
    )
    return {
        "elementos": {} if body.sin_geometria else elementos,
        "joints": {} if body.sin_geometria else joints,
        "joints_z": {} if body.sin_geometria else joints_z,
        "resultados": resultados,
        "mensaje_sin_resultados": "" if resultados else mensaje_sin_resultados(model),
    }


def shell_results_multi(model: Any, body: ShellResultsMultiBody) -> dict[str, Any]:
    elementos, joints, elementos_por_area = shell_geometry(model, body.shells)
    componentes = body.componentes or _componentes_validos(body.tipo)
    franjas = [f.model_dump() for f in body.franjas]
    filas: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    for ref in body.combos:
        _select_case_for_output(model, ref.combinacion)
        try:
            por_componente = read_shell_results(
                model, elementos, body.tipo, componentes, elementos_por_area, ref.paso
            )
        except Exception as exc:
            logger.exception("shell-results-multi: falló al traer los componentes para %r", ref.combinacion)
            errores.append({"combinacion": ref.combinacion, "paso": ref.paso, "error": str(exc)})
            continue
        valores_por_franja: list[dict[str, float]] = [{} for _ in franjas]
        for componente, resultados in por_componente.items():
            if not resultados:
                continue
            for i, franja in enumerate(franjas):
                extremo = extremo_gobernante(
                    calcular_estaciones_franja(elementos, joints, resultados, franja)
                )
                if extremo is not None:
                    valores_por_franja[i][componente] = extremo
        for i in range(len(franjas)):
            filas.append(
                {
                    "franja": i,
                    "combinacion": ref.combinacion,
                    "paso": ref.paso,
                    "valores": valores_por_franja[i],
                }
            )
    return {"filas": filas, "errores": errores}


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/foundations/{shell-results,shell-results-multi,
    pedestal-candidates}`` con el envoltorio ``call`` de
    ``bridge.actions.install_routes`` (mismo actor STA y mapeo de errores)."""
    prefix = "/v1/sap/actions/foundations"

    @app.post(f"{prefix}/shell-results")
    def foundations_shell_results(body: ShellResultsBody):
        return call(lambda model: shell_results(model, body))

    @app.post(f"{prefix}/shell-results-multi")
    def foundations_shell_results_multi(body: ShellResultsMultiBody):
        return call(lambda model: shell_results_multi(model, body))

    @app.post(f"{prefix}/pedestal-candidates")
    def foundations_pedestal_candidates(body: PedestalCandidatesBody):
        joints = set(body.joints)
        return call(lambda model: {"candidatos": pedestal_candidates(model, joints)})
