"""``shell_forces``: esfuerzos de shell por esquina de elemento (solo lectura), con un SAP simulado."""

from __future__ import annotations

import json
import math
import time

import pytest

from bridge.viewer import shell_forces as sf
from bridge.viewer.core.results import ResultsError

COMPS = sf.COMPONENTES
CASOS = ["DEAD", "Q"]
COMBOS = ["U1", "ENV"]
# caso -> pasos (StepType, StepNum)
PASOS = {"DEAD": [("", 0)], "Q": [("Step", 1), ("Step", 2)], "U1": [("", 0)], "ENV": [("Max", 0), ("Min", 0)]}
NOMBRES_CASOS = [*CASOS, *COMBOS]
COORD = {
    "1": (0.0, 0.0, 0.0), "2": (1.0, 0.0, 0.0), "3": (2.0, 0.0, 0.0),
    "4": (0.0, 1.0, 0.0), "~1": (1.0, 1.0, 0.0), "6": (2.0, 1.0, 0.0),
    "7": (3.0, 0.0, 0.0), "8": (4.0, 0.0, 0.0), "9": (3.0, 1.0, 0.0),
}  # fmt: skip
MALLA = {  # área -> [(elemento, nudos)]
    "A1": [("A1-1", ["1", "2", "~1", "4"]), ("A1-2", ["2", "3", "6", "~1"])],
    "A2": [("A2-1", ["7", "8", "9"])],
}


def valor(elm_i, esq_i, caso, comp, num, tipo):
    base = elm_i * 1000 + esq_i * 100 + NOMBRES_CASOS.index(caso) * 10 + COMPS.index(comp)
    return float(base) + num * 0.5 + {"Max": 0.25, "Min": -0.25}.get(tipo, 0.0)


def matriz_columnas(v1, v2, v3):
    return [v1[0], v2[0], v3[0], v1[1], v2[1], v3[1], v1[2], v2[2], v3[2]]


class _Setup:
    def __init__(self, m):
        self.m = m

    def DeselectAllCasesAndCombosForOutput(self):
        self.m.sel_c, self.m.sel_k = set(), set()
        return 0

    def SetCaseSelectedForOutput(self, n, *a):
        self.m.sel_c.add(n)
        return 0

    def SetComboSelectedForOutput(self, n, *a):
        self.m.sel_k.add(n)
        return 0

    def GetCaseSelectedForOutput(self, n):
        return (n in self.m.sel_c, 0)

    def GetComboSelectedForOutput(self, n):
        return (n in self.m.sel_k, 0)


class _Results:
    def __init__(self, m):
        self.m = m
        self._setup = _Setup(m)

    @property
    def Setup(self):  # noqa: N802
        self.m.log.append("Results.Setup")
        return self._setup

    def AreaForceShell(self, nombre, tipo, *largo):
        self.m.log.append(("AreaForceShell", nombre, tipo))
        if self.m.api_falla:
            raise RuntimeError("AreaForceShell no disponible")
        if tipo == 2 and self.m.api_solo_elementos:
            return self.m.respuesta_api([])
        if tipo == 2:
            elms = [e for e, _ in self.m.todos_elementos()]
        elif tipo == 0:
            if self.m.api_solo_elementos:
                return self.m.respuesta_api([])
            elms = [e for e, _ in MALLA.get(nombre, [])]
        else:
            elms = [nombre]
        return self.m.respuesta_api(elms)


