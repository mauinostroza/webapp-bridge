"""Regresion: la posicion intermedia de una seccion vinculada debe llegar a la UI en modo ayudante local.

Causa: el manifiesto del servidor (`routers/projects.py`) añade `anchor_heading_index`, pero el
puente local (`bridge/documentos_locales.py`, `GET /v1/documentos/manifest`) devolvia solo el
manifiesto en bruto. La UI (`ReportBuilderWindow.tsx`) cae entonces a `headings.length` y pinta
cada seccion al final del documento.

Este test reproduce el caso con un documento de tres capitulos: una seccion vinculada despues del
capitulo 2 debe indicar el titulo 3 como el que la sigue (indice 2 en la lista de posiciones).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import linked_documents
from backend.report_builder import service
from bridge import documentos_locales as dl
from bridge import pairing

ESBELTECES_JSON = {"results": [{"frame_name": "V1", "section_name": "W310", "norma": "NCh2369_2025"}]}
CAPITULOS = ["Capitulo uno", "Capitulo dos", "Capitulo tres"]


@pytest.fixture(autouse=True)
def _estado_aislado(tmp_path, monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "datos"))
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    pairing.reset_state_for_tests()
    dl._registro.reset_for_tests()
    yield
    pairing.reset_state_for_tests()
    dl._registro.reset_for_tests()


@pytest.fixture
def client():
    from bridge import sap2000_agent

    cliente = TestClient(sap2000_agent.app, base_url="http://127.0.0.1:8765")
    cliente.headers.update({"Authorization": f"Bearer {sap2000_agent.TOKEN}"})
    return cliente


def _memoria(path: Path) -> Path:
    document = Document()
    document.add_heading("Memoria de cálculo", 0)
    for capitulo in CAPITULOS:
        document.add_heading(capitulo, level=1)
        document.add_paragraph(f"Texto de {capitulo}.")
    document.save(str(path))
    return path


def _enlazar_y_preparar(client, word: Path) -> str:
    with patch.object(linked_documents, "pick_docx", lambda: str(word)):
        respuesta = client.post("/v1/documentos/pick")
    assert respuesta.status_code == 200, respuesta.text
    doc_id = respuesta.json()["doc_id"]
    prep = client.post("/v1/documentos/prepare", json={"doc_id": doc_id})
    assert prep.status_code == 200, prep.text
    return doc_id


def _indice_de(client, doc_id: str, titulo: str) -> int:
    posiciones = client.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    return next(p["index"] for p in posiciones if p["heading_text"] == titulo)


def _ordinal_titulo(client, doc_id: str, titulo: str) -> int:
    """Posicion del titulo entre los titulos reales (la UI ignora «Al final del documento»)."""
    posiciones = client.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    titulos = [p["heading_text"] for p in posiciones if p["level"] is not None]
    return titulos.index(titulo)


def test_manifest_del_puente_local_indica_la_posicion_intermedia(client, tmp_path):
    usuario = tmp_path / "Memorias"
    usuario.mkdir()
    doc_id = _enlazar_y_preparar(client, _memoria(usuario / "Memoria.docx"))

    # Seccion vinculada justo despues del capitulo dos (antes del capitulo tres).
    ancla = client.post(
        "/v1/documentos/anchors",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "source": "json_inline",
            "datos": ESBELTECES_JSON,
            "position_index": _indice_de(client, doc_id, "Capitulo dos"),
        },
    )
    assert ancla.status_code == 200, ancla.text
    tag = ancla.json()["tag"]

    manifiesto = client.get("/v1/documentos/manifest", params={"doc_id": doc_id}).json()["manifest"]

    # El indice apunta al titulo que sigue al ancla: «Capitulo tres».
    esperado = _ordinal_titulo(client, doc_id, "Capitulo tres")
    assert manifiesto["anchor_heading_index"] == {tag: esperado}
    assert esperado == 2


def test_manifest_del_puente_coincide_con_el_del_servidor(client, tmp_path):
    """El puente local y el servidor deben calcular el mismo indice para el mismo documento."""
    usuario = tmp_path / "Memorias"
    usuario.mkdir()
    word = _memoria(usuario / "Memoria.docx")
    doc_id = _enlazar_y_preparar(client, word)
    client.post(
        "/v1/documentos/anchors",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "source": "json_inline",
            "datos": ESBELTECES_JSON,
            "position_index": _indice_de(client, doc_id, "Capitulo uno"),
        },
    )
    local = client.get("/v1/documentos/manifest", params={"doc_id": doc_id}).json()["manifest"]
    assert local["anchor_heading_index"] == service.anchor_heading_offsets(word)
    assert json.dumps(local["anchor_heading_index"])
