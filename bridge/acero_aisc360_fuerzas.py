"""SAP2000 acciones para Acero AISC 360-22: lectura de fuerzas de barras y su
conversión a demandas de diseño.

Lee ``Results.FrameForce`` de las barras pedidas (o de la selección de SAP2000)
para las combinaciones indicadas y calcula, por barra y combinación:
``Pu``, ``Tu``, ``Vux``/``Vuy``, ``Mux``/``Muy``, ``T`` y ``Cb`` (AISC 360-22
Ec. F1-1). La ``envolvente`` toma cada componente como el máximo de las
combinaciones (conservadora: cada componente puede venir de una combinación
distinta) e indica en ``gobierna`` qué combinación da cada valor.

Mismo patrón que ``bridge/hormigon.py`` y ``bridge/diagrama.py``: sin conexión
COM propia; cada operación recibe el ``SapModel`` del actor STA de la sesión y
las rutas se instalan desde ``bridge.actions.install_routes`` con el envoltorio
``call`` y la autenticación por token del agente.

Convenciones:

* Unidades: se fuerzan a kN-m (``SetPresentUnits(6)``) durante la lectura y se
  restauran siempre al terminar. Si la restauración falla, se avisa en
  ``warnings``.
* P de tracción positiva (convención SAP2000). ``Pu`` es la compresión máxima y
  ``Tu`` la tracción máxima.
* Ejes locales 1-2-3. Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14): el eje fuerte es
  el 3-3 (``EJE_FUERTE = "M3"``, con V2 acompañando), así que ``Mux`` es max|M3| y ``Vux`` es
  max|V2|. El eje débil es el 2-2 (M2 con V3). Cambiar ``EJE_FUERTE`` a ``"M2"`` invierte la
  asignación.
* ``Lb`` no sale de SAP2000 y no se calcula aquí.
* ``Cb`` usa |M| del eje fuerte interpolado en L/4, L/2 y 3L/4 y el máximo del
  tramo. El momento en los extremos no entra en la ecuación F1-1 y no se usa.
  Entre estaciones la interpolación es lineal sobre el momento con signo y se avisa.
  Si el Cb se interpola y la separación máxima entre estaciones de un paso supera
  ``FRACCION_MAX_SEPARACION_CB`` · L, el Cb de ese paso pasa a 1,0 (conservador) y se avisa.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from bridge.actions import (
    ActionValidationError,
    _valid_combo_name,
    _valid_name,
    selected_frames,
    split_return,
)
from bridge.com_orden import normalizar
from bridge.com_tiempo import con_limite
from bridge.viewer.sap2000.units import UNIT_CODES, length_factor_to_meters, unit_label

logger = logging.getLogger(__name__)

EUNITS_KN_M = 6  # eUnits.kN_m_C
EJE_FUERTE = "M3"  # validado con SAP2000 23.3.1 (sonda v3, 2026-10-14)
VALIDADO_CON = "SAP2000 23.3.1 (pywin32), sonda v3"
VALIDADO_FECHA = "2026-10-14"
MAX_FRAMES = 500
MAX_COMBOS = 300  # combinaciones + casos por petición; la web las pide en lotes de 50 (LOTE_COMBOS)
MAX_ESTACIONES = 50
LIMITE_COM_S = 120.0  # lectura de muchas barras x combinaciones
_TOL_RELATIVA = 1e-6  # tolerancia para reconocer una estación en L/4, L/2, 3L/4
_TOL_ESTACION = 1e-3  # tolerancia de la selección de estaciones (fracción de L)
FRACCION_MAX_SEPARACION_CB = 0.1  # separación máxima entre estaciones (fracción de L) para un Cb interpolado

ADVERTENCIA_LB = (
    "Lb no sale de SAP2000: no se calcula aquí; la comprobación de pandeo lateral la define el usuario."
)
ADVERTENCIA_ENVOLVENTE = (
    "La envolvente es por componente sobre todas las combinaciones (conservadora): "
    "cada componente puede venir de una combinación distinta. 'gobierna' indica cuál."
)


class SapAceroFuerzasError(RuntimeError):
    """Error de lectura de SAP2000 para Acero AISC 360-22.

    ``detail`` sigue el criterio de ``bridge.hormigon.SapHormigonError``: el envoltorio
    ``call`` de ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