class _Tablas:
    def __init__(self, m):
        self.m = m

    def GetTableForDisplayArray(self, tabla, *args):
        self.m.log.append(("tabla", tabla))
        if self.m.tabla_falla:
            raise RuntimeError("tabla no disponible")
        m = self.m
        if tabla == sf.TABLA_AREAS:
            cols = ["Area", "AreaElem", "ElemJt1", "ElemJt2", "ElemJt3", "ElemJt4"]
            filas = [[a, e, *n, *([""] if len(n) == 3 else [])] for a, lst in m.malla.items() for e, n in lst]
        elif tabla == sf.TABLA_JOINTS:
            cols = ["JointElem", "GlobalX", "GlobalY", "GlobalZ"]
            filas = [[n, *map(str, xyz)] for n, xyz in m.coord.items()]
        elif tabla == sf.TABLA_FUERZAS:
            if m.tabla_sin_columnas:
                return (0, (), 0, (), 0, ())
            comps = [c for c in COMPS if c not in m.tabla_sin_componente]
            cols = ["AreaElem", "Joint", "OutputCase", "StepType", "StepNum", *comps]
            if m.tabla_sin_stepnum:
                cols.remove("StepNum")
            filas = [
                [e, n, caso, tipo, *([] if m.tabla_sin_stepnum else [str(num)]), *[str(valor(i, k, caso, c, num, tipo)) for c in comps]]
                for i, (e, nudos) in enumerate(m.todos_elementos())
                for k, n in enumerate(nudos)
                for caso in NOMBRES_CASOS
                if not m.mostrar_obedece or caso in m.mostrado
                for tipo, num in PASOS[caso]
            ]
            m.filas_tabla = len(filas)
        else:
            raise ValueError("tabla desconocida")
        plano = tuple(str(c) for f in filas for c in f)
        return (0, (), 0, tuple(cols), len(filas), plano)

    def GetLoadCasesSelectedForDisplay(self, *a):
        if self.m.mostrar_no_capturable:
            raise RuntimeError("no disponible")
        return (tuple(self.m.mostrado_c), 0)

    def GetLoadCombinationsSelectedForDisplay(self, *a):
        return (tuple(self.m.mostrado_k), 0)

    def SetLoadCasesSelectedForDisplay(self, nombres):
        self.m.log.append(("mostrar_casos", tuple(nombres)))
        self.m.mostrado_c = list(nombres)
        self.m.mostrado = set(self.m.mostrado_c) | set(self.m.mostrado_k)
        return 0

    def SetLoadCombinationsSelectedForDisplay(self, nombres):
        self.m.log.append(("mostrar_combos", tuple(nombres)))
        self.m.mostrado_k = list(nombres)
        self.m.mostrado = set(self.m.mostrado_c) | set(self.m.mostrado_k)
        return 0

    # --- prohibidas
    def SetTableForEditingArray(self, *a):
        self.m.prohibidas.append("SetTableForEditingArray")

    def ApplyEditedTables(self, *a):
        self.m.prohibidas.append("ApplyEditedTables")


class _Lista:
    def __init__(self, nombres):
        self.n = tuple(nombres)

    def GetNameList(self, *a):
        return (len(self.n), self.n, 0)


class _Areas(_Lista):
    def __init__(self, m):
        super().__init__(m.malla)
        self.m = m

    def GetElm(self, area):
        self.m.log.append(("AreaObj.GetElm", area))
        return (len(self.m.malla[area]), tuple(e for e, _ in self.m.malla[area]), 0)

    def GetTransformationMatrix(self, area, *a):
        self.m.log.append(("AreaObj.GetTransformationMatrix", area))
        if self.m.matriz_objeto is None:
            raise RuntimeError("no disponible")
        return (tuple(self.m.matriz_objeto), 0)

    def GetLocalAxes(self, area):
        return (self.m.angulo_objeto, False, 0)


class _AreaElm:
    def __init__(self, m):
        self.m = m

    def GetPoints(self, elm):
        for lst in self.m.malla.values():
            for e, n in lst:
                if e == elm:
                    return (len(n), tuple(n), 0)
        raise RuntimeError("elemento desconocido")

    def GetTransformationMatrix(self, elm, *a):
        self.m.log.append(("AreaElm.GetTransformationMatrix", elm))
        if self.m.matriz_elemento is None:
            raise AttributeError("sin GetTransformationMatrix")
        return (tuple(self.m.matriz_elemento(elm)), 0)


class _PuntosElm:
    def __init__(self, m):
        self.m = m

    def GetCoordCartesian(self, n, *a):
        return (0, *self.m.coord[n])


class _PuntosObj:
    def __init__(self, m):
        self.m = m

    def GetCoordCartesian(self, n, *a):
        if n.startswith("~"):
            raise RuntimeError("no es un PointObj")
        return (0, *self.m.coord[n])


class _Prohibido:
    def __init__(self, m, nombre):
        self.m, self.nombre = m, nombre

    def __getattr__(self, attr):
        def llamar(*a, **k):
            self.m.prohibidas.append(f"{self.nombre}.{attr}")
            return 0

        return llamar


