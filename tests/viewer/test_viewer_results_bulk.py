"""Resultados en bloque del Viewer: reacciones, esfuerzos de varias barras y envolventes."""

from __future__ import annotations

import pytest

from bridge.viewer.core import results_bulk as bulk
from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeResultsModel

PREFIJO = "/v1/sap/actions/viewer"


class Connector:
    def __init__(self):
        self.model = FakeResultsModel()
        self.is_demo = True

    def connect_demo(self):
        return self.info()

    def connect_to_open_instance(self):
        return ConnectionInfo(connected=False, message="no")

    def info(self):
        return ConnectionInfo(connected=True, model_name="Demo", units="tonf_m_C", message="")

    def is_alive(self):
        return True

    def disconnect(self):
        pass


@pytest.fixture
def conectado(rutas_viewer):
    bulk.clear_cache()
    with rutas_viewer(Connector) as c:
        c.conectar_demo()
        c.modelo = c.puente._connector.model if hasattr(c.puente, "_connector") else None
        yield c
    bulk.clear_cache()


def _modelo(cliente) -> FakeResultsModel:
    # El puente guarda el conector; se busca el modelo simulado sin depender del nombre interno.
    for valor in vars(cliente.puente).values():
        model = getattr(valor, "model", None)
        if isinstance(model, FakeResultsModel):
            return model
    raise AssertionError("no se encontró el modelo simulado")


def _get(cliente, ruta, **params):
    return cliente.get(f"{PREFIJO}/results/{ruta}", params=params)


def _detail(r):
    return r.json()["detail"]


# --- Reacciones -----------------------------------------------------------------------


def test_reacciones_solo_apoyos_y_suma_de_fuerzas(conectado):
    r = _get(conectado, "reactions", case="Dead")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["nodes"]) == {"J1", "J2", "J3"}  # solo nodos con apoyo
    assert [body["nodes"][j][2] for j in ("J1", "J2", "J3")] == [10.0, 12.0, 8.0]
    # Carga aplicada simulada: 10 + 12 + 8 tonf hacia abajo.
    assert body["sum"][:3] == [0.0, 0.0, 30.0] and body["sum"][3:] == [None, None, None]
    assert body["max"] == 12.0
    assert body["units"] == {"force": "tonf", "length": "m"} and body["toMeters"] == 1.0
    assert body["case"] == "Dead" and body["warnings"] == []


def test_reacciones_con_varios_pasos_usan_el_mayor_en_modulo_y_avisan(conectado):
    body = _get(conectado, "reactions", case="ENV").json()
    assert body["nodes"]["J1"][2] == 10.0  # Max = 10, Min = -5: manda el de mayor módulo
    assert body["sum"] is None
    assert any("varios pasos" in w for w in body["warnings"])


# --- Esfuerzos en bloque -----------------------------------------------------------------


def test_bloque_de_una_barra_igual_a_la_ruta_individual(conectado):
    bloque = _get(conectado, "frames", case="Dead", ids="F2").json()
    individual = _get(conectado, "frame/F2", case="Dead").json()
    assert bloque["frames"]["F2"] == individual["stations"]
    assert bloque["units"] == {"force": "tonf", "length": "m"} and bloque["next"] is None


