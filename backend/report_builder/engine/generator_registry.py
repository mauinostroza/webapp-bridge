"""
Descubre los generadores disponibles inspeccionando el paquete generators/.

Cada generador expone MODULE_ID y build(doc, data, style_map). El schema
opcional vive en generators/schemas/<module_id>.vN.schema.json (se usa el de
mayor version disponible).
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.report_builder import generators


def _generators_package_dir() -> Path:
    """
    Directorio real de generators/, sin confiar ciegamente en
    generators.__file__: en un .exe de PyInstaller (--onefile) ese atributo
    puede venir en None, o apuntar a un lugar que no coincide con donde
    --add-data realmente extrajo los archivos (ver sys._MEIPASS) - eso
    rompia esta cuenta de forma no controlada, a nivel de modulo, antes de
    que discover_generators() pudiera hacer nada defensivo.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "generators"
            if candidate.exists():
                return candidate
    try:
        path = Path(generators.__file__).parent
    except (TypeError, AttributeError):
        path = None
    if path is not None and path.exists():
        return path
    return Path(__file__).resolve().parent.parent / "generators"


GENERATORS_PACKAGE_DIR = _generators_package_dir()
SCHEMAS_DIR = GENERATORS_PACKAGE_DIR / "schemas"
REPORT_BUILDER_ROOT = SCHEMAS_DIR.parent.parent

_EXCLUDED_MODULES = {"base"}

# Etiquetas legibles para el combobox de modulos. Los modulos que no esten
# aca (ej. generadores nuevos agregados a futuro) muestran su module_id
# crudo como fallback - no hace falta tocar este diccionario para que
# aparezcan, solo para que su etiqueta se vea prolija.
MODULE_DISPLAY_NAMES = {
    "placas_base": "Placas bases",
    "fundaciones": "Fundaciones",
    "esbelteces": "Esbelteces",
    "combinaciones": "Combinaciones",
    "espectro": "Parámetros sísmicos",
    "diagrama_interaccion": "Diagrama de interacción",
    "nomogramas": "Nomogramas",
    "hormigon_simple": "Hormigón simple",
    "equilibrio_plastico": "Equilibrio plástico",
    "sulzberger": "Sulzberger",
    "bombas": "Vibración de bombas",
    "zapata_biaxial": "Zapatas biaxiales",
    "acero_aisc360": "Acero AISC 360",
}

# Nombres de archivo (generators/<nombre>.py) que siempre se intentan
# importar directamente, sin depender de pkgutil.iter_modules(). En un .exe
# de PyInstaller (--onefile) pkgutil puede fallar en listar el contenido de
# un paquete empaquetado segun como quede armado el bundle - importarlos
# por nombre explicito evita que el combobox de modulos quede vacio en ese
# caso. pkgutil se sigue intentando despues, best-effort, para no perder la
# posibilidad de descubrir generadores nuevos sin tocar esta lista.
_KNOWN_MODULE_NAMES = ("placas_base", "fundaciones", "esbelteces", "combinaciones", "espectro")
_KNOWN_MODULE_NAMES += ("diagrama_interaccion",)
_KNOWN_MODULE_NAMES += ("nomogramas",)
_KNOWN_MODULE_NAMES += ("hormigon_simple",)
_KNOWN_MODULE_NAMES += ("equilibrio_plastico",)
_KNOWN_MODULE_NAMES += ("sulzberger",)
_KNOWN_MODULE_NAMES += ("bombas",)
_KNOWN_MODULE_NAMES += ("zapata_biaxial",)
_KNOWN_MODULE_NAMES += ("acero_aisc360",)


@dataclass
class GeneratorInfo:
    module_id: str
    import_path: str  # ej. "generators.placas_base:build"
    schema_path: str | None

    @property
    def display_name(self) -> str:
        return MODULE_DISPLAY_NAMES.get(self.module_id, self.module_id)


def _build_generator_info(module_name: str):
    """Devuelve (GeneratorInfo, None) si se pudo cargar, o (None, mensaje)
    si no - el mensaje es para diagnostico en la UI (ver
    discover_generators_with_errors), no solo para print()."""
    try:
        module = importlib.import_module(f"backend.report_builder.generators.{module_name}")
    except Exception as exc:
        msg = f"generators.{module_name}: {type(exc).__name__}: {exc}"
        print(f"[generator_registry] No se pudo cargar {msg}")
        return None, msg
    if not hasattr(module, "build") or not hasattr(module, "MODULE_ID"):
        return None, f"generators.{module_name}: no define build()/MODULE_ID"
    module_id = module.MODULE_ID
    schema_path = _latest_schema_path(module_id)
    info = GeneratorInfo(
        module_id=module_id,
        import_path=f"generators.{module_name}:build",
        schema_path=_relative_schema_path(schema_path) if schema_path else None,
    )
    return info, None