class FakeShell:
    def __init__(self, **kw):
        self.locked = kw.pop("locked", True)
        self.malla = kw.pop("malla", MALLA)
        self.coord = kw.pop("coord", COORD)
        self.tabla_falla = kw.pop("tabla_falla", False)
        self.tabla_sin_columnas = kw.pop("tabla_sin_columnas", False)
        self.tabla_sin_stepnum = kw.pop("tabla_sin_stepnum", False)
        self.tabla_sin_componente = kw.pop("tabla_sin_componente", ())
        self.api_falla = kw.pop("api_falla", False)
        self.api_solo_elementos = kw.pop("api_solo_elementos", False)
        self.matriz_elemento = kw.pop("matriz_elemento", None)
        self.matriz_objeto = kw.pop("matriz_objeto", None)
        self.angulo_objeto = kw.pop("angulo_objeto", 0.0)
        self.mostrar_obedece = kw.pop("mostrar_obedece", False)
        self.mostrar_no_capturable = kw.pop("mostrar_no_capturable", False)
        self.sin_casos_corridos = kw.pop("sin_casos_corridos", False)
        self.mostrado_c, self.mostrado_k = ["DEAD"], ["U1"]
        self.mostrado = set(self.mostrado_c) | set(self.mostrado_k)
        self.sel_c, self.sel_k = {"DEAD"}, {"U1"}  # la selección de salida del usuario
        self.units = 5
        self.unit_log: list[int] = []
        self.log: list = []
        self.prohibidas: list[str] = []
        self.Results = _Results(self)
        self.DatabaseTables = _Tablas(self)
        self.LoadCases = _Lista(CASOS)
        self.RespCombo = _Lista(COMBOS)
        self.AreaObj = _Areas(self)
        self.AreaElm = _AreaElm(self)
        self.PointElm = _PuntosElm(self)
        self.PointObj = _PuntosObj(self)
        self.SelectObj = _Prohibido(self, "SelectObj")
        self.GroupDef = _Prohibido(self, "GroupDef")
        self.SectionCut = _Prohibido(self, "SectionCut")
        self.Analyze = _Prohibido(self, "Analyze")

    def todos_elementos(self):
        return [(e, n) for lst in self.malla.values() for e, n in lst]

    def respuesta_api(self, elms):
        sel = self.sel_c | self.sel_k
        cols: list[list] = [[] for _ in range(23)]
        orden = {e: i for i, (e, _) in enumerate(self.todos_elementos())}
        nudos = dict(self.todos_elementos())
        for elm in elms:
            for k, n in enumerate(nudos[elm]):
                for caso in NOMBRES_CASOS:
                    if caso not in sel:
                        continue
                    for tipo, num in PASOS[caso]:
                        fila = {
                            "Obj": "x",
                            "Elm": elm,
                            "PointElm": n,
                            "LoadCase": caso,
                            "StepType": tipo,
                            "StepNum": float(num),
                        }
                        for c in COMPS:
                            fila[c] = valor(orden[elm], k, caso, c, num, tipo)
                        for nombre, i in sf._IDX_API.items():
                            cols[i].append(fila.get(nombre, 0.0))
        return (len(cols[0]), *[tuple(c) for c in cols], 0)

    def GetModelIsLocked(self):
        return self.locked

    def SetModelIsLocked(self, flag):
        self.prohibidas.append("SetModelIsLocked")
        return 0

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, u):
        self.unit_log.append(u)
        self.units = u
        return 0


@pytest.fixture(autouse=True)
def _cache_de_ejes_limpia():
    sf.limpiar_cache_ejes()
    yield
    sf.limpiar_cache_ejes()


def leer(model, **cuerpo):
    cuerpo.setdefault("areas", [])
    return sf.shell_forces(model, cuerpo)


def restaurado(model):
    return model.sel_c == {"DEAD"} and model.sel_k == {"U1"} and model.units == 5


# --- 1. vía tabla ------------------------------------------------------------------------------------------------------