def test_paginacion_sin_duplicados_ni_huecos_y_sin_lecturas_por_barra(conectado):
    vistos: list[str] = []
    cursor = None
    paginas = 0
    while True:
        params = {"case": "Dead", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = _get(conectado, "frames", **params).json()
        vistos += list(body["frames"])
        paginas += 1
        cursor = body["next"]
        if not cursor:
            break
    assert paginas == 2 and vistos == ["F1", "F2", "F3"]
    llamadas = _modelo(conectado).Results.calls
    assert all(item == "ALL" for _, item, _ in llamadas)  # nunca una lectura por barra
    assert len([c for c in llamadas if c[0] == "FrameForce"]) == 1  # la 2.ª página sale de la caché


def test_ids_inexistentes_avisan_y_si_no_hay_ninguno_es_409(conectado):
    body = _get(conectado, "frames", case="Dead", ids="F1,F99").json()
    assert list(body["frames"]) == ["F1"] and any("F99" in w for w in body["warnings"])
    r = _get(conectado, "frames", case="Dead", ids="F98,F99")
    assert r.status_code == 409 and _detail(r)["code"] == "no_results"


def test_cursor_invalido(conectado):
    r = _get(conectado, "frames", case="Dead", cursor="@@@")
    assert r.status_code == 400 and _detail(r)["code"] == "bad_cursor"


# --- Envolvente --------------------------------------------------------------------------


def test_envolvente_de_tres_casos(conectado):
    body = _get(conectado, "envelope", kind="frames", cases="Dead,Live,COMB1", ids="F1").json()
    assert body["cases"] == ["Dead", "Live", "COMB1"]
    maximos = {p["x"]: p for p in body["max"]["F1"]}
    minimos = {p["x"]: p for p in body["min"]["F1"]}
    # F1: M3(Dead) = x-2, M3(Live) = -0.5(x-2), M3(COMB1) = 1.2(x-2) - 0.8(x-2) = 0.4(x-2)
    assert maximos[0.0]["M3"] == pytest.approx(1.0) and minimos[0.0]["M3"] == pytest.approx(-2.0)
    assert maximos[4.0]["M3"] == pytest.approx(2.0) and minimos[4.0]["M3"] == pytest.approx(-1.0)
    # P: Dead -10, Live +5, COMB1 -4 → manda Live arriba y Dead abajo.
    assert body["governing"]["F1"]["P"] == {"max": "Live", "min": "Dead"}
    for fid in body["max"]:
        for hi, lo in zip(body["max"][fid], body["min"][fid]):
            for comp in ("P", "V2", "V3", "T", "M2", "M3"):
                assert lo[comp] <= hi[comp]


def test_envolvente_lee_la_combinacion_de_tipo_envolvente_con_pasos_max_min(conectado):
    body = _get(conectado, "envelope", kind="frames", cases="ENV", ids="F1").json()
    maximos = {p["x"]: p for p in body["max"]["F1"]}
    minimos = {p["x"]: p for p in body["min"]["F1"]}
    assert maximos[0.0]["M3"] == pytest.approx(1.0) and minimos[0.0]["M3"] == pytest.approx(-2.0)
    assert body["governing"]["F1"]["P"] == {"max": "ENV", "min": "ENV"}
    assert [c[0] for c in _modelo(conectado).Results.calls].count("FrameForce") == 1


def test_envolvente_de_reacciones(conectado):
    body = _get(conectado, "envelope", kind="reactions", cases="Dead,Live,COMB1").json()
    # F3 de J1: Dead 10, Live -5, COMB1 4.
    assert body["max"]["J1"][2] == 10.0 and body["min"]["J1"][2] == -5.0
    assert body["governing"]["J1"]["F3"] == {"max": "Dead", "min": "Live"}
    assert body["next"] is None
    assert all(lo[2] <= hi[2] for lo, hi in zip(body["min"].values(), body["max"].values()))


# --- Selección de salida, unidades y errores ---------------------------------------------------


def test_la_seleccion_de_salida_se_restaura_tras_cada_lectura(conectado):
    modelo = _modelo(conectado)
    setup = modelo.Results.Setup
    setup.SetCaseSelectedForOutput("Live", True)
    setup.SetComboSelectedForOutput("ENV", True)
    for ruta, params in (
        ("reactions", {"case": "Dead"}),
        ("frames", {"case": "Dead", "ids": "F1"}),
        ("envelope", {"kind": "frames", "cases": "Dead,Live", "ids": "F1"}),
        ("frame/F1", {"case": "Dead"}),
        ("displacements", {"case": "Dead"}),
    ):
        _get(conectado, ruta, **params)
        assert (setup.cases, setup.combos) == ({"Live"}, {"ENV"}), ruta


def test_unidad_desconocida_da_to_meters_null(conectado):
    _modelo(conectado).present_units = 9999
    for body in (
        _get(conectado, "reactions", case="Dead").json(),
        _get(conectado, "frames", case="Dead", ids="F1").json(),
        _get(conectado, "envelope", kind="frames", cases="Dead", ids="F1").json(),
    ):
        assert body["toMeters"] is None  # nunca 1.0
        assert body["units"] == {"force": None, "length": None}
        assert any("no reconocidas" in w for w in body["warnings"])


def test_errores(conectado):
    r = _get(conectado, "reactions", case="NoExiste")
    assert r.status_code == 404 and _detail(r)["code"] == "case_not_found"
    r = _get(conectado, "envelope", kind="frames", cases="Dead,NoExiste")
    assert r.status_code == 404 and _detail(r)["code"] == "case_not_found"
    muchos = ",".join(f"C{i}" for i in range(bulk.MAX_CASES + 1))
    r = _get(conectado, "envelope", kind="reactions", cases=muchos)
    assert r.status_code == 400 and _detail(r)["code"] == "too_many_cases"
    assert _get(conectado, "envelope", kind="otra", cases="Dead").status_code == 400


def test_sin_analisis_es_409_no_results(conectado):
    modelo = _modelo(conectado)
    modelo.DatabaseTables = None  # sin respaldo por tablas
    modelo.Results.FrameForce = lambda item, item_type: (0, [], [], [], [], [], [], [], [], [], [], [], [], 0)
    modelo.Results.JointReact = lambda item, item_type: (0, [], [], [], [], [], [], [], [], [], [], [], 0)
    for ruta, params in (
        ("frames", {"case": "Dead"}),
        ("reactions", {"case": "Dead"}),
        ("envelope", {"kind": "frames", "cases": "Dead"}),
    ):
        r = _get(conectado, ruta, **params)
        assert r.status_code == 409 and _detail(r)["code"] == "no_results", ruta
