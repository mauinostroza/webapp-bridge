"""Puente SAP2000: Host de loopback, Private Network Access y orígenes."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge import sap2000_agent

    return sap2000_agent


def test_host_allowed_only_loopback(agent):
    for ok in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST:1", "127.0.0.1", "[::1]:8765"):
        assert agent.host_allowed(ok), ok
    for bad in ("evil.example.com:8765", "127.0.0.1.evil.com:8765", "", "10.0.0.5:8765"):
        assert not agent.host_allowed(bad), bad


def test_rebinding_host_is_rejected(agent):
    client = TestClient(agent.app, base_url="http://evil.example.com:8765")
    response = client.get("/v1/sap/status", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 400


def test_preflight_private_network_header(agent):
    client = TestClient(agent.app, base_url="http://127.0.0.1:8765")
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Private-Network": "true",
    }
    response = client.options("/v1/sap/status", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-private-network"] == "true"
    headers.pop("Access-Control-Request-Private-Network")
    plain = client.options("/v1/sap/status", headers=headers)
    assert "access-control-allow-private-network" not in plain.headers


def test_default_origins_include_hosted_entry(agent, monkeypatch, tmp_path):
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    monkeypatch.setattr(agent, "origins_file_path", lambda: tmp_path / "origins.json")
    assert agent.HOSTED_ORIGIN in agent.build_origins()
    assert "https://fusion.invalid" in agent.build_origins()


def test_env_overrides_defaults_and_file_adds(agent, monkeypatch, tmp_path):
    path = tmp_path / "origins.json"
    path.write_text(json.dumps(["https://otro.example.com/", "ftp://no", 5]), encoding="utf-8")
    monkeypatch.setattr(agent, "origins_file_path", lambda: path)
    monkeypatch.setenv("SAP2000_AGENT_ORIGINS", "http://a.test")
    assert agent.build_origins() == ["http://a.test", "https://otro.example.com"]


def test_broken_origins_file_is_ignored(agent, monkeypatch, tmp_path):
    path = tmp_path / "origins.json"
    path.write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(agent, "origins_file_path", lambda: path)
    monkeypatch.setenv("SAP2000_AGENT_ORIGINS", "http://a.test")
    assert agent.build_origins() == ["http://a.test"]


def test_launcher_default_origins_include_hosted_site():
    """run_sap_bridge fija SAP2000_AGENT_ORIGINS: debe traer también la web de Render.

    Sin esto el .exe solo aceptaba la web local y el navegador rechazaba por CORS
    el emparejamiento desde Render.
    """
    import run_sap_bridge
    from bridge.hosted import HOSTED_ORIGIN

    origins = run_sap_bridge.default_origins("8000").split(",")
    assert HOSTED_ORIGIN in origins
    assert "https://fusion.invalid" in origins
    assert "http://127.0.0.1:8000" in origins and "http://localhost:8000" in origins


def test_launcher_default_origins_include_shared_apps():
    """SAP Graph, Viewer y Placa Base se emparejan con el mismo .exe: el lanzador
    fija SAP2000_AGENT_ORIGINS y esa variable reemplaza los orígenes por defecto."""
    import run_sap_bridge
    from bridge.hosted import SHARED_APP_ORIGINS

    origins = run_sap_bridge.default_origins("8000").split(",")
    for origen in SHARED_APP_ORIGINS:
        assert origen in origins