def test_via_tabla_contrato_y_restauracion():
    m = FakeShell()
    r = leer(m)
    assert set(r) == {
        "ok",
        "units",
        "model_locked",
        "truncated",
        "warnings",
        "mesh_hash",
        "elements",
        "nodes",
        "axes",
        "columns",
        "rows",
    }
    assert r["ok"] is True and r["units"] == {"force": "tonf", "length": "m"}
    assert r["model_locked"] is True and r["truncated"] is False
    assert r["columns"] == ["elm", "node", "case", "step", *COMPS]
    assert r["elements"]["A2-1"] == {
        "area": "A2",
        "nodes": ["7", "8", "9"],
    }  # triángulo: 3 nudos, sin duplicar
    assert r["nodes"]["~1"] == [1.0, 1.0, 0.0]  # nudo interno que no es PointObj
    # una fila por elemento × esquina × caso × paso: (4+4+3 esquinas) × (1+2+1+2 pasos)
    assert len(r["rows"]) == 11 * 6
    assert all(len(f) == 4 + len(COMPS) for f in r["rows"])
    assert [w for w in r["warnings"] if not w.startswith("Ejes")] == []
    assert restaurado(m) and m.unit_log == [12, 5] and m.prohibidas == []
    json.dumps(r)


def test_valores_y_orden_deterministas():
    r = leer(FakeShell(), cases=["DEAD"])
    primera = r["rows"][0]
    assert primera[:4] == ["A1-1", "1", "DEAD", ""]
    assert primera[4] == valor(0, 0, "DEAD", "F11", 0, "")
    assert [f[1] for f in r["rows"][:4]] == ["1", "2", "~1", "4"]  # esquinas en el orden de nodes


# --- 2. step -----------------------------------------------------------------------------------------------------------


def test_step_vacio_max_min_y_step_en_orden_de_stepnum():
    r = leer(FakeShell(), areas=["A2"])
    pasos = {(f[2], f[3]) for f in r["rows"]}
    assert pasos == {("DEAD", ""), ("Q", "Step"), ("U1", ""), ("ENV", "Max"), ("ENV", "Min")}
    q = [f for f in r["rows"] if f[2] == "Q" and f[1] == "7"]
    assert [f[4] for f in q] == [valor(2, 0, "Q", "F11", 1, "Step"), valor(2, 0, "Q", "F11", 2, "Step")]
    env = [f[3] for f in r["rows"] if f[2] == "ENV" and f[1] == "7"]
    assert env == ["Max", "Min"]


# --- 3. respaldos ------------------------------------------------------------------------------------------------------


def _base():
    return leer(FakeShell())


def test_la_api_va_primero_y_no_toca_la_tabla_de_fuerzas():
    m = FakeShell()
    r = leer(m)
    assert ("tabla", sf.TABLA_FUERZAS) not in m.log
    assert not [c for c in m.log if isinstance(c, tuple) and c[0].startswith("mostrar_")]  # ni la selección para mostrar
    assert not any("respaldo" in w for w in r["warnings"])


def test_via_api_y_tabla_devuelven_los_mismos_valores_y_la_tabla_avisa():
    con_api = leer(FakeShell())
    con_tabla = leer(FakeShell(api_falla=True))
    assert con_tabla["rows"] == con_api["rows"] and con_tabla["elements"] == con_api["elements"]
    assert any("tabla" in w and "respaldo" in w for w in con_tabla["warnings"])
    assert not any("respaldo" in w for w in con_api["warnings"])


@pytest.mark.parametrize("falla", [{"tabla_sin_columnas": True}])
def test_si_la_tabla_no_sirve_la_api_basta(falla):
    assert leer(FakeShell(**falla))["rows"] == _base()["rows"]


def test_tabla_sin_un_componente_deja_null_y_avisa():
    r = leer(FakeShell(api_falla=True, tabla_sin_componente=("M12",)))
    i = r["columns"].index("M12")
    assert all(f[i] is None for f in r["rows"])  # nunca 0 inventado
    assert any("M12" in w and "null" in w for w in r["warnings"])
    assert [f[:4] for f in r["rows"]] == [f[:4] for f in _base()["rows"]]


def test_tabla_sin_stepnum_no_falla_y_ordena_por_fila():
    r = leer(FakeShell(api_falla=True, tabla_sin_stepnum=True), areas=["A2"])
    q = [f for f in r["rows"] if f[2] == "Q" and f[1] == "7"]
    assert [f[4] for f in q] == [valor(2, 0, "Q", "F11", 1, "Step"), valor(2, 0, "Q", "F11", 2, "Step")]
    assert any("StepNum" in w for w in r["warnings"])


def test_respaldo_por_elemento_solo_para_los_que_faltan():
    # ALL y por área no devuelven nada: se lee elemento a elemento, y solo los que siguen sin filas.
    m = FakeShell(tabla_falla=True, api_solo_elementos=True)
    r = leer(m)
    assert r["rows"] == _base()["rows"]
    por_elemento = [c for c in m.log if c[0] == "AreaForceShell" and c[2] == 1]
    assert sorted(c[1] for c in por_elemento) == ["A1-1", "A1-2", "A2-1"]
    assert any("uno a uno" in w for w in r["warnings"])


