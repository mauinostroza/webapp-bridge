"""El flujo plan/aplicador del puente funciona sin poder importar módulos de generadores.

Durante pick, prepare, anchors, plan-fragmentos y aplicar-fragmentos se bloquea la importación de
``backend.report_builder.generators.<x>``. Los helpers ``base``, ``formato``, ``__init__`` y ``schemas``
no son generadores y siguen permitidos. Cada intento de importar un generador queda registrado y el
test falla si hubo alguno. El fragmento se genera fuera del bloqueo, como lo haría el servidor.

El montaje (token, registro aislado, documento de prueba y ancla de esbelteces) es el de
``tests/test_documentos_fragmentos.py``.
"""

from __future__ import annotations

import base64
import importlib
import importlib.abc
import io
import json
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import linked_documents
from backend.report_builder import manifest_ops, service
from backend.report_builder.engine import generator_registry
from backend.report_builder.engine.anchor_locator import find_sdt_by_tag, get_sdt_content
from backend.report_builder.engine.fragmento import Fragmento, generar_fragmento
from backend.report_builder.engine.hasher import hash_content
from backend.report_builder.engine.manifest_loader import load_manifest, save_manifest
from backend.report_builder.engine.sync_engine import build_style_map, resolve_generator
from bridge import documentos_locales as dl
from bridge import pairing

SECCION = "Verificaciones estructurales"
ESBELTECES_JSON = {"results": [{"frame_name": "V1", "section_name": "W310", "norma": "NCh2369_2025"}]}

PREFIJO_GENERADORES = "backend.report_builder.generators."
# Helpers y paquete de schemas: no son generadores, así que no se bloquean.
NO_GENERADORES = {"__init__", "base", "formato", "schemas"}


def _es_generador(nombre: str) -> bool:
    if not nombre.startswith(PREFIJO_GENERADORES):
        return False
    return nombre[len(PREFIJO_GENERADORES) :].split(".")[0] not in NO_GENERADORES


class _BloqueoGeneradores(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.intentos: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if _es_generador(fullname):
            self.intentos.append(fullname)
            raise ImportError(f"Bloqueado: el puente no debe importar {fullname}")
        return None


@contextmanager
def sin_generadores() -> Iterator[_BloqueoGeneradores]:
    """Quita los generadores de sys.modules y bloquea volver a importarlos mientras dure el bloque."""
    ocultos = {nombre: modulo for nombre, modulo in sys.modules.items() if _es_generador(nombre)}
    for nombre in ocultos:
        del sys.modules[nombre]
    bloqueo = _BloqueoGeneradores()
    sys.meta_path.insert(0, bloqueo)
    try:
        yield bloqueo
    finally:
        sys.meta_path.remove(bloqueo)
        sys.modules.update(ocultos)


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
    """Documento nuevo con la sección de esbelteces ya anclada (pick, prepare, anchors)."""
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


def _generar_como_servidor(modulo: dict, lienzo: bytes) -> Fragmento:
    """Lo que hará el servidor con un paquete: datos del ZIP, generador y lienzo ligero."""
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(modulo["paquete_b64"]))) as archivo:
        datos = json.loads(archivo.read(f"{modulo['module_id']}.json"))
    import_path = next(
        g.import_path for g in generator_registry.discover_generators() if g.module_id == modulo["module_id"]
    )
    return generar_fragmento(
        resolve_generator(import_path),
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


# --- Pruebas ----------------------------------------------------------------------


def test_el_bloqueo_de_prueba_realmente_impide_importar_un_generador():
    """Sin esto, el resto del test podría pasar sin bloquear nada."""
    with sin_generadores() as bloqueo:
        with pytest.raises(ImportError):
            importlib.import_module("backend.report_builder.generators.esbelteces")
        assert bloqueo.intentos == ["backend.report_builder.generators.esbelteces"]
        # Los helpers siguen importables.
        importlib.import_module("backend.report_builder.generators.base")


def test_catalogo_funciona_sin_generadores():
    with sin_generadores() as bloqueo:
        infos = generator_registry.catalogo_generadores()
    assert len(infos) == 13
    assert bloqueo.intentos == []


def test_flujo_completo_del_puente_sin_importar_generadores(client, usuario):
    # Referencia: sincronización normal en otra copia (aquí sí se pueden importar generadores).
    doc_b, word_b, tag_b = _con_ancla(client, usuario / "B")
    referencia = client.post("/v1/documentos/sync", json={"doc_id": doc_b, "dry_run": False})
    assert referencia.status_code == 200, referencia.text
    assert referencia.json()["results"][0]["status"] == "applied"
    esperado = _hash_del_ancla(word_b, tag_b)

    # Puente sin generadores: pick, prepare, anchors y plan.
    with sin_generadores() as bloqueo_plan:
        doc_a, word_a, tag_a = _con_ancla(client, usuario / "A")
        plan = _plan(client, doc_a)
    assert bloqueo_plan.intentos == []
    assert plan["contrato"] == 1
    assert len(plan["modulos"]) == 1
    modulo = plan["modulos"][0]
    assert modulo["module_id"] == "esbelteces" and modulo["tag"] == tag_a

    # El fragmento lo genera el servidor, fuera del bloqueo.
    fragmento = _generar_como_servidor(modulo, base64.b64decode(plan["lienzo_b64"]))

    # Aplicar con el bloqueo activo.
    with sin_generadores() as bloqueo_aplicar:
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
    assert bloqueo_aplicar.intentos == []
    assert aplicado.status_code == 200, aplicado.text
    cuerpo = aplicado.json()
    assert cuerpo["ok"] is True
    assert cuerpo["results"][0]["status"] == "applied"

    assert _hash_del_ancla(word_a, tag_a) == esperado


def test_manifest_y_catalogo_no_importan_generadores(client, usuario):
    doc_id, _, _ = _con_ancla(client, usuario / "A")
    with sin_generadores() as bloqueo:
        respuesta = client.get("/v1/documentos/manifest", params={"doc_id": doc_id})
        infos = generator_registry.catalogo_generadores()
    assert respuesta.status_code == 200, respuesta.text
    assert bloqueo.intentos == []
    assert "esbelteces" in {g.module_id for g in infos}


def test_motor_rechaza_en_plan_un_generador_fuera_del_catalogo(client, usuario):
    """Con generador_externo no se importa nada, pero el import_path sigue debiendo estar en el catálogo."""
    doc_id, word, tag = _con_ancla(client, usuario / "A")
    manifest_path = manifest_ops.manifest_path_for(word)
    manifiesto = load_manifest(manifest_path)
    manifiesto.modules[0].generator = "generators.no_existe:build"
    save_manifest(manifiesto, manifest_path)

    llamadas = []

    def recolector(entry, data, carpeta_fuente, titulo, desplazamiento):
        llamadas.append(entry.module_id)
        raise AssertionError("no debe llegar al generador externo")

    with sin_generadores() as bloqueo:
        resultados = service.sync(manifest_path, dry_run=True, generador_externo=recolector)
    assert bloqueo.intentos == []
    assert llamadas == []
    assert resultados[0]["status"] == "error"
    assert "Generador no reconocido" in resultados[0]["detail"]
