"""SAP Graph en el puente compartido: lectura del modelo (``bridge/graph.py``).

El doble COM sigue el orden comtypes (salidas y, al final, el código de retorno). Cada
lectura debe dar el mismo resultado con el código al final y al inicio (``_RetFirst``,
mismo proxy que ``test_bridge_esbelteces.py``), y el contenido debe ser el esperado.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge import graph
from bridge.actions import install_routes
from bridge.graph import GraphReadError, _units_name, read_model, read_model_response
from tests.test_bridge_esbelteces import _RetFirst

CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
ROUTE = "/v1/sap/actions/graph/model"

POINTS = {"P1": (0.0, 0.0, 0.0), "P2": (6.0, 0.0, 0.0), "P3": (6.0, 4.0, 0.0), "P4": (0.0, 4.0, 0.0)}
RESTRAINTS = {
    "P1": [True, True, True, True, True, True],
    "P2": [False] * 6,
    "P3": [False] * 6,
    "P4": [False] * 6,
}


def _vacio(n_arreglos: int):
    """Objeto sin cargas: SAP devuelve arreglos vacíos (o None)."""
    return lambda name, *_: (0, *([None] * n_arreglos), 0)


def _carga_distribuida(name, *_):
    """F1: gravedad 12→12, parcial 0.25–0.75 en GlobalX, momento y eje local (se omiten)."""
    if name != "F1":
        return (0, *([None] * 11), 0)
    return (
        5,
        ["F1"] * 5,
        ["DEAD", "WIND", "WIND", "LIVE", "LIVE"],
        [1, 1, 2, 1, 1],  # MyType: 1 = fuerza, 2 = momento
        ["Global", "Global", "Global", "Global", "Local"],
        [10, 4, 4, 1, 6],  # Dir: Gravity, GlobalX, GlobalX (momento), local 1, GlobalZ en CSys local
        [0.0, 0.25, 0.0, 0.0, 0.0],
        [1.0, 0.75, 1.0, 1.0, 1.0],
        [0.0, 1.5, 0.0, 0.0, 0.0],
        [6.0, 4.5, 6.0, 6.0, 6.0],
        [12.0, 3.0, 9.0, 5.0, 7.0],
        [12.0, 3.0, 9.0, 5.0, 7.0],
        0,
    )


def _fuerza_en_nudo(name, *_):
    if name not in ("P2", "P3", "P4"):
        return (0, *([None] * 10), 0)
    if name == "P4":  # carga en un sistema distinto de Global: se omite
        return (1, [name], ["LIVE"], [""], ["Local"], [1.0], [0.0], [0.0], [0.0], [0.0], [0.0], 0)
    return (1, [name], ["LIVE"], ["MAX"], ["Global"], [0.0], [0.0], [-5.0], [0.0], [0.0], [0.0], 0)


def _presion_en_area(name, *_):
    return (2, [name, name], ["DEAD", "LIVE"], ["Global", "Global"], [10, 3], [-2.0, -1.0], 0)


def _modelo_com(unidades: int = 12, fallo_en: str | None = None) -> SimpleNamespace:
    """Doble COM en orden comtypes: salidas y, al final, el código (0 = correcto)."""

    def coord(name, *_):
        if name == fallo_en:
            raise RuntimeError("COM caído")
        return (*POINTS[name], 0)

    return SimpleNamespace(
        GetPresentUnits=lambda: unidades,
        PointObj=SimpleNamespace(
            GetNameList=lambda *_: (len(POINTS), list(POINTS), 0),
            GetCoordCartesian=coord,
            GetRestraint=lambda name, *_: (RESTRAINTS[name], 0),
            GetLoadForce=_fuerza_en_nudo,
        ),
        FrameObj=SimpleNamespace(
            GetNameList=lambda *_: (1, ["F1"], 0),
            GetPoints=lambda name, *_: ("P1", "P2", 0),
            GetSection=lambda name, *_: ("R30x60", False, 0),
            GetLoadDistributed=_carga_distribuida,
        ),
        AreaObj=SimpleNamespace(
            GetNameList=lambda *_: (1, ["A1"], 0),
            GetPoints=lambda name, *_: (4, ["P1", "P2", "P3", "P4"], 0),
            GetProperty=lambda name, *_: ("S20", 0),
            GetLoadUniform=_presion_en_area,
        ),
        PropMaterial=SimpleNamespace(
            GetNameList=lambda *_: (1, ["C25"], 0),
            GetMaterial=lambda name, *_: (2, 0, "", "", 0),  # 2 = Concrete
            GetMPIsotropic=lambda name, *_: (2.5e6, 0.2, 1e-5, 0.0, 0),
            GetWeightAndMass=lambda name, *_: (24.0, 2.4, 0),
        ),
        PropFrame=SimpleNamespace(
            GetNameList=lambda *_: (1, ["R30x60"], 0),
            GetRectangle=lambda name, *_: ("", "C25", 0.6, 0.3, 0, "", "", 0),
        ),
        PropArea=SimpleNamespace(
            GetNameList=lambda *_: (1, ["S20"], 0),
            GetShell_1=lambda name, *_: (1, False, "C25", 0.0, 0.2, 0.0, 0, "", "", 0),  # 1 = Shell-Thin
        ),
        LoadPatterns=SimpleNamespace(GetNameList=lambda *_: (1, ["DEAD"], 0)),
    )


def _sin_tiempo(respuesta: dict) -> dict:
    return {k: v for k, v in respuesta.items() if k != "read_ms"}


@pytest.mark.parametrize("codigo_primero", [False, True], ids=["codigo_al_final", "codigo_al_inicio"])
def test_mismo_resultado_con_ambos_ordenes_de_retorno(codigo_primero):
    referencia = read_model_response(_modelo_com())
    modelo = _RetFirst(_modelo_com()) if codigo_primero else _modelo_com()
    assert _sin_tiempo(read_model_response(modelo)) == _sin_tiempo(referencia)


@pytest.mark.parametrize("codigo_primero", [False, True], ids=["codigo_al_final", "codigo_al_inicio"])
def test_contenido_de_la_lectura_con_ambos_ordenes(codigo_primero):
    modelo = _RetFirst(_modelo_com()) if codigo_primero else _modelo_com()
    respuesta = read_model_response(modelo)

    assert set(respuesta) == {"model", "units", "warnings", "read_ms"}
    assert respuesta["units"] == "tonf · m · °C"
    assert respuesta["warnings"] == []
    assert isinstance(respuesta["read_ms"], int)

    datos = respuesta["model"]
    assert set(datos) == {
        "materials",
        "frame_sections",
        "shell_sections",
        "points",
        "frames",
        "shells",
        "load_patterns",
        "assignments",
    }
    assert datos["points"]["P1"] == {"name": "P1", "x": 0.0, "y": 0.0, "z": 0.0, "restraint": [True] * 6}
    assert datos["points"]["P2"] == {"name": "P2", "x": 6.0, "y": 0.0, "z": 0.0, "restraint": None}
    assert datos["frames"] == [{"name": "F1", "p1": "P1", "p2": "P2", "section": "R30x60"}]
    assert datos["shells"] == [{"name": "A1", "points": ["P1", "P2", "P3", "P4"], "section": "S20"}]
    assert datos["frame_sections"] == {
        "R30x60": {"name": "R30x60", "shape": "Rectangular", "material": "C25", "t3": 0.6, "t2": 0.3}
    }
    assert datos["shell_sections"] == {
        "S20": {"name": "S20", "thickness": 0.2, "type": "Shell-Thin", "material": "C25"}
    }
    assert datos["materials"]["C25"]["type"] == "Concrete"
    assert datos["materials"]["C25"]["E"] == 2.5e6
    assert set(datos["load_patterns"]) == {"DEAD"}


@pytest.mark.parametrize("codigo_primero", [False, True], ids=["codigo_al_final", "codigo_al_inicio"])
def test_cargas_aplicadas_por_nombre(codigo_primero):
    modelo = _RetFirst(_modelo_com()) if codigo_primero else _modelo_com()
    respuesta = read_model_response(modelo)
    assert respuesta["warnings"] == []
    asignaciones = respuesta["model"]["assignments"]

    # Barras: gravedad y la carga parcial en GlobalX; se omiten momento, eje local y CSys local.
    assert {
        "kind": "frame_distributed",
        "pattern": "DEAD",
        "frames": ["F1"],
        "dir": "Gravity",
        "w1": 12.0,
        "w2": 12.0,
        "r1": 0.0,
        "r2": 1.0,
    } in asignaciones
    assert {
        "kind": "frame_distributed",
        "pattern": "WIND",
        "frames": ["F1"],
        "dir": "GlobalX",
        "w1": 3.0,
        "w2": 3.0,
        "r1": 0.25,
        "r2": 0.75,
    } in asignaciones
    distribuidas = [a for a in asignaciones if a["kind"] == "frame_distributed"]
    assert len(distribuidas) == 2

    # Nudos: P2 y P3 comparten la misma carga y se agrupan; P4 (sistema Local) se omite.
    assert [a for a in asignaciones if a["kind"] == "point_load"] == [
        {
            "kind": "point_load",
            "pattern": "LIVE",
            "forces": [0.0, 0.0, -5.0],
            "moments": [0.0, 0.0, 0.0],
            "points": ["P2", "P3"],
        }
    ]

    # Shells: solo la presión en gravedad; la de dirección 3 (local) se omite.
    assert [a for a in asignaciones if a["kind"] == "shell_pressure"] == [
        {"kind": "shell_pressure", "pattern": "DEAD", "dir": "Gravity", "p": -2.0, "shells": ["A1"]}
    ]


def test_modelo_sin_cargas_devuelve_lista_vacia():
    modelo = _modelo_com()
    modelo.FrameObj.GetLoadDistributed = _vacio(11)
    modelo.PointObj.GetLoadForce = _vacio(10)
    modelo.AreaObj.GetLoadUniform = _vacio(5)
    assert read_model(modelo)["assignments"] == []


def test_fallo_al_leer_cargas_se_anota_sin_abortar():
    modelo = _modelo_com()

    def falla(*_):
        raise RuntimeError("COM caído")

    modelo.FrameObj.GetLoadDistributed = falla
    resultado = read_model(modelo)
    assert any("GetLoadDistributed(F1)" in w for w in resultado["warnings"])
    assert {a["kind"] for a in resultado["assignments"]} == {"point_load", "shell_pressure"}
    assert "P1" in resultado["points"]


def test_lectura_pura_no_escribe_en_el_modelo():
    """Sólo getters: ningún ``Set*``, ``Add*`` o ``Delete*`` (el doble no los define)."""
    modelo = _modelo_com()
    assert read_model(modelo)["warnings"] == []


def test_fallo_parcial_se_anota_y_no_aborta_la_lectura():
    respuesta = read_model_response(_modelo_com(fallo_en="P3"))
    assert "GetCoordCartesian(P3): COM caído" in respuesta["warnings"]
    assert "P3" not in respuesta["model"]["points"]
    assert set(respuesta["model"]["points"]) == {"P1", "P2", "P4"}
    assert respuesta["model"]["frames"] == [{"name": "F1", "p1": "P1", "p2": "P2", "section": "R30x60"}]


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        (12, "tonf · m · °C"),
        ((0, 12), "tonf · m · °C"),
        ((12, 0), "tonf · m · °C"),
    ],
)
def test_unidades_como_texto_tolerando_tupla(raw, esperado):
    assert _units_name(SimpleNamespace(GetPresentUnits=lambda: raw)) == esperado


def test_unidades_no_reconocidas_y_error_de_lectura():
    def falla():
        raise RuntimeError("x")

    assert _units_name(SimpleNamespace(GetPresentUnits=lambda: 99)) == "Unidades SAP no reconocidas (99)"
    assert _units_name(SimpleNamespace(GetPresentUnits=falla)) is None


def test_respuesta_mayor_que_el_limite_lanza_payload_too_large(monkeypatch):
    monkeypatch.setattr(graph, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(GraphReadError) as info:
        read_model_response(_modelo_com())
    assert info.value.detail["code"] == "payload_too_large"
    assert "MB del puente" in info.value.detail["message"]


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


class _FakeActor:
    def call(self, fn):
        return fn()


class _FakeSession:
    def __init__(self, model):
        self.actor = _FakeActor()
        self.model = model

    def _model(self):
        return self.model


def _client(model) -> TestClient:
    app = FastAPI()
    install_routes(app, _FakeSession(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_ruta_devuelve_la_forma_del_contrato():
    respuesta = _client(_modelo_com()).get(ROUTE)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    contrato = json.loads(CONTRACTS_PATH.read_text("utf-8"))["routes"][f"GET {ROUTE}"]
    assert set(cuerpo) == set(contrato["keys"])
    assert cuerpo["units"] == "tonf · m · °C"
    assert cuerpo["model"]["points"]["P1"]["restraint"] == [True] * 6


def test_ruta_responde_413_si_el_modelo_supera_el_limite(monkeypatch):
    monkeypatch.setattr(graph, "MAX_RESPONSE_BYTES", 100)
    respuesta = _client(_modelo_com()).get(ROUTE)
    assert respuesta.status_code == 413
    assert respuesta.json()["detail"]["code"] == "payload_too_large"


def test_ruta_exige_token(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.get(ROUTE).status_code == 401
    autorizada = client.get(ROUTE, headers={"Authorization": "Bearer test-token"})
    assert autorizada.status_code not in (401, 403, 404)


def test_aviso_si_no_se_puede_detectar_el_orden_del_codigo():
    """Sin una llamada inequívoca la lectura no falla en silencio: deja un aviso."""
    from bridge.graph import read_model

    class _Vacio:
        class LoadPatterns:
            @staticmethod
            def GetNameList(*_args):
                raise RuntimeError("sin modelo")

        class PointObj:
            @staticmethod
            def GetNameList(*_args):
                return (0, (), 0)

    resultado = read_model(_Vacio())
    assert any("orden del código de retorno" in w for w in resultado["warnings"])
