"""Fachada API para report_builder (web + desktop).

Envuelve el motor portado sin UI. Los routers FastAPI deben llamar aquí,
no a engine.* directamente, para estabilizar el contrato HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import docx

from backend.report_builder import manifest_ops
from backend.report_builder.engine.generator_registry import (
    GeneratorInfo,
    catalogo_generadores,
    discover_generators,
)
from backend.report_builder.engine.hasher import HASH_PREFIX, hash_source
from backend.report_builder.engine.heading_scanner import anchor_heading_offsets as _anchor_heading_offsets
from backend.report_builder.engine.heading_scanner import build_position_options
from backend.report_builder.engine.manifest_loader import ManifestError, avisos_de_rutas, load_manifest
from backend.report_builder.engine.sync_engine import (
    DocumentSaveError,
    ModuleSyncResult,
    StyleMapError,
    SyncEngineError,
    build_style_map,
    run_sync,
)
from backend.report_builder.engine.validation import validate_against_docx


def status() -> dict[str, Any]:
    gens = discover_generators()
    return {
        "module": "report_builder",
        "status": "ready",
        "hash_prefix": HASH_PREFIX,
        "generators": [
            {
                "module_id": g.module_id,
                "display_name": g.display_name,
                "import_path": g.import_path,
                "schema_path": g.schema_path,
            }
            for g in gens
        ],
        "ported": [
            "engine/* (sin format_reviewer)",
            "generators/* + schemas",
            "manifest_ops",
            "tests (203 passed)",
            "service facade",
        ],
        "deferred": ["format_reviewer (COM/Windows)", "UI Tk/Qt"],
    }


def hash_json_source(data: dict) -> str:
    return hash_source(data)


def list_generators() -> list[dict[str, Any]]:
    return [
        {
            "module_id": g.module_id,
            "display_name": g.display_name,
            "import_path": g.import_path,
            "schema_path": g.schema_path,
        }
        for g in discover_generators()
    ]


def sync(
    manifest_path: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    generador_externo=None,
) -> list[dict[str, Any]]:
    """Ejecuta run_sync y serializa ModuleSyncResult a dicts JSON-friendly.

    ``generador_externo`` se pasa tal cual a ``run_sync`` (modo plan/aplicador).
    """
    results = run_sync(manifest_path, dry_run=dry_run, force=force, generador_externo=generador_externo)
    return [_result_to_dict(r) for r in results]


def preview_sync(manifest_path: str | Path) -> list[dict[str, Any]]:
    return sync(manifest_path, dry_run=True, force=False)


def load_project_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    return {
        # Avisos sin rutas: rutas absolutas ajenas que se conservan tal cual (ver manifest_loader).
        "warnings": avisos_de_rutas(manifest, manifest_path.parent),
        "schema_version": manifest.schema_version,
        "project": {
            "name": manifest.project.name,
            "docx_path": str(manifest.project.docx_path),
        },
        "modules": [
            {
                "module_id": m.module_id,
                "tag": m.tag,
                "enabled": m.enabled,
                "source_json": m.source_json,
                "json_schema_ref": m.json_schema_ref,
                "generator": m.generator,
                "heading_level_offset": getattr(m, "heading_level_offset", 0),
                "last_sync": None
                if m.last_sync is None
                else {
                    "source_hash": m.last_sync.source_hash,
                    "content_hash": m.last_sync.content_hash,
                    "generated_at": m.last_sync.generated_at,
                    "docx_backup": m.last_sync.docx_backup,
                },
                "source_status": _module_source_status(m, manifest_path.parent),
            }
            for m in manifest.modules
        ],
    }


def validate_manifest_against_docx(manifest_path: str | Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    docx_path = Path(manifest.project.docx_path)
    if not docx_path.is_absolute():
        docx_path = Path(manifest_path).resolve().parent / docx_path
    document = docx.Document(str(docx_path))
    report = validate_against_docx(manifest, document.element)
    return {
        "ok": report.ok,
        "extra_tags": sorted(report.extra_tags),
        "modules": [{"module_id": m.module_id, "tag": m.tag, "status": m.status} for m in report.modules],
    }


def anchor_heading_offsets(docx_path: str | Path) -> dict[str, int]:
    """tag de ancla -> indice (en la lista de posiciones) del titulo que va justo despues de ella."""
    return _anchor_heading_offsets(docx.Document(str(docx_path)))


def list_position_options(docx_path: str | Path) -> list[dict[str, Any]]:
    document = docx.Document(str(docx_path))
    options = build_position_options(document)
    out: list[dict[str, Any]] = []
    for i, opt in enumerate(options):
        heading = getattr(opt, "heading", None)
        out.append(
            {
                "index": i,
                "label": getattr(opt, "label", str(opt)),
                "preview_text": getattr(opt, "preview", None),
                # Jerarquia real del titulo de referencia (Heading 1/2/3...) para
                # que la UI la pinte; None en "Al final del documento".
                "level": heading.level if heading is not None else None,
                "heading_text": heading.text if heading is not None else None,
            }
        )
    return out


def install_anchor(
    *,
    docx_path: str | Path,
    module_id: str,
    json_path: str | Path,
    position_index: int = 0,
    generator: GeneratorInfo | None = None,
    subchapter: bool = False,
    insert_at_start: bool = False,
) -> dict[str, Any]:
    """Instala ancla + actualiza manifiesto vía manifest_ops (sin UI).

    `insert_at_start` reproduce el checkbox "Insertar al inicio de la
    sección (como subtítulo)" de gui_qt/report_builder_window.py: cuando la
    posición elegida viene de un título (no de "Al final del documento"),
    reemplaza esa posición por la variante "al inicio de la sección"
    (engine.heading_scanner.start_of_section_option, vía
    manifest_ops.resolve_position) antes de instalar el ancla. Sin efecto si
    `position_index` es "Al final del documento" (no tiene título asociado).
    """
    docx_path = Path(docx_path)
    document = docx.Document(str(docx_path))
    options = build_position_options(document)
    if not options:
        raise ValueError("No hay opciones de posición en el documento")
    if position_index < 0 or position_index >= len(options):
        raise ValueError(f"position_index fuera de rango (0..{len(options) - 1})")
    position = manifest_ops.resolve_position(options[position_index], insert_at_start)

    # Sólo hace falta import_path/schema_path: se toma del catálogo, sin importar generadores.
    gens = {g.module_id: g for g in catalogo_generadores()}
    gen_info = generator or gens.get(module_id)
    if gen_info is None:
        raise ValueError(f"No hay generador para module_id={module_id}")

    # La fuente se guarda relativa a la carpeta del documento (la del manifiesto): ver
    # manifest_ops._ref_fuente. Aqui basta con pasar la ruta real.
    tag = manifest_ops.install_anchor_and_update_manifest(
        document,
        docx_path,
        module_id,
        str(Path(json_path)),
        position,
        gen_info,
        subchapter,
    )
    return {
        "ok": True,
        "tag": tag,
        "module_id": module_id,
        "manifest_path": str(manifest_ops.manifest_path_for(docx_path)),
        "position_label": getattr(position, "label", None),
    }


class DocumentCheckError(ValueError):
    """El documento no sirve para el Report Builder; el mensaje va tal cual al usuario."""


class DocumentLockedError(DocumentCheckError):
    """Word (u otro programa) tiene el .docx abierto: no se puede escribir en él ahora."""


def revisar_documento(docx_path: str | Path) -> dict[str, Any]:
    """Comprueba que el .docx se puede leer y escribir, y resume su estructura.

    Lanza DocumentCheckError (documento inválido o temporal) o DocumentLockedError
    (no se puede escribir: lo normal es que Word lo tenga abierto). Devuelve los títulos
    (`title_count`), las posiciones ofrecidas al vincular (`positions`) y los avisos.
    """
    docx_path = Path(docx_path)
    nombre = docx_path.name
    if nombre.startswith("~$"):
        raise DocumentCheckError(
            "Ese archivo es temporal de Word (~$…), no el documento. Elija el .docx original."
        )
    if docx_path.suffix.lower() != ".docx":
        raise DocumentCheckError("Solo se pueden usar documentos .docx.")
    try:
        document = docx.Document(str(docx_path))
    except Exception as exc:
        raise DocumentCheckError(
            f"«{nombre}» no es un .docx válido: puede estar dañado o no ser un documento de Word moderno."
        ) from exc
    try:
        with open(docx_path, "r+b"):
            pass
    except PermissionError as exc:
        raise DocumentLockedError(
            f"No se puede escribir en «{nombre}». Si Word lo tiene abierto, ciérrelo y vuelva a intentarlo."
        ) from exc
    except OSError as exc:
        raise DocumentLockedError(f"No se puede escribir en «{nombre}»: {exc}") from exc

    avisos: list[str] = []
    if (docx_path.parent / f"~${nombre}").exists():
        avisos.append(
            "Hay un archivo de bloqueo de Word (~$…) junto al documento. Si Word lo tiene abierto, ciérrelo antes de sincronizar."
        )
    try:
        build_style_map(document)
    except StyleMapError as exc:
        avisos.append(f"{exc}. La sincronización fallará hasta que el documento tenga esos estilos.")
    options = build_position_options(document)
    titulos = [opcion for opcion in options if opcion.heading is not None]
    if not titulos:
        avisos.append(
            "El documento no tiene títulos: solo podrá vincular secciones «Al final del documento»."
        )
    return {"title_count": len(titulos), "positions": len(options), "warnings": avisos}


def remove_anchor(*, docx_path: str | Path, tag: str) -> bool:
    """Quita el ancla `tag` del documento y su entrada del manifiesto
    (port de _remove_anchor/manifest_ops.remove_anchor_and_update_manifest
    en gui_qt/report_builder_window.py). Devuelve False si no había ningún
    ancla con ese tag en el documento (el llamador decide si es un 404);
    True si se quitó. Lanza DocumentSaveError si no se pudo guardar el
    .docx - el llamador decide cómo mostrarlo."""
    docx_path = Path(docx_path)
    document = docx.Document(str(docx_path))
    return manifest_ops.remove_anchor_and_update_manifest(document, docx_path, tag)


# (status_id, texto) de una fila de "FUENTES DE DATOS", igual criterio que
# _source_status en gui_qt/report_builder_window.py: 'datos_listos' si el
# módulo aún no se sincronizó o si el hash de la fuente actual difiere del
# último sincronizado; 'sin_cambios' si coincide; 'sin_vincular' si la
# fuente no se pudo leer (nunca debería pasar en la web, donde el archivo
# vive junto al manifiesto, pero un source_id borrado por fuera igual puede
# dejar la referencia huérfana). No se expone la ruta real del archivo en el
# texto (ver comentario "Only names/IDs leave the workspace" en
# backend/routers/projects.py) - a diferencia del original, que sí la
# muestra (corre localmente, sin ese límite).
SOURCE_STATUS_READY = "datos_listos"
SOURCE_STATUS_UNCHANGED = "sin_cambios"
SOURCE_STATUS_UNLINKED = "sin_vincular"


def _module_source_status(entry, manifest_dir: Path) -> dict[str, str]:
    json_path = Path(entry.source_json)
    if not json_path.is_absolute():
        json_path = manifest_dir / json_path
    if not json_path.exists():
        return {"id": SOURCE_STATUS_UNLINKED, "text": "El archivo fuente ya no existe en el proyecto."}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        current_hash = hash_source(data)
    except Exception:
        return {"id": SOURCE_STATUS_UNLINKED, "text": "No se pudo leer el JSON fuente."}

    if entry.last_sync is None:
        return {"id": SOURCE_STATUS_READY, "text": "Listo para sincronizar por primera vez."}
    if entry.last_sync.source_hash != current_hash:
        return {
            "id": SOURCE_STATUS_READY,
            "text": f"Datos actualizados desde {entry.last_sync.generated_at}.",
        }
    return {
        "id": SOURCE_STATUS_UNCHANGED,
        "text": f"Sin cambios desde la última sincronización ({entry.last_sync.generated_at}).",
    }


def _result_to_dict(r: ModuleSyncResult) -> dict[str, Any]:
    last = r.new_last_sync
    return {
        "module_id": r.module_id,
        "tag": r.tag,
        "status": r.status,
        "detail": r.detail,
        "new_last_sync": None
        if last is None
        else {
            "source_hash": last.source_hash,
            "content_hash": last.content_hash,
            "generated_at": last.generated_at,
            "docx_backup": last.docx_backup,
        },
    }


# Re-export errores utiles para la capa HTTP
__all__ = [
    "status",
    "hash_json_source",
    "list_generators",
    "sync",
    "preview_sync",
    "load_project_manifest",
    "validate_manifest_against_docx",
    "list_position_options",
    "install_anchor",
    "remove_anchor",
    "SyncEngineError",
    "StyleMapError",
    "DocumentSaveError",
    "ManifestError",
]