# ---------------------------------------------------------------------------
# Utilidades COM
# ---------------------------------------------------------------------------


def _salidas(res: Any, what: str) -> list[Any]:
    """Salidas de una llamada COM sin el código de retorno; error si el código no es 0."""
    try:
        codigo, salidas = split_return(res)
    except RuntimeError as exc:
        raise SapAceroFuerzasError(f"SAP2000 API: {what} devolvió una respuesta inesperada.") from exc
    if codigo != 0:
        raise SapAceroFuerzasError(f"SAP2000 API: {what} falló (código {codigo}).")
    return list(salidas)


def _valor_unico(res: Any, what: str) -> Any:
    """Dato de una llamada que devuelve UNO solo (p. ej. ``GetPresentUnits``).

    pywin32 lo entrega como escalar; otros enlaces como ``(ret, dato)`` o ``(dato, ret)``.
    """
    if not isinstance(res, (list, tuple)):
        return res
    salidas = _salidas(res, what)
    if len(salidas) != 1:
        raise SapAceroFuerzasError(f"SAP2000 API: {what} devolvió {len(salidas)} datos; se esperaba 1.")
    return salidas[0]


def _nombres(res: Any, what: str) -> list[str]:
    """Nombres de un ``GetNameList``: la última salida que sea una lista."""
    listas = [s for s in _salidas(res, what) if isinstance(s, (list, tuple))]
    return [str(n) for n in listas[-1]] if listas else []


def _factor_longitud(codigo_unidades: int) -> float:
    """Factor de longitud a metros del código eUnits; error si SAP2000 usa un sistema no soportado."""
    if codigo_unidades not in UNIT_CODES.values():
        raise SapAceroFuerzasError(f"Unidades de SAP2000 no soportadas (eUnits={codigo_unidades}).")
    return length_factor_to_meters(unit_label(codigo_unidades))


def _unidades_actuales(model: Any) -> int:
    try:
        return int(_valor_unico(model.GetPresentUnits(), "GetPresentUnits"))
    except SapAceroFuerzasError:
        raise
    except Exception as exc:
        raise SapAceroFuerzasError(f"No se pudieron leer las unidades de SAP2000: {exc}") from exc


def _restaurar_unidades(model: Any, previas: int) -> str | None:
    """Restaura las unidades originales. Devuelve el aviso si no se pudo; nunca lanza."""
    try:
        codigo, _ = split_return(model.SetPresentUnits(previas))
    except Exception as exc:
        mensaje = f"No se pudieron restaurar las unidades de SAP2000 (eUnits={previas}): {exc}."
    else:
        if codigo == 0:
            return None
        mensaje = f"No se pudieron restaurar las unidades de SAP2000 (eUnits={previas}, código {codigo})."
    logger.warning(mensaje)
    return mensaje + " Revise las unidades del modelo."


def listar_combinaciones(model: Any) -> dict[str, list[str]]:
    """Combinaciones (``RespCombo``) y casos de carga (``LoadCases``) del modelo."""
    try:
        combinaciones = _nombres(model.RespCombo.GetNameList(0, []), "RespCombo.GetNameList")
        casos = _nombres(model.LoadCases.GetNameList(0, []), "LoadCases.GetNameList")
    except SapAceroFuerzasError:
        raise
    except Exception as exc:
        raise SapAceroFuerzasError(str(exc)) from exc
    return {"combinaciones": combinaciones, "casos": casos}


def _nombres_barras(model: Any) -> list[str]:
    return _nombres(model.FrameObj.GetNameList(0, []), "FrameObj.GetNameList")


def _seccion(model: Any, barra: str) -> str | None:
    try:
        salidas = _salidas(model.FrameObj.GetSection(barra, "", ""), "FrameObj.GetSection")
    except Exception:
        return None
    return next((s for s in salidas if isinstance(s, str) and s), None)


