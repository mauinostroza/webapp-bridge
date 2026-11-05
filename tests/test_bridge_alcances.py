"""Emparejamiento por origen y alcance de rutas (puente compartido, ver docs/PUENTE_COMPARTIDO.md)."""

from __future__ import annotations

import pytest

from bridge.alcances import ruta_permitida
from bridge.hosted import HOSTED_ORIGIN, SAP_GRAPH_ORIGIN, VIEWER_ORIGIN


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


def test_los_tres_origenes_alojados_pueden_emparejarse(client):
    for origen in (HOSTED_ORIGIN, SAP_GRAPH_ORIGIN, VIEWER_ORIGIN):
        assert client.post("/v1/sap/pairing/request", headers={"Origin": origen}).status_code == 200


@pytest.mark.parametrize(
    ("origen", "ruta", "esperado"),
    [
        (SAP_GRAPH_ORIGIN, "/v1/sap/status", True),
        (SAP_GRAPH_ORIGIN, "/v1/sap/actions/graph/model", True),
        (SAP_GRAPH_ORIGIN, "/v1/sap/actions/viewer/k/apply", False),
        (SAP_GRAPH_ORIGIN, "/v1/sap/actions/esbelteces/k-factors/apply", False),
        (SAP_GRAPH_ORIGIN, "/v1/sap/analysis/run", False),
        (VIEWER_ORIGIN, "/v1/sap/actions/viewer/snapshot", True),
        (VIEWER_ORIGIN, "/v1/sap/actions/graph/model", False),
        (VIEWER_ORIGIN, "/v1/sap/actions/espectro/send", False),
        (HOSTED_ORIGIN, "/v1/sap/actions/espectro/send", True),
        (HOSTED_ORIGIN, "/v1/sap/analysis/run", True),
        ("http://localhost:5173", "/v1/sap/actions/viewer/snapshot", True),
    ],
)
def test_ruta_permitida_por_origen(origen, ruta, esperado):
    assert ruta_permitida(origen, ruta) is esperado


def test_ruta_permitida_no_confunde_prefijos():
    assert ruta_permitida(SAP_GRAPH_ORIGIN, "/v1/sap/statusx") is False
    assert ruta_permitida(SAP_GRAPH_ORIGIN, "/v1/sap/actions/graphx/model") is False


def test_secreto_de_sap_graph_no_llega_a_acciones_ajenas(client):
    secreto = _emparejar(client, SAP_GRAPH_ORIGIN)
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": SAP_GRAPH_ORIGIN}
    fuera = client.post("/v1/sap/actions/espectro/send", headers=cabeceras, json={})
    assert fuera.status_code == 403
    assert "alcance" in fuera.json()["detail"]
    # Dentro del alcance la capa de autenticación deja pasar (la ruta aún puede no existir: 404, no 401/403).
    dentro = client.get("/v1/sap/actions/graph/model", headers=cabeceras)
    assert dentro.status_code not in (401, 403)


def test_secreto_ligado_a_su_origen(client):
    secreto = _emparejar(client, SAP_GRAPH_ORIGIN)
    robado = client.get(
        "/v1/sap/status", headers={"Authorization": f"Bearer {secreto}", "Origin": VIEWER_ORIGIN}
    )
    assert robado.status_code == 403
    assert "Origen" in robado.json()["detail"]


def test_workbench_conserva_acceso_completo(client):
    secreto = _emparejar(client, HOSTED_ORIGIN)
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": HOSTED_ORIGIN}
    respuesta = client.post("/v1/sap/actions/espectro/send", headers=cabeceras, json={})
    assert respuesta.status_code not in (401, 403)


def test_rechazos_401_y_403_llevan_cabecera_cors(client):
    """Sin la cabecera el navegador no puede leer el 401 de un secreto caducado."""
    # 401: secreto inválido.
    malo = client.get("/v1/sap/status", headers={"Authorization": "Bearer invalido", "Origin": VIEWER_ORIGIN})
    assert malo.status_code == 401
    assert malo.headers.get("access-control-allow-origin") == VIEWER_ORIGIN
    assert "Origin" in malo.headers.get("vary", "")
    # 403 por ruta fuera de alcance.
    secreto = _emparejar(client, SAP_GRAPH_ORIGIN)
    fuera = client.post(
        "/v1/sap/actions/espectro/send",
        headers={"Authorization": f"Bearer {secreto}", "Origin": SAP_GRAPH_ORIGIN},
        json={},
    )
    assert fuera.status_code == 403
    assert fuera.headers.get("access-control-allow-origin") == SAP_GRAPH_ORIGIN
    # 403 por origen distinto del emparejado.
    robado = client.get(
        "/v1/sap/status", headers={"Authorization": f"Bearer {secreto}", "Origin": VIEWER_ORIGIN}
    )
    assert robado.status_code == 403
    assert robado.headers.get("access-control-allow-origin") == VIEWER_ORIGIN


def test_origen_no_permitido_no_recibe_cabecera_cors(client):
    respuesta = client.get(
        "/v1/sap/status", headers={"Authorization": "Bearer invalido", "Origin": "http://evil.example"}
    )
    assert respuesta.status_code == 401
    assert "access-control-allow-origin" not in respuesta.headers
