"""Manifiesto del Report Builder con rutas RELATIVAS (sin rutas absolutas del PC).

Cubre: instalar un ancla (el JSON no contiene rutas absolutas), sincronizar tras mover la
carpeta entera (Word + manifiesto + `_reporte`), migracion de un manifiesto antiguo con rutas
absolutas, absolutas ajenas (se conservan y se avisan), exportar/importar `.ewproj` y la API
del puente local (`GET /v1/documentos/manifest`, reancla una fuente absoluta antigua).
"""

from __future__ import annotations

import json
import re
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import linked_documents
from backend.report_builder import manifest_ops, service
from backend.report_builder.engine import manifest_paths
from backend.report_builder.engine.generator_registry import GeneratorInfo
from backend.report_builder.engine.heading_scanner import PositionOption
from backend.report_builder.engine.manifest_loader import load_manifest, save_manifest
from backend.report_builder.engine.manifest_schema import Manifest, ModuleEntry, ProjectInfo
from backend.report_builder.engine.sync_engine import STATUS_ERROR, STATUS_UNCHANGED, run_sync

FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "report_builder" / "tests" / "fixtures"
TEMPLATE = FIXTURES / "Template_Example.docx"
CASOS = FIXTURES / "Casos_Test.json"
SCHEMA_PLACAS = "generators/schemas/placas_base.v1.schema.json"
GEN_PLACAS = GeneratorInfo(
    module_id="placas_base", import_path="generators.placas_base:build", schema_path=SCHEMA_PLACAS
)
ESBELTECES_JSON = {"results": [{"frame_name": "V1", "section_name": "W310", "norma": "NCh2369_2025"}]}
SECCION = "Verificaciones estructurales"

# Cualquier ruta absoluta de Windows (C:\ o C:/), UNC, o POSIX (/home, /tmp, /Users, ...).
_ABSOLUTA_EN_TEXTO = re.compile(
    r"[A-Za-z]:[\\/]|\\\\|(?<![\w.])/(?:home|Users|tmp|var|root|mnt|opt|srv|etc|usr)/"
)


def _sin_rutas_absolutas(texto: str, carpeta: Path) -> None:
    assert str(carpeta) not in texto
    assert str(carpeta.parent) not in texto
    assert not _ABSOLUTA_EN_TEXTO.search(texto), texto