def discover_generators() -> list:
    """Descubre los generadores validos. Ver discover_generators_with_errors
    si ademas se necesita saber por que algun modulo conocido no cargo
    (p.ej. para mostrarlo en la UI - un .exe --windowed no tiene consola
    donde ver los print() de diagnostico)."""
    infos, _errors = discover_generators_with_errors()
    return infos


def discover_generators_with_errors():
    """
    Igual que discover_generators(), pero ademas devuelve la lista de
    mensajes de error para los modulos de _KNOWN_MODULE_NAMES que no se
    pudieron cargar. Un modulo con error de import/sintaxis no debe impedir
    que se descubran los demas (ej. mientras se desarrolla un generador
    nuevo, el Anchor Wizard sigue funcionando con los que ya andan).

    Los modulos de _KNOWN_MODULE_NAMES se importan siempre por nombre
    explicito (ver comentario ahi); pkgutil.iter_modules solo se usa
    despues, best-effort, para encontrar generadores nuevos que todavia no
    se agregaron a esa lista (sus fallos no se reportan como error: son
    best-effort por definicion).

    Retorna (infos: list[GeneratorInfo], errors: list[str]).
    """
    infos = []
    seen_module_ids = set()
    errors = []

    for module_name in _KNOWN_MODULE_NAMES:
        info, error = _build_generator_info(module_name)
        if info is not None and info.module_id not in seen_module_ids:
            infos.append(info)
            seen_module_ids.add(info.module_id)
        elif error is not None:
            errors.append(error)

    try:
        discovered_names = [m.name for m in pkgutil.iter_modules([str(GENERATORS_PACKAGE_DIR)])]
    except Exception as exc:
        print(f"[generator_registry] No se pudo listar generators/ dinamicamente: {exc}")
        discovered_names = []

    for module_name in discovered_names:
        if module_name in _EXCLUDED_MODULES or module_name in _KNOWN_MODULE_NAMES:
            continue
        info, _error = _build_generator_info(module_name)
        if info is not None and info.module_id not in seen_module_ids:
            infos.append(info)
            seen_module_ids.add(info.module_id)

    return infos, errors


CATALOGO_PATH = GENERATORS_PACKAGE_DIR / "catalogo.json"


def catalogo_generadores() -> list[GeneratorInfo]:
    """Generadores permitidos según el catálogo estático ``generators/catalogo.json``.

    Sólo lee ese JSON: no importa ningún módulo de generadores, así que sirve donde esos
    módulos no se pueden cargar (p. ej. el puente local en el flujo plan/aplicador). Debe
    coincidir con ``discover_generators()``: lo verifica
    ``backend/report_builder/tests/test_catalogo_generadores.py`` y lo regenera
    ``scripts/generar_catalogo_generadores.py``.
    """
    try:
        bruto = json.loads(CATALOGO_PATH.read_text(encoding="utf-8"))
        if not isinstance(bruto, list):
            raise ValueError("debe ser una lista")
        return [
            GeneratorInfo(
                module_id=item["module_id"],
                import_path=item["import_path"],
                schema_path=item.get("schema_path"),
            )
            for item in bruto
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Catálogo de generadores no válido ({CATALOGO_PATH}): {exc}") from exc


def _latest_schema_path(module_id: str) -> Path | None:
    candidates = sorted(SCHEMAS_DIR.glob(f"{module_id}.v*.schema.json"))
    return candidates[-1] if candidates else None


def _relative_schema_path(schema_path: Path) -> str:
    """
    Ruta relativa a la raiz de report_builder cuando es posible, para que el
    manifiesto de un proyecto siga siendo valido si se mueve la carpeta
    completa de la suite a otra maquina. Si por algun motivo el schema vive
    fuera de esa raiz, se guarda la ruta absoluta como fallback.
    """
    try:
        return str(schema_path.relative_to(REPORT_BUILDER_ROOT))
    except ValueError:
        return str(schema_path)
