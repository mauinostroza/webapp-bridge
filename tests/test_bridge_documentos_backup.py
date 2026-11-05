"""Ruta de respaldo ``GET /v1/documentos/backup`` (``bridge/documentos_locales.py``).

Devuelve un ZIP con el .docx registrado y, si existe, su manifiesto. Sólo lectura: el
documento y el manifiesto no cambian. Mismos dobles que ``test_bridge_documentos_locales``:
sin diálogo, sin Word; el registro vive en un directorio temporal.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import linked_documents
from bridge import documentos_locales as dl
from bridge import pairing
from bridge.hosted import SAP_GRAPH_ORIGIN


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
    from bridge.sap2000_agent import app

    cliente = TestClient(app, base_url="http://127.0.0.1:8765")
    cliente.headers.update({"Authorization": f"Bearer {sap2000_agent.TOKEN}"})
    return cliente


@pytest.fixture
def usuario(tmp_path) -> Path:
    carpeta = tmp_path / "Memorias"
    carpeta.mkdir()
    return carpeta


def _docx(path: Path) -> Path:
    document = Document()
    document.add_heading("Memoria de cálculo", 0)
    document.add_heading("Verificaciones estructurales", level=1)
    document.add_paragraph("Texto de la sección.")
    document.save(str(path))
    return path


def _enlazar(client, path: Path) -> str:
    with patch.object(linked_documents, "pick_docx", lambda: str(path)):
        respuesta = client.post("/v1/documentos/pick")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["doc_id"]


def _zip(respuesta) -> zipfile.ZipFile:
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"].startswith("application/zip")
    return zipfile.ZipFile(io.BytesIO(respuesta.content))


def test_sin_credenciales_responde_401(client):
    client.headers.pop("Authorization")
    assert client.get("/v1/documentos/backup", params={"doc_id": "a" * 32}).status_code == 401


def test_origen_compartido_no_llega_al_respaldo(client):
    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": SAP_GRAPH_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    secreto = client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": SAP_GRAPH_ORIGIN}
    client.headers.pop("Authorization")
    respuesta = client.get("/v1/documentos/backup", params={"doc_id": "a" * 32}, headers=cabeceras)
    assert respuesta.status_code == 403


def test_doc_id_desconocido_responde_404(client):
    assert client.get("/v1/documentos/backup", params={"doc_id": "b" * 32}).status_code == 404
    assert client.get("/v1/documentos/backup", params={"doc_id": "../../x"}).status_code == 404


def test_respaldo_trae_el_docx_y_el_manifiesto_sin_modificarlos(client, usuario):
    word = _docx(usuario / "Memoria.docx")
    doc_id = _enlazar(client, word)
    assert client.post("/v1/documentos/prepare", json={"doc_id": doc_id}).status_code == 200
    antes_docx = word.read_bytes()
    manifiesto = dl.manifest_ops.manifest_path_for(word)
    antes_manifiesto = manifiesto.read_bytes()

    archivo = _zip(client.get("/v1/documentos/backup", params={"doc_id": doc_id}))

    assert sorted(archivo.namelist()) == sorted(["Memoria.docx", manifiesto.name])
    assert archivo.read("Memoria.docx") == antes_docx
    assert json.loads(archivo.read(manifiesto.name))["project"]["docx_path"]
    assert word.read_bytes() == antes_docx
    assert manifiesto.read_bytes() == antes_manifiesto


def test_sin_manifiesto_el_respaldo_trae_solo_el_docx(client, usuario):
    word = _docx(usuario / "Sin manifiesto.docx")
    doc_id = _enlazar(client, word)

    archivo = _zip(client.get("/v1/documentos/backup", params={"doc_id": doc_id}))

    assert archivo.namelist() == ["Sin manifiesto.docx"]
    assert not dl.manifest_ops.manifest_path_for(word).exists()


def test_word_que_ya_no_esta_responde_409(client, usuario):
    word = _docx(usuario / "Movido.docx")
    doc_id = _enlazar(client, word)
    word.unlink()
    respuesta = client.get("/v1/documentos/backup", params={"doc_id": doc_id})
    assert respuesta.status_code == 409
    assert "Movido.docx" in respuesta.json()["detail"]


def test_contrato_del_respaldo_esta_declarado_como_binario():
    contratos = json.loads(
        (Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json").read_text("utf-8")
    )
    spec = contratos["routes"]["GET /v1/documentos/backup"]
    assert spec["keys"] == []
    assert spec["binario"] == "application/zip"
