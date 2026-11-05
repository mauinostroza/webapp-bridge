"""Normalizador del orden del código de retorno COM (bridge/com_orden.py)."""

from __future__ import annotations

import pytest

from bridge.com_orden import detectar_codigo_primero, normalizar


class _Patrones:
    def __init__(self, primero: bool):
        self.primero = primero

    def GetNameList(self, *_args):
        return (0, 2, ("DEAD", "LIVE")) if self.primero else (2, ("DEAD", "LIVE"), 0)


class _Frame:
    def __init__(self, primero: bool):
        self.primero = primero

    def GetPoints(self, *_args):
        return (0, "P1", "P2") if self.primero else ("P1", "P2", 0)

    def SetSelected(self, *_args):
        return 0


class _Modelo:
    def __init__(self, primero: bool):
        self.LoadPatterns = _Patrones(primero)
        self.FrameObj = _Frame(primero)
        self.nombre = "modelo"


@pytest.mark.parametrize("primero", [True, False])
def test_detecta_el_lado_del_codigo(primero):
    assert detectar_codigo_primero(_Modelo(primero)) is primero


def test_sin_llamada_valida_no_se_puede_detectar():
    class _Vacio:
        class LoadPatterns:
            @staticmethod
            def GetNameList(*_args):
                raise RuntimeError("sin modelo")

    assert detectar_codigo_primero(_Vacio()) is None


@pytest.mark.parametrize("origen", [True, False])
@pytest.mark.parametrize("destino", [True, False])
def test_todas_las_tuplas_llegan_en_el_orden_pedido(origen, destino):
    modelo = normalizar(_Modelo(origen), destino_primero=destino)
    esperado = (0, "P1", "P2") if destino else ("P1", "P2", 0)
    assert modelo.FrameObj.GetPoints("F1") == esperado
    nombres = modelo.LoadPatterns.GetNameList(0, [])
    assert nombres == ((0, 2, ("DEAD", "LIVE")) if destino else (2, ("DEAD", "LIVE"), 0))
    # Un entero suelto (sólo código) no se toca y los atributos simples se conservan.
    assert modelo.FrameObj.SetSelected("F1", True) == 0
    assert modelo.nombre == "modelo"


def test_sin_deteccion_devuelve_el_modelo_sin_cambios():
    class _Raro:
        class LoadPatterns:
            @staticmethod
            def GetNameList(*_args):
                return 5

    modelo = _Raro()
    assert normalizar(modelo, destino_primero=False) is modelo


class _SubobjetoCallable:
    """Como un CDispatch de pywin32: subobjeto con atributos que además es «callable»."""

    def __call__(self, *args):
        raise TypeError("no se llama")

    def GetNameList(self, *args):
        return (2, ["A", "B"], 0)


class _ModeloDispatch:
    FrameObj = _SubobjetoCallable()


def test_subobjeto_callable_conserva_sus_metodos():
    from bridge.com_orden import normalizar

    modelo = normalizar(_ModeloDispatch(), destino_primero=True, origen_primero=False)

    assert modelo.FrameObj.GetNameList(0, []) == (0, 2, ["A", "B"])
