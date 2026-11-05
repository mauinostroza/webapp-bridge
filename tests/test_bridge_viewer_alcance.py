"""Alcance del Viewer en el puente compartido: sus rutas sí, las de SAP Graph no (docs/PUENTE_COMPARTIDO.md)."""

from __future__ import annotations

import pytest

from bridge.hosted import VIEWER_ORIGIN

RUTA_STATUS = "/v1/sap/actions/viewer/status"
RUTA_GRAPH = "/v1/sap/actions/graph/model"


@pytest.fixture(autouse=True)
def _estado_aislado(tmp_path, monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    from bridge import pairing

    pairing.reset_state_for_tests()
    yield
    pairing.reset_state_for_tests()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    return TestClient(app, base_url="http://127.0.0.1:8765")


def _emparejar(client, origen: str) -> str:
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": origen}).json()["request_id"]
    pairing.approve(request_id)
    return client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]


def test_origen_del_viewer_puede_leer_su_estado(client):
    secreto = _emparejar(client, VIEWER_ORIGIN)
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": VIEWER_ORIGIN}
    respuesta = client.get(RUTA_STATUS, headers=cabeceras)
    assert respuesta.status_code not in (401, 403)
    assert respuesta.status_code == 200
    assert respuesta.json()["connected"] is False  # sin conexión: el estado es el de caché, sin tocar COM


def test_origen_del_viewer_no_puede_leer_el_modelo_de_sap_graph(client):
    secreto = _emparejar(client, VIEWER_ORIGIN)
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": VIEWER_ORIGIN}
    respuesta = client.get(RUTA_GRAPH, headers=cabeceras)
    assert respuesta.status_code == 403
    assert "alcance" in respuesta.json()["detail"]


def test_alias_de_tablas_se_guardan_en_la_carpeta_de_datos_del_usuario(tmp_path, monkeypatch):
    """El ejecutable instalado puede estar en una carpeta de sólo lectura."""
    from fastapi import FastAPI

    from bridge.viewer.routes import install_routes
    from bridge.viewer.sap2000 import table_aliases

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    original = table_aliases.alias_path
    try:
        install_routes(FastAPI())
        ruta = table_aliases.alias_path()
        assert str(tmp_path) in str(ruta)
        assert ruta.parent.name == "viewer"
    finally:
        table_aliases.alias_path = original