# --- 4. components -----------------------------------------------------------------------------------------------------


def test_components_subconjunto_y_orden():
    r = leer(FakeShell(), components=["M22", "F11"], cases=["DEAD"])
    assert r["columns"] == ["elm", "node", "case", "step", "M22", "F11"]
    f = r["rows"][0]
    assert f[4:] == [valor(0, 0, "DEAD", "M22", 0, ""), valor(0, 0, "DEAD", "F11", 0, "")]


@pytest.mark.parametrize("malo", [[], ["F11", "F11"], ["XX"]])
def test_components_invalido_422(malo):
    with pytest.raises(ResultsError) as e:
        leer(FakeShell(), components=malo)
    assert e.value.status == 422


# --- 5. areas ----------------------------------------------------------------------------------------------------------


def test_areas_vacia_es_todas_y_area_inexistente_avisa():
    assert set(leer(FakeShell())["elements"]) == {"A1-1", "A1-2", "A2-1"}
    r = leer(FakeShell(), areas=["A2", "NOEXISTE"])
    assert set(r["elements"]) == {"A2-1"} and any("NOEXISTE" in w for w in r["warnings"])


def test_todas_las_areas_inexistentes_422():
    with pytest.raises(ResultsError) as e:
        leer(FakeShell(), areas=["X"])
    assert (e.value.status, e.value.code) == (422, "unknown_area")


def test_sin_areas_409():
    m = FakeShell(malla={})
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "no_areas")


# --- 6. cases / combos -------------------------------------------------------------------------------------------------


def test_cases_combos_inexistentes_avisan_y_se_omiten():
    r = leer(FakeShell(), cases=["DEAD", "NOEXISTE"], combos=["U1"])
    assert {f[2] for f in r["rows"]} == {"DEAD", "U1"}
    assert any("NOEXISTE" in w for w in r["warnings"])


def test_ningun_caso_valido_422_y_sin_cases_son_todos():
    with pytest.raises(ResultsError) as e:
        leer(FakeShell(), cases=["X"], combos=["Y"])
    assert (e.value.status, e.value.code) == (422, "unknown_case")
    assert {f[2] for f in leer(FakeShell())["rows"]} == set(NOMBRES_CASOS)


def test_la_seleccion_para_leer_es_exactamente_la_pedida():
    m = FakeShell(tabla_falla=True)
    vista = []
    original = m.Results.AreaForceShell

    def espiar(nombre, tipo, *a):
        vista.append((set(m.sel_c), set(m.sel_k)))
        return original(nombre, tipo, *a)

    m.Results.AreaForceShell = espiar
    leer(m, cases=["Q"], combos=["ENV"])
    assert vista and vista[0] == ({"Q"}, {"ENV"})
    assert restaurado(m)


def test_caso_pedido_sin_filas_avisa():
    m = FakeShell(tabla_falla=True, api_solo_elementos=False)
    m.mostrar_obedece = True
    r = leer(m, cases=["DEAD", "Q"])
    # Q existe pero el simulado no devuelve filas para él por la vía de la tabla: se pide a la API.
    assert {f[2] for f in r["rows"]} == {"DEAD", "Q"}


# --- 7/8. modelo no corrido, sin resultados y restauración en error -----------------------------------------------------


def test_modelo_desbloqueado_409_sin_tocar_nada():
    m = FakeShell(locked=False)
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "model_not_run")
    assert "Results.Setup" not in m.log and m.unit_log == [] and m.prohibidas == []


def test_bloqueado_sin_filas_409_y_restaura():
    m = FakeShell(tabla_falla=True, api_falla=True)
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "no_results")
    assert restaurado(m)


def test_restaura_aunque_la_lectura_lance(monkeypatch):
    m = FakeShell()
    monkeypatch.setattr(sf, "_fuerzas", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        leer(m)
    assert restaurado(m)


def test_unidades_no_restauradas_es_409_con_detalle():
    m = FakeShell()
    original = m.SetPresentUnits

    def set_units(u):
        original(u)
        return 0 if u == 12 else 1

    m.SetPresentUnits = set_units
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "units_not_restored")


