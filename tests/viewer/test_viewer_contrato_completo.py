"""Contrato completo del Viewer: las claves de cada respuesta de éxito coinciden con
``web/src/bridge_contracts.json`` (la misma fuente que lee la web de Workbench).

Modo demo (COM simulado). ``results/design`` puede traer ``combos`` como clave opcional
(docs/SAP_ACTIONS.md): se ignora al comparar. Las rutas que en demo responden 409/422
(sin resultados de análisis) o 422 (``connect``) no pueden comprobarse por respuesta: se
comprueba que la ruta existe y que su entrada del contrato tiene las claves que fija el
código en ``bridge/viewer/core/results.py`` y ``bridge/viewer/routes.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeSapObject

PREFIJO = "/v1/sap/actions/viewer"
CONTRATOS = Path(__file__).resolve().parents[2] / "web" / "src" / "bridge_contracts.json"
OPCIONALES = {"combos"}  # docs/SAP_ACTIONS.md, «Claves opcionales del Viewer»
# Snapshot ampliado (cargas por componente y combinaciones): claves opcionales como `combos`, sin
# añadirlas a `keys` de una ruta ya protegida. Su presencia se exige aparte en el test del snapshot.
OPCIONALES |= {"combinations", "combinationsTruncated", "loadsTruncated"}
CLAVES_SNAPSHOT_AMPLIADO = {"combinations", "combinationsTruncated", "loadsTruncated"}

SNAPSHOT = "GET /v1/sap/actions/viewer/snapshot"


class Connector:
    def __init__(self):
        self.model = FakeSapObject().SapModel
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


@pytest.fixture(scope="module")
def rutas_contrato() -> dict:
    return json.loads(CONTRATOS.read_text(encoding="utf-8"))["routes"]


@pytest.fixture
def cliente(rutas_viewer):
    return rutas_viewer(Connector)


def _claves(rutas_contrato: dict, ruta: str) -> set[str]:
    return set(rutas_contrato[ruta]["keys"])


def _claves_respuesta(body: dict) -> set[str]:
    return set(body) - OPCIONALES


def test_respuestas_de_exito_coinciden_con_el_contrato(cliente, rutas_contrato):
    cliente.conectar_demo()

    assert CLAVES_SNAPSHOT_AMPLIADO <= set(cliente.get(PREFIJO + "/snapshot").json())
    assert _claves_respuesta(cliente.get(PREFIJO + "/snapshot").json()) == _claves(rutas_contrato, SNAPSHOT)
    assert _claves_respuesta(cliente.post(PREFIJO + "/refresh").json()) == _claves(
        rutas_contrato, "POST /v1/sap/actions/viewer/refresh"
    )

    design = cliente.get(PREFIJO + "/results/design")
    assert design.status_code == 200
    assert _claves_respuesta(design.json()) == _claves(
        rutas_contrato, "GET /v1/sap/actions/viewer/results/design"
    )

    for ruta in ("k/design", "k/effective", "k/diagnose"):
        r = cliente.get(PREFIJO + "/" + ruta)
        assert r.status_code == 200, ruta
        clave = f"GET /v1/sap/actions/viewer/{ruta}"
        assert _claves_respuesta(r.json()) == _claves(rutas_contrato, clave), clave

    preview = cliente.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}})
    assert preview.status_code == 200
    assert _claves_respuesta(preview.json()) == _claves(
        rutas_contrato, "POST /v1/sap/actions/viewer/k/preview"
    )

    apply = cliente.post(PREFIJO + "/k/apply", json={"nonce": preview.json()["nonce"]})
    assert apply.status_code == 200
    assert _claves_respuesta(apply.json()) == _claves(rutas_contrato, "POST /v1/sap/actions/viewer/k/apply")


def test_connect_en_demo_responde_422_y_su_contrato_es_el_del_snapshot(cliente, rutas_contrato):
    # connect en demo se rechaza (422, «corre en la web»); la forma de éxito es la del snapshot.
    assert cliente.post(PREFIJO + "/connect", json={"mode": "demo"}).status_code == 422
    assert _claves(rutas_contrato, "POST /v1/sap/actions/viewer/connect") == _claves(rutas_contrato, SNAPSHOT)


@pytest.mark.parametrize(
    "ruta,claves_codigo",
    [
        # bridge/viewer/core/results.py: area_stress, displacements, frame_forces (return dict)
        (
            "GET /v1/sap/actions/viewer/results/area",
            {"case", "component", "values", "max", "unit", "warnings"},
        ),
        (
            "GET /v1/sap/actions/viewer/results/displacements",
            {"case", "nodes", "unit", "toMeters", "max", "warnings"},
        ),
        (
            "GET /v1/sap/actions/viewer/results/frame/{frame}",
            {"case", "frame", "stations", "units", "warnings"},
        ),
    ],
)
def test_rutas_sin_resultados_en_demo_existen_y_su_contrato_coincide_con_el_codigo(
    cliente, rutas_contrato, ruta, claves_codigo
):
    cliente.conectar_demo()
    # En demo no hay resultados de análisis: la ruta responde 409/422, nunca 404.
    url = ruta.split(" ", 1)[1].replace("{frame}", "F1")
    r = cliente.get(url, params={"case": "CASO_INEXISTENTE", "component": "S11"})
    assert r.status_code in {409, 422}, (ruta, r.status_code)
    assert _claves(rutas_contrato, ruta) == claves_codigo
