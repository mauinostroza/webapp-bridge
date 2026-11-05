"""
Estructuras de datos del manifiesto de report_builder.

El manifiesto es la fuente de verdad que conecta modulo -> ancla (w:tag) ->
funcion generadora -> estado de la ultima sincronizacion. Se persiste como
JSON en config/report_manifest.json (o el archivo que indique el proyecto).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CURRENT_SCHEMA_VERSION = "1.0"


@dataclass
class LastSync:
    source_hash: str
    content_hash: str
    generated_at: str
    docx_backup: str | None = None


@dataclass
class ModuleEntry:
    module_id: str
    tag: str
    source_json: str
    generator: str
    json_schema_ref: str | None = None
    enabled: bool = True
    last_sync: LastSync | None = None
    # 0 = capitulo nuevo (default, comportamiento historico); N = anidado N
    # niveles mas profundo que el titulo de referencia elegido al instalar
    # (ver manifest_ops.install_anchor_and_update_manifest, toggle
    # "Considerar como subcapitulo"). Se decide una sola vez al instalar y
    # se re-aplica en cada sync (engine.content_builder.shift_heading_levels).
    heading_level_offset: int = 0


@dataclass
class ProjectInfo:
    name: str
    docx_path: str


@dataclass
class Manifest:
    project: ProjectInfo
    modules: list[ModuleEntry] = field(default_factory=list)
    schema_version: str = CURRENT_SCHEMA_VERSION

    def get_module(self, module_id: str) -> ModuleEntry | None:
        """Devuelve la primera instancia de ese modulo (puede haber mas de
        una - ver get_modules_by_id)."""
        for entry in self.modules:
            if entry.module_id == module_id:
                return entry
        return None

    def get_modules_by_id(self, module_id: str) -> list[ModuleEntry]:
        """Todas las instancias instaladas de ese modulo (un modulo puede
        instalarse mas de una vez, cada una con su propio tag)."""
        return [entry for entry in self.modules if entry.module_id == module_id]

    def get_module_by_tag(self, tag: str) -> ModuleEntry | None:
        """Lookup por tag exacto - el tag es el identificador real de una
        instancia instalada, module_id no lo es."""
        for entry in self.modules:
            if entry.tag == tag:
                return entry
        return None

    def remove_module(self, module_id: str) -> bool:
        """Quita la primera instancia de ese modulo del manifiesto. Devuelve
        False si no existia (no es un error)."""
        entry = self.get_module(module_id)
        if entry is None:
            return False
        self.modules.remove(entry)
        return True

    def remove_module_by_tag(self, tag: str) -> bool:
        """Quita la instancia con ese tag exacto. Devuelve False si no
        existia (no es un error)."""
        entry = self.get_module_by_tag(tag)
        if entry is None:
            return False
        self.modules.remove(entry)
        return True
