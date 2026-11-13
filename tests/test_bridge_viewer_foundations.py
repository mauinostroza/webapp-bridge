"""Fundaciones en el visor: resultados por grupos sin dejar cambiada la selección de salida."""

from __future__ import annotations

import pytest

from bridge.viewer import foundations as fund


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")


class _Setup:
    def __init__(self, outer):
        self.o = outer

    def DeselectAllCasesAndCombosForOutput(self):
        self.o.sel_c, self.o.sel_k = set(), set()
        return 0

    def SetCaseSelectedForOutput(self, n, *a):
        self.o.sel_c.add(n)
        return 0

    def SetComboSelectedForOutput(self, n, *a):
        self.o.sel_k.add(n)
        return 0

    def GetCaseSelectedForOutput(self, n):
        return (n in self.o.sel_c, 0)

    def GetComboSelectedForOutput(self, n):
        return (n in self.o.sel_k, 0)


def _fila(nombre, n=1):
    col = lambda v: tuple([v] * n)  # noqa: E731
    return (n, col(nombre), col(nombre), col("DEAD"), col("Max"), col(2.0), *[col(1.0)] * 6, 0)


class _Results:
    def __init__(self, outer):
        self.Setup = _Setup(outer)
        self.o = outer

    def JointReact(self, nombre, _t):
        return _fila(nombre, self.o.filas)

    def JointDispl(self, nombre, _t):
        return _fila(nombre, self.o.filas)


class _Lista:
    def __init__(self, nombres):
        self.nombres = tuple(nombres)

    def GetNameList(self, *a):
        return (len(self.nombres), self.nombres, 0)


class _Puntos(_Lista):
    def GetCoordCartesian(self, n, *a):
        return (0, float(n), 0.0, 0.0)


class _Areas(_Lista):
    def GetPoints(self, nombre, *a):
        if nombre not in {"A1", "A9"}:
            raise RuntimeError("área desconocida")
        return (3, ("1", "2", "3"), 0)


class _Tablas:
    @staticmethod
    def GetTableForDisplayArray(*args):
        return (0, (), 0, ("Area", "OutputCase", "Pressure"), 2, ("A1", "DEAD", "-2.5", "A9", "DEAD", "-1"))


class FakeFund:
    def __init__(self, filas=1):
        self.filas = filas
        self.sel_c, self.sel_k = {"USER"}, {"U9"}
        self.units = 5
        self.Results = _Results(self)
        self.LoadCases = _Lista(("DEAD", "USER"))
        self.RespCombo = _Lista(("U1", "U9"))
        self.PointObj = _Puntos(("1", "2", "3"))
        self.DatabaseTables = _Tablas()
        self.AreaObj = _Areas(("A1", "A9"))

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, v):
        self.units = v
        return 0

    def GetModelFilename(self):
        return r"C:\modelos\zapatas.sdb"

    def GetModelIsLocked(self):
        return False


GRUPOS = [
    {"name": "F1", "joints": ["1", "2"], "shells": ["A1"]},
    {"name": "F2", "joints": ["2"], "shells": []},
]


def test_resultados_acota_a_los_grupos_y_restaura_la_seleccion():
    model = FakeFund()
    res = fund.resultados(model, GRUPOS, ["DEAD"], ["U1"])
    assert res["units"] == "Ton_m_C"
    assert res["model"] == {"name": "zapatas.sdb", "path": r"C:\modelos\zapatas.sdb"}
    assert [c["joint"] for c in res["coordinates"]] == ["1", "2"]  # unión sin repetir
    assert {r["joint"] for r in res["reactions"]} == {"1", "2"}
    assert (res["reactions"][0]["step_type"], res["reactions"][0]["step_num"]) == ("Max", 2.0)
    assert [p["shell"] for p in res["soil_pressures"]] == ["A1"]  # A9 no es de los grupos
    assert res["truncated"] is False and res["model_locked"] is False
    assert res["groups"][0] == {"id": "F1", "name": "F1", "nodes": ["1", "2"], "shells": ["A1"]}
    assert (model.sel_c, model.sel_k) == ({"USER"}, {"U9"})  # selección del usuario restaurada
    assert model.units == 5  # unidades restauradas


def test_resultados_restaura_la_seleccion_aunque_falle_la_lectura():
    model = FakeFund()
    model.Results.JointReact = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        fund.resultados(model, GRUPOS, ["DEAD"], None)
    assert (model.sel_c, model.sel_k) == ({"USER"}, {"U9"})


def test_resultados_trunca_a_50000_filas(monkeypatch):
    monkeypatch.setattr(fund, "MAX_FILAS", 3)
    res = fund.resultados(FakeFund(filas=2), GRUPOS, ["DEAD"], None)  # 2 nudos x 2 filas = 4 > 3
    assert res["truncated"] is True
    assert len(res["reactions"]) == 3 and len(res["displacements"]) == 3


def test_sin_shells_no_se_lee_la_tabla_de_presiones():
    res = fund.resultados(FakeFund(), [{"name": "F", "joints": ["1"], "shells": []}], ["DEAD"], None)
    assert res["soil_pressures"] == []


def test_resultados_trae_los_nudos_de_cada_area_y_los_nombres_que_faltan():
    grupos = [{"name": "F1", "joints": ["1", "99"], "shells": ["A1", "A404"]}]
    res = fund.resultados(FakeFund(), grupos, ["DEAD"], None)
    assert res["areas"] == [{"name": "A1", "nodes": ["1", "2", "3"]}]
    assert res["faltantes"] == {"joints": ["99"], "shells": ["A404"]}
    assert any(w.startswith("Área A404:") for w in res["warnings"])


def test_resultados_sin_lista_de_nombres_omite_faltantes_con_aviso():
    model = FakeFund()
    model.AreaObj.GetNameList = lambda *a: object()
    res = fund.resultados(model, [{"name": "F", "joints": ["1"], "shells": ["A1"]}], ["DEAD"], None)
    assert "faltantes" not in res and res["areas"][0]["name"] == "A1"
    assert any("no se confirman los nombres" in w for w in res["warnings"])
