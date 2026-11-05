"""Documentos Word locales: el puente guarda el informe en el PC del usuario.

La web alojada (Render, o cualquier otro servidor) es la interfaz; el archivo de
trabajo vive aquí. El .docx elegido se modifica en su sitio, el manifiesto
(``<nombre>.report_manifest.json``) se crea junto a él y la carpeta
``<nombre>_reporte/<slug_modulo>/`` guarda el espejo local (JSON e imágenes) de
cada módulo exportado. El servidor sólo recibe los paquetes como respaldo.

Seguridad (el origen de la web no es de plena confianza):

- Ninguna ruta lee ni escribe una ruta que la web envíe. Sólo se trabaja con un
  ``doc_id`` registrado por el diálogo nativo (``pick``) o con una ruta pegada a
  mano y validada (``link``, misma validación que ``backend.linked_documents``).
- Todo lo que se escribe queda dentro de la carpeta del .docx elegido, comprobado
  con ``resolve()`` antes de cada escritura. Los nombres de carpeta e imagen se
  sanean (``backend.report_builder.carpeta_reporte``).
- Las fuentes JSON de un informe son sólo el espejo ``_reporte`` del propio
  documento o un JSON ``json_inline`` que se valida antes de escribirse.
- ``open-in-word`` abre el .docx registrado con el programa asociado; no ejecuta
  nada más, sin shell.
- La autenticación, CORS, Host y alcances son los del puente (``require_token``,
  ``bridge/alcances.py``): la web alojada tiene acceso completo a esta familia de
  rutas y los demás orígenes compartidos no tienen acceso.

Contrato de rutas: ver docs/AYUDANTE_LOCAL_DOCUMENTOS.md. Las funciones de
negocio son las mismas de ``backend/routers/projects.py`` (informe vinculado en
su sitio); cuando se cambie una, cambiar la otra.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend import linked_documents
from backend.report_builder import carpeta_reporte as espejo
from backend.report_builder import manifest_ops, modulos_exportacion, service
from backend.report_builder.engine.anchor_writer import AnchorAlreadyExistsError
from backend.report_builder.engine.atomic_write import replace_with
from backend.report_builder.engine.generator_registry import catalogo_generadores
from backend.report_builder.engine.manifest_loader import ManifestError, load_manifest, save_manifest
from backend.report_builder.engine.manifest_paths import buscar_por_cola, es_absoluta, resolver, ruta_relativa
from backend.report_builder.engine.schema_validation import (
    SchemaValidationError,
    load_and_validate,
    validate_against_schema,
)
from backend.report_builder.engine.sync_engine import DocumentSaveError
from runtime_paths import user_data_dir

log = logging.getLogger(__name__)

# Identificador opaco: uuid4 en hexadecimal (32 caracteres).
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")
MAX_REGISTRADOS = 200
MAX_JSON_BYTES = 20_000_000
MAX_PAQUETE_BYTES = 20_000_000
MAX_PAQUETE_ARCHIVOS = 200
MAX_PAQUETE_TOTAL_BYTES = 100_000_000
MAX_ARCHIVO_BYTES = 20_000_000
# base64 ocupa ~4/3 del binario: el tope de texto se deriva del tope binario.
MAX_BASE64_CHARS = (MAX_PAQUETE_BYTES * 4) // 3 + 16
KINDS_PAQUETE = frozenset({"docx", "json", "png", "jpg", "jpeg", "csv", "xlsx"})
CARPETA_RESPALDOS = "report_backups"
CARPETA_REGISTROS = "report_logs"


# --- Registro de documentos (id opaco -> ruta) -------------------------------


def _registro_path() -> Path:
    return user_data_dir("EngineeringWorkbench") / "documentos.json"


def _misma_ruta(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class RegistroDocumentos:
    """Documentos elegidos por la persona, en memoria y persistidos en ``documentos.json``.

    La carga desde disco es perezosa (la primera vez que se usa) para que importar el
    módulo no toque el disco y para que las pruebas puedan redirigir ``XDG_DATA_HOME``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: dict[str, str] = {}
        self._cargado = False

    def reset_for_tests(self) -> None:
        with self._lock:
            self._docs.clear()
            self._cargado = False

    def _cargar_locked(self) -> None:
        if self._cargado:
            return
        self._cargado = True
        try:
            raw = json.loads(_registro_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        docs = raw.get("documentos") if isinstance(raw, dict) else None
        if not isinstance(docs, dict):
            return
        for doc_id, ruta in docs.items():
            if isinstance(doc_id, str) and _ID_RE.match(doc_id) and isinstance(ruta, str) and ruta:
                self._docs[doc_id] = ruta

    def _guardar_locked(self) -> None:
        path = _registro_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".documentos-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"version": 1, "documentos": self._docs}, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError:
            # El registro en memoria sigue sirviendo en esta sesión; sólo se pierde el recuerdo.
            log.warning("No se pudo guardar el registro de documentos en %s", path, exc_info=True)

    def registrar(self, ruta: str) -> str:
        with self._lock:
            self._cargar_locked()
            for doc_id, existente in self._docs.items():
                if _misma_ruta(existente, ruta):
                    return doc_id
            doc_id = uuid.uuid4().hex
            self._docs[doc_id] = ruta
            while len(self._docs) > MAX_REGISTRADOS:
                self._docs.pop(next(iter(self._docs)))
            self._guardar_locked()
            return doc_id

    def ruta(self, doc_id: str) -> str | None:
        with self._lock:
            self._cargar_locked()
            return self._docs.get(doc_id)

    def todos(self) -> list[tuple[str, str]]:
        with self._lock:
            self._cargar_locked()
            return list(self._docs.items())


_registro = RegistroDocumentos()


def _vigente(path: Path) -> bool:
    """Archivo .docx regular, no enlace simbólico ni temporal de Word (``~$``)."""
    try:
        return (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() == ".docx"
            and not path.name.startswith("~$")
        )
    except OSError:
        return False


def _documento(doc_id: str | None) -> tuple[str, Path]:
    """Ruta registrada del ``doc_id``. 404 si el id no existe; 409 si el archivo ya no está."""
    ruta = _registro.ruta(doc_id) if doc_id and _ID_RE.match(doc_id) else None
    if ruta is None:
        raise HTTPException(404, "Documento desconocido: elíjalo de nuevo con «Elegir documento»")
    path = Path(ruta)
    if not _vigente(path):
        raise HTTPException(
            409,
            f"«{path.name}» ya no está en su ruta o ya no es un .docx válido. "
            "Muévalo de nuevo o vuelva a elegirlo.",
        )
    return ruta, path


def _dentro_de(carpeta: Path, destino: Path) -> None:
    """Defensa en profundidad: nada se escribe fuera de la carpeta del Word elegido."""
    if not destino.resolve().is_relative_to(carpeta.resolve()):
        raise HTTPException(422, "Ruta de escritura fuera de la carpeta del documento")


def _resumen(doc_id: str, ruta: str) -> dict[str, Any]:
    path = Path(ruta)
    return {
        "doc_id": doc_id,
        "nombre": path.name,
        "carpeta_mostrar": str(path.parent),
        "available": _vigente(path),
    }


def _registrar(ruta: str) -> dict[str, Any]:
    path = linked_documents.check_linkable(ruta)  # 422 con el mensaje de la app
    doc_id = _registro.registrar(str(path))
    return {"doc_id": doc_id, "nombre": path.name, "carpeta_mostrar": str(path.parent)}


# --- Generadores y manifiesto ------------------------------------------------


def _generador(module_id: str):
    gen = next((g for g in catalogo_generadores() if g.module_id == module_id), None)
    if gen is None:
        raise HTTPException(422, "Módulo desconocido")
    return gen


def _esquema(gen) -> dict[str, Any] | None:
    if not gen.schema_path:
        return None
    return json.loads((Path(service.__file__).parent / gen.schema_path).read_text(encoding="utf-8"))


def _manifiesto_seguro(doc: Path) -> Path:
    """Carga y valida el manifiesto del documento; reancla la ruta si la carpeta se movió.

    Las fuentes de cada módulo sólo pueden estar en el espejo ``_reporte`` del propio documento.
    """
    path = manifest_ops.manifest_path_for(doc)
    if not path.exists():
        raise HTTPException(404, "Primero prepare el documento: todavía no tiene manifiesto")
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        raise HTTPException(422, f"Manifiesto inválido: {exc}") from exc
    base = doc.parent.resolve()
    reporte = espejo.carpeta_reporte(doc).resolve()
    if (base / manifest.project.docx_path).resolve() != doc.resolve():
        # Carpeta movida o manifiesto antiguo con la ruta absoluta de otro equipo: si es el
        # manifiesto de este mismo documento, se reancla al archivo real; si no, se rechaza.
        same_name = Path(manifest.project.docx_path).name == doc.name
        if not same_name or path != manifest_ops.new_manifest_path_for(doc):
            raise HTTPException(422, "Documento de manifiesto inválido")
        manifest.project.docx_path = doc.name
        save_manifest(manifest, path)
    # Fuentes absolutas de una instalación anterior (la carpeta se movió): se reancla cada una al
    # JSON del espejo «_reporte» con el mismo nombre relativo. Las relativas ya funcionan tal cual.
    dirty = False
    for entry in manifest.modules:
        if es_absoluta(entry.source_json):
            hallado = buscar_por_cola(entry.source_json, [reporte])
            if hallado is not None:
                entry.source_json = ruta_relativa(hallado, base)
                dirty = True
    if dirty:
        save_manifest(manifest, path)
    gens = {g.module_id: g for g in catalogo_generadores()}
    for entry in manifest.modules:
        gen = gens.get(entry.module_id)
        if gen is None or entry.generator != gen.import_path or entry.json_schema_ref != gen.schema_path:
            raise HTTPException(422, "Generador o esquema no permitido")
        if not resolver(entry.source_json, base).is_relative_to(reporte):
            raise HTTPException(422, "Fuente fuera de la carpeta _reporte del documento")
    return path


@contextmanager
def _cambio_seguro(doc: Path) -> Iterator[None]:
    """Restaura el .docx y el manifiesto si la escritura falla (igual que projects.report_change)."""
    manifest = manifest_ops.manifest_path_for(doc)
    original = doc.read_bytes()
    original_manifest = manifest.read_bytes() if manifest.exists() else None
    try:
        yield
    except Exception:
        with suppress(OSError):
            if doc.read_bytes() != original:
                replace_with(doc, lambda temp: temp.write_bytes(original))
        with suppress(OSError):
            if original_manifest is None:
                manifest.unlink(missing_ok=True)
            else:
                replace_with(manifest, lambda temp: temp.write_bytes(original_manifest))
        raise


# --- Espejo _reporte ---------------------------------------------------------


def _fuente_en_reporte(doc: Path, relativa: str) -> Path:
    """JSON existente dentro de ``_reporte`` a partir de una ruta relativa a esa carpeta."""
    texto = relativa.replace("\\", "/")
    partes = PurePosixPath(texto).parts
    if not texto or PurePosixPath(texto).is_absolute() or ":" in texto or ".." in partes:
        raise HTTPException(422, "La fuente debe ser una ruta relativa dentro de la carpeta _reporte")
    raiz = espejo.carpeta_reporte(doc)
    candidato = raiz.joinpath(*partes)
    if not candidato.resolve().is_relative_to(raiz.resolve()):
        raise HTTPException(422, "La fuente queda fuera de la carpeta _reporte")
    if not candidato.is_file() or candidato.is_symlink() or candidato.suffix.lower() != ".json":
        raise HTTPException(422, "No hay un JSON de ese módulo en la carpeta _reporte del documento")
    return candidato


def _escribir_json_inline(doc: Path, module_id: str, datos: dict[str, Any]) -> Path:
    """Escribe el JSON recibido en el cuerpo como espejo del módulo (sin tocar sus imágenes)."""
    contenido = json.dumps(datos, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(contenido) > MAX_JSON_BYTES:
        raise HTTPException(413, "El JSON supera 20 MB")
    destino = espejo.archivo_json_modulo(doc, module_id)
    _dentro_de(espejo.carpeta_reporte(doc), destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    replace_with(destino, lambda temp: temp.write_bytes(contenido))
    return destino


def _ruta_zip_segura(name: str, vistos: dict[str, Any]) -> PurePosixPath:
    """Misma regla que projects.check_zip_path: sin rutas absolutas, «..», «:» ni duplicados."""
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or ":" in name or str(path) in vistos:
        raise ValueError("Ruta inválida o duplicada")
    if str(path) == ".":
        raise ValueError("Ruta inválida o duplicada")
    return path


def _leer_miembro(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limite: int) -> bytes:
    """Lee con tope real: no se confía sólo en el tamaño declarado en el ZIP."""
    with archive.open(info) as handle:
        raw = handle.read(limite + 1)
    if len(raw) > limite:
        raise ValueError("Un archivo supera el tamaño permitido")
    return raw


def _leer_paquete(contenido: bytes) -> dict[str, dict[str, Any]]:
    """Desempaqueta un ZIP de módulo con las mismas reglas que POST /bundles de projects."""
    entradas: dict[str, dict[str, Any]] = {}
    try:
        with zipfile.ZipFile(BytesIO(contenido)) as archive:
            infos = [x for x in archive.infolist() if not x.is_dir()]
            if len(infos) > MAX_PAQUETE_ARCHIVOS or sum(x.file_size for x in infos) > MAX_PAQUETE_TOTAL_BYTES:
                raise ValueError("Paquete demasiado grande")
            for info in infos:
                name = info.filename.replace("\\", "/")
                path = _ruta_zip_segura(name, entradas)
                kind = path.suffix.lower().lstrip(".")
                if kind not in KINDS_PAQUETE:
                    raise ValueError("Formato no permitido dentro del paquete")
                if info.file_size > MAX_ARCHIVO_BYTES:
                    raise ValueError("Un archivo supera 20 MB")
                raw = _leer_miembro(archive, info, MAX_ARCHIVO_BYTES)
                entrada: dict[str, Any] = {"name": str(path), "kind": kind, "content": raw}
                if kind == "json":
                    entrada["data"] = _json_objeto(raw)
                entradas[str(path)] = entrada
    except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(422, f"ZIP inválido: {exc}") from exc
    if not entradas:
        raise HTTPException(422, "El ZIP no contiene archivos")
    return entradas


def _json_objeto(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("El JSON no es válido") from exc
    if not isinstance(value, dict):
        raise ValueError("El JSON del módulo debe ser un objeto")
    return value


def _reescribir_referencias(valor: Any, padre: PurePosixPath, mapa: dict[str, str]) -> Any:
    """Las referencias de imagen del JSON son relativas a su carpeta dentro del paquete."""
    if isinstance(valor, dict):
        return {k: _reescribir_referencias(v, padre, mapa) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_reescribir_referencias(v, padre, mapa) for v in valor]
    if isinstance(valor, str):
        return mapa.get(str(padre / valor.replace("\\", "/")), valor)
    return valor


# --- Rutas --------------------------------------------------------------------


class DocBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)


class AnchorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)
    module_id: str = Field(min_length=1, max_length=80)
    # Ruta relativa a la carpeta _reporte (p. ej. «esbelteces/esbelteces.json») o «json_inline».
    source: str = Field(min_length=1, max_length=512)
    datos: dict[str, Any] | None = None
    position_index: int = Field(default=0, ge=0)
    subchapter: bool = False
    insert_at_start: bool = False


class SyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)
    dry_run: bool = True
    force: bool = False


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)
    module_id: str = Field(min_length=1, max_length=80)
    bundle_base64: str = Field(min_length=4, max_length=MAX_BASE64_CHARS)


