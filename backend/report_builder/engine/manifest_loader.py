"""
Carga, validacion y guardado del manifiesto (<nombre>.report_manifest.json, junto al .docx).

Solo valida consistencia interna del manifiesto (version soportada, sin tags
ni module_id duplicados). La validacion cruzada contra el .docx real vive en
engine/validation.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from backend.report_builder.engine import manifest_paths
from backend.report_builder.engine.manifest_schema import (
    CURRENT_SCHEMA_VERSION,
    LastSync,
    Manifest,
    ModuleEntry,
    ProjectInfo,
)


class ManifestError(Exception):
    """Error de carga o validacion del manifiesto."""


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    if not path.exists():
        raise ManifestError(f"No existe el manifiesto: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"No se pudo leer el manifiesto {path}: {exc}. "
            "Puede estar corrupto (escritura interrumpida) o en conflicto de sincronizacion de carpeta."
        ) from exc

    schema_version = raw.get("schema_version")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise ManifestError(
            f"Version de manifiesto no soportada: {schema_version!r} (se esperaba {CURRENT_SCHEMA_VERSION!r})"
        )

    project_raw = raw.get("project", {})
    if "docx_path" not in project_raw:
        raise ManifestError("El manifiesto no declara project.docx_path")
    project = ProjectInfo(name=project_raw.get("name", ""), docx_path=project_raw["docx_path"])

    modules = [_parse_module_entry(entry) for entry in raw.get("modules", [])]

    manifest = Manifest(project=project, modules=modules, schema_version=schema_version)
    validate_manifest(manifest)
    return manifest


def _parse_module_entry(entry: dict) -> ModuleEntry:
    for required in ("module_id", "tag", "source_json", "generator"):
        if required not in entry:
            raise ManifestError(f"Entrada de modulo sin campo requerido {required!r}: {entry}")

    last_sync_raw = entry.get("last_sync")
    last_sync = LastSync(**last_sync_raw) if last_sync_raw else None

    return ModuleEntry(
        module_id=entry["module_id"],
        tag=entry["tag"],
        source_json=entry["source_json"],
        generator=entry["generator"],
        json_schema_ref=entry.get("json_schema_ref"),
        enabled=entry.get("enabled", True),
        last_sync=last_sync,
        heading_level_offset=entry.get("heading_level_offset", 0),
    )


def validate_manifest(manifest: Manifest) -> None:
    # Un modulo puede instalarse mas de una vez (cada instancia con su
    # propio tag) - solo el tag debe ser unico, no el module_id.
    seen_tags: dict[str, str] = {}

    for entry in manifest.modules:
        if entry.tag in seen_tags:
            raise ManifestError(
                f"Tag duplicado en el manifiesto: {entry.tag!r} "
                f"(modulos {seen_tags[entry.tag]!r} y {entry.module_id!r})"
            )
        seen_tags[entry.tag] = entry.module_id


def save_manifest(manifest: Manifest, path: str | Path) -> None:
    """Guarda el manifiesto con sus rutas en forma relativa a su carpeta (ver
    engine.manifest_paths). Es el unico punto de escritura: aqui se migran los
    manifiestos antiguos con rutas absolutas dentro de la carpeta. Las absolutas
    ajenas se conservan tal cual (ver avisos_de_rutas)."""
    validate_manifest(manifest)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    carpeta = path.parent

    try:
        docx_ref = manifest_paths.normalizar(manifest.project.docx_path, carpeta)
        modules = []
        for entry in manifest.modules:
            last_sync = asdict(entry.last_sync) if entry.last_sync else None
            if last_sync and last_sync.get("docx_backup"):
                last_sync["docx_backup"] = manifest_paths.normalizar(last_sync["docx_backup"], carpeta)
            modules.append(
                {
                    "module_id": entry.module_id,
                    "tag": entry.tag,
                    "enabled": entry.enabled,
                    "source_json": manifest_paths.normalizar(entry.source_json, carpeta),
                    "json_schema_ref": entry.json_schema_ref,
                    "generator": entry.generator,
                    "heading_level_offset": entry.heading_level_offset,
                    "last_sync": last_sync,
                }
            )
    except ValueError as exc:
        raise ManifestError(f"No se puede guardar el manifiesto con rutas relativas: {exc}") from exc

    raw = {
        "schema_version": manifest.schema_version,
        "project": {"name": manifest.project.name, "docx_path": docx_ref},
        "modules": modules,
    }

    # Escritura atomica: si el proceso se interrumpe a mitad de la escritura
    # (crash, corte de luz), el manifiesto real nunca queda a medio escribir -
    # o se completa el reemplazo, o el archivo original sigue intacto.
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def avisos_de_rutas(manifest: Manifest, carpeta: str | Path) -> list[str]:
    """Avisos (sin rutas dentro del texto) por rutas absolutas ajenas a la carpeta del
    manifiesto: se conservan tal cual, pero no son portables. Vacio si todo es relativo."""
    avisos = []
    if manifest_paths.es_ajena(manifest.project.docx_path, carpeta):
        avisos.append(
            "El documento se guarda con una ruta absoluta de otro equipo: se usa tal cual; "
            "guarde el informe en la carpeta del documento para convertirla en relativa."
        )
    for entry in manifest.modules:
        if manifest_paths.es_ajena(entry.source_json, carpeta):
            avisos.append(
                f"La fuente del módulo «{entry.module_id}» tiene una ruta absoluta de otro equipo: "
                "se conserva tal cual. Vuelva a instalar esa sección para guardarla relativa."
            )
    return avisos
