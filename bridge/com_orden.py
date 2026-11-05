"""Normaliza el orden del código de retorno de las llamadas COM de SAP2000.

Con comtypes y los envoltorios tipados el código va al final ``(salidas…, ret)``;
con pywin32 en enlace tardío puede ir al inicio ``(ret, salidas…)``. El código
que se portó de otras aplicaciones se escribió para uno solo de los órdenes.
En vez de adivinar en cada llamada, se detecta el orden real una vez por sesión
con una llamada inequívoca y se envuelve el modelo para que todas las tuplas
lleguen en el orden que espera el módulo.
"""

from __future__ import annotations

from typing import Any


def _es_secuencia(valor: Any) -> bool:
    return isinstance(valor, (list, tuple))


def _es_entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def detectar_codigo_primero(modelo: Any) -> bool | None:
    """``True`` si el enlace pone el código de retorno al inicio, ``False`` si al final.

    ``None`` si no se pudo determinar. Se usa ``LoadPatterns.GetNameList``, cuyas
    salidas son ``(n, nombres)``: la secuencia de nombres delata de qué lado va el código.
    """
    for argumentos in ((), (0, [])):
        try:
            crudo = modelo.LoadPatterns.GetNameList(*argumentos)
        except Exception:
            continue
        if not _es_secuencia(crudo) or len(crudo) < 2:
            continue
        if _es_secuencia(crudo[-1]):
            return True
        if _es_entero(crudo[-1]) and (_es_entero(crudo[0]) or _es_secuencia(crudo[0])):
            return False
    return None


def _mover_codigo(valor: Any, origen_primero: bool, destino_primero: bool) -> Any:
    if origen_primero == destino_primero or not isinstance(valor, tuple) or len(valor) < 2:
        return valor
    if origen_primero:  # (ret, a, b) -> (a, b, ret)
        return (*valor[1:], valor[0])
    return (valor[-1], *valor[:-1])  # (a, b, ret) -> (ret, a, b)


class ModeloNormalizado:
    """Proxy que adapta el orden del código de retorno de toda llamada del modelo."""

    def __init__(self, objeto: Any, origen_primero: bool, destino_primero: bool) -> None:
        object.__setattr__(self, "_objeto", objeto)
        object.__setattr__(self, "_origen", origen_primero)
        object.__setattr__(self, "_destino", destino_primero)

    def __getattr__(self, nombre: str) -> Any:
        atributo = getattr(self._objeto, nombre)
        if isinstance(atributo, (str, int, float, bool, tuple, list, type(None))):
            return atributo
        # Con pywin32 los subobjetos COM (FrameObj, RespCombo...) también son «callable»,
        # así que no se puede distinguir método de objeto: el proxy sirve para ambos.
        return ModeloNormalizado(atributo, self._origen, self._destino)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _mover_codigo(self._objeto(*args, **kwargs), self._origen, self._destino)

    def __setattr__(self, nombre: str, valor: Any) -> None:
        setattr(self._objeto, nombre, valor)


def normalizar(modelo: Any, destino_primero: bool, origen_primero: bool | None = None) -> Any:
    """Devuelve ``modelo`` con las tuplas en el orden ``destino_primero``.

    Si ``origen_primero`` no se indica se detecta; si no se puede detectar, el
    modelo se devuelve sin cambios (el módulo seguirá con su convención).
    """
    if origen_primero is None:
        origen_primero = detectar_codigo_primero(modelo)
    if origen_primero is None or origen_primero == destino_primero:
        return modelo
    return ModeloNormalizado(modelo, origen_primero, destino_primero)