def _longitud_m(model: Any, barra: str, factor: float) -> float | None:
    """Longitud entre los extremos (``GetPoints`` + ``GetCoordCartesian``), en m. ``None`` si no se puede."""
    try:
        p1, p2 = _salidas(model.FrameObj.GetPoints(barra, "", ""), "FrameObj.GetPoints")[:2]
        c1 = _salidas(model.PointObj.GetCoordCartesian(p1, 0.0, 0.0, 0.0), "PointObj.GetCoordCartesian")
        c2 = _salidas(model.PointObj.GetCoordCartesian(p2, 0.0, 0.0, 0.0), "PointObj.GetCoordCartesian")
        return math.dist(c1[:3], c2[:3]) * factor
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lecturas
# ---------------------------------------------------------------------------


def seleccion_acero(model: Any) -> list[dict[str, Any]]:
    """Barras seleccionadas en SAP2000 con su sección y longitud (en m, con las unidades actuales)."""
    factor = _factor_longitud(_unidades_actuales(model))
    return [
        {
            "frame": item["frame"],
            "seccion": item["section"] or None,
            "longitud_m": _longitud_m(model, item["frame"], factor),
        }
        for item in selected_frames(model)
    ]


def _seleccionar_salida(model: Any, combos: list[str], combinaciones: set[str]) -> None:
    setup = model.Results.Setup
    _salidas(setup.DeselectAllCasesAndCombosForOutput(), "DeselectAllCasesAndCombosForOutput")
    for c in combos:
        if c in combinaciones:
            _salidas(setup.SetComboSelectedForOutput(c), f"SetComboSelectedForOutput({c})")
        else:
            _salidas(setup.SetCaseSelectedForOutput(c), f"SetCaseSelectedForOutput({c})")


def _frame_force(model: Any, barra: str) -> Any:
    """``Results.FrameForce`` con la firma corta, que es la que funciona con pywin32 (SAP2000 23.3.1).

    La firma larga (ItemTypeElm, NumberResults y 13 arreglos de salida) solo se intenta si la
    corta falla; con pywin32 la larga de 17 argumentos lanza TypeError.
    """
    try:
        return model.Results.FrameForce(barra, 0)
    except Exception as corta:
        try:
            return model.Results.FrameForce(barra, 0, 0, *[[] for _ in range(13)])
        except Exception as larga:
            raise SapAceroFuerzasError(
                f"Results.FrameForce('{barra}') falló (firma corta: {corta}; firma larga: {larga})."
            ) from larga


def _tipos(salidas: list[Any]) -> str:
    return "[" + ", ".join(type(s).__name__ for s in salidas) + "]"


