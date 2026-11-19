"""Lógica pura de scripts/sonda_combinaciones.py: fotos, diferencias, veredictos y guardas."""

from __future__ import annotations

import importlib.util
from pathlib import Path

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "sonda_combinaciones.py"
spec = importlib.util.spec_from_file_location("sonda_combinaciones", RUTA)
sonda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonda)


class _RespCombo:
    def __init__(self, combos):
        self.combos = dict(combos)

    def GetNameList(self):
        return (len(self.combos), tuple(self.combos), 0)

    def GetCaseList(self, name, *_):
        return (1, (0,), (name,), (1.0,), 0)


class _Setup:
    def GetComboSelectedForOutput(self, name):
        return (False, 0)

    GetCaseSelectedForOutput = GetComboSelectedForOutput


class _Modelo:
    def __init__(self, combos):
        self.RespCombo = _RespCombo(combos)
        self.LoadCases = type("L", (), {"GetNameList": lambda self: (1, ("D",), 0)})()
        self.Results = type("R", (), {"Setup": _Setup()})()
        self.DesignSteel = type("D", (), {"GetCode": lambda self, _n: ("AISC", 0)})()

    def GetModelFilename(self):
        return "x.sdb"

    def GetModelIsLocked(self):
        return True

    def GetPresentUnits(self):
        return 6


def test_foto_y_diferencias_detectan_combinacion_eliminada():
    modelo = _Modelo({"C1": 1, "DSTL1": 1})
    antes = sonda.foto(modelo)
    assert antes["combos"]["DSTL1"]["auto_diseno"] and not antes["combos"]["C1"]["auto_diseno"]
    del modelo.RespCombo.combos["C1"]
    dif = sonda.diferencias(antes, sonda.foto(modelo))
    assert dif["eliminadas"] == ["C1"]
    assert sonda.veredicto(dif, {})[0] == sonda.INESPERADO


def test_veredicto_sin_cambio_y_cambio_esperado():
    modelo = _Modelo({"C1": 1})
    f = sonda.foto(modelo)
    assert sonda.veredicto(sonda.diferencias(f, f), {})[0] == sonda.SIN_CAMBIO
    modelo.RespCombo.combos["ZZ_SONDA_A"] = 1
    d = sonda.diferencias(f, sonda.foto(modelo))
    assert sonda.veredicto(d, {})[0] == sonda.ESPERADO


def test_combinacion_de_la_sonda_eliminada_sin_esperarlo_es_inesperada():
    modelo = _Modelo({"ZZ_SONDA_A": 1})
    antes = sonda.foto(modelo)
    modelo.RespCombo.combos.clear()
    d = sonda.diferencias(antes, sonda.foto(modelo))
    assert sonda.veredicto(d, {})[0] == sonda.INESPERADO
    assert sonda.veredicto(d, {"zz_ok": True})[0] == sonda.ESPERADO


def test_informe_markdown_lista_los_inesperados():
    inf = sonda.Informe()
    inf.agregar(fase="L", id="t", veredicto=sonda.INESPERADO, nota="se borró C1", dif={})
    assert "se borró C1" in inf.a_markdown()


def test_causa_desenvuelve_el_grupo_de_excepciones():
    try:
        raise BaseExceptionGroup("x", [ValueError("falló k")])
    except BaseExceptionGroup as exc:
        texto = sonda._causa(exc)
    assert texto.startswith("ValueError: falló k")
