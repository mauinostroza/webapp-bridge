"""
Operaciones puras sobre el manifiesto del proyecto (report_manifest.json) y
el documento (.docx): resolucion de rutas, instalar/quitar anclas + su
entrada en el manifiesto, guardado con backup, reasignar el documento de un
proyecto. Nada de este modulo importa tkinter - a diferencia de ui_common.py
(que si depende de Tk para arboles, dialogos y tooltips), este modulo puede
importarse desde cualquier contexto, con o sin GUI Tkinter disponible.

Compartido por anchor_wizard.py (standalone) y el host embebido en la suite
(apps/report_builder/app.py) - ver ui_common.py para la logica que si
necesita widgets Tk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.report_builder.engine import manifest_paths
from backend.report_builder.engine.anchor_writer import install_module_anchor, remove_module_anchor
from backend.report_builder.engine.atomic_write import save_document_atomic
from backend.report_builder.engine.backup import create_backup
from backend.report_builder.engine.content_builder import mark_fields_for_update
from backend.report_builder.engine.manifest_loader import ManifestError, load_manifest, save_manifest
from backend.report_builder.engine.manifest_schema import Manifest, ModuleEntry, ProjectInfo
from backend.report_builder.engine.sync_engine import DocumentSaveError

# ================= resolucion de rutas =================

_LEGACY_MANIFEST_NAME = "report_manifest.json"


def new_manifest_path_for(docx_path: Path) -> Path:
    """Ruta de manifiesto por documento: `<nombre_del_docx>.report_manifest.json`,
    junto al .docx (mismo criterio que ya usa engine.backup.create_backup para
    nombrar backups por docx_path.stem). A diferencia de manifest_path_for,
    esta funcion no resuelve nada - siempre devuelve esta ruta, exista o no,
    para los llamadores que necesitan garantizar un manifiesto nuevo e
    independiente (p. ej. "Vincular como documento nuevo" o "Copiar anclas a
    este documento" en el dialogo de conflicto de _change_document)."""
    return docx_path.parent / f"{docx_path.stem}.{_LEGACY_MANIFEST_NAME}"


def manifest_path_for(docx_path: Path) -> Path:
    """Resuelve el manifiesto de `docx_path`: por documento
    (new_manifest_path_for) si ya existe; si no, el legacy
    `report_manifest.json` de la carpeta si existe y pertenece a este mismo
    documento (proyectos de un solo documento por carpeta, de antes de este
    naming, siguen funcionando sin migrar nada); si no, el nombre por
    documento (todavia no existe - cualquier instalacion nueva lo crea ahi).

    Antes esto era una constante (`docx_path.parent / "report_manifest.json"`)
    identificada solo por carpeta: dos documentos .docx distintos en la misma
    carpeta competian por el mismo manifiesto. Con esta resolucion, un
    documento sin manifiesto propio nunca "choca" con el legacy de otro
    documento - simplemente cae al nombre por documento (ver punto 3), sin
    disparar el dialogo de conflicto de _change_document."""
    new_style = new_manifest_path_for(docx_path)
    if new_style.exists():
        return new_style

    legacy = docx_path.parent / _LEGACY_MANIFEST_NAME
    if legacy.exists():
        try:
            manifest = load_manifest(legacy)
            if _resolve_manifest_relative_path(manifest.project.docx_path, legacy) == docx_path.resolve():
                return legacy
        except ManifestError:
            pass

    return new_style


def find_other_project_in_folder(docx_path: Path) -> Path | None:
    """Busca en la carpeta de `docx_path` el manifiesto de OTRO proyecto ya
    existente (legacy `report_manifest.json`, o cualquier
    `<otro_nombre>.report_manifest.json`) - para que el llamador pueda
    ofrecerle al usuario trasladar o copiar esas anclas en vez de arrancar un
    proyecto en blanco (ver el dialogo de conflicto en
    gui_qt/report_builder_window.py:_change_document, en engineering_suite).
    Solo tiene sentido llamarla cuando `manifest_path_for(docx_path)` ya
    determino que no hay manifiesto propio para `docx_path` (si lo hubiera,
    por construccion ningun otro archivo del glob puede pertenecerle a otro
    documento y esta a la vez involucrado en esa resolucion). Devuelve la
    ruta de ese otro manifiesto, o None si la carpeta no tiene ninguno (o los
    que hay estan corruptos)."""
    own_new_style = new_manifest_path_for(docx_path)
    for candidate in docx_path.parent.glob(f"*{_LEGACY_MANIFEST_NAME}"):
        if candidate == own_new_style:
            continue
        try:
            load_manifest(candidate)
        except ManifestError:
            continue
        return candidate
    return None


def _resolve_manifest_relative_path(raw_path: str, manifest_path: Path) -> Path:
    """Resuelve un path guardado en un manifiesto (p. ej. project.docx_path): relativo
    a la carpeta del manifiesto (forma actual) o absoluto (manifiestos antiguos)."""
    return manifest_paths.resolver(raw_path, manifest_path.parent)


def _ref_fuente(json_path: str | Path, docx_path: Path) -> str:
    """Ruta de una fuente tal como se guarda: relativa a la carpeta del documento (que es la
    del manifiesto). Dentro de ella es `<nombre>_reporte/...` o `<uuid>.json`. Fuera solo se
    llega con `..`: es el unico caso en que no hay alternativa a una ruta absoluta."""
    try:
        return manifest_paths.ruta_relativa(json_path, docx_path.parent)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


def manifest_belongs_to_other_docx(docx_path: Path, manifest_path: Path) -> Path | None:
    """Si `manifest_path` ya existe y su project.docx_path resuelve a un
    archivo distinto de `docx_path`, devuelve esa otra ruta (absoluta) -
    caso tipico: dos .docx en la misma carpeta (el manifiesto se identifica
    por carpeta, no por archivo), y se abre el que no es "dueno" del
    manifiesto ahi presente. None si el manifiesto no existe todavia o ya
    pertenece a este documento."""
    if not manifest_path.exists():
        return None
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        return None

    other = _resolve_manifest_relative_path(manifest.project.docx_path, manifest_path)
    if other != docx_path.resolve():
        return other
    return None


def docx_path_from_manifest(manifest_path: Path) -> Path:
    """Carga el manifiesto en `manifest_path` y resuelve su project.docx_path
    a un Path absoluto (relativo a la carpeta del manifiesto si no lo era ya).
    No valida que el archivo exista - el llamador decide como manejar un
    documento movido/borrado (ver "Cambiar..." en ambos hosts). Lanza
    ManifestError si `manifest_path` no existe o no es valido."""
    manifest = load_manifest(manifest_path)
    return _resolve_manifest_relative_path(manifest.project.docx_path, manifest_path)


def crear_manifiesto_vacio(docx_path: Path) -> Path:
    """Crea el manifiesto sin secciones junto al .docx y devuelve su ruta.

    Es el paso «preparar»: el manifiesto existe desde que se elige el documento, no sólo
    desde la primera ancla. Sólo debe llamarse si `manifest_path_for` no encuentra uno.
    """
    path = new_manifest_path_for(docx_path)
    save_manifest(Manifest(project=ProjectInfo(name=docx_path.stem, docx_path=docx_path.name)), path)
    return path


def reassign_project_docx(manifest_path: Path, new_docx_path: Path) -> Manifest:
    """Actualiza project.docx_path del manifiesto en `manifest_path` para que
    apunte a `new_docx_path` y guarda el manifiesto (sin tocar los modulos ya
    instalados). Devuelve el Manifest actualizado. Lanza ManifestError si
    `manifest_path` no existe o no es valido, o si `new_docx_path` no existe
    (antes no se validaba, y el manifiesto quedaba apuntando a un .docx
    inexistente sin ningun aviso hasta la proxima operacion que intentara
    abrirlo)."""
    if not new_docx_path.exists():
        raise ManifestError(f"El archivo {new_docx_path} no existe.")
    manifest = load_manifest(manifest_path)
    # Relativa a la carpeta del manifiesto: `..` solo si el documento se reasigna a OTRA carpeta
    # (decision explicita del usuario al elegir el archivo).
    manifest.project.docx_path = manifest_paths.ruta_relativa(new_docx_path, manifest_path.parent)
    save_manifest(manifest, manifest_path)
    return manifest


# ================= instalar ancla + manifiesto =================


def install_anchor_and_update_manifest(
    document, docx_path: Path, module_id: str, json_path: str, position, generator, subchapter: bool = False
) -> str:
    """Instala el ancla, respalda y guarda el .docx, y actualiza el
    manifiesto del proyecto. Devuelve el tag instalado. Lanza
    AnchorAlreadyExistsError (engine.anchor_writer) o DocumentSaveError - el
    llamador decide como mostrarlo.

    Cada generador escribe su propio titulo como "Heading 1" (nivel base 1,
    ver p. ej. generators/fundaciones.py). Si `position` viene de un titulo
    (no de "Al final del documento"), el contenido de esta ancla se genera,
    en cada sincronizacion, al MISMO nivel que ese titulo de referencia por
    default - o un nivel mas profundo si `subchapter` es True - en vez de
    quedar siempre en Heading 1 sin importar el nivel real del titulo
    elegido (ver engine.content_builder.shift_heading_levels)."""
    tag, _ = install_module_anchor(
        document, module_id, insert_after=position.insert_after, insert_before=position.insert_before
    )

    try:
        backups_dir = docx_path.parent / "report_backups"
        create_backup(docx_path, backups_dir)
        mark_fields_for_update(document)
        save_document_atomic(document, docx_path)
    except Exception as exc:
        # install_module_anchor() ya mutó el `document` en memoria antes de
        # este try - si el guardado falla, se revierte esa mutación aquí
        # mismo en vez de dejarla en el objeto. Antes, si el llamador
        # reintentaba reusando el mismo `document` sin recargarlo desde
        # disco (el mensaje de error sugiere "vuelve a elegir el documento",
        # pero nada lo fuerza), el reintento instalaba una segunda ancla al
        # lado de la que quedó fantasma en memoria.
        remove_module_anchor(document, tag)
        raise DocumentSaveError(
            f"No se pudo guardar {docx_path.name}: {exc}. "
            "Verifica que no esté abierto en Word. El ancla no quedó guardada en el documento; "
            "vuelve a elegir el documento antes de reintentar."
        ) from exc

    if position.heading is None:
        heading_level_offset = 0
    elif subchapter:
        heading_level_offset = min(position.heading.level, 2)
    else:
        heading_level_offset = position.heading.level - 1
    update_manifest_after_install(docx_path, module_id, tag, json_path, generator, heading_level_offset)
    return tag


@dataclass
class _GeneratorRef:
    """Adaptador minimo con la forma que update_manifest_after_install espera
    de `generator_info` (.import_path/.schema_path), armado a partir de una
    ModuleEntry ya existente en vez de resolver el generador real - usado por
    copy_anchors_to_new_document, que copia entradas de manifiesto ya
    validas, no instala anclas nuevas desde cero."""

    import_path: str
    schema_path: str | None


def copy_anchors_to_new_document(source_manifest_path: Path, document, docx_path: Path) -> Path:
    """Clona todas las anclas de `source_manifest_path` en `document`/`docx_path`
    - a diferencia de reassign_project_docx (que solo cambia a que documento
    apunta un manifiesto, sin instalar nada), esto instala una ancla fisica
    nueva (w:sdt con tag propio) por cada modulo del manifiesto de origen,
    todas al final del documento (el manifiesto no guarda la posicion
    original de cada ancla - el usuario las reordena a mano despues si
    quiere), y arma un manifiesto nuevo e independiente para docx_path
    (new_manifest_path_for) con una entrada por cada una. El manifiesto y el
    documento de origen no se tocan. Las anclas copiadas quedan con el
    placeholder hasta la proxima sincronizacion. Devuelve la ruta del
    manifiesto nuevo. Lanza ManifestError o DocumentSaveError - el llamador
    decide como mostrarlo.

    El `heading_level_offset` de cada entrada de origen NO se copia: como
    toda ancla copiada se instala al final del documento (sin titulo de
    referencia), se fuerza a 0, igual que install_anchor_and_update_manifest
    hace cuando `position.heading is None`. De lo contrario, un ancla que en
    el documento de origen estaba anidada bajo un titulo (offset > 0) seguia
    renderizando anidada para siempre en el documento nuevo, sin importar
    que ya no tenga ningun titulo de referencia junto a ella."""
    source_manifest = load_manifest(source_manifest_path)

    installed = []
    for entry in source_manifest.modules:
        tag, _ = install_module_anchor(document, entry.module_id, insert_after=None, insert_before=None)
        installed.append((tag, entry))

    try:
        backups_dir = docx_path.parent / "report_backups"
        create_backup(docx_path, backups_dir)
        mark_fields_for_update(document)
        save_document_atomic(document, docx_path)
    except Exception as exc:
        for tag, _entry in installed:
            remove_module_anchor(document, tag)
        raise DocumentSaveError(
            f"No se pudo guardar {docx_path.name}: {exc}. Verifica que no esté abierto en Word. "
            "No se copió ninguna ancla; vuelve a elegir el documento antes de reintentar."
        ) from exc

    # `new_path` es siempre nuevo (new_manifest_path_for nunca resuelve un
    # archivo existente) - se arma el Manifest en memoria y se aplican todas
    # las entradas de una vez con _apply_module_entry, guardando con un solo
    # save_manifest al final, en vez de un load+save por ancla. Antes, si
    # save_manifest fallaba a mitad del loop (manifiesto tocado por otro
    # proceso, disco lleno), el .docx ya tenia TODAS las anclas fisicas
    # instaladas pero el manifiesto solo las entradas escritas hasta ese
    # punto - el resto quedaba huerfano (ancla fisica sin entrada), invisible
    # para run_sync. Con un solo guardado, o quedan todas las entradas
    # (exito) o ninguna (la excepcion se propaga con el manifiesto nuevo
    # todavia sin crear, igual que si `installed` hubiera fallado antes).
    new_path = new_manifest_path_for(docx_path)
    new_manifest = Manifest(project=ProjectInfo(name=docx_path.stem, docx_path=docx_path.name))
    carpeta_origen = source_manifest_path.parent
    for tag, entry in installed:
        # La fuente se resuelve desde la carpeta del manifiesto de origen y se vuelve a expresar
        # desde la del documento nuevo (`..` solo porque el usuario pidio copiar las anclas).
        fuente = manifest_paths.resolver(entry.source_json, carpeta_origen)
        try:
            ref = _ref_fuente(fuente, docx_path)
        except ManifestError:
            ref = str(fuente)  # absoluta de otra unidad: no hay forma relativa; se conserva y se avisa
        _apply_module_entry(
            new_manifest,
            entry.module_id,
            tag,
            ref,
            _GeneratorRef(entry.generator, entry.json_schema_ref),
            0,  # sin titulo de referencia (se instala al final del doc): capitulo nuevo
        )
    save_manifest(new_manifest, new_path)
    return new_path


def save_document(document, docx_path: Path) -> None:
    """Guardado explicito y suelto del .docx (con backup automatico), para
    usarlo en cualquier momento sin depender de instalar un ancla o
    sincronizar. Lanza DocumentSaveError - el llamador decide como mostrarlo."""
    try:
        backups_dir = docx_path.parent / "report_backups"
        create_backup(docx_path, backups_dir)
        mark_fields_for_update(document)
        save_document_atomic(document, docx_path)
    except Exception as exc:
        raise DocumentSaveError(
            f"No se pudo guardar {docx_path.name}: {exc}. Verifica que no esté abierto en Word."
        ) from exc


def remove_anchor_and_update_manifest(document, docx_path: Path, tag: str) -> bool:
    """Quita el ancla con ese `tag` exacto del documento, guarda el .docx
    (con backup previo) y quita la entrada del manifiesto si la tenia.
    Devuelve False si no habia ningun ancla con ese tag en el documento (no
    se guarda nada en ese caso). Funciona igual para anclas "huerfanas" (sin
    entrada en el manifiesto, p. ej. de una version anterior): se quitan del
    documento aunque el manifiesto no las tuviera registradas. Lanza
    DocumentSaveError - el llamador decide como mostrarlo."""
    if not remove_module_anchor(document, tag):
        return False

    save_document(document, docx_path)

    manifest_path = manifest_path_for(docx_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        return True
    if manifest.remove_module_by_tag(tag):
        save_manifest(manifest, manifest_path)
    return True


def _apply_module_entry(
    manifest: Manifest,
    module_id: str,
    tag: str,
    json_path: str,
    generator_info,
    heading_level_offset: int = 0,
) -> None:
    """Agrega o actualiza (en memoria, sin tocar disco) la entrada de `tag`
    en `manifest`. Extraido de `update_manifest_after_install` para que
    `copy_anchors_to_new_document` pueda aplicar varias entradas sobre el
    mismo `Manifest` y guardarlas todas juntas en un solo `save_manifest` al
    final, en vez de un round-trip de disco (load+save) por ancla."""
    existing = manifest.get_module_by_tag(tag)
    if existing is not None:
        existing.tag = tag
        existing.source_json = json_path
        existing.generator = generator_info.import_path
        existing.json_schema_ref = generator_info.schema_path
        existing.enabled = True
        existing.heading_level_offset = heading_level_offset
        # Reinstalar una ancla reutiliza el mismo tag (tag_for_module es
        # deterministico) - sin este reset, el last_sync de la instancia
        # ANTERIOR (ya eliminada) queda asociado a esta instancia nueva, y el
        # sync engine podria comparar el JSON fuente actual contra el
        # source_hash de una instalacion completamente distinta y creer
        # erroneamente que "no cambio nada".
        existing.last_sync = None
    else:
        manifest.modules.append(
            ModuleEntry(
                module_id=module_id,
                tag=tag,
                source_json=json_path,
                generator=generator_info.import_path,
                json_schema_ref=generator_info.schema_path,
                enabled=True,
                heading_level_offset=heading_level_offset,
            )
        )


def update_manifest_after_install(
    docx_path: Path, module_id: str, tag: str, json_path: str, generator_info, heading_level_offset: int = 0
) -> None:
    manifest_path = manifest_path_for(docx_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        manifest = Manifest(project=ProjectInfo(name=docx_path.stem, docx_path=docx_path.name))

    if manifest_paths.es_absoluta(str(json_path)):
        json_path = _ref_fuente(json_path, docx_path)
    _apply_module_entry(manifest, module_id, tag, str(json_path), generator_info, heading_level_offset)
    save_manifest(manifest, manifest_path)


# ================= posicion de insercion =================


def resolve_position(option, insert_at_start: bool):
    """
    Si `insert_at_start` esta activo y la opcion viene de un titulo (no de
    "Al final del documento"), devuelve la variante "al inicio de la
    seccion" (ver engine.heading_scanner.start_of_section_option) en vez de
    la opcion tal cual viene del arbol (que por default apunta al final de
    la seccion). Si `option` es None o no tiene titulo asociado, se devuelve
    sin cambios - el toggle no tiene efecto sobre "Al final del documento"."""
    if option is None or not insert_at_start or option.heading is None:
        return option
    from backend.report_builder.engine.heading_scanner import start_of_section_option

    return start_of_section_option(option.heading)
