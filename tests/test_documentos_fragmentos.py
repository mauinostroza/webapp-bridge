"""Rutas plan/aplicador de documentos Word locales (``bridge/documentos_fragmentos.py``).

El montaje (token, registro aislado, documento de prueba y ancla de esbelteces) es el de
``tests/test_bridge_documentos_locales.py``. El generador se ejecuta como lo haría el servidor:
``generar_fragmento`` sobre el lienzo ligero y los datos del paquete.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import linked_documents
from backend.report_builder.engine import generator_registry
from backend.report_builder.engine.anchor_locator import find_sdt_by_tag, get_sdt_content
from backend.report_builder.engine.fragmento import Fragmento, generar_fragmento
from backend.report_builder.engine.hasher import hash_content
from backend.report_builder.engine.sync_engine import build_style_map, resolve_generator
from bridge import documentos_fragmentos as df
from bridge import documentos_locales as dl
from bridge import pairing

SECCION = "Verificaciones estructurales"
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
def client():
    from bridge import sap2000_agent

    cliente = TestClient(sap2000_agent.app, base_url="http://127.0.0.1:8765")
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
    document.add_heading(SECCION, level=1)
    document.add_paragraph("Texto de la sección.")
    document.save(str(path))
    return path


def _enlazar(client, ruta: Path) -> str:
    with patch.object(linked_documents, "pick_docx", lambda: str(ruta)):
        respuesta = client.post("/v1/documentos/pick")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["doc_id"]


def _con_ancla(client, carpeta: Path) -> tuple[str, Path, str]:
    """Documento nuevo con la sección de esbelteces ya anclada. Devuelve (doc_id, ruta, tag)."""
    carpeta.mkdir(parents=True)
    word = _docx(carpeta / "Memoria.docx")
    doc_id = _enlazar(client, word)
    prep = client.post("/v1/documentos/prepare", json={"doc_id": doc_id})
    assert prep.status_code == 200, prep.text
    posiciones = client.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    indice = next(p["index"] for p in posiciones if p["heading_text"] == SECCION)
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
    return doc_id, word, ancla.json()["tag"]


def _hash_del_ancla(word: Path, tag: str) -> str:
    coincidencias = find_sdt_by_tag(Document(str(word)).element, tag)
    assert len(coincidencias) == 1
    return hash_content(list(get_sdt_content(coincidencias[0])))


def _generador_de(module_id: str):
    import_path = next(
        g.import_path for g in generator_registry.discover_generators() if g.module_id == module_id
    )
    return resolve_generator(import_path)


def _generar_como_servidor(modulo: dict, lienzo: bytes) -> Fragmento:
    """Lo que hará el servidor con un paquete: datos del ZIP, generador y lienzo ligero."""
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(modulo["paquete_b64"]))) as archivo:
        datos = json.loads(archivo.read(f"{modulo['module_id']}.json"))
    return generar_fragmento(
        _generador_de(modulo["module_id"]),
        datos,
        build_style_map(Document(io.BytesIO(lienzo))),
        lienzo,
        titulo=modulo["titulo"],
        desplazamiento=modulo["desplazamiento"],
    )


def _plan(client, doc_id: str) -> dict:
    respuesta = client.post("/v1/documentos/plan-fragmentos", json={"doc_id": doc_id})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _aplicar(client, doc_id: str, huella: str, fragmentos: dict) -> object:
    return client.post(
        "/v1/documentos/aplicar-fragmentos",
        json={"doc_id": doc_id, "huella_documento": huella, "fragmentos": fragmentos},
    )


# --- Flujo completo -------------------------------------------------------------


def test_plan_y_aplicar_dejan_el_mismo_contenido_que_una_sincronizacion_normal(client, usuario):
    doc_a, word_a, tag_a = _con_ancla(client, usuario / "A")
    doc_b, word_b, tag_b = _con_ancla(client, usuario / "B")

    referencia = client.post("/v1/documentos/sync", json={"doc_id": doc_b, "dry_run": False})
    assert referencia.status_code == 200, referencia.text
    assert referencia.json()["results"][0]["status"] == "applied"
    esperado = _hash_del_ancla(word_b, tag_b)

    plan = _plan(client, doc_a)
    assert set(plan) == {"contrato", "huella_documento", "huella_estilos", "lienzo_b64", "modulos"}
    assert plan["contrato"] == 1
    assert plan["huella_documento"] == hashlib.sha256(word_a.read_bytes()).hexdigest()
    assert len(plan["modulos"]) == 1
    modulo = plan["modulos"][0]
    assert {"module_id", "tag", "titulo", "desplazamiento", "paquete_b64"} <= set(modulo)
    assert modulo["module_id"] == "esbelteces" and modulo["tag"] == tag_a

    lienzo = base64.b64decode(plan["lienzo_b64"])
    fragmento = _generar_como_servidor(modulo, lienzo)

    aplicado = _aplicar(
        client,
        doc_a,
        plan["huella_documento"],
        {
            "esbelteces": {
                "datos_b64": base64.b64encode(fragmento.datos).decode(),
                "content_hash": fragmento.content_hash,
            }
        },
    )
    assert aplicado.status_code == 200, aplicado.text
    cuerpo = aplicado.json()
    assert set(cuerpo) == {"ok", "dry_run", "results"}
    assert cuerpo["ok"] is True and cuerpo["dry_run"] is False
    assert cuerpo["results"][0]["status"] == "applied"
    assert "new_last_sync" in cuerpo["results"][0]
    assert "docx_backup" not in (cuerpo["results"][0]["new_last_sync"] or {})

    assert _hash_del_ancla(word_a, tag_a) == esperado
    assert client.get("/v1/documentos/manifest", params={"doc_id": doc_a}).status_code == 200


def test_aplicar_tras_cambiar_el_documento_responde_409_y_no_lo_toca(client, usuario):
    doc_id, word, _ = _con_ancla(client, usuario / "A")
    plan = _plan(client, doc_id)

    documento = Document(str(word))
    documento.add_paragraph("Cambio hecho a mano después del plan.")
    documento.save(str(word))
    antes = word.read_bytes()

    respuesta = _aplicar(client, doc_id, plan["huella_documento"], {})
    assert respuesta.status_code == 409, respuesta.text
    assert "vuelve a sincronizar" in respuesta.json()["detail"]
    assert word.read_bytes() == antes


def test_aplicar_sin_fragmento_del_modulo_deja_el_error_del_modulo(client, usuario):
    doc_id, word, _ = _con_ancla(client, usuario / "A")
    plan = _plan(client, doc_id)
    antes = word.read_bytes()

    respuesta = _aplicar(client, doc_id, plan["huella_documento"], {})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is False
    resultado = cuerpo["results"][0]
    assert resultado["status"] == "error"
    assert "falta el fragmento de esbelteces" in resultado["detail"]
    assert word.read_bytes() == antes  # sin módulos aplicados no se reescribe el .docx


def test_plan_de_documento_desconocido_responde_404(client):
    respuesta = client.post("/v1/documentos/plan-fragmentos", json={"doc_id": "0" * 32})
    assert respuesta.status_code == 404


def test_aplicar_con_huella_mal_formada_responde_422(client, usuario):
    doc_id, _, _ = _con_ancla(client, usuario / "A")
    respuesta = _aplicar(client, doc_id, "no-es-una-huella", {})
    assert respuesta.status_code == 422


def test_fragmento_con_base64_invalido_responde_422(client, usuario):
    doc_id, _, _ = _con_ancla(client, usuario / "A")
    plan = _plan(client, doc_id)
    fragmentos = {"esbelteces": {"datos_b64": "%%%%%%%%", "content_hash": "sha256:" + "0" * 64}}
    respuesta = _aplicar(client, doc_id, plan["huella_documento"], fragmentos)
    assert respuesta.status_code == 422


# --- Paquete de imágenes -----------------------------------------------------------


def test_paquete_solo_incluye_imagenes_dentro_de_la_carpeta_fuente(tmp_path):
    carpeta = tmp_path / "reporte" / "esbelteces"
    (carpeta / "img").mkdir(parents=True)
    (carpeta / "img" / "a.png").write_bytes(b"\x89PNG-a")
    (carpeta / "x.txt").write_text("no es imagen", encoding="utf-8")
    (tmp_path / "reporte" / "fuera.png").write_bytes(b"fuera")
    datos = {
        "titulo": "T",
        "grafico": {"ruta": "img/a.png"},
        "otro": "../fuera.png",
        "texto": "x.txt",
        "absoluta": str(tmp_path / "reporte" / "fuera.png"),
        "_json_dir": str(carpeta),
    }

    paquete = df._paquete("esbelteces", datos, carpeta)

    with zipfile.ZipFile(io.BytesIO(paquete)) as archivo:
        assert sorted(archivo.namelist()) == ["esbelteces.json", "img/a.png"]
        cuerpo = json.loads(archivo.read("esbelteces.json"))
        assert archivo.read("img/a.png") == b"\x89PNG-a"
    assert "_json_dir" not in cuerpo
    assert cuerpo["grafico"]["ruta"] == "img/a.png"


# --- Documento bloqueado por Word ---------------------------------------------------


def test_quitar_seccion_con_el_documento_bloqueado_responde_409_y_no_500(client, usuario):
    """Un OSError al escribir (Word abierto) debe ser 409 con CORS, no un 500 que el navegador ve como red caída."""
    doc_id, _, tag = _con_ancla(client, usuario / "A")
    with patch("bridge.documentos_locales.service.remove_anchor", side_effect=PermissionError("en uso")):
        respuesta = client.delete(f"/v1/documentos/anchors/{tag}", params={"doc_id": doc_id})
    assert respuesta.status_code == 409, respuesta.text
    assert "Word" in respuesta.json()["detail"]