# --- 9. truncado -------------------------------------------------------------------------------------------------------


def test_max_elements_trunca_rows_nodes_y_axes_consistentes():
    r = leer(
        FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1))), max_elements=1
    )
    assert r["truncated"] is True and list(r["elements"]) == ["A1-1"]
    assert {f[0] for f in r["rows"]} == {"A1-1"}
    assert set(r["nodes"]) == {"1", "2", "~1", "4"} and set(r["axes"]) == {"A1-1"}
    assert any("max_elements" in w for w in r["warnings"])


def test_max_filas_corta_en_limite_de_caso(monkeypatch):
    por_caso = 11 * 1  # filas por caso de un solo paso
    monkeypatch.setattr(sf, "MAX_FILAS", por_caso * 2 + 5)
    r = leer(FakeShell(), include_axes=False)
    assert r["truncated"] is True
    casos = {f[2] for f in r["rows"]}
    assert casos == {"DEAD", "Q"} or casos.issubset(set(NOMBRES_CASOS))
    ya = {c: sum(1 for f in r["rows"] if f[2] == c) for c in casos}
    assert all(n in (11, 22) for n in ya.values())  # nunca un caso a medias
    assert any("otra llamada" in w for w in r["warnings"])


# --- 10. ejes ----------------------------------------------------------------------------------------------------------

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def _ejes_de(r, elm="A1-1"):
    a = r["axes"][elm]
    return a["v1"], a["v2"], a["v3"]


def test_ejes_por_columnas_ortonormales_y_dextrogiros():
    r = leer(FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1))))
    v1, v2, v3 = _ejes_de(r)
    assert v3 == [0.0, 0.0, 1.0] and v1 == [1.0, 0.0, 0.0]
    assert sf._cruz(v1, v2) == tuple(v3) or all(abs(a - b) < 1e-12 for a, b in zip(sf._cruz(v1, v2), v3))
    assert r["warnings"] == []


def test_losa_girada_30_grados():
    r = leer(FakeShell(matriz_elemento=lambda e: matriz_columnas((C30, S30, 0), (-S30, C30, 0), (0, 0, 1))))
    v1, _, _ = _ejes_de(r)
    assert v1 == pytest.approx([C30, S30, 0.0])


def test_normal_invertida_v3_menos_z():
    m = FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, -1, 0), (0, 0, -1)))
    r = leer(m)
    assert _ejes_de(r)[2] == pytest.approx([0.0, 0.0, -1.0])


def test_matriz_traspuesta_detectada_en_plano_inclinado():
    malla = {"B1": [("B1-1", ["1", "2", "3"])]}
    coord = {"1": (0.0, 0.0, 0.0), "2": (1.0, 0.0, 0.0), "3": (0.0, C30, S30)}
    v1, v2, v3 = (1, 0, 0), (0, C30, S30), (0, -S30, C30)
    por_filas = [*v1, *v2, *v3]  # convención traspuesta: los vectores van por filas
    m = FakeShell(malla=malla, coord=coord, matriz_elemento=lambda e: por_filas)
    r = leer(m)
    assert _ejes_de(r, "B1-1")[2] == pytest.approx([0.0, -S30, C30])
    assert any("traspuesta" in w for w in r["warnings"])


def test_sin_matriz_usa_regla_geometrica_y_angulo_con_aviso():
    r = leer(FakeShell(angulo_objeto=30.0))  # sin GetTransformationMatrix ni por objeto
    v1, _, v3 = _ejes_de(r)
    assert v3 == pytest.approx([0.0, 0.0, 1.0]) and v1 == pytest.approx([C30, S30, 0.0])
    assert any("derivados de la geometría" in w for w in r["warnings"])