def _leer_crudo(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _instalar_placas(carpeta: Path) -> tuple[Path, Path, Path]:
    """Word + JSON en «<Memoria>_reporte/placas_base/» + ancla instalada + manifiesto junto al Word."""
    docx = carpeta / "Memoria.docx"
    shutil.copy2(TEMPLATE, docx)
    destino = carpeta / "Memoria_reporte" / "placas_base"
    destino.mkdir(parents=True)
    json_path = destino / "Casos_Test.json"
    shutil.copy2(CASOS, json_path)
    document = Document(str(docx))
    manifest_ops.install_anchor_and_update_manifest(
        document, docx, "placas_base", str(json_path), PositionOption(label="al final"), GEN_PLACAS
    )
    return docx, json_path, manifest_ops.manifest_path_for(docx)


def test_instalar_ancla_no_escribe_rutas_absolutas(tmp_path):
    carpeta = tmp_path / "Proyecto"
    carpeta.mkdir()
    docx, _json_path, manifest = _instalar_placas(carpeta)

    # Luego sincronizar para que el manifiesto tenga last_sync y respaldo.
    run_sync(manifest)

    texto = manifest.read_text(encoding="utf-8")
    _sin_rutas_absolutas(texto, carpeta)
    crudo = _leer_crudo(manifest)
    assert crudo["project"]["docx_path"] == "Memoria.docx"
    modulo = crudo["modules"][0]
    assert modulo["source_json"] == "Memoria_reporte/placas_base/Casos_Test.json"
    assert modulo["last_sync"]["docx_backup"].startswith("report_backups/Memoria_")
    assert not manifest_paths.es_absoluta(crudo["project"]["docx_path"])
    assert not manifest_paths.es_absoluta(modulo["source_json"])


def test_sincroniza_tras_mover_la_carpeta_entera(tmp_path):
    original = tmp_path / "PC_A" / "Memorias"
    original.mkdir(parents=True)
    _instalar_placas(original)
    manifest_a = original / "Memoria.report_manifest.json"
    assert all(r.status != STATUS_ERROR for r in run_sync(manifest_a))

    movida = tmp_path / "PC_B" / "Otra carpeta" / "Memorias"
    movida.parent.mkdir(parents=True)
    shutil.move(str(original), str(movida))
    manifest_b = movida / "Memoria.report_manifest.json"

    resultados = run_sync(manifest_b)
    assert resultados and all(r.status != STATUS_ERROR for r in resultados), [r.detail for r in resultados]
    assert all(r.status == STATUS_UNCHANGED for r in resultados)
    _sin_rutas_absolutas(manifest_b.read_text(encoding="utf-8"), movida)


def _manifiesto_antiguo(carpeta: Path, json_path: Path) -> Path:
    """Manifiesto como lo escribian las versiones anteriores: rutas absolutas del PC."""
    docx = carpeta / "Memoria.docx"
    manifest = Manifest(
        project=ProjectInfo(name="Memoria", docx_path=str(docx)),
        modules=[
            ModuleEntry(
                module_id="placas_base",
                tag="report:placas_base:seccion",
                source_json=str(json_path),
                generator=GEN_PLACAS.import_path,
                json_schema_ref=SCHEMA_PLACAS,
            )
        ],
    )
    path = carpeta / "Memoria.report_manifest.json"
    # Escritura cruda (save_manifest ya migraria): simula el archivo viejo tal cual.
    path.write_text(
        json.dumps(
            {
                "schema_version": manifest.schema_version,
                "project": {"name": "Memoria", "docx_path": str(docx)},
                "modules": [
                    {
                        "module_id": "placas_base",
                        "tag": "report:placas_base:seccion",
                        "enabled": True,
                        "source_json": str(json_path),
                        "json_schema_ref": SCHEMA_PLACAS,
                        "generator": GEN_PLACAS.import_path,
                        "heading_level_offset": 0,
                        "last_sync": None,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_manifiesto_antiguo_absoluto_funciona_y_se_migra_al_guardar(tmp_path):
    carpeta = tmp_path / "Memorias"
    carpeta.mkdir()
    docx, json_path, _ = _instalar_placas(carpeta)
    antiguo = _manifiesto_antiguo(carpeta, json_path)
    assert str(carpeta) in antiguo.read_text(encoding="utf-8")

    # Compatibilidad: el motor lee y sincroniza el manifiesto antiguo tal cual.
    assert all(r.status != STATUS_ERROR for r in run_sync(antiguo, dry_run=True))

    # Al guardarse, las rutas dentro de la carpeta pasan a relativas.
    save_manifest(load_manifest(antiguo), antiguo)
    crudo = _leer_crudo(antiguo)
    assert crudo["project"]["docx_path"] == "Memoria.docx"
    assert crudo["modules"][0]["source_json"] == "Memoria_reporte/placas_base/Casos_Test.json"
    _sin_rutas_absolutas(antiguo.read_text(encoding="utf-8"), carpeta)
    assert service.load_project_manifest(antiguo)["warnings"] == []


def test_absoluta_ajena_se_conserva_y_se_avisa_sin_filtrar_la_ruta(tmp_path):
    carpeta = tmp_path / "Memorias"
    carpeta.mkdir()
    ajena = tmp_path / "otro_equipo" / "Casos_Test.json"
    ajena.parent.mkdir()
    shutil.copy2(CASOS, ajena)
    docx, _json_path, _ = _instalar_placas(carpeta)
    manifiesto = _manifiesto_antiguo(carpeta, ajena)

    save_manifest(load_manifest(manifiesto), manifiesto)

    crudo = _leer_crudo(manifiesto)
    assert crudo["modules"][0]["source_json"] == str(ajena)  # no se pierde la informacion
    resumen = service.load_project_manifest(manifiesto)
    assert resumen["warnings"], "debe avisar de la ruta absoluta ajena"
    assert all(str(ajena) not in aviso and str(carpeta) not in aviso for aviso in resumen["warnings"])


def _subir(client, pid, nombre, contenido):
    respuesta = client.post(f"/projects/{pid}/files", files={"file": (nombre, contenido)})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("Memoria de cálculo", 0)
    document.add_heading(SECCION, level=1)
    document.add_paragraph("Texto original.")
    flujo = BytesIO()
    document.save(flujo)
    return flujo.getvalue()


# --- Puente local: GET /v1/documentos/manifest ---------------------------------


@pytest.fixture
def puente(tmp_path, monkeypatch):
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


def _documento_del_puente(cliente, usuario, monkeypatch) -> str:
    word = usuario / "Memoria.docx"
    doc = Document()
    doc.add_heading("Memoria de cálculo", 0)
    doc.add_heading(SECCION, level=1)
    doc.add_paragraph("Texto de la sección.")
    doc.save(str(word))
    monkeypatch.setattr(linked_documents, "pick_docx", lambda: str(word))
    doc_id = cliente.post("/v1/documentos/pick").json()["doc_id"]
    assert cliente.post("/v1/documentos/prepare", json={"doc_id": doc_id}).status_code == 200
    return doc_id


def test_puente_instala_ancla_y_el_manifiesto_no_tiene_rutas_del_pc(puente, tmp_path, monkeypatch):
    usuario = tmp_path / "Memorias"
    usuario.mkdir()
    doc_id = _documento_del_puente(puente, usuario, monkeypatch)
    posiciones = puente.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    indice = next(p["index"] for p in posiciones if p["heading_text"] == SECCION)

    ancla = puente.post(
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
    assert ancla.json()["json_relativo"] == "Memoria_reporte/esbelteces/esbelteces.json"

    _sin_rutas_absolutas((usuario / "Memoria.report_manifest.json").read_text(encoding="utf-8"), usuario)
    api = puente.get("/v1/documentos/manifest", params={"doc_id": doc_id}).json()["manifest"]
    assert api["project"]["docx_path"] == "Memoria.docx"
    assert api["modules"][0]["source_json"] == "Memoria_reporte/esbelteces/esbelteces.json"
    _sin_rutas_absolutas(json.dumps(api), usuario)


def test_puente_reancla_fuente_absoluta_de_una_carpeta_movida(puente, tmp_path, monkeypatch):
    usuario = tmp_path / "Memorias"
    usuario.mkdir()
    doc_id = _documento_del_puente(puente, usuario, monkeypatch)
    posiciones = puente.get("/v1/documentos/positions", params={"doc_id": doc_id}).json()["positions"]
    indice = next(p["index"] for p in posiciones if p["heading_text"] == SECCION)
    assert (
        puente.post(
            "/v1/documentos/anchors",
            json={
                "doc_id": doc_id,
                "module_id": "esbelteces",
                "source": "json_inline",
                "datos": ESBELTECES_JSON,
                "position_index": indice,
            },
        ).status_code
        == 200
    )

    # Manifiesto antiguo: la fuente apunta a la carpeta donde estaba antes (otro equipo).
    manifiesto = usuario / "Memoria.report_manifest.json"
    crudo = _leer_crudo(manifiesto)
    crudo["modules"][0]["source_json"] = (
        "C:\\Users\\Ana\\Memorias\\Memoria_reporte\\esbelteces\\esbelteces.json"
    )
    manifiesto.write_text(json.dumps(crudo), encoding="utf-8")

    respuesta = puente.get("/v1/documentos/manifest", params={"doc_id": doc_id})
    assert respuesta.status_code == 200, respuesta.text
    assert (
        _leer_crudo(manifiesto)["modules"][0]["source_json"] == "Memoria_reporte/esbelteces/esbelteces.json"
    )
    _sin_rutas_absolutas(manifiesto.read_text(encoding="utf-8"), usuario)


# --- Piezas puras -------------------------------------------------------------


def test_ruta_relativa_y_resolver_son_inversas(tmp_path):
    carpeta = tmp_path / "Memorias"
    destino = carpeta / "Memoria_reporte" / "x" / "x.json"
    relativa = manifest_paths.ruta_relativa(destino, carpeta)
    assert relativa == "Memoria_reporte/x/x.json"
    assert manifest_paths.resolver(relativa, carpeta) == destino.resolve()


def test_normalizar_detecta_absolutas_de_windows_en_linux():
    assert manifest_paths.es_absoluta("C:\\Users\\Ana\\Memoria.docx")
    assert manifest_paths.es_absoluta("\\\\servidor\\share\\x.json")
    assert not manifest_paths.es_absoluta("Memoria_reporte/x/x.json")
    assert (
        manifest_paths.normalizar("C:\\Users\\Ana\\x.json", Path("/home/u/Memorias"))
        == "C:\\Users\\Ana\\x.json"
    )
