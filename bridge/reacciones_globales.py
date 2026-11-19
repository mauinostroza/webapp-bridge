"""Reacciones de apoyo en ejes globales: ``Results.JointReact`` las entrega en el sistema local del nodo.

Según la documentación OAPI, ``Results.JointReact`` devuelve fuerzas y momentos en los ejes locales del nodo (no está
verificado con un apoyo girado); el Workbench (Str2Fund, zapatas biaxiales y Fundaciones del visor) asume ejes globales.
``reacciones_a_globales`` convierte las filas de los nodos con ejes locales girados y deja constancia de lo que hizo:

* Nodo sin giro (``Advanced`` falso y los tres ángulos ≈ 0): no se toca nada.
* Nodo girado: ``F1..F3`` y ``M1..M3`` se llevan a globales con la matriz de rotación del nodo.
* Nodo con ejes avanzados (definidos por vectores) o cuyos ejes no se pueden leer: no se adivina; las filas quedan tal
  como las devuelve SAP2000 y el nodo se lista como no resuelto.

La convención de las rotaciones sucesivas está aislada en ``matriz_local_a_global`` y está PENDIENTE DE VERIFICAR con la
sonda (docs/SAP2000_ACCEPTANCE.md). No modifica el modelo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from bridge.actions import split_return

TOL_ANGULO_DEG = 1e-6  # un giro menor que esto se considera nulo
REACTIONS_FRAME = "global"
_STEPS_ENVOLVENTE = {"max", "min"}


@dataclass
class EjesNodo:
    """Ejes locales de un nodo: ``estado`` es ``sin_giro``, ``girado``, ``avanzado`` o ``ilegible``."""

    estado: str
    angulos: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class ResultadoConversion:
    """Qué hizo la conversión (campos aditivos de las respuestas) y los avisos para ``warnings``."""

    joints_rotated: list[str] = field(default_factory=list)
    joints_axes_unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reactions_frame: str = REACTIONS_FRAME

    def campos(self) -> dict[str, Any]:
        """Las tres claves aditivas que se añaden a la respuesta de cada ruta."""
        return {
            "reactions_frame": self.reactions_frame,
            "joints_rotated": list(self.joints_rotated),
            "joints_axes_unresolved": list(self.joints_axes_unresolved),
        }


def leer_ejes_nodo(model: Any, nodo: str) -> EjesNodo:
    """Lee ``PointObj.GetLocalAxes`` (ángulos a, b, c en grados y la bandera ``Advanced``); nunca lanza."""
    raw: Any = None
    for args in ((nodo,), (nodo, 0.0, 0.0, 0.0, False)):
        try:
            raw = model.PointObj.GetLocalAxes(*args)
            break
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        return EjesNodo("ilegible")
    try:
        codigo, salidas = split_return(raw)
    except RuntimeError:
        return EjesNodo("ilegible")
    if codigo != 0:
        return EjesNodo("ilegible")
    angulos = [float(v) for v in salidas if isinstance(v, (int, float)) and not isinstance(v, bool)]
    avanzado = [v for v in salidas if isinstance(v, bool)]
    if len(angulos) < 3 or not all(math.isfinite(a) for a in angulos[:3]):
        return EjesNodo("ilegible")
    abc = (angulos[0], angulos[1], angulos[2])
    if avanzado and avanzado[0]:
        return EjesNodo("avanzado", abc)
    if any(abs(a) > TOL_ANGULO_DEG for a in abc):
        return EjesNodo("girado", abc)
    return EjesNodo("sin_giro", abc)


def matriz_local_a_global(
    a_deg: float, b_deg: float, c_deg: float
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Matriz ``R`` (filas) tal que ``vector_global = R · vector_local`` para los ejes locales de un nodo.

    PENDIENTE DE VERIFICAR con la sonda (apoyo girado conocido contra la tabla «Joint Reactions» en globales).

    Convención asumida de la documentación CSI de ``PointObj.GetLocalAxes`` (ángulos en grados, regla de la mano
    derecha): primero ``a`` alrededor del eje 3 (Z global), luego ``b`` alrededor del NUEVO eje 2 y por último ``c``
    alrededor del NUEVO eje 1. Rotaciones sucesivas sobre ejes que se mueven (intrínsecas): ``R = Rz(a)·Ry(b)·Rx(c)``,
    y las columnas de ``R`` son los ejes locales 1, 2 y 3 expresados en globales. Si la sonda muestra otro orden o signo,
    es lo único que hay que cambiar.
    """
    a, b, c = math.radians(a_deg), math.radians(b_deg), math.radians(c_deg)
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    rz = ((ca, -sa, 0.0), (sa, ca, 0.0), (0.0, 0.0, 1.0))
    ry = ((cb, 0.0, sb), (0.0, 1.0, 0.0), (-sb, 0.0, cb))
    rx = ((1.0, 0.0, 0.0), (0.0, cc, -sc), (0.0, sc, cc))

    def prod(
        m: Sequence[Sequence[float]], n: Sequence[Sequence[float]]
    ) -> tuple[tuple[float, float, float], ...]:
        return tuple(tuple(sum(m[i][k] * n[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]

    return prod(prod(rz, ry), rx)  # type: ignore[return-value]


def _rotar(matriz: Sequence[Sequence[float]], v: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matriz[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def _lista(nombres: list[str], tope: int = 10) -> str:
    return ", ".join(nombres[:tope]) + (f" y {len(nombres) - tope} más" if len(nombres) > tope else "")


def reacciones_a_globales(
    model: Any,
    filas: list[dict[str, Any]],
    *,
    clave_nodo: str = "joint",
    claves_fuerza: tuple[str, str, str] = ("f1", "f2", "f3"),
    claves_momento: tuple[str, str, str] = ("m1", "m2", "m3"),
    clave_paso: str = "step_type",
) -> ResultadoConversion:
    """Convierte EN SU SITIO las filas de reacciones de los nodos con ejes locales girados a ejes globales.

    ``filas``: diccionarios con el nodo, las tres fuerzas, los tres momentos y el ``StepType``; los nombres de las claves
    se indican porque cada ruta usa los suyos (``f1..m3``, ``Vx Vy P Mx My Mz``…). Solo lee ``PointObj.GetLocalAxes`` y
    solo de los nodos que aparecen en ``filas``.
    """
    resultado = ResultadoConversion()
    ejes: dict[str, EjesNodo] = {}
    for fila in filas:
        nodo = str(fila[clave_nodo])
        if nodo not in ejes:
            ejes[nodo] = leer_ejes_nodo(model, nodo)
    resultado.joints_rotated = [n for n, e in ejes.items() if e.estado == "girado"]
    resultado.joints_axes_unresolved = [n for n, e in ejes.items() if e.estado in ("avanzado", "ilegible")]
    matrices = {n: matriz_local_a_global(*ejes[n].angulos) for n in resultado.joints_rotated}
    envolventes = 0
    for fila in filas:
        nodo = str(fila[clave_nodo])
        if nodo not in matrices:
            continue
        fuerza = _rotar(matrices[nodo], tuple(float(fila[k]) for k in claves_fuerza))  # type: ignore[arg-type]
        momento = _rotar(matrices[nodo], tuple(float(fila[k]) for k in claves_momento))  # type: ignore[arg-type]
        for clave, valor in (
            *zip(claves_fuerza, fuerza, strict=True),
            *zip(claves_momento, momento, strict=True),
        ):
            fila[clave] = valor
        if str(fila.get(clave_paso, "")).strip().lower() in _STEPS_ENVOLVENTE:
            envolventes += 1
    if resultado.joints_rotated:
        resultado.warnings.append(
            "Reacciones de nodos con ejes locales girados convertidas a ejes globales: "
            f"{_lista(resultado.joints_rotated)}. La convención de los ángulos del nodo está pendiente de "
            "verificar con SAP2000."
        )
    if envolventes:
        resultado.warnings.append(
            f"{envolventes} filas con StepType Max/Min (casos espectrales o envolventes) de nodos con ejes girados se "
            "convirtieron componente a componente: rotar máximos modales no es exacto; aplique su corrección de signo."
        )
    if resultado.joints_axes_unresolved:
        resultado.warnings.append(
            "No se pudieron convertir a ejes globales las reacciones de los nodos "
            f"{_lista(resultado.joints_axes_unresolved)} (ejes avanzados o no legibles): quedan tal como las devuelve "
            "SAP2000, en los ejes locales del nodo."
        )
    return resultado