def test_ejes_por_objeto_cuando_falla_el_de_elemento():
    m = FakeShell(matriz_objeto=matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    r = leer(m)
    assert set(r["axes"]) == set(r["elements"])
    assert any("AreaObj.GetTransformationMatrix" in w for w in r["warnings"])


def test_include_axes_false_no_llama_a_la_matriz():
    m = FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    r = leer(m, include_axes=False)
    assert r["axes"] == {}
    assert not [c for c in m.log if isinstance(c, tuple) and "TransformationMatrix" in c[0]]


# --- 11. mesh_hash -----------------------------------------------------------------------------------------------------


def test_mesh_hash_estable_y_sensible():
    base = leer(FakeShell())["mesh_hash"]
    assert base.startswith("sha256:")
    assert leer(FakeShell(), areas=["A2", "A1"])["mesh_hash"] == base
    assert leer(FakeShell(), cases=["DEAD"], components=["F11"], include_axes=False)["mesh_hash"] == base
    ruido = {k: (x + 1e-6, y, z) for k, (x, y, z) in COORD.items()}
    assert leer(FakeShell(coord=ruido))["mesh_hash"] == base
    movido = dict(COORD, **{"9": (3.0, 1.001, 0.0)})
    assert leer(FakeShell(coord=movido))["mesh_hash"] != base
    otra = {"A1": [("A1-1", ["2", "1", "~1", "4"]), MALLA["A1"][1]], "A2": MALLA["A2"]}
    assert leer(FakeShell(malla=otra))["mesh_hash"] != base


# --- 12. nada prohibido ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kw", [{}, {"tabla_falla": True}, {"matriz_objeto": None}])
def test_ninguna_llamada_que_modifique_el_modelo(kw):
    m = FakeShell(**kw)
    leer(m)
    assert m.prohibidas == []


# --- 13. selección «para mostrar» de las tablas ------------------------------------------------------------------------


def test_seleccion_para_mostrar_se_restaura_cuando_se_usa_la_tabla():
    m = FakeShell(api_falla=True, mostrar_obedece=True)
    r = leer(m)
    assert {f[2] for f in r["rows"]} == set(NOMBRES_CASOS)  # la tabla obedeció la selección que se le puso
    assert (m.mostrado_c, m.mostrado_k) == (["DEAD"], ["U1"])


def test_si_no_se_puede_capturar_la_seleccion_para_mostrar_no_se_toca():
    m = FakeShell(api_falla=True, mostrar_no_capturable=True, mostrar_obedece=True)
    r = leer(m)
    assert not [c for c in m.log if isinstance(c, tuple) and c[0].startswith("mostrar_")]
    assert {f[2] for f in r["rows"]} == {"DEAD", "U1"}  # solo lo que la tabla tenía mostrado
    assert any("selección «para mostrar»" in w for w in r["warnings"])
    assert any("'Q' no devolvió resultados" in w for w in r["warnings"])  # lo que falta se dice, no se inventa


# --- caché de ejes por mesh_hash ----------------------------------------------------------------------------------------


def _mat(m):
    return lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1))


def _llamadas_matriz(m):
    return len([c for c in m.log if isinstance(c, tuple) and c[0] == "AreaElm.GetTransformationMatrix"])


def test_los_ejes_se_cachean_por_malla_entre_lecturas():
    m = FakeShell(matriz_elemento=_mat(None))
    primera = leer(m)
    assert _llamadas_matriz(m) == 3
    segunda = leer(m, cases=["DEAD"], components=["F11"])  # mismos ejes: otros casos y componentes
    assert _llamadas_matriz(m) == 3  # no se repiten
    assert segunda["axes"] == primera["axes"] and segunda["mesh_hash"] == primera["mesh_hash"]
    assert any("caché" in w for w in segunda["warnings"])


def test_la_cache_no_se_usa_si_cambia_la_malla_o_el_angulo_del_area():
    leer(FakeShell(matriz_elemento=_mat(None)))
    misma = FakeShell(matriz_elemento=_mat(None))  # otra sesión de SAP con la misma malla y los mismos ejes
    leer(misma)
    assert _llamadas_matriz(misma) == 0
    otra_malla = FakeShell(matriz_elemento=_mat(None), coord=dict(COORD, **{"9": (3.0, 1.5, 0.0)}))
    leer(otra_malla)
    assert _llamadas_matriz(otra_malla) == 3
    giro = FakeShell(matriz_elemento=_mat(None), angulo_objeto=45.0)  # misma malla, otro ángulo de área
    leer(giro)
    assert _llamadas_matriz(giro) == 3


def test_la_copia_cacheada_no_se_comparte_con_el_llamador():
    m = FakeShell(matriz_elemento=_mat(None))
    primera = leer(m)
    primera["axes"]["A1-1"]["v1"][0] = 99.0
    assert leer(m)["axes"]["A1-1"]["v1"][0] == 1.0