def _filas_barra(
    model: Any, barra: str, combos: set[str], advertencias: list[str] | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Filas de ``Results.FrameForce`` de una barra (kN, kN·m, m; ya en kN-m).

    Devuelve también cuántas filas de casos o combinaciones no pedidos trajo SAP2000 (se descartan).
    Si SAP2000 devuelve ``n`` ``None`` o 0 (sin resultados) no hay filas y se añade un aviso a
    ``advertencias``; si la forma de la respuesta no es la esperada lanza ``SapAceroFuerzasError``.
    """
    # Orden de salidas (OAPI, comprobado con SAP2000 23.3.1): n, Obj, ObjSta, Elm, ElmSta, LoadCase,
    # StepType, StepNum, P, V2, V3, T, M2, M3; el código de retorno va al inicio con pywin32.
    salidas = _salidas(_frame_force(model, barra), f"Results.FrameForce('{barra}')")
    if len(salidas) < 14:
        raise SapAceroFuerzasError(
            f"Results.FrameForce('{barra}') devolvió una respuesta con forma inesperada: se esperaban "
            f"al menos 14 salidas y llegaron {len(salidas)} (salidas: {_tipos(salidas)})."
        )
    n, _obj, obj_sta, _elm, _elm_sta, caso, paso, _num, P, V2, V3, T, M2, M3 = salidas[:14]
    if n is None or (isinstance(n, (int, float)) and not isinstance(n, bool) and n == 0):
        if advertencias is not None:
            advertencias.append(
                f"Barra '{barra}': SAP2000 no devolvió resultados "
                "(¿modelo analizado? ¿casos o combinaciones seleccionados para salida?)."
            )
        return [], 0
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        raise SapAceroFuerzasError(
            f"Results.FrameForce('{barra}') devolvió una respuesta con forma inesperada: el número de "
            f"resultados no es numérico ({n!r}); {len(salidas)} salidas (salidas: {_tipos(salidas)})."
        )
    filas: list[dict[str, Any]] = []
    descartadas = 0
    for i in range(int(n)):
        combo = str(caso[i])
        if combo not in combos:
            descartadas += 1
            continue
        filas.append(
            {
                "combo": combo,
                "step": str(paso[i]).strip() if paso[i] else "",
                "x": float(obj_sta[i]),
                "P": float(P[i]),
                "V2": float(V2[i]),
                "V3": float(V3[i]),
                "T": float(T[i]),
                "M2": float(M2[i]),
                "M3": float(M3[i]),
            }
        )
    return filas, descartadas


def _avisar_filas(
    barra: str,
    filas: list[dict[str, Any]],
    descartadas: int,
    solicitadas: list[str],
    advertencias: list[str],
) -> None:
    """Comprueba que la salida es exactamente estaciones x combinaciones pedidas."""
    if descartadas:
        advertencias.append(
            f"Barra '{barra}': SAP2000 devolvió {descartadas} filas de casos o combinaciones no pedidos; "
            "se descartan (revise la selección de salida)."
        )
    conteo = Counter(f["combo"] for f in filas)
    por_combo = sorted({conteo[c] for c in solicitadas if conteo[c]})
    if len(por_combo) > 1:
        advertencias.append(
            f"Barra '{barra}': número de filas distinto entre combinaciones ({por_combo}); "
            "revise las estaciones de salida."
        )


# ---------------------------------------------------------------------------
# Cálculo (sin COM)
# ---------------------------------------------------------------------------


def cb_f1(xs: list[float], ms: list[float], longitud: float) -> tuple[float | None, bool]:
    """Cb de AISC 360-22 Ec. F1-1 = 12.5·Mmax / (2.5·Mmax + 3·MA + 4·MB + 3·MC).

    ``MA``, ``MB`` y ``MC`` son |M| en L/4, L/2 y 3L/4; ``Mmax`` es max|M| de las
    estaciones. Devuelve ``(Cb, interpolado)``; ``interpolado`` indica que alguna
    de esas posiciones no era una estación y se interpoló linealmente (con signo).
    ``Cb`` es ``None`` si no hay momento o si no se puede calcular.
    """
    if len(xs) < 2 or longitud <= 0 or len(xs) != len(ms):
        return None, False
    pares = sorted(zip(xs, ms))
    tol = _TOL_RELATIVA * longitud
    interpolado = False

    def en(p: float) -> float:
        nonlocal interpolado
        for x, m in pares:
            if abs(x - p) <= tol:
                return m
        interpolado = True
        if p <= pares[0][0]:
            return pares[0][1]
        if p >= pares[-1][0]:
            return pares[-1][1]
        for (x0, m0), (x1, m1) in zip(pares, pares[1:]):
            if x0 <= p <= x1:
                return m0 + (m1 - m0) * (p - x0) / (x1 - x0)
        return pares[-1][1]

    ma = abs(en(longitud / 4))
    mb = abs(en(longitud / 2))
    mc = abs(en(3 * longitud / 4))
    mmax = max(abs(m) for m in ms)
    denominador = 2.5 * mmax + 3 * ma + 4 * mb + 3 * mc
    if mmax <= 0 or denominador <= 0:
        return None, interpolado
    return 12.5 * mmax / denominador, interpolado


def cb_para_demanda(
    xs: list[float], ms: list[float], longitud: float, regla: bool = True
) -> tuple[float | None, bool, bool, float | None]:
    """Cb de un paso con la regla de separación entre estaciones.

    Devuelve ``(cb, interpolado, forzado, gap_max)``. ``gap_max`` es la mayor separación entre estaciones
    consecutivas (``None`` si hay menos de dos). Si ``regla`` está activa, el Cb se calculó interpolando
    y ``gap_max`` supera ``FRACCION_MAX_SEPARACION_CB`` · L, el Cb pasa a 1,0 y ``forzado`` es True:
    interpolar un diagrama de momentos curvo con estaciones separadas subestima los momentos de L/4 y
    3L/4 y sube el Cb. Sin momento el Cb es ``None`` y no se fuerza nada.
    """
    cb, interpolado = cb_f1(xs, ms, longitud)
    orden = sorted(xs)
    gap_max = max((b - a for a, b in zip(orden, orden[1:])), default=None)
    limite = FRACCION_MAX_SEPARACION_CB * longitud * (1 + 1e-9)
    forzado = bool(
        regla and cb is not None and interpolado and gap_max is not None and longitud > 0 and gap_max > limite
    )
    if forzado:
        cb = 1.0
    return cb, interpolado, forzado, gap_max


def _ejes() -> tuple[str, str, str, str]:
    """(momento fuerte, cortante del plano fuerte, momento débil, cortante del plano débil)."""
    if EJE_FUERTE == "M3":
        return "M3", "V2", "M2", "V3"
    return "M2", "V3", "M3", "V2"


def _estaciones_seleccionadas(
    filas: list[dict[str, Any]], longitud: float | None, estaciones: list[float] | None
) -> list[dict[str, Any]]:
    if not estaciones or not longitud:
        return filas
    return [f for f in filas if any(abs(f["x"] / longitud - r) <= _TOL_ESTACION for r in estaciones)]


def _axiales(P: list[float]) -> tuple[float, float]:
    """(Pu, Tu) en magnitud a partir de P de todas las estaciones y pasos de una combinación.

    SAP2000 da P con tracción positiva: la compresión es -P y la tracción +P. ``Pu`` es la compresión
    máxima y ``Tu`` la tracción máxima; cada una vale 0 si no aparece. Un valor negativo de P se
    convierte en Pu, nunca en 0.

    Si P cambia de signo, Pu y Tu son ambas > 0 y se conservan las dos. El motor comprueba la
    compresión (Pu, en H1-1 y en la comprobación de compresión) y la tracción (Tu) y cada
    comprobación crece con su solicitación, así que evaluar las dos a la vez da lo mismo que evaluar
    los dos casos por separado y quedarse con el mayor. Quedarse sólo con el caso dominante no es
    conservador: con P = -100 kN en un extremo y +300 kN en el otro, sólo la compresión daría un
    ratio gobernante de 0,047, y la tracción real da 0,106 (medido con el motor). Pendiente en el
    motor: Tu no entra en H1-1, así que la tracción combinada con momento está subestimada.
    """
    return max(-min(P), 0.0), max(max(P), 0.0)


def _es(valor: float, decimales: int) -> str:
    """Número con coma decimal, como se escribe en castellano."""
    return f"{valor:.{decimales}f}".replace(".", ",")


def _demanda(
    barra: str,
    combo: str,
    filas: list[dict[str, Any]],
    longitud: float,
    advertencias: list[str],
    regla_separacion: bool = True,
) -> dict[str, Any]:
    """Demanda de una barra para una combinación (envolvente por componente dentro de la combinación).

    Momentos y cortantes van siempre en magnitud (``abs``); el signo axial lo reparte ``_axiales``.
    ``regla_separacion`` False (longitud no medida en SAP2000) desactiva la regla de ``cb_para_demanda``.
    """
    fuerte, cortante_fuerte, _debil, cortante_debil = _ejes()
    cbs: list[float] = []
    interpolado = False
    forzados: list[tuple[float, int, float]] = []  # (separación máxima, estaciones, Cb interpolado)
    for paso in dict.fromkeys(f["step"] for f in filas):
        grupo = sorted((f for f in filas if f["step"] == paso), key=lambda f: f["x"])
        xs = [f["x"] for f in grupo]
        ms = [f[fuerte] for f in grupo]
        cb, interp, forzado, gap = cb_para_demanda(xs, ms, longitud, regla=regla_separacion)
        if forzado:
            cb_interpolado, _ = cb_f1(xs, ms, longitud)
            forzados.append((gap, len(grupo), cb_interpolado))
        else:
            interpolado = interpolado or interp
        if cb is not None:
            cbs.append(cb)
    if interpolado:
        advertencias.append(
            f"Barra '{barra}', combinación '{combo}': Cb interpolado entre estaciones "
            "(no hay estación en L/4, L/2 o 3L/4)."
        )
    if forzados:
        gap, n, cb_interpolado = max(forzados)
        advertencias.append(
            f"Barra '{barra}', combinación '{combo}': solo {n} estaciones de salida en SAP2000 "
            f"(separación máxima {_es(gap, 2)} m > L/10): Cb = 1,0 en lugar del Cb interpolado "
            f"({_es(cb_interpolado, 3)}). Asigne más estaciones (Assign > Frame > Output Stations, "
            "p. ej. 11 secciones o segmento máximo 0,5 m) y vuelva a traer fuerzas."
        )
    fila_mmax = max(filas, key=lambda f: abs(f[fuerte]))
    pu, tu = _axiales([f["P"] for f in filas])
    if pu > 0 and tu > 0:
        advertencias.append(
            f"Barra '{barra}', combinación '{combo}': la carga axial cambia de signo "
            f"(compresión máx. {pu:.1f} kN, tracción máx. {tu:.1f} kN); se diseña con ambas."
        )
    return {
        "frame": barra,
        "combo": combo,
        "Pu_kN": pu,
        "Tu_kN": tu,
        "Vux_kN": max(abs(f[cortante_fuerte]) for f in filas),
        "Vuy_kN": max(abs(f[cortante_debil]) for f in filas),
        "Mux_kNm": abs(fila_mmax[fuerte]),
        "Muy_kNm": max(abs(f[_debil]) for f in filas),
        "T_kNm": max(abs(f["T"]) for f in filas),
        "Cb": min(cbs) if cbs else None,
        "x_Mmax_m": fila_mmax["x"],
    }


def _cb_pasos(filas: list[dict[str, Any]], longitud: float, regla: bool) -> list[dict[str, Any]]:
    """Cb de cada paso (p. ej. Max y Min por separado) con su indicador de interpolación y la separación máxima entre estaciones (``gap_max_m``) usada para calcularlo."""
    fuerte, *_ = _ejes()
    salida = []
    for paso in dict.fromkeys(f["step"] for f in filas):
        grupo = sorted((f for f in filas if f["step"] == paso), key=lambda f: f["x"])
        cb, interpolado, forzado, gap = cb_para_demanda(
            [f["x"] for f in grupo], [f[fuerte] for f in grupo], longitud, regla=regla
        )
        salida.append(
            {"step": paso, "Cb": cb, "interpolado": interpolado, "forzado": forzado, "gap_max_m": gap}
        )
    return salida


def _demanda_estacion(
    barra: str, combo: str, paso: str, f: dict[str, Any], cb: float | None
) -> dict[str, Any]:
    """Demanda de UNA estación: solo las fuerzas de esa estación, caso y paso (sin mezclar estaciones)."""
    fuerte, cortante_fuerte, debil, cortante_debil = _ejes()
    pu, tu = _axiales([f["P"]])
    return {
        "frame": barra,
        "combo": combo,
        "step": paso,
        "x_m": f["x"],
        "Pu_kN": pu,
        "Tu_kN": tu,
        "Vux_kN": abs(f[cortante_fuerte]),
        "Vuy_kN": abs(f[cortante_debil]),
        "Mux_kNm": abs(f[fuerte]),
        "Muy_kNm": abs(f[debil]),
        "T_kNm": abs(f["T"]),
        "Cb": cb,
    }


def _extremo(pares: Iterable[tuple[str, float]], mayor: bool) -> tuple[float, str]:
    """Valor máximo (o mínimo) y su combinación; en empate gana la primera."""
    mejor: tuple[str, float] | None = None
    for combo, valor in pares:
        if mejor is None or (valor > mejor[1] if mayor else valor < mejor[1]):
            mejor = (combo, valor)
    assert mejor is not None
    return mejor[1], mejor[0]


_COMPONENTES = ("Pu_kN", "Tu_kN", "Vux_kN", "Vuy_kN", "Mux_kNm", "Muy_kNm", "T_kNm")


def _envolvente(barra: str, demandas: list[dict[str, Any]]) -> dict[str, Any]:
    salida: dict[str, Any] = {"frame": barra}
    gobierna: dict[str, str | None] = {}
    for componente in _COMPONENTES:
        valor, combo = _extremo([(d["combo"], d[componente]) for d in demandas], mayor=True)
        salida[componente] = valor
        gobierna[componente] = combo
    cbs = [(d["combo"], d["Cb"]) for d in demandas if d["Cb"] is not None]
    # Cb multiplica la capacidad: la envolvente toma el Cb mínimo (conservador).
    salida["Cb"], gobierna["Cb"] = _extremo(cbs, mayor=False) if cbs else (None, None)
    salida["gobierna"] = gobierna
    return salida


def _limpio(value: Any) -> Any:
    """Valores no finitos a ``None`` y tuplas a listas, recursivamente (JSON válido)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _limpio(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_limpio(v) for v in value]
    return value


def _sin_repetir(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def leer_fuerzas(
    model: Any,
    frames: list[str] | Literal["selected"],
    combos: list[str],
    estaciones: list[float] | None = None,
    por_estacion: bool = False,
) -> dict[str, Any]:
    """Fuerzas de las barras para ``combos`` y sus demandas de diseño AISC 360-22.

    Con ``por_estacion`` la respuesta añade (sin cambiar nada de lo anterior) ``cb_pasos`` en cada
    demanda y ``demandas_estacion``: una demanda por estación, caso y paso con las fuerzas de esa
    misma estación y el Cb de su paso.

    Las barras que no existen en el modelo se omiten con un aviso en ``warnings``;
    si ninguna existe, se responde con error. Una combinación o caso que no existe
    en el modelo es un error. Las unidades se fuerzan a kN-m y se restauran siempre.
    """
    model = normalizar(model, destino_primero=False)
    advertencias: list[str] = [ADVERTENCIA_LB, ADVERTENCIA_ENVOLVENTE]

    solicitadas = _sin_repetir(combos)
    if not solicitadas:
        raise ActionValidationError("Indique al menos una combinación.")

    if frames == "selected":
        nombres = [item["frame"] for item in selected_frames(model)]
        if not nombres:
            raise SapAceroFuerzasError("No hay barras seleccionadas en SAP2000 y no se indicaron nombres.")
    else:
        nombres = _sin_repetir(frames)
    if len(nombres) > MAX_FRAMES:
        raise ActionValidationError(f"Como máximo {MAX_FRAMES} barras por petición.")

    existentes = set(_nombres_barras(model))
    presentes: list[str] = []
    for nombre in nombres:
        if nombre in existentes:
            presentes.append(nombre)
        else:
            advertencias.append(f"Barra '{nombre}' no existe en el modelo; se omite.")
    if not presentes:
        raise SapAceroFuerzasError("Ninguna de las barras indicadas existe en el modelo.")

    disponibles = listar_combinaciones(model)
    combinaciones = set(disponibles["combinaciones"])
    desconocidas = [c for c in solicitadas if c not in combinaciones | set(disponibles["casos"])]
    if desconocidas:
        raise SapAceroFuerzasError(
            "Combinación o caso inexistente en el modelo: " + ", ".join(desconocidas) + "."
        )

    previas = _unidades_actuales(model)
    try:
        _salidas(model.SetPresentUnits(EUNITS_KN_M), "SetPresentUnits(kN_m_C)")
        factor = _factor_longitud(EUNITS_KN_M)
        _seleccionar_salida(model, solicitadas, combinaciones)
        leidas = []
        for barra in presentes:
            filas, descartadas = _filas_barra(model, barra, set(solicitadas), advertencias)
            _avisar_filas(barra, filas, descartadas, solicitadas, advertencias)
            leidas.append((barra, _seccion(model, barra), _longitud_m(model, barra, factor), filas))
    finally:
        aviso = _restaurar_unidades(model, previas)
        if aviso:
            advertencias.append(aviso)

    frames_out: list[dict[str, Any]] = []
    demandas: list[dict[str, Any]] = []
    envolvente: list[dict[str, Any]] = []
    demandas_estacion: list[dict[str, Any]] = []
    for barra, seccion, longitud, filas in leidas:
        longitud_medida = longitud is not None  # la regla de separación solo vale con L medida en SAP2000
        if longitud is None and filas:
            longitud = max(f["x"] for f in filas) - min(f["x"] for f in filas)
            advertencias.append(
                f"Barra '{barra}': longitud no disponible; Cb usa la distancia entre estaciones."
            )
        combinaciones_out: list[dict[str, Any]] = []
        demandas_barra: list[dict[str, Any]] = []
        for combo in solicitadas:
            filas_c = [f for f in filas if f["combo"] == combo]
            if not filas_c:
                advertencias.append(
                    f"Barra '{barra}', combinación '{combo}': SAP2000 no devolvió resultados."
                )
                continue
            for paso in dict.fromkeys(f["step"] for f in filas_c):
                grupo = sorted((f for f in filas_c if f["step"] == paso), key=lambda f: f["x"])
                visibles = _estaciones_seleccionadas(grupo, longitud, estaciones)
                combinaciones_out.append(
                    {
                        "combo": combo,
                        "step": paso,
                        "estaciones": [
                            {
                                "x_m": f["x"],
                                "P_kN": f["P"],
                                "V2_kN": f["V2"],
                                "V3_kN": f["V3"],
                                "T_kNm": f["T"],
                                "M2_kNm": f["M2"],
                                "M3_kNm": f["M3"],
                            }
                            for f in visibles
                        ],
                    }
                )
            if longitud is not None:
                demanda = _demanda(
                    barra, combo, filas_c, longitud, advertencias, regla_separacion=longitud_medida
                )
                if por_estacion:
                    pasos = _cb_pasos(filas_c, longitud, longitud_medida)
                    demanda["cb_pasos"] = pasos
                    cb_de = {p["step"]: p["Cb"] for p in pasos}
                    for paso in cb_de:
                        grupo = sorted((f for f in filas_c if f["step"] == paso), key=lambda f: f["x"])
                        for f in _estaciones_seleccionadas(grupo, longitud, estaciones):
                            demandas_estacion.append(_demanda_estacion(barra, combo, paso, f, cb_de[paso]))
                demandas_barra.append(demanda)
        frames_out.append(
            {"frame": barra, "longitud_m": longitud, "seccion": seccion, "combinaciones": combinaciones_out}
        )
        demandas.extend(demandas_barra)
        if demandas_barra:
            envolvente.append(_envolvente(barra, demandas_barra))

    salida: dict[str, Any] = {
        "unidades": "kN-m",
        "ejes": "locales 1-2-3",
        "validado_con_sap": True,
        "validado_con": VALIDADO_CON,
        "validado_fecha": VALIDADO_FECHA,
        "frames": frames_out,
        "demandas": demandas,
        "envolvente": envolvente,
        "warnings": advertencias,
    }
    if por_estacion:
        salida["demandas_estacion"] = demandas_estacion
    return _limpio(salida)


# ---------------------------------------------------------------------------
# Cuerpo de la petición y rutas
# ---------------------------------------------------------------------------


class FuerzasBody(BaseModel):
    frames: list[str] | Literal["selected"]
    combos: list[str]
    estaciones: list[float] | None = None
    # Opcional: añade cb_pasos y demandas_estacion; sin él la respuesta es idéntica a la de siempre.
    por_estacion: bool = False

    @field_validator("frames")
    @classmethod
    def bounded_frames(cls, value: list[str] | str) -> list[str] | str:
        if isinstance(value, str):
            return value
        if not value or len(value) > MAX_FRAMES:
            raise ValueError(f"frames debe contener entre 1 y {MAX_FRAMES} barras, o 'selected'")
        return [_valid_name(name, "frame") for name in value]

    @field_validator("combos")
    @classmethod
    def bounded_combos(cls, value: list[str]) -> list[str]:
        if not value or len(value) > MAX_COMBOS:
            raise ValueError(f"combos debe contener entre 1 y {MAX_COMBOS} combinaciones")
        return [_valid_combo_name(combo, "combinación") for combo in value]

    @field_validator("estaciones")
    @classmethod
    def bounded_estaciones(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        if len(value) > MAX_ESTACIONES or any(not 0.0 <= r <= 1.0 for r in value):
            raise ValueError(f"estaciones: hasta {MAX_ESTACIONES} valores x/L entre 0 y 1")
        return value


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/acero-aisc360/*`` con el envoltorio ``call`` de ``bridge.actions``."""
    prefix = "/v1/sap/actions/acero-aisc360"

    @app.get(f"{prefix}/combos")
    def acero_combos():
        return call(listar_combinaciones)

    @app.get(f"{prefix}/selected")
    def acero_selected():
        return call(lambda model: {"frames": seleccion_acero(model)})

    @app.post(f"{prefix}/fuerzas")
    def acero_fuerzas(body: FuerzasBody):
        with con_limite(LIMITE_COM_S):
            return call(
                lambda model: leer_fuerzas(
                    model, body.frames, body.combos, body.estaciones, body.por_estacion
                )
            )
