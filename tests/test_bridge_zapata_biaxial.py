"""Reacciones, coordenadas y sonda de zapatas biaxiales con SAP2000 simulado."""

from __future__ import annotations

import pytest

from bridge import zapata_biaxial as zb


class _Setup:
    def __init__(self, outer):
        self.o = outer

    def DeselectAllCasesAndCombosForOutput(self):
        self.o.sel_c, self.o.sel_k = set(), set()
        return 0

    def SetCaseSelectedForOutput(self, n):
        self.o.sel_c.add(n)
        return 0

    def SetComboSelectedForOutput(self, n):
        self.o.sel_k.add(n)
        return 0

    def GetCaseSelectedForOutput(self, n):
        return (n in self.o.sel_c, 0)

    def GetComboSelectedForOutput(self, n):
        return (n in self.o.sel_k, 0)


class _Results:
    def __init__(self, outer):
        self.o = outer
        self.Setup = _Setup(outer)

    def JointReact(self, nodo, _tipo):
        filas = [(c, "", 1.0) for c in sorted(self.o.sel_c)] + [
            (k, "Max", 2.0) for k in sorted(self.o.sel_k)
        ]
        n = len(filas)
        col = lambda f: tuple(f(x) for x in filas)  # noqa: E731
        return (
            n, (nodo,) * n, ("",) * n, col(lambda x: x[0]), col(lambda x: x[1]), col(lambda x: x[2]),
            (1.0,) * n, (2.0,) * n, (3.0,) * n, (4.0,) * n, (5.0,) * n, (6.0,) * n, 0,
        )  # fmt: skip


class _Lista:
    def __init__(self, nombres):
        self.nombres = tuple(nombres)

    def GetNameList(self, *a):
        return (len(self.nombres), self.nombres, 0)


class _Casos(_Lista):
    def GetTypeOAPI_1(self, nombre):
        return (4 if nombre == "SX" else 1, 0, 0)


class _Puntos(_Lista):
    def __init__(self, giro):
        super().__init__(("1", "2"))
        self.giro = giro

    def GetCoordCartesian(self, n, *a):
        return (float(n), 0.0, 10.0 + 0.5 * int(n), 0)

    def GetLocalAxes(self, n):
        return (30.0 if n in self.giro else 0.0, 0.0, 0.0, False, 0)


class FakeZapata:
    def __init__(self, giro=()):
        self.sel_c, self.sel_k = {"DEAD"}, {"U9"}
        self.units = 6
        self.Results = _Results(self)
        self.LoadCases = _Casos(("DEAD", "SX"))
        self.RespCombo = _Lista(("U1", "U9"))
        self.PointObj = _Puntos(giro)

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, v):
        self.units = v
        return 0

    def GetModelFilename(self):
        return "m.sdb"


def test_reacciones_agrega_stepnum_al_final_tipo_real_y_restaura_seleccion():
    model = FakeZapata()
    res = zb.reacciones(model, ["1"], ["DEAD", "SX"], ["U1"])
    assert res["columns"][-1] == "StepNum"
    assert res["columns"][:10] == zb.COLUMNAS_REACCIONES[:10]
    tipos = {f[1]: f[2] for f in res["data"]}
    assert tipos == {"DEAD": "LinStatic", "SX": "LinRespSpec", "U1": "Combination"}
    assert {f[1]: f[10] for f in res["data"]} == {"DEAD": 1.0, "SX": 1.0, "U1": 2.0}
    assert all(len(f) == 11 for f in res["data"])
    assert (model.sel_c, model.sel_k) == ({"DEAD"}, {"U9"})  # selección del usuario restaurada
    assert res["avisos"] == []


def test_reacciones_avisa_de_nodos_con_ejes_locales_girados():
    res = zb.reacciones(FakeZapata(giro=("2",)), ["1", "2"], ["DEAD"], [])
    assert res["nodos_ejes_locales"] == ["2"]
    assert any("ejes locales girados" in a for a in res["avisos"])


def test_coordenadas_de_nodos_concretos_y_dispersion_de_cotas():
    res = zb.coordenadas(FakeZapata(), ["1", "2"])
    assert res["data"] == [["1", 1.0, 0.0, 10.5], ["2", 2.0, 0.0, 11.0]]
    assert res["dispersion_z"] == pytest.approx(0.5)
    with pytest.raises(zb.ZapataBiaxialError, match="no existen"):
        zb.coordenadas(FakeZapata(), ["99"])


def test_sonda_devuelve_forma_cruda_y_restaura_seleccion():
    model = FakeZapata()
    res = zb.sonda_joint_react(model, "1", None, "U1")
    assert res["n_salidas"] == 13
    assert res["salidas"][4] == ["Max"]
    assert model.sel_k == {"U9"}