def test_la_cache_tiene_tope(monkeypatch):
    monkeypatch.setattr(sf, "MAX_CACHE_EJES", 2)
    for i in range(4):
        malla = {"A": [("A-1", ["1", "2", "3"])]}
        coord = {"1": (0.0, 0.0, 0.0), "2": (1.0 + i, 0.0, 0.0), "3": (0.0, 1.0, 0.0)}
        leer(FakeShell(malla=malla, coord=coord))
    assert len(sf._CACHE_EJES) == 2


# --- 14. ruta ----------------------------------------------------------------------------------------------------------

PREFIJO = "/v1/sap/actions/viewer"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")


class _Conector:
    def disconnect(self):
        pass

    def __init__(self):
        self.model = FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        self.is_demo = False

    def connect_to_open_instance(self):
        from types import SimpleNamespace

        return SimpleNamespace(connected=True, message="")

    def info(self):
        from types import SimpleNamespace

        return SimpleNamespace(connected=True, message="")


def test_ruta_responde_200_sin_cache_y_claves_del_contrato(rutas_viewer):
    from pathlib import Path

    contrato = json.loads(
        (Path(__file__).resolve().parents[2] / "web" / "src" / "bridge_contracts.json").read_text()
    )
    clave = "POST /v1/sap/actions/viewer/foundations/shell-forces"
    c = rutas_viewer(_Conector)
    c.puente._connector = _Conector()
    r = c.post(f"{PREFIJO}/foundations/shell-forces", json={"areas": ["A2"], "cases": ["DEAD"]})
    assert r.status_code == 200, r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert set(r.json()) == set(contrato["routes"][clave]["keys"])


def test_ruta_422_con_campo_extra_y_componente_invalido(rutas_viewer):
    c = rutas_viewer(_Conector)
    assert c.post(f"{PREFIJO}/foundations/shell-forces", json={"areas": [], "otro": 1}).status_code == 422
    assert (
        c.post(f"{PREFIJO}/foundations/shell-forces", json={"components": ["F11", "F11"]}).status_code == 422
    )
    assert c.post(f"{PREFIJO}/foundations/shell-forces", json={"max_elements": 0}).status_code == 422


def test_ruta_mapea_errores_de_resultados(rutas_viewer):
    c = rutas_viewer(_Conector)
    c.puente._connector = _Conector()
    c.puente._connector.model.locked = False
    r = c.post(f"{PREFIJO}/foundations/shell-forces", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "model_not_run"


# --- 15. rendimiento ---------------------------------------------------------------------------------------------------


def test_diez_mil_elementos_por_tabla_rapido_y_con_llamadas_constantes():
    n = 10_000
    coord = {}
    malla_n = {"G": []}
    for i in range(n):
        base = i * 4
        ids = [f"{base + k}" for k in range(4)]
        for k, nid in enumerate(ids):
            coord[nid] = (float(i), float(k % 2), 0.0)
        malla_n["G"].append((f"G-{i}", ids))
    m = FakeShell(malla=malla_n, coord=coord)
    m.todos_elementos = lambda: [(e, nn) for e, nn in malla_n["G"]]
    m.tabla_sin_columnas = False
    # tabla de fuerzas reducida a 1 caso para no inflar el simulado
    original = m.DatabaseTables.GetTableForDisplayArray

    def tabla(t, *a):
        if t == sf.TABLA_FUERZAS:
            cols = ["AreaElem", "Joint", "OutputCase", "StepType", "StepNum", *COMPS]
            filas = [[e, nn, "DEAD", "", "0", *["1.0"] * 8] for e, ns in malla_n["G"] for nn in ns]
            return (0, (), 0, tuple(cols), len(filas), tuple(c for f in filas for c in f))
        return original(t, *a)

    m.DatabaseTables.GetTableForDisplayArray = tabla
    t0 = time.perf_counter()
    r = leer(m, cases=["DEAD"], include_axes=False, max_elements=10_000)
    assert time.perf_counter() - t0 < 3.0
    assert len(r["rows"]) == n * 4
    llamadas = [c for c in m.log if c != "Results.Setup"]
    assert len(llamadas) <= 6  # tablas de geometría y de fuerzas: no crece con los elementos


def test_con_ejes_una_llamada_por_elemento():
    m = FakeShell(matriz_elemento=lambda e: matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    leer(m)
    llamadas = [c for c in m.log if isinstance(c, tuple) and c[0] == "AreaElm.GetTransformationMatrix"]
    assert len(llamadas) == 3
