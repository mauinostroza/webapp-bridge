"""Servicio de documentos Word locales del puente (``bridge/documentos_locales.py``).

Todo con dobles: sin diálogo de Windows (``pick_docx`` simulado), sin Word, sin
``os.startfile`` real (se registra la llamada). El registro persistido vive en un
directorio temporal (``XDG_DATA_HOME``) para no tocar el ``documentos.json`` real.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from backend import linked_documents
from backend.report_builder import service
from bridge import documentos_locales as dl
from bridge import pairing
from bridge.hosted import SAP_GRAPH_ORIGIN

SECCION = "Verificaciones estructurales"
CONTRATOS = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
ESBELTECES_JSON = {"results": [{"frame_name": "V1", "section_name": "W310", "norma": "NCh2369_2025"}]}


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
def token() -> str:
    from bridge import sap2000_agent

    return sap2000_agent.TOKEN


@pytest.fixture
def client(token):
    from bridge.sap2000_agent import app

    cliente = TestClient(app, base_url="http://127.0.0.1:8765")
    cliente.headers.update({"Authorization": f"Bearer {token}"})
    return cliente


@pytest.fixture
def usuario(tmp_path) -> Path:
    carpeta = tmp_path / "Memorias"
    carpeta.mkdir()
    return carpeta


def _docx(path: Path) -> Path:
    document = Document()
    document.add_heading("Memoria de cálculo", 0)
    document.add_heading(SECCION, level=1)
    document.add_paragraph("Texto de la sección.")
    document.save(str(path))
    return path


def _texto_del_word(path: Path) -> str:
    document = Document(str(path))
    return "".join(t.text or "" for t in document.element.body.iter(qn("w:t")))


def _zip_base64(entradas: dict[str, bytes]) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for nombre, contenido in entradas.items():
            archive.writestr(nombre, contenido)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _pick_de(client, ruta) -> object:
    """Registra `ruta` por el único camino vigente: el diálogo (`pick`) con `pick_docx` simulado."""
    with patch.object(linked_documents, "pick_docx", lambda: str(ruta)):
        return client.post("/v1/documentos/pick")


def _enlazar(client, path: Path) -> str:
    respuesta = _pick_de(client, path)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["doc_id"]


def _preparado(client, usuario: Path) -> tuple[str, Path]:
    word = _docx(usuario / "Memoria.docx")
    doc_id = _enlazar(client, word)
    prep = client.post("/v1/documentos/prepare", json={"doc_id": doc_id})
    assert prep.status_code == 200, prep.text
    return doc_id, word


def _indice_de(client, doc_id: str, titulo: str) -> int:
    posiciones = client.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    return next(p["index"] for p in posiciones if p["heading_text"] == titulo)


def _contrato(ruta: str) -> dict:
    return json.loads(CONTRATOS.read_text("utf-8"))["routes"][ruta]


# --- Autenticación y alcance -------------------------------------------------


def test_sin_credenciales_responde_401(client):
    client.headers.pop("Authorization")
    assert client.get("/v1/documentos/list").status_code == 401
    assert client.post("/v1/documentos/pick").status_code == 401


def test_origen_compartido_emparejado_no_llega_a_documentos(client):
    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": SAP_GRAPH_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    secreto = client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": SAP_GRAPH_ORIGIN}
    client.headers.pop("Authorization")
    respuesta = client.get("/v1/documentos/list", headers=cabeceras)
    assert respuesta.status_code == 403
    assert "alcance" in respuesta.json()["detail"]


# --- Registro ----------------------------------------------------------------


def test_pick_registra_el_documento_elegido_y_lo_persiste(client, usuario, monkeypatch):
    word = _docx(usuario / "Memoria.docx")
    monkeypatch.setattr(linked_documents, "pick_docx", lambda: str(word))

    respuesta = client.post("/v1/documentos/pick")

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["cancelled"] is False
    assert re.fullmatch(r"[0-9a-f]{32}", cuerpo["doc_id"])
    assert cuerpo["nombre"] == "Memoria.docx"
    assert cuerpo["carpeta_mostrar"] == str(usuario)
    assert json.loads(dl._registro_path().read_text("utf-8"))["documentos"][cuerpo["doc_id"]] == str(word)


def test_pick_cancelado_y_sin_tkinter(client, monkeypatch):
    monkeypatch.setattr(linked_documents, "pick_docx", lambda: None)
    cancelado = client.post("/v1/documentos/pick").json()
    assert cancelado == {"cancelled": True, "doc_id": None, "nombre": None, "carpeta_mostrar": None}

    def sin_tk():
        raise linked_documents.PickerUnavailable("El diálogo de archivos no está disponible")

    monkeypatch.setattr(linked_documents, "pick_docx", sin_tk)
    assert client.post("/v1/documentos/pick").status_code == 501


def test_pick_aplica_las_mismas_validaciones_que_la_app(client, usuario, monkeypatch):
    doc = _docx(usuario / "Memoria.docx")
    antiguo = usuario / "Memoria.doc"
    antiguo.write_bytes(b"binario")
    temporal = _docx(usuario / "~$Memoria.docx")
    enlace = usuario / "acceso.docx"
    os.symlink(doc, enlace)

    assert _pick_de(client, str(antiguo)).status_code == 422
    assert _pick_de(client, str(temporal)).status_code == 422
    assert _pick_de(client, str(enlace)).status_code == 422
    assert _pick_de(client, str(usuario / "no_existe.docx")).status_code == 422
    assert _pick_de(client, "relativa/Memoria.docx").status_code == 422
    assert _pick_de(client, str(usuario)).status_code == 422

    monkeypatch.setattr(linked_documents, "MAX_LINKED_BYTES", 10)
    assert _pick_de(client, str(doc)).status_code == 422


def test_list_recuerda_documentos_y_marca_los_que_faltan(client, usuario):
    word = _docx(usuario / "Memoria.docx")
    doc_id = _enlazar(client, word)

    # Simula un reinicio del puente: el registro en memoria se vacía y se vuelve a leer del disco.
    dl._registro.reset_for_tests()
    lista = client.get("/v1/documentos/list").json()["documentos"]
    assert lista == [
        {"doc_id": doc_id, "nombre": "Memoria.docx", "carpeta_mostrar": str(usuario), "available": True}
    ]

    word.unlink()
    assert client.get("/v1/documentos/list").json()["documentos"][0]["available"] is False
    assert client.post("/v1/documentos/prepare", json={"doc_id": doc_id}).status_code == 409


def test_doc_id_desconocido_responde_404(client):
    desconocido = "f" * 32
    assert client.post("/v1/documentos/prepare", json={"doc_id": desconocido}).status_code == 404
    assert client.get("/v1/documentos/storage", params={"doc_id": "no-es-un-id"}).status_code == 404
    assert client.post("/v1/documentos/open-in-word", json={"doc_id": desconocido}).status_code == 404


# --- Preparación, manifiesto y posiciones -------------------------------------


def test_prepare_crea_el_manifiesto_junto_al_word_una_sola_vez(client, usuario):
    word = _docx(usuario / "Memoria.docx")
    doc_id = _enlazar(client, word)

    primera = client.post("/v1/documentos/prepare", json={"doc_id": doc_id})
    assert primera.status_code == 200, primera.text
    cuerpo = primera.json()
    assert set(cuerpo) == set(_contrato("POST /v1/documentos/prepare")["keys"])
    assert cuerpo["created"] is True
    assert cuerpo["manifest_path"] == str(usuario / "Memoria.report_manifest.json")
    assert cuerpo["title_count"] >= 1
    assert (usuario / "Memoria.report_manifest.json").is_file()

    segunda = client.post("/v1/documentos/prepare", json={"doc_id": doc_id}).json()
    assert segunda["created"] is False


def test_prepare_con_word_bloqueado_responde_409(client, usuario, monkeypatch):
    doc_id = _enlazar(client, _docx(usuario / "Memoria.docx"))

    def bloqueado(_path):
        raise service.DocumentLockedError("No se puede escribir en «Memoria.docx»")

    monkeypatch.setattr(service, "revisar_documento", bloqueado)
    assert client.post("/v1/documentos/prepare", json={"doc_id": doc_id}).status_code == 409


def test_positions_y_manifest_responden_con_la_forma_del_contrato(client, usuario):
    doc_id, _word = _preparado(client, usuario)

    posiciones = client.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()
    assert set(posiciones) == set(_contrato("GET /v1/documentos/positions")["keys"])
    assert any(p["heading_text"] == SECCION for p in posiciones["positions"])

    manifiesto = client.get("/v1/documentos/manifest", params={"doc_id": doc_id}).json()
    assert set(manifiesto) == set(_contrato("GET /v1/documentos/manifest")["keys"])
    assert manifiesto["manifest"]["project"]["document_id"] == doc_id
    assert manifiesto["manifest"]["modules"] == []


# --- Anclas, sincronización y borrado -----------------------------------------


def test_anchor_json_inline_se_sincroniza_y_el_word_queda_modificado(client, usuario):
    doc_id, word = _preparado(client, usuario)
    indice = _indice_de(client, doc_id, SECCION)

    ancla = client.post(
        "/v1/documentos/anchors",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "source": "json_inline",
            "datos": ESBELTECES_JSON,
            "position_index": indice,
        },
    )
    assert ancla.status_code == 200, ancla.text
    cuerpo = ancla.json()
    assert set(cuerpo) == set(_contrato("POST /v1/documentos/anchors")["keys"])
    assert cuerpo["json_relativo"] == "Memoria_reporte/esbelteces/esbelteces.json"
    assert (usuario / "Memoria_reporte" / "esbelteces" / "esbelteces.json").is_file()

    manifiesto = client.get("/v1/documentos/manifest", params={"doc_id": doc_id}).json()["manifest"]
    assert manifiesto["modules"][0]["source_json"] == "Memoria_reporte/esbelteces/esbelteces.json"

    sync = client.post("/v1/documentos/sync", json={"doc_id": doc_id, "dry_run": False})
    assert sync.status_code == 200, sync.text
    resumen = sync.json()
    assert set(resumen) == set(_contrato("POST /v1/documentos/sync")["keys"])
    assert resumen["ok"] is True
    assert "V1" in _texto_del_word(word)  # el Word se reabre y tiene el contenido generado


def test_anchor_json_inline_invalido_no_deja_archivos(client, usuario):
    doc_id, _word = _preparado(client, usuario)
    indice = _indice_de(client, doc_id, SECCION)

    respuesta = client.post(
        "/v1/documentos/anchors",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "source": "json_inline",
            "datos": {"otra_cosa": 1},
            "position_index": indice,
        },
    )
    assert respuesta.status_code == 422
    assert not (usuario / "Memoria_reporte").exists()


def test_anchor_rechaza_fuentes_fuera_de_la_carpeta_reporte(client, usuario):
    doc_id, _word = _preparado(client, usuario)
    indice = _indice_de(client, doc_id, SECCION)
    (usuario / "Otro.json").write_text(json.dumps(ESBELTECES_JSON), encoding="utf-8")

    for fuente in ("../Otro.json", "/etc/passwd", "C:/Windows/win.ini", "esbelteces/../../Otro.json"):
        respuesta = client.post(
            "/v1/documentos/anchors",
            json={"doc_id": doc_id, "module_id": "esbelteces", "source": fuente, "position_index": indice},
        )
        assert respuesta.status_code == 422, (fuente, respuesta.text)


def test_anchor_desde_espejo_y_borrado_de_la_seccion(client, usuario):
    doc_id, word = _preparado(client, usuario)
    indice = _indice_de(client, doc_id, SECCION)
    exportado = client.post(
        "/v1/documentos/export-module",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "bundle_base64": _zip_base64({"esbelteces/reporte.json": json.dumps(ESBELTECES_JSON).encode()}),
        },
    )
    assert exportado.status_code == 200, exportado.text

    ancla = client.post(
        "/v1/documentos/anchors",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "source": "esbelteces/esbelteces.json",
            "position_index": indice,
        },
    ).json()
    assert ancla["ok"] is True

    borrado = client.delete(f"/v1/documentos/anchors/{ancla['tag']}", params={"doc_id": doc_id})
    assert borrado.status_code == 200 and borrado.json() == {"ok": True}
    assert (
        client.delete(f"/v1/documentos/anchors/{ancla['tag']}", params={"doc_id": doc_id}).status_code == 404
    )
    assert "V1" not in _texto_del_word(word)


def test_anchor_con_tag_no_valido_responde_422(client, usuario):
    doc_id, _word = _preparado(client, usuario)
    assert client.delete("/v1/documentos/anchors/mal%20tag", params={"doc_id": doc_id}).status_code == 422


# --- Exportación de módulo -----------------------------------------------------


def test_export_module_escribe_json_e_imagenes_con_rutas_relativas(client, usuario):
    doc_id, _word = _preparado(client, usuario)
    cuerpo_json = {"results": ESBELTECES_JSON["results"], "grafico": "imagenes/grafico.png"}
    paquete = _zip_base64(
        {
            "esbelteces/reporte.json": json.dumps(cuerpo_json).encode("utf-8"),
            "esbelteces/imagenes/grafico.png": b"\x89PNG-falso",
        }
    )

    respuesta = client.post(
        "/v1/documentos/export-module",
        json={"doc_id": doc_id, "module_id": "esbelteces", "bundle_base64": paquete},
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert set(cuerpo) == set(_contrato("POST /v1/documentos/export-module")["keys"])
    assert cuerpo["slug"] == "esbelteces"
    assert cuerpo["n_imagenes"] == 1
    assert cuerpo["json_relativo"] == "Memoria_reporte/esbelteces/esbelteces.json"
    carpeta = usuario / "Memoria_reporte" / "esbelteces"
    assert (carpeta / "imagenes" / "grafico.png").read_bytes() == b"\x89PNG-falso"
    espejo = json.loads((carpeta / "esbelteces.json").read_text("utf-8"))
    assert espejo["grafico"] == "imagenes/grafico.png"


def test_export_module_rechaza_zip_slip_y_rutas_absolutas(client, usuario):
    doc_id, _word = _preparado(client, usuario)
    for nombre in ("../evil.json", "/tmp/evil.json", "esbelteces/../../evil.json"):
        respuesta = client.post(
            "/v1/documentos/export-module",
            json={
                "doc_id": doc_id,
                "module_id": "esbelteces",
                "bundle_base64": _zip_base64({nombre: json.dumps(ESBELTECES_JSON).encode()}),
            },
        )
        assert respuesta.status_code == 422, (nombre, respuesta.text)
    assert not (usuario.parent / "evil.json").exists()
    assert not (usuario / "evil.json").exists()


def test_export_module_rechaza_formatos_y_paquetes_mal_formados(client, usuario):
    doc_id, _word = _preparado(client, usuario)

    def exportar(bundle: str):
        return client.post(
            "/v1/documentos/export-module",
            json={"doc_id": doc_id, "module_id": "esbelteces", "bundle_base64": bundle},
        )

    assert exportar(_zip_base64({"esbelteces/x.exe": b"MZ", "esbelteces/a.json": b"{}"})).status_code == 422
    assert exportar(_zip_base64({"esbelteces/a.json": b"no es json"})).status_code == 422
    assert exportar(_zip_base64({"a.png": b"\x89PNG"})).status_code == 422  # sin JSON de módulo
    assert exportar("no-base64!!").status_code == 422
    assert (
        exportar(_zip_base64({"esbelteces/a.json": json.dumps(ESBELTECES_JSON).encode()})).status_code == 200
    )
    assert (
        client.post(
            "/v1/documentos/export-module",
            json={
                "doc_id": doc_id,
                "module_id": "modulo_inventado",
                "bundle_base64": _zip_base64({"a.json": b"{}"}),
            },
        ).status_code
        == 422
    )


# --- Abrir en Word y almacenamiento -------------------------------------------


def test_open_in_word_abre_la_ruta_registrada_sin_copiar(client, usuario, monkeypatch):
    doc_id, word = _preparado(client, usuario)
    llamadas: list[str] = []
    monkeypatch.setattr(dl, "_es_windows", lambda: True)
    monkeypatch.setattr(os, "startfile", lambda ruta: llamadas.append(ruta), raising=False)

    respuesta = client.post("/v1/documentos/open-in-word", json={"doc_id": doc_id})

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"ok": True, "nombre": "Memoria.docx"}
    assert llamadas == [str(word)]
    assert list(usuario.glob("*.docx")) == [word]  # no se creó ninguna copia


def test_storage_devuelve_las_rutas_reales(client, usuario):
    doc_id, word = _preparado(client, usuario)
    client.post(
        "/v1/documentos/export-module",
        json={
            "doc_id": doc_id,
            "module_id": "esbelteces",
            "bundle_base64": _zip_base64({"esbelteces/reporte.json": json.dumps(ESBELTECES_JSON).encode()}),
        },
    )

    cuerpo = client.get("/v1/documentos/storage", params={"doc_id": doc_id}).json()

    assert set(cuerpo) == set(_contrato("GET /v1/documentos/storage")["keys"])
    assert cuerpo["modo"] == "local"
    documento = cuerpo["documento"]
    assert documento["ruta"] == str(word)
    assert documento["manifiesto"] == {
        "ruta": str(usuario / "Memoria.report_manifest.json"),
        "existe": True,
    }
    assert documento["carpeta_reporte"]["ruta"] == str(usuario / "Memoria_reporte")
    assert documento["carpeta_reporte"]["modulos"] == ["esbelteces"]
    assert documento["respaldos"]["ruta"] == str(usuario / "report_backups")
    assert documento["registros"]["ruta"] == str(usuario / "report_logs")
    assert client.get("/v1/documentos/storage").json()["documento"] is None
