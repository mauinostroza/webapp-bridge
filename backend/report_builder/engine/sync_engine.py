"""
Motor de sincronizacion: para cada modulo habilitado del manifiesto, compara
el JSON fuente contra el ultimo hash conocido, genera el contenido nuevo si
hace falta, y lo reemplaza quirurgicamente dentro de su ancla (w:sdt) en el
.docx real. El resto del documento nunca se toca - solo se vacian/reinsertan
los hijos de w:sdtContent, nunca el wrapping del Content Control.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import docx

from backend.report_builder.engine import generator_registry, manifest_paths
from backend.report_builder.engine.anchor_locator import find_sdt_by_tag, get_sdt_content
from backend.report_builder.engine.anchor_writer import placeholder_content_hash
from backend.report_builder.engine.atomic_write import save_document_atomic
from backend.report_builder.engine.backup import create_backup
from backend.report_builder.engine.content_builder import (
    blank_canvas_from,
    build_content_elements,
    drop_image_relationships,
    mark_fields_for_update,
    relocate_images,
    set_title_heading_text,
    shift_heading_levels,
)
from backend.report_builder.engine.diff_preview import summarize_elements
from backend.report_builder.engine.fragmento import aplicar_fragmento
from backend.report_builder.engine.hasher import hash_content, hash_source
from backend.report_builder.engine.manifest_loader import load_manifest, save_manifest
from backend.report_builder.engine.manifest_schema import LastSync, ModuleEntry
from backend.report_builder.engine.report_log import write_log
from backend.report_builder.engine.schema_validation import SchemaValidationError, load_and_validate
from backend.report_builder.engine.validation import STATUS_ANCHOR_DUPLICATED, validate_against_docx

# Raíz del paquete report_builder (…/backend/report_builder).
# Este archivo vive en engine/, así que hace falta parent.parent — no .parent
# (eso apuntaría a engine/ y rompería resolución de schemas/generators).
REPORT_BUILDER_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_STYLES = {
    "heading_1": "Heading 1",
    "heading_2": "Heading 2",
    "heading_3": "Heading 3",
    "body": "Párrafo",
    "caption": "Caption",
    "table_reference": "Table Grid",
    "table_results": "Normal Table",
    "bullet_list": "Viñeta con símbolos",
}

# Nombre alternativo aceptado para el estilo de parrafo base: algunos
# templates reales definen "Normal" como el estilo de cuerpo en vez de
# "Párrafo" (python-docx y Word los consideran equivalentes en la mayoria
# de los casos). build_style_map prueba primero "Párrafo" y cae a "Normal"
# si no existe, para no abortar el sync de un template valido.
BODY_STYLE_FALLBACKS = ("Párrafo", "Normal")

# Idem para el estilo de lista con vinetas: distintos templates lo nombran
# distinto ("Viñeta con símbolos" es el nombre del template real de la
# empresa; "List Bullet" es el nombre del estilo integrado de Word).
LIST_STYLE_FALLBACKS = ("Viñeta con símbolos", "List Bullet", "List Paragraph")

STATUS_APPLIED = "applied"
STATUS_UNCHANGED = "unchanged"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_ERROR = "error"
STATUS_SKIPPED_DISABLED = "skipped_disabled"


class StyleMapError(Exception):
    """El template real no tiene alguno de los estilos requeridos por los generadores."""


class SyncEngineError(Exception):
    """Error bloqueante global (ej. tags duplicados) que aborta el sync completo antes de escribir nada."""


class DocumentSaveError(Exception):
    """No se pudo abrir o guardar el .docx (ej. abierto en Word, sin permisos, archivo movido)."""


@dataclass
class ModuleSyncResult:
    module_id: str
    tag: str
    status: str
    detail: str = ""
    new_last_sync: LastSync | None = None

    def __str__(self) -> str:
        return f"{self.module_id} ({self.tag}): {self.status}"


def build_style_map(document) -> dict:
    available = {s.name for s in document.styles}
    style_map = dict(REQUIRED_STYLES)
    # Fallback del estilo de cuerpo: si "Párrafo" no existe en el template,
    # usar "Normal" (comun en muchos templates reales). Se valida el nombre
    # efectivamente seleccionado.
    if style_map["body"] not in available:
        for candidate in BODY_STYLE_FALLBACKS:
            if candidate in available:
                style_map["body"] = candidate
                break
    if style_map["bullet_list"] not in available:
        for candidate in LIST_STYLE_FALLBACKS:
            if candidate in available:
                style_map["bullet_list"] = candidate
                break
    missing = {key: name for key, name in style_map.items() if name not in available}
    if missing:
        raise StyleMapError(f"Estilos faltantes en el template: {missing}")
    return style_map


def resolve_generator(import_path: str):
    allowed = {g.import_path for g in generator_registry.discover_generators()}
    if import_path not in allowed:
        raise ValueError(f"Generador no reconocido: {import_path!r}")
    module_path, func_name = import_path.split(":")
    module = importlib.import_module(
        "backend.report_builder." + module_path if module_path.startswith("generators.") else module_path
    )
    return getattr(module, func_name)


def _resolve_path(raw_path: str, base: Path) -> Path:
    """Ruta guardada en el manifiesto -> Path real (relativa a `base`, o absoluta legacy)."""
    return manifest_paths.resolver(raw_path, base)


def run_sync(
    manifest_path: str | Path,
    dry_run: bool = False,
    force: bool = False,
    on_module_start=None,
    generador_externo=None,
) -> list:
    """``generador_externo(entry, data, carpeta_fuente, titulo, desplazamiento) -> Fragmento`` sustituye
    la generación local (modo plan/aplicador: ver ``engine/fragmento.py``). Con él, la clave ``_json_dir``
    no se inyecta (el generador corre en otro lugar) y el contenido se aplica con ``aplicar_fragmento``."""
    started_at = datetime.now()
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    docx_path = _resolve_path(manifest.project.docx_path, manifest_path.parent)

    try:
        document = docx.Document(str(docx_path))
        # Lienzo con los mismos estilos del template real, construido una
        # sola vez y reutilizado por todos los modulos de esta corrida (ver
        # content_builder.build_content_elements) - evita releer el .docx
        # real desde disco una vez por modulo.
        canvas = blank_canvas_from(docx_path)
    except Exception as exc:
        write_log(
            docx_path.parent / "report_logs",
            docx_path,
            None,
            [],
            started_at,
            datetime.now(),
            dry_run=dry_run,
            save_error=str(exc),
        )
        raise DocumentSaveError(
            f"No se pudo abrir {docx_path.name}: {exc}. "
            "Verifica que el archivo exista y no este abierto en Word."
        ) from exc

    validation = validate_against_docx(manifest, document.element)
    duplicated = [m for m in validation.modules if m.status == STATUS_ANCHOR_DUPLICATED]
    if duplicated:
        raise SyncEngineError(
            "Tags duplicados en el documento, se aborta sin escribir nada: "
            + ", ".join(f"{m.module_id} ({m.tag})" for m in duplicated)
        )

    style_map = build_style_map(document)

    results = []
    any_applied = False
    any_manifest_change = False

    total_modules = len(manifest.modules)
    for index, entry in enumerate(manifest.modules, start=1):
        if on_module_start is not None:
            on_module_start(index, total_modules, entry.module_id)
        result = _sync_module(
            entry, document, style_map, docx_path, canvas, force=force, generador_externo=generador_externo
        )
        results.append(result)
        if result.new_last_sync is not None:
            any_manifest_change = True
            if not dry_run:
                entry.last_sync = result.new_last_sync
        if result.status == STATUS_APPLIED:
            any_applied = True

    backup_path = None
    save_error = None
    if not dry_run and any_applied:
        try:
            backups_dir = docx_path.parent / "report_backups"
            backup_path = create_backup(docx_path, backups_dir)
            mark_fields_for_update(document)
            save_document_atomic(document, docx_path)
        except Exception as exc:
            save_error = str(exc)
        else:
            # Relativo a la carpeta del manifiesto: `report_backups/<archivo>.docx`.
            ref_backup = manifest_paths.ruta_relativa(backup_path, docx_path.parent)
            for entry in manifest.modules:
                if entry.last_sync is not None and entry.last_sync.docx_backup is None:
                    entry.last_sync.docx_backup = ref_backup

    # Si el guardado fallo, el .docx en disco sigue teniendo el contenido viejo -
    # no persistir el manifiesto, o la proxima corrida creeria erroneamente que
    # el contenido nuevo ya esta aplicado.
    if not dry_run and (any_applied or any_manifest_change) and save_error is None:
        save_manifest(manifest, manifest_path)

    logs_dir = docx_path.parent / "report_logs"
    write_log(
        logs_dir,
        docx_path,
        backup_path,
        results,
        started_at,
        datetime.now(),
        dry_run=dry_run,
        save_error=save_error,
    )

    if save_error is not None:
        raise DocumentSaveError(
            f"No se pudo guardar {docx_path.name}: {save_error}. "
            "Verifica que no este abierto en Word. El documento en disco no fue modificado "
            "(la copia de respaldo previa sigue disponible en report_backups/)."
        )

    return results


def _sync_module(
    entry: ModuleEntry,
    document,
    style_map: dict,
    docx_path: Path,
    canvas,
    force: bool,
    generador_externo=None,
) -> ModuleSyncResult:
    if not entry.enabled:
        return ModuleSyncResult(entry.module_id, entry.tag, STATUS_SKIPPED_DISABLED)

    source_path = _resolve_path(entry.source_json, docx_path.parent)
    schema_path = _resolve_path(entry.json_schema_ref, REPORT_BUILDER_ROOT) if entry.json_schema_ref else None

    try:
        if schema_path is not None:
            data = load_and_validate(source_path, schema_path)
        else:
            with open(source_path, encoding="utf-8") as f:
                data = json.load(f)
    except (SchemaValidationError, OSError, json.JSONDecodeError) as exc:
        return ModuleSyncResult(
            entry.module_id, entry.tag, STATUS_ERROR, f"JSON invalido o inaccesible: {exc}"
        )

    new_source_hash = hash_source(data)

    matches = find_sdt_by_tag(document.element, entry.tag)
    if len(matches) == 0:
        return ModuleSyncResult(entry.module_id, entry.tag, STATUS_ERROR, "ANCHOR_NOT_FOUND")
    if len(matches) > 1:
        return ModuleSyncResult(entry.module_id, entry.tag, STATUS_ERROR, "ANCHOR_DUPLICATED")
    sdt_content = get_sdt_content(matches[0])
    current_elements = list(sdt_content)
    current_content_hash = hash_content(current_elements)

    # Un ancla recien (re)instalada todavia tiene el placeholder de
    # anchor_writer, no el contenido de la ultima sincronizacion registrada
    # en el manifiesto - aunque el JSON fuente no haya cambiado, no es
    # "sin cambios": nunca se genero contenido real para esta instancia del
    # ancla, hay que generarlo igual que la primera vez.
    is_fresh_anchor = current_content_hash == placeholder_content_hash()

    if (
        not is_fresh_anchor
        and entry.last_sync
        and entry.last_sync.source_hash == new_source_hash
        and current_content_hash == entry.last_sync.content_hash
    ):
        return ModuleSyncResult(entry.module_id, entry.tag, STATUS_UNCHANGED, "fuente sin cambios")

    fragmento = None
    new_elements: list = []
    generator_fn = None
    if generador_externo is None:
        try:
            generator_fn = resolve_generator(entry.generator)
        except (ImportError, AttributeError, ValueError) as exc:
            return ModuleSyncResult(
                entry.module_id, entry.tag, STATUS_ERROR, f"No se pudo resolver el generador: {exc}"
            )
    else:
        # Modo plan/aplicador: el generador corre fuera de este proceso (generador_externo), asi que
        # no se importa aqui. Solo se exige que este en el catalogo permitido.
        try:
            permitidos = {g.import_path for g in generator_registry.catalogo_generadores()}
        except RuntimeError as exc:
            return ModuleSyncResult(
                entry.module_id, entry.tag, STATUS_ERROR, f"No se pudo resolver el generador: {exc}"
            )
        if entry.generator not in permitidos:
            return ModuleSyncResult(
                entry.module_id,
                entry.tag,
                STATUS_ERROR,
                f"No se pudo resolver el generador: Generador no reconocido: {entry.generator!r}",
            )

    try:
        display_name = generator_registry.MODULE_DISPLAY_NAMES.get(entry.module_id, entry.module_id)
        if entry.heading_level_offset == 0:  # capitulo propio (jerarquia 1): titulo en mayusculas
            display_name = display_name.upper()
        if generador_externo is not None:
            fragmento = generador_externo(
                entry, data, source_path.parent, display_name, entry.heading_level_offset
            )
            new_content_hash = fragmento.content_hash
        else:
            # Clave reservada para que el generador pueda resolver rutas relativas
            # (p.ej. imagenes en "<nombre_proyecto>_imagenes/") a la carpeta donde
            # vive su JSON. Se inyecta despues de hash_source() para no meter una
            # ruta absoluta especifica de la maquina dentro del hash de deteccion
            # de cambios.
            data["_json_dir"] = str(source_path.parent)
            new_elements = build_content_elements(generator_fn, data, style_map, canvas)
            set_title_heading_text(new_elements, canvas, display_name)
            if entry.heading_level_offset:
                shift_heading_levels(new_elements, canvas, entry.heading_level_offset)
            new_content_hash = hash_content(new_elements)
    except Exception as exc:
        return ModuleSyncResult(
            entry.module_id,
            entry.tag,
            STATUS_ERROR,
            f"Error al generar contenido: {type(exc).__name__}: {exc}",
        )

    if new_content_hash == current_content_hash:
        refreshed = LastSync(
            source_hash=new_source_hash,
            content_hash=new_content_hash,
            generated_at=datetime.now().isoformat(),
            docx_backup=entry.last_sync.docx_backup if entry.last_sync else None,
        )
        return ModuleSyncResult(
            entry.module_id, entry.tag, STATUS_UNCHANGED, "contenido generado identico", refreshed
        )

    # Un ancla recien instalada nunca tuvo contenido real: su placeholder no
    # cuenta como "posible edicion manual" aunque difiera del content_hash
    # que el manifiesto tenia registrado de una instancia anterior del ancla.
    expected_hash = None if is_fresh_anchor else (entry.last_sync.content_hash if entry.last_sync else None)
    manual_edit_detected = expected_hash is not None and current_content_hash != expected_hash
    if manual_edit_detected and not force:
        return ModuleSyncResult(
            entry.module_id,
            entry.tag,
            STATUS_NEEDS_CONFIRMATION,
            "el contenido actual dentro del ancla no coincide con la ultima sincronizacion "
            "(posible edicion manual) - reintentar con force=True para sobrescribir",
        )

    # relocate_images se llama recien aca, no al generar new_elements mas
    # arriba: crea la relacion OPC + el ImagePart en document.part (el real,
    # no el canvas descartable) como efecto secundario, y hasta este punto
    # new_elements todavia podia descartarse sin usarse (los `return`
    # STATUS_UNCHANGED/STATUS_NEEDS_CONFIRMATION de arriba). Antes se
    # llamaba antes de esas comparaciones: si ESTE modulo resultaba sin
    # cambios o con edicion manual detectada pero OTRO modulo de la misma
    # corrida si se aplicaba (haciendo que run_sync guardara el documento
    # igual), la relacion de imagen recien creada quedaba huerfana -sin que
    # ningun elemento la referenciara ni drop_image_relationships la
    # liberara- persistida en el .docx: una imagen huerfana mas en
    # word/media/ por cada sync asi. El hash de contenido no se ve afectado
    # por este reordenamiento: hash_content ya excluye los atributos r:*
    # (ver hasher.py), asi que new_content_hash es el mismo se llame antes o
    # despues.
    if fragmento is not None:
        aplicado = aplicar_fragmento(document, sdt_content, fragmento)
        if aplicado != new_content_hash:
            return ModuleSyncResult(
                entry.module_id,
                entry.tag,
                STATUS_ERROR,
                "El fragmento aplicado no coincide con su huella (contenido alterado en el camino)",
            )
        new_elements = list(sdt_content)
    else:
        relocate_images(new_elements, canvas.part, document.part)

        drop_image_relationships(current_elements, document.part)
        for child in list(sdt_content):
            sdt_content.remove(child)
        for el in new_elements:
            sdt_content.append(el)

    new_last_sync = LastSync(
        source_hash=new_source_hash,
        content_hash=new_content_hash,
        generated_at=datetime.now().isoformat(),
        docx_backup=None,
    )
    preview = summarize_elements(new_elements)
    return ModuleSyncResult(entry.module_id, entry.tag, STATUS_APPLIED, preview, new_last_sync)
