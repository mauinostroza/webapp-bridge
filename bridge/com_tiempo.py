"""Tiempo límite de las llamadas al hilo COM, ajustable por acción.

``StaActor.call`` espera 30 s por defecto. Las lecturas completas de un modelo
grande (SAP Graph, Viewer) necesitan más; la ruta fija un valor mayor con
``con_limite`` sólo mientras dura su llamada.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LIMITE_PREDETERMINADO = 30.0

limite: ContextVar[float] = ContextVar("sap_com_limite", default=LIMITE_PREDETERMINADO)


@contextmanager
def con_limite(segundos: float) -> Iterator[None]:
    token = limite.set(float(segundos))
    try:
        yield
    finally:
        limite.reset(token)
