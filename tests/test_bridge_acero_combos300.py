"""Límite de combinaciones de «Traer fuerzas» (acero-aisc360/fuerzas): 300 se acepta, 301 es 422.

Reutiliza el COM falso de tests/test_bridge_acero_fuerzas.py. Las combinaciones C0..C299 existen
en el modelo falso (RespCombo) pero no tienen resultados, así que una petición válida responde 200
con avisos y no 409; la prueba mide el límite, no la lectura.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.acero_aisc360_fuerzas import MAX_COMBOS
from bridge.actions import install_routes
from tests.test_bridge_acero_fuerzas import FakeAcero, _FakeSession, _Lista

RUTA = "/v1/sap/actions/acero-aisc360/fuerzas"


def _cliente(combos: list[str]) -> TestClient:
    modelo = FakeAcero()
    modelo.RespCombo = _Lista(tuple(combos))
    app = FastAPI()
    install_routes(app, _FakeSession(modelo), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_limite_es_300():
    assert MAX_COMBOS == 300


def test_300_combinaciones_se_aceptan():
    combos = [f"C{i}" for i in range(300)]
    response = _cliente(combos).post(RUTA, json={"frames": ["V1"], "combos": combos})
    assert response.status_code == 200
    assert response.json()["frames"][0]["frame"] == "V1"


def test_301_combinaciones_es_422_con_el_maximo():
    combos = [f"C{i}" for i in range(301)]
    response = _cliente(combos).post(RUTA, json={"frames": ["V1"], "combos": combos})
    assert response.status_code == 422
    assert "300" in str(response.json()["detail"])


def test_300_combinaciones_con_barra_de_la_seleccion_sin_error_de_limite():
    combos = [f"C{i}" for i in range(300)]
    response = _cliente(combos).post(RUTA, json={"frames": "selected", "combos": combos})
    assert response.status_code in (200, 409)
    assert response.status_code != 422
