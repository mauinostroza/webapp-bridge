"""El puente ya no registra documentos por ruta pegada: sólo `POST /v1/documentos/pick`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "datos"))
    from bridge import documentos_locales as dl
    from bridge import pairing
    from bridge.sap2000_agent import TOKEN, app

    pairing.reset_state_for_tests()
    dl._registro.reset_for_tests()
    cliente = TestClient(app, base_url="http://127.0.0.1:8765")
    cliente.headers.update({"Authorization": f"Bearer {TOKEN}"})
    yield cliente
    pairing.reset_state_for_tests()
    dl._registro.reset_for_tests()


def test_post_link_ya_no_existe(client, tmp_path):
    word = tmp_path / "Memoria.docx"
    word.write_bytes(b"no importa: la ruta ni se valida")

    respuesta = client.post("/v1/documentos/link", json={"path": str(word)})

    assert respuesta.status_code in (404, 405), respuesta.text


def test_pick_sigue_siendo_la_unica_forma_de_registrar(client, monkeypatch):
    from backend import linked_documents

    monkeypatch.setattr(linked_documents, "pick_docx", lambda: None)

    respuesta = client.post("/v1/documentos/pick")

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["cancelled"] is True