# Un OSError al escribir el .docx casi siempre es Word con el archivo abierto. Sin esto la excepción sube
# como 500 sin cabeceras CORS y el navegador la ve como «puente no disponible».
_MENSAJE_BLOQUEADO = "No se pudo escribir en el documento: ciérrelo en Word y vuelva a intentarlo."


def install_routes(app: FastAPI) -> None:
    """Instala las rutas ``/v1/documentos/*``. Todas van por ``require_token`` del puente."""

    @app.post("/v1/documentos/pick")
    def pick():
        # Diálogo nativo: proceso hijo en desarrollo, hilo propio en el .exe (ver linked_documents).
        # Es una ruta síncrona, así que FastAPI la ejecuta en su pool y no bloquea el puente.
        try:
            elegido = linked_documents.pick_docx()
        except linked_documents.PickerUnavailable as exc:
            raise HTTPException(501, str(exc)) from exc
        if elegido is None:
            # Misma forma que el caso con documento, para que la web no tenga que bifurcar.
            return {"cancelled": True, "doc_id": None, "nombre": None, "carpeta_mostrar": None}
        return {"cancelled": False, **_registrar(elegido)}

    @app.get("/v1/documentos/list")
    def list_documents():
        return {"documentos": [_resumen(doc_id, ruta) for doc_id, ruta in _registro.todos()]}

    @app.post("/v1/documentos/prepare")
    def prepare(body: DocBody):
        _, doc = _documento(body.doc_id)
        try:
            revision = service.revisar_documento(doc)
        except service.DocumentLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except service.DocumentCheckError as exc:
            raise HTTPException(422, str(exc)) from exc
        manifest = manifest_ops.manifest_path_for(doc)
        created = False
        if manifest.exists():
            try:
                load_manifest(manifest)
            except ManifestError as exc:
                raise HTTPException(
                    409,
                    "El manifiesto que ya existe junto al documento no se puede leer. "
                    "No se ha modificado: revise ese archivo o quítelo y vuelva a preparar el documento.",
                ) from exc
        else:
            _dentro_de(doc.parent, manifest_ops.new_manifest_path_for(doc))
            manifest = manifest_ops.crear_manifiesto_vacio(doc)
            created = True
        return {
            "ok": True,
            "manifest_path": str(manifest),
            "created": created,
            "title_count": revision["title_count"],
            "positions": revision["positions"],
            "warnings": revision["warnings"],
        }

    @app.get("/v1/documentos/positions")
    def positions(doc_id: str):
        _, doc = _documento(doc_id)
        return {"positions": service.list_position_options(doc)}

    @app.get("/v1/documentos/manifest")
    def manifest(doc_id: str):
        _, doc = _documento(doc_id)
        path = _manifiesto_seguro(doc)
        try:
            data = service.load_project_manifest(path)
        except ManifestError as exc:
            raise HTTPException(422, f"Manifiesto inválido: {exc}") from exc
        data["project"]["document_id"] = doc_id
        # Mismo dato que routers/projects.py: posicion de cada ancla en la estructura. Sin el, la UI
        # pinta todas las secciones al final del documento (ver tests/test_bridge_manifest_posicion_ancla.py).
        data["anchor_heading_index"] = service.anchor_heading_offsets(doc)
        return {"manifest": data}

    @app.post("/v1/documentos/anchors")
    def anchors(body: AnchorBody):
        _, doc = _documento(body.doc_id)
        gen = _generador(body.module_id)
        if body.source == "json_inline":
            if body.datos is None:
                raise HTTPException(422, "Con source «json_inline» indique el JSON en «datos»")
            # Se valida antes de escribir nada: un JSON inválido no deja basura en _reporte.
            esquema = _esquema(gen)
            if esquema is not None:
                errores = validate_against_schema(body.datos, esquema)
                if errores:
                    raise HTTPException(422, str(SchemaValidationError(errores)))
            fuente = _escribir_json_inline(doc, body.module_id, body.datos)
        else:
            fuente = _fuente_en_reporte(doc, body.source)
            if gen.schema_path:
                try:
                    load_and_validate(fuente, Path(service.__file__).parent / gen.schema_path)
                except SchemaValidationError as exc:
                    raise HTTPException(422, str(exc)) from exc
        try:
            with _cambio_seguro(doc):
                result = service.install_anchor(
                    docx_path=doc,
                    module_id=body.module_id,
                    json_path=fuente,
                    position_index=body.position_index,
                    subchapter=body.subchapter,
                    insert_at_start=body.insert_at_start,
                )
        except (
            ValueError,
            SchemaValidationError,
            ManifestError,
            DocumentSaveError,
            AnchorAlreadyExistsError,
        ) as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, _MENSAJE_BLOQUEADO) from exc
        return {
            "ok": True,
            "tag": result["tag"],
            "module_id": body.module_id,
            "json_relativo": espejo.ruta_relativa_al_docx(doc, fuente),
        }

    @app.delete("/v1/documentos/anchors/{tag}")
    def remove_anchor(tag: str, doc_id: str):
        if not _TAG_RE.match(tag):
            raise HTTPException(422, "Etiqueta de sección no válida")
        _, doc = _documento(doc_id)
        try:
            with _cambio_seguro(doc):
                removed = service.remove_anchor(docx_path=doc, tag=tag)
        except DocumentSaveError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, _MENSAJE_BLOQUEADO) from exc
        if not removed:
            raise HTTPException(404, "No se encontró esta sección en el documento")
        return {"ok": True}

    @app.post("/v1/documentos/sync")
    def sync(body: SyncBody):
        _, doc = _documento(body.doc_id)
        manifest_path = _manifiesto_seguro(doc)
        try:
            with _cambio_seguro(doc):
                results = service.sync(manifest_path, dry_run=body.dry_run, force=body.force)
        except (service.SyncEngineError, service.StyleMapError, DocumentSaveError) as exc:
            raise HTTPException(422, str(exc)) from exc
        for result in results:
            if result.get("new_last_sync"):
                result["new_last_sync"].pop("docx_backup", None)
        return {
            "ok": not any(r["status"] == "error" for r in results),
            "dry_run": body.dry_run,
            "results": results,
        }

    @app.post("/v1/documentos/export-module")
    def export_module(body: ExportBody):
        _, doc = _documento(body.doc_id)
        _generador(body.module_id)
        try:
            contenido = base64.b64decode(body.bundle_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, "bundle_base64 no es base64 válido") from exc
        if len(contenido) > MAX_PAQUETE_BYTES:
            raise HTTPException(413, "Máximo 20 MB por paquete")
        entradas = _leer_paquete(contenido)
        jsons = [e for e in entradas.values() if e["kind"] == "json"]
        if len(jsons) != 1:
            raise HTTPException(422, "El paquete debe traer exactamente un JSON de módulo")
        item = jsons[0]
        detectado = modulos_exportacion.detectar_modulo(item["name"], item["data"])
        if detectado is not None and detectado != body.module_id:
            raise HTTPException(422, "El JSON del paquete parece de otro módulo")
        imagenes = [e for e in entradas.values() if e["kind"] != "json"]
        nombres = espejo.nombres_unicos([e["name"] for e in imagenes])
        mapa = {e["name"]: f"{espejo.CARPETA_IMAGENES}/{n}" for e, n in zip(imagenes, nombres, strict=True)}
        datos = _reescribir_referencias(item["data"], PurePosixPath(item["name"]).parent, mapa)
        json_bytes = json.dumps(datos, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            escrito = espejo.escribir_modulo(
                doc,
                body.module_id,
                json_bytes,
                [(n, e["content"]) for e, n in zip(imagenes, nombres, strict=True)],
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(409, f"No se pudo escribir la carpeta _reporte: {exc}") from exc
        return {
            "slug": espejo.slug_modulo(body.module_id),
            "json_relativo": espejo.ruta_relativa_al_docx(doc, escrito["json"]),
            "n_imagenes": len(imagenes),
            "ruta": str(espejo.carpeta_modulo(doc, body.module_id)),
        }

    @app.post("/v1/documentos/open-in-word")
    def open_in_word(body: DocBody):
        _, doc = _documento(body.doc_id)
        try:
            _abrir_con_programa(doc)
        except OSError as exc:
            raise HTTPException(500, f"No se pudo abrir {doc.name} con el programa asociado: {exc}") from exc
        return {"ok": True, "nombre": doc.name}

    @app.get("/v1/documentos/storage")
    def storage(doc_id: str | None = None):
        registro = _registro_path()
        salida: dict[str, Any] = {
            "modo": "local",
            "registro": {"ruta": str(registro), "existe": registro.is_file()},
            "documento": None,
        }
        if doc_id is not None:
            _, doc = _documento(doc_id)
            manifiesto = manifest_ops.manifest_path_for(doc)
            reporte = espejo.carpeta_reporte(doc)
            salida["documento"] = {
                "nombre": doc.name,
                "ruta": str(doc),
                "manifiesto": {"ruta": str(manifiesto), "existe": manifiesto.is_file()},
                "carpeta_reporte": {
                    "ruta": str(reporte),
                    "existe": reporte.is_dir(),
                    "modulos": sorted(p.name for p in reporte.iterdir() if p.is_dir())
                    if reporte.is_dir()
                    else [],
                },
                "respaldos": {"ruta": str(doc.parent / CARPETA_RESPALDOS)},
                "registros": {"ruta": str(doc.parent / CARPETA_REGISTROS)},
            }
        return salida

    @app.get("/v1/documentos/backup")
    def backup(doc_id: str):
        # Copia para el respaldo del servidor: el .docx y su manifiesto (si existe) en un ZIP.
        # Sólo lectura; no toca el documento ni el manifiesto.
        _, doc = _documento(doc_id)
        if doc.stat().st_size > MAX_PAQUETE_BYTES:
            raise HTTPException(413, "El documento supera 20 MB: no se puede copiar al servidor")
        manifiesto = manifest_ops.manifest_path_for(doc)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(doc.name, doc.read_bytes())
            if manifiesto.is_file() and not manifiesto.is_symlink():
                archive.writestr(manifiesto.name, manifiesto.read_bytes())
        return Response(content=buffer.getvalue(), media_type="application/zip")

    # Rutas plan/aplicador (generación de fragmentos): ver bridge/documentos_fragmentos.py.
    from bridge.documentos_fragmentos import install_routes as install_fragmentos_routes

    install_fragmentos_routes(app)


def _es_windows() -> bool:
    return sys.platform.startswith("win")


def _abrir_con_programa(path: Path) -> None:
    """Abre ``path`` con el programa asociado del sistema, sin shell."""
    if _es_windows():
        os.startfile(str(path))  # noqa: S606 - ruta del documento registrado por la persona
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603
