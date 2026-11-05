"""Rutas «plan/aplicador» de los documentos Word locales (``/v1/documentos/*-fragmentos``).

El flujo separa la generación del contenido de la escritura en el .docx:

1. ``plan-fragmentos`` (este PC): calcula la huella del documento y del estilo, empaqueta el JSON de
   cada módulo que habría que regenerar (con sus imágenes) y devuelve el lienzo ligero del documento.
   No genera nada: el generador corre donde se llame a ``generar_fragmento`` (el servidor).
2. ``aplicar-fragmentos`` (este PC): recibe los fragmentos ya generados y los pega en las anclas con la
   misma lógica que ``/v1/documentos/sync``. Si el .docx cambió desde el plan, responde 409.

Seguridad: mismo control de acceso que ``/v1/documentos/sync`` (``require_token`` del puente y el
``doc_id`` registrado). Las imágenes del paquete sólo salen de la carpeta fuente del módulo, nunca con
``..`` ni rutas absolutas, y sólo si tienen extensión de imagen.

Contrato de rutas: ver docs/AYUDANTE_LOCAL_DOCUMENTOS.md. Se instala desde
``bridge.documentos_locales.install_routes``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.report_builder import service
from backend.report_builder.engine.fragmento import Fragmento, huella_estilos, lienzo_ligero
from backend.report_builder.engine.sync_engine import DocumentSaveError

CONTRATO_FRAGMENTOS = 1
MAX_FRAGMENTO_BYTES = 20_000_000
# base64 ocupa ~4/3 del binario: el tope de texto se deriva del tope binario.
MAX_FRAGMENTO_BASE64_CHARS = (MAX_FRAGMENTO_BYTES * 4) // 3 + 16
MAX_PAQUETE_BYTES = 20_000_000
MAX_PAQUETE_ARCHIVOS = 200
MAX_IMAGEN_BYTES = 20_000_000
MAX_MODULOS_FRAGMENTOS = 200
EXTENSIONES_IMAGEN = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg"})
_HUELLA_RE = r"^[0-9a-f]{64}$"
# content_hash de los generadores: mismo prefijo que ``engine.hasher.HASH_PREFIX``.
_CONTENT_HASH_RE = r"^sha256:[0-9a-f]{64}$"


class _PaqueteRecogido(Exception):
    """Corta la generación local del dry-run: el paquete ya está recogido, nada más que hacer."""


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)
    force: bool = False


class FragmentoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datos_b64: str = Field(min_length=4, max_length=MAX_FRAGMENTO_BASE64_CHARS)
    content_hash: str = Field(pattern=_CONTENT_HASH_RE)


class AplicarBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=32, max_length=32)
    huella_documento: str = Field(pattern=_HUELLA_RE)
    force: bool = False
    fragmentos: dict[str, FragmentoBody] = Field(default_factory=dict, max_length=MAX_MODULOS_FRAGMENTOS)


def _sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _ruta_imagen(texto: str, raiz: Path) -> tuple[str, Path] | None:
    """Ruta relativa normalizada y fichero real si ``texto`` apunta a una imagen dentro de ``raiz``."""
    texto = texto.replace("\\", "/")
    relativa = PurePosixPath(texto)
    if not texto or relativa.is_absolute() or ":" in texto or ".." in relativa.parts:
        return None
    if relativa.suffix.lower() not in EXTENSIONES_IMAGEN:
        return None
    try:
        candidato = raiz.joinpath(*relativa.parts)
        if candidato.is_symlink() or not candidato.is_file():
            return None
        if not candidato.resolve().is_relative_to(raiz):
            return None
    except (OSError, ValueError):
        return None
    return str(relativa), candidato


def _imagenes_referenciadas(valor: Any, raiz: Path, halladas: dict[str, Path]) -> dict[str, Path]:
    """Recorre el JSON y reúne cada cadena que sea una imagen existente dentro de ``raiz``."""
    if isinstance(valor, dict):
        for hijo in valor.values():
            _imagenes_referenciadas(hijo, raiz, halladas)
    elif isinstance(valor, list):
        for hijo in valor:
            _imagenes_referenciadas(hijo, raiz, halladas)
    elif isinstance(valor, str):
        hallada = _ruta_imagen(valor, raiz)
        if hallada is not None:
            halladas[hallada[0]] = hallada[1]
    return halladas


def _paquete(module_id: str, datos: dict[str, Any], carpeta_fuente: Path) -> bytes:
    """ZIP en memoria con ``<module_id>.json`` (sin ``_json_dir``) y las imágenes que referencia."""
    cuerpo = {clave: valor for clave, valor in datos.items() if clave != "_json_dir"}
    raiz = carpeta_fuente.resolve()
    imagenes = _imagenes_referenciadas(cuerpo, raiz, {})
    if len(imagenes) > MAX_PAQUETE_ARCHIVOS:
        raise ValueError("el módulo referencia demasiadas imágenes")
    buffer = BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        json_bytes = json.dumps(cuerpo, ensure_ascii=False, allow_nan=False).encode("utf-8")
        archive.writestr(f"{module_id}.json", json_bytes)
        for relativa, ruta in imagenes.items():
            tamano = ruta.stat().st_size
            total += tamano
            if tamano > MAX_IMAGEN_BYTES or total > MAX_PAQUETE_BYTES:
                raise ValueError("las imágenes del módulo superan el tamaño permitido")
            archive.writestr(relativa, ruta.read_bytes())
    paquete = buffer.getvalue()
    if len(paquete) > MAX_PAQUETE_BYTES:
        raise ValueError("el paquete del módulo supera 20 MB")
    return paquete


def _decodificar(module_id: str, cuerpo: FragmentoBody) -> Fragmento:
    try:
        datos = base64.b64decode(cuerpo.datos_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f"El fragmento de {module_id} no es base64 válido") from exc
    if len(datos) > MAX_FRAGMENTO_BYTES:
        raise HTTPException(413, f"El fragmento de {module_id} supera 20 MB")
    return Fragmento(datos, cuerpo.content_hash)


def install_routes(app: FastAPI) -> None:
    """Instala ``/v1/documentos/plan-fragmentos`` y ``/v1/documentos/aplicar-fragmentos``."""
    # Import diferido: ``documentos_locales`` llama a esta función desde su propio ``install_routes``.
    from bridge.documentos_locales import _cambio_seguro, _documento, _manifiesto_seguro

    @app.post("/v1/documentos/plan-fragmentos")
    def plan_fragmentos(body: PlanBody):
        _, doc = _documento(body.doc_id)
        manifest_path = _manifiesto_seguro(doc)
        # Una sola lectura del .docx: la huella y el lienzo describen exactamente el mismo contenido.
        origen = doc.read_bytes()
        recogidos: list[dict[str, Any]] = []
        errores: list[str] = []

        def recolector(entry, data, carpeta_fuente, titulo, desplazamiento):
            try:
                paquete = _paquete(entry.module_id, data, carpeta_fuente)
            except Exception as exc:  # noqa: BLE001 - el motivo se devuelve a la persona, no se oculta
                errores.append(f"{entry.module_id}: {exc}")
            else:
                recogidos.append(
                    {
                        "module_id": entry.module_id,
                        "tag": entry.tag,
                        "titulo": titulo,
                        "desplazamiento": desplazamiento,
                        "paquete_b64": base64.b64encode(paquete).decode("ascii"),
                    }
                )
            # Con el dry-run no se genera ni se pega nada: el motor sólo necesita pasar de largo.
            raise _PaqueteRecogido()

        try:
            service.sync(manifest_path, dry_run=True, force=body.force, generador_externo=recolector)
        except (service.SyncEngineError, service.StyleMapError, DocumentSaveError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if errores:
            raise HTTPException(422, "No se pudo preparar el paquete de " + "; ".join(errores))
        return {
            "contrato": CONTRATO_FRAGMENTOS,
            "huella_documento": _sha256(origen),
            "huella_estilos": huella_estilos(origen),
            "lienzo_b64": base64.b64encode(lienzo_ligero(origen)).decode("ascii"),
            "modulos": recogidos,
        }

    @app.post("/v1/documentos/aplicar-fragmentos")
    def aplicar_fragmentos(body: AplicarBody):
        _, doc = _documento(body.doc_id)
        fragmentos = {module_id: _decodificar(module_id, f) for module_id, f in body.fragmentos.items()}
        if _sha256(doc.read_bytes()) != body.huella_documento:
            raise HTTPException(409, "El documento cambió desde que se preparó; vuelve a sincronizar")
        manifest_path = _manifiesto_seguro(doc)

        def generador(entry, data, carpeta_fuente, titulo, desplazamiento):
            fragmento = fragmentos.get(entry.module_id)
            if fragmento is None:
                raise ValueError(f"falta el fragmento de {entry.module_id}")
            return fragmento

        try:
            with _cambio_seguro(doc):
                results = service.sync(
                    manifest_path, dry_run=False, force=body.force, generador_externo=generador
                )
        except (service.SyncEngineError, service.StyleMapError, DocumentSaveError) as exc:
            raise HTTPException(422, str(exc)) from exc
        for result in results:
            if result.get("new_last_sync"):
                result["new_last_sync"].pop("docx_backup", None)
        return {
            "ok": not any(r["status"] == "error" for r in results),
            "dry_run": False,
            "results": results,
        }
