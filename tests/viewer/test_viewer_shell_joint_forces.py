"""``shell_joint_forces``: fuerzas de elemento en sus nudos (``AreaJointForceShell``), solo lectura, con un SAP simulado."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from bridge.viewer import shell_forces as sf
from bridge.viewer import shell_joint_forces as sjf
from bridge.viewer.core.results import ResultsError
from tests.viewer.test_viewer_shell_forces import (
    COORD,
    MALLA,
    NOMBRES_CASOS,
    PASOS,
    FakeShell,
    _Conector,
    matriz_columnas,
    restaurado,
)

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))
COMP = ("F1", "F2", "F3", "M1", "M2", "M3")


def valor(elm_i, esq_i, caso, comp, num, tipo):
    base = elm_i * 1000 + esq_i * 100 + NOMBRES_CASOS.index(caso) * 10 + COMP.index(comp) + 1
    return float(base) + num * 0.5 + {"Max": 0.25, "Min": -0.25}.get(tipo, 0.0)


class FakeJoint(FakeShell):
    """``FakeShell`` con ``Results.AreaJointForceShell`` (12 arreglos tras el código de retorno y el conteo)."""

    def __init__(self, **kw):
        self.joint_api_falla = kw.pop("joint_api_falla", False)
        self.joint_solo_elementos = kw.pop("joint_solo_elementos", False)
        self.joint_all_vacio = kw.pop("joint_all_vacio", False)
        self.joint_sin_valor = kw.pop("joint_sin_valor", None)  # (comp) que sale no finito
        super().__init__(**kw)
        self.Results.AreaJointForceShell = self._area_joint

    def _area_joint(self, nombre, tipo, *largo):
        self.log.append(("AreaJointForceShell", nombre, tipo))
        if self.joint_api_falla:
            raise RuntimeError("AreaJointForceShell no disponible")
        if tipo == 2 and self.joint_all_vacio:
            return (0, *[()] * 12, 0)
        if tipo in (0, 2) and self.joint_solo_elementos:
            return (0, *[()] * 12, 0)
        if tipo == 2:
            elms = [e for e, _ in self.todos_elementos()]
        elif tipo == 0:
            elms = [e for e, _ in self.malla.get(nombre, [])]
        else:
            elms = [nombre]
        sel = self.sel_c | self.sel_k
        orden = {e: i for i, (e, _) in enumerate(self.todos_elementos())}
        nudos = dict(self.todos_elementos())
        cols: list[list] = [[] for _ in range(12)]
        for elm in elms:
            for k, n in enumerate(nudos[elm]):
                for caso in NOMBRES_CASOS:
                    if caso not in sel:
                        continue
                    for tipo_p, num in PASOS[caso]:
                        fila = ["x", elm, n, caso, tipo_p, float(num)]
                        for c in COMP:
                            v = valor(orden[elm], k, caso, c, num, tipo_p)
                            fila.append(float("nan") if c == self.joint_sin_valor else v)
                        for i, v in enumerate(fila):
                            cols[i].append(v)
        n = len(cols[0])
        return (0, n, *[tuple(c) for c in cols])  # el código de retorno va primero (pywin32)


def leer(model, **cuerpo):
    cuerpo.setdefault("areas", [])
    return sjf.shell_joint_forces(model, cuerpo)


@pytest.fixture(autouse=True)
def _limpia():
    sf.limpiar_cache_ejes()
    yield
    sf.limpiar_cache_ejes()


def _identidad(_e):
    return matriz_columnas((1, 0, 0), (0, 1, 0), (0, 0, 1))


# --- contrato -------------------------------------------------------------------------------------------------------------


def test_contrato_y_restauracion():
    m = FakeJoint(matriz_elemento=_identidad)
    r = leer(m)
    assert set(r) == {
        "ok", "units", "model_locked", "truncated", "warnings", "mesh_hash", "forces_frame",
        "elements", "nodes", "axes", "columns", "rows",
    }  # fmt: skip
    assert r["ok"] is True and r["units"] == {"force": "tonf", "length": "m"} and r["model_locked"] is True
    assert r["forces_frame"] == "global"
    assert r["columns"] == ["elm", "node", "case", "step", *COMP]
    assert r["axes"] == {}  # include_axes es false por defecto
    assert r["elements"]["A2-1"]["nodes"] == ["7", "8", "9"]  # triángulo con 3 nudos
    assert len(r["rows"]) == 11 * 6  # elemento × esquina × caso × paso
    assert restaurado(m) and m.unit_log == [12, 5] and m.prohibidas == []
    assert r["mesh_hash"] == sf.mesh_hash(r["elements"], r["nodes"])
    json.dumps(r)


def test_pasos_vacio_max_min_y_step_en_orden():
    r = leer(FakeJoint(matriz_elemento=_identidad), areas=["A2"])
    assert {(f[2], f[3]) for f in r["rows"]} == {("DEAD", ""), ("Q", "Step"), ("U1", ""), ("ENV", "Max"), ("ENV", "Min")}
    q = [f for f in r["rows"] if f[2] == "Q" and f[1] == "7"]
    assert [f[4] for f in q] == [valor(2, 0, "Q", "F1", 1, "Step"), valor(2, 0, "Q", "F1", 2, "Step")]


def test_usa_la_api_de_nudos_y_no_la_de_esfuerzos_ni_tablas_de_fuerzas():
    m = FakeJoint(matriz_elemento=_identidad)
    leer(m)
    assert ("AreaJointForceShell", "ALL", 2) in m.log
    assert not [c for c in m.log if isinstance(c, tuple) and c[0] == "AreaForceShell"]
    assert ("tabla", sf.TABLA_FUERZAS) not in m.log


# --- ejes de los valores ---------------------------------------------------------------------------------------------------


def test_frame_element_devuelve_los_valores_tal_como_los_entrega_sap():
    r = leer(FakeJoint(), frame="element", cases=["DEAD"])
    assert r["forces_frame"] == "element"
    f = r["rows"][0]
    assert f[4:] == [valor(0, 0, "DEAD", c, 0, "") for c in COMP]


def test_frame_global_con_ejes_iguales_a_los_globales_no_cambia_nada():
    glob = leer(FakeJoint(matriz_elemento=_identidad), cases=["DEAD"])
    elem = leer(FakeJoint(), frame="element", cases=["DEAD"])
    assert glob["rows"] == elem["rows"]


def test_frame_global_rota_fuerzas_y_momentos_con_los_ejes_del_elemento():
    mat = lambda e: matriz_columnas((C30, S30, 0), (-S30, C30, 0), (0, 0, 1))  # noqa: E731
    glob = leer(FakeJoint(matriz_elemento=mat), cases=["DEAD"], areas=["A1"])
    elem = leer(FakeJoint(), frame="element", cases=["DEAD"], areas=["A1"])
    for g, e in zip(glob["rows"], elem["rows"], strict=True):
        f1, f2, f3, m1, m2, m3 = e[4:]
        assert g[4:] == pytest.approx([C30 * f1 - S30 * f2, S30 * f1 + C30 * f2, f3, C30 * m1 - S30 * m2, S30 * m1 + C30 * m2, m3])
    assert any("ejes globales" in w for w in glob["warnings"])


def test_frame_global_con_normal_invertida_cambia_el_signo_de_m2():
    # L3 de la sonda: v1 = (−1, 0, 0), v2 = (0, 1, 0), v3 = (0, 0, −1)
    mat = lambda e: matriz_columnas((-1, 0, 0), (0, 1, 0), (0, 0, -1))  # noqa: E731
    glob = leer(FakeJoint(matriz_elemento=mat), cases=["DEAD"], areas=["A1"])
    elem = leer(FakeJoint(), frame="element", cases=["DEAD"], areas=["A1"])
    g, e = glob["rows"][0], elem["rows"][0]
    assert g[4:] == pytest.approx([-e[4], e[5], -e[6], -e[7], e[8], -e[9]])


def test_valor_no_finito_deja_el_trio_en_null_sin_inventar_cero():
    m = FakeJoint(matriz_elemento=_identidad, joint_sin_valor="M2")
    r = leer(m, cases=["DEAD"])
    f = r["rows"][0]
    assert f[7:] == [None, None, None] and f[4:7] != [None, None, None]


def test_include_axes_devuelve_los_ejes_y_la_cache_evita_repetir_las_llamadas():
    m = FakeJoint(matriz_elemento=_identidad)
    r = leer(m, include_axes=True)
    assert set(r["axes"]) == set(r["elements"])
    n = len([c for c in m.log if isinstance(c, tuple) and c[0] == "AreaElm.GetTransformationMatrix"])
    leer(m)  # el marco global usa los mismos ejes: de la caché
    assert len([c for c in m.log if isinstance(c, tuple) and c[0] == "AreaElm.GetTransformationMatrix"]) == n


# --- respaldos, límites y errores ------------------------------------------------------------------------------------------


def test_respaldo_por_area_cuando_all_no_devuelve_filas_con_aviso():
    ref = leer(FakeJoint(matriz_elemento=_identidad))
    m = FakeJoint(matriz_elemento=_identidad, joint_all_vacio=True)
    r = leer(m)
    assert r["rows"] == ref["rows"]
    assert any("por área" in w and "respaldo" in w for w in r["warnings"])


def test_si_la_api_de_nudos_no_responde_es_no_results_sin_tabla_de_respaldo():
    m = FakeJoint(matriz_elemento=_identidad, joint_api_falla=True)
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "no_results")
    assert restaurado(m)


def test_respaldo_por_elemento_cuando_all_y_area_no_devuelven_filas():
    m = FakeJoint(matriz_elemento=_identidad, joint_solo_elementos=True)
    # tipo 1 (elemento) sí responde: el simulado solo vacía los tipos 0 y 2
    r = leer(m)
    assert {f[0] for f in r["rows"]} == {"A1-1", "A1-2", "A2-1"}
    assert any("uno a uno" in w for w in r["warnings"])


def test_modelo_desbloqueado_409_sin_tocar_nada():
    m = FakeJoint(locked=False)
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert (e.value.status, e.value.code) == (409, "model_not_run")
    assert "Results.Setup" not in m.log and m.unit_log == [] and m.prohibidas == []


@pytest.mark.parametrize("kw", [{"areas": ["X"]}, {"cases": ["X"], "combos": ["Y"]}, {"frame": "otro"}])
def test_errores_de_validacion(kw):
    with pytest.raises(ResultsError) as e:
        leer(FakeJoint(matriz_elemento=_identidad), **kw)
    assert e.value.status in (409, 422)


def test_max_elements_y_max_filas(monkeypatch):
    r = leer(FakeJoint(matriz_elemento=_identidad), max_elements=1)
    assert r["truncated"] is True and list(r["elements"]) == ["A1-1"] and {f[0] for f in r["rows"]} == {"A1-1"}
    monkeypatch.setattr(sf, "MAX_FILAS", 30)
    r = leer(FakeJoint(matriz_elemento=_identidad))
    assert r["truncated"] is True and any("otra llamada" in w for w in r["warnings"])


def test_seleccion_para_leer_es_la_pedida_y_se_restaura():
    m = FakeJoint(matriz_elemento=_identidad)
    vista = []
    original = m.Results.AreaJointForceShell

    def espiar(nombre, tipo, *a):
        vista.append((set(m.sel_c), set(m.sel_k)))
        return original(nombre, tipo, *a)

    m.Results.AreaJointForceShell = espiar
    leer(m, cases=["Q"], combos=["ENV"])
    assert vista[0] == ({"Q"}, {"ENV"}) and restaurado(m)


def test_unidades_no_restauradas_409():
    m = FakeJoint(matriz_elemento=_identidad)
    original = m.SetPresentUnits
    m.SetPresentUnits = lambda u: (original(u), 0 if u == 12 else 1)[1]
    with pytest.raises(ResultsError) as e:
        leer(m)
    assert e.value.code == "units_not_restored"


def test_equilibrio_nodal_de_un_corte_suma_las_fuerzas_de_los_nudos():
    """Las fuerzas de nudo de los elementos de un lado del corte se pueden sumar sin más: totales por componente."""
    r = leer(FakeJoint(matriz_elemento=_identidad), cases=["DEAD"], areas=["A1"])
    total_f3 = sum(f[6] for f in r["rows"])
    esperado = sum(valor(i, k, "DEAD", "F3", 0, "") for i in range(2) for k in range(4))
    assert total_f3 == pytest.approx(esperado)


# --- ruta ------------------------------------------------------------------------------------------------------------------

PREFIJO = "/v1/sap/actions/viewer"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")


def _conector():
    c = _Conector()
    c.model = FakeJoint(matriz_elemento=_identidad)
    return c


def test_ruta_200_sin_cache_y_claves_del_contrato(rutas_viewer):
    contrato = json.loads((Path(__file__).resolve().parents[2] / "web" / "src" / "bridge_contracts.json").read_text())
    clave = "POST /v1/sap/actions/viewer/foundations/shell-joint-forces"
    c = rutas_viewer(_conector)
    c.puente._connector = _conector()
    r = c.post(f"{PREFIJO}/foundations/shell-joint-forces", json={"areas": ["A2"], "cases": ["DEAD"]})
    assert r.status_code == 200, r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert set(r.json()) == set(contrato["routes"][clave]["keys"])


def test_ruta_422_campo_extra_frame_invalido_y_components(rutas_viewer):
    c = rutas_viewer(_conector)
    for cuerpo in ({"otro": 1}, {"frame": "x"}, {"components": ["F1"]}, {"max_elements": 0}):
        assert c.post(f"{PREFIJO}/foundations/shell-joint-forces", json=cuerpo).status_code == 422


def test_ruta_mapea_modelo_no_corrido(rutas_viewer):
    c = rutas_viewer(_conector)
    c.puente._connector = _conector()
    c.puente._connector.model.locked = False
    r = c.post(f"{PREFIJO}/foundations/shell-joint-forces", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "model_not_run"
