"""SAP2000 acciones para "Diseño de secciones" (section cuts) de Fundaciones.

Puerto literal de ``app/sap2000/section_cuts.py`` del escritorio
(engineering_suite / foundation_calc) al patrón de este repositorio: sin
conexión COM propia, cada operación recibe el ``SapModel`` ya conectado por el
actor STA de la sesión y las rutas se instalan desde
``bridge.actions.install_routes`` con el mismo envoltorio ``call`` (igual que
``bridge/esbelteces.py`` y ``bridge/espectro.py``).

Qué permite y qué no permite el OAPI (heredado del original):

* No existe ningún objeto para CREAR section cuts. La única vía programática
  es editar la tabla "Section Cut Definitions" con ``DatabaseTables``
  (``GetTableForEditingArray`` -> ``SetTableForEditingArray`` ->
  ``ApplyEditedTables``). Eso exige el modelo DESBLOQUEADO y desbloquear BORRA
  los resultados del análisis, por eso la creación nunca desbloquea por su
  cuenta: devuelve ``requiere_desbloqueo`` y el llamador (la web) pide
  confirmación explícita antes de reenviar con ``desbloquear: true``.
* Lectura: ``Results.Setup.SetSectionCutSelectedForOutput`` +
  ``Results.SectionCutAnalysis`` (11 arrays de salida, los últimos del tuple).

Las firmas de ``SectionCutAnalysis``, ``GetTableForEditingArray``,
``SetTableForEditingArray`` y ``ApplyEditedTables`` NO están verificadas contra
un SAP2000 real (tampoco lo estaban en el escritorio): se portan tal cual, con
los mismos intentos tolerantes. Ver docs/SAP2000_ACCEPTANCE.md.

Los nombres de campo de la tabla cambian entre versiones de SAP2000, así que no
se hardcodean: se leen del propio modelo y se hace coincidir por nombre
normalizado (``_indice_campo``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, field_validator

from bridge.actions import (
    ActionValidationError,
    _call_display_table,
    _call_group_assignments,
    _get_name_list,
    _parse_group_assignments,
    _valid_combo_name,
    _valid_name,
    _with_ton_m_c,
    load_cases_and_combos,
    model_is_locked,
    select_all_cases_for_output,
)

TABLA_SECTION_CUTS = "Section Cut Definitions"

# Alias (normalizados) de los campos que se necesitan escribir. SAP2000 los
# nombra distinto según la versión; se prueba cada alias en orden.
ALIAS_CAMPO_NOMBRE = ("name", "sectioncut", "sectcutname", "sectcut", "seccutname")
ALIAS_CAMPO_DEFINIDO_POR = ("definedby", "defby", "definedbyoption")
ALIAS_CAMPO_GRUPO = ("group", "groupname", "sectcutgroup")
ALIAS_CAMPO_TIPO_RESULTADO = ("resulttype", "restype", "resultsstype")

_MAX_ITEMS = 500

MSG_MODELO_BLOQUEADO = (
    "El modelo está bloqueado (ya fue analizado). Definir un section cut "
    "requiere desbloquearlo, y eso borra todos los resultados del análisis."
)


class SectionCutError(RuntimeError):
    """Error de section cuts con cuerpo estructurado para el 409 del puente.

    ``model_not_run`` replica el ``modelo_no_corrido`` del original: la web lo
    usa para elegir entre "Modelo no corrido" y "Error" al fallar la
    extracción de fuerzas.
    """

    def __init__(self, message: str, *, model_not_run: bool = False):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message, "model_not_run": model_not_run}


# ---------------------------------------------------------------------------
# Frames seleccionados: ver bridge.actions.selected_objects(model, "frames")
# (eObjectType 2 = Frame), mismo patrón que obtener_frames_seleccionados.
# ---------------------------------------------------------------------------


def listar_grupos_disponibles(model: Any) -> list[str]:
    """Port de ``listar_grupos_disponibles``: ``GroupDef.GetNameList``."""
    return _get_name_list(model.GroupDef, "GroupDef")


def obtener_miembros_de_grupo(model: Any, nombre_grupo: str) -> dict[str, list[str]]:
    """Port de ``_obtener_miembros_de_grupo``: miembros de un grupo por tipo
    de objeto (1=Point, 2=Frame, 5=Area)."""
    if not nombre_grupo:
        return {"puntos": [], "frames": [], "shells": []}
    raw = _call_group_assignments(model, nombre_grupo)
    tipos, nombres = _parse_group_assignments(raw)
    return {
        "puntos": [nombres[i] for i in range(len(nombres)) if tipos[i] == 1],
        "frames": [nombres[i] for i in range(len(nombres)) if tipos[i] == 2],
        "shells": [nombres[i] for i in range(len(nombres)) if tipos[i] == 5],
    }


def _asegurar_grupo(model: Any, nombre_grupo: str, nombres_frames: list[str]) -> None:
    try:
        model.GroupDef.SetGroup(nombre_grupo)
    except Exception:
        pass
    for nombre in nombres_frames:
        try:
            model.FrameObj.SetGroupAssign(nombre, nombre_grupo)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tablas de base de datos (única vía OAPI para definir section cuts)
# ---------------------------------------------------------------------------


def _normalizar_campo(nombre: Any) -> str:
    """Reduce un field key a minúsculas alfanuméricas, para comparar alias."""
    return re.sub(r"[^a-z0-9]", "", str(nombre).lower())


def _indice_campo(campos: list[str], alias: tuple[str, ...]) -> int | None:
    """Índice del primer campo cuyo nombre normalizado coincida con algún alias."""
    normalizados = [_normalizar_campo(c) for c in campos]
    for a in alias:
        if a in normalizados:
            return normalizados.index(a)
    return None


def _parsear_tabla_bd(ret: Any) -> tuple[list[str], list[list[str]]]:
    """Normaliza la salida de GetTableForDisplayArray/GetTableForEditingArray a
    ``(campos, filas)``. Port literal de ``_parsear_tabla_bd``: busca el par de
    arreglos que satisface ``len(datos) == n_registros * len(campos)`` en vez
    de asumir posiciones fijas en la tupla."""
    if not isinstance(ret, (list, tuple)):
        return [], []

    arrays = [[str(x) for x in v] for v in ret if isinstance(v, (list, tuple))]
    enteros = [int(v) for v in ret if isinstance(v, int) and not isinstance(v, bool)]

    mejor: tuple[int, int] | None = None
    for i, campos in enumerate(arrays):
        if not campos:
            continue
        for j, datos in enumerate(arrays):
            if i == j or not datos:
                continue
            if len(datos) % len(campos) != 0:
                continue
            n = len(datos) // len(campos)
            if enteros and n not in enteros:
                continue
            if mejor is None or len(arrays[mejor[1]]) < len(datos):
                mejor = (i, j)

    if mejor is None:
        # Tabla sin registros: solo se pueden recuperar los nombres de campo.
        campos = max(arrays, key=len) if arrays else []
        return campos, []

    campos, datos = arrays[mejor[0]], arrays[mejor[1]]
    n_campos = len(campos)
    filas = [datos[k : k + n_campos] for k in range(0, len(datos), n_campos)]
    return campos, filas


def _llamar_get_table_editing(model: Any, table_key: str) -> Any:
    """``DatabaseTables.GetTableForEditingArray`` tolerando firmas cortas y largas."""
    db = model.DatabaseTables
    try:
        return db.GetTableForEditingArray(table_key, "", 0, [], 0, [])
    except Exception:
        return db.GetTableForEditingArray(table_key, "")


def _campos_de_tabla(model: Any, table_key: str) -> list[str]:
    """Field keys de una tabla vía ``GetAllFieldsInTable``; respaldo cuando la
    tabla todavía no tiene registros y ``_parsear_tabla_bd`` no puede inferir
    las columnas desde los datos."""
    try:
        ret = model.DatabaseTables.GetAllFieldsInTable(table_key)
    except Exception:
        try:
            ret = model.DatabaseTables.GetAllFieldsInTable(table_key, 0, [], [], [], [], [])
        except Exception:
            return []

    if not isinstance(ret, (list, tuple)):
        return []
    arrays = [[str(x) for x in v] for v in ret if isinstance(v, (list, tuple)) and v]
    enteros = [int(v) for v in ret if isinstance(v, int) and not isinstance(v, bool)]
    # GetAllFieldsInTable devuelve varios arreglos paralelos (FieldKey,
    # FieldName, Description, UnitsString, IsImportable); el primero de largo
    # NumberFields es el de field keys.
    for arr in arrays:
        if not enteros or len(arr) in enteros:
            return arr
    return arrays[0] if arrays else []


def _llamar_apply_edited_tables(model: Any) -> str:
    """Aplica las tablas editadas y devuelve el import log (vacío si no hubo
    problemas). Lanza ``RuntimeError`` si SAP2000 reporta errores fatales.
    Port literal de ``_llamar_apply_edited_tables``."""
    try:
        ret = model.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    except Exception:
        ret = model.DatabaseTables.ApplyEditedTables(True)

    if not isinstance(ret, (list, tuple)):
        if isinstance(ret, int) and ret != 0:
            raise RuntimeError(f"ApplyEditedTables devolvió código {ret}.")
        return ""

    enteros = [v for v in ret if isinstance(v, int) and not isinstance(v, bool)]
    log = " ".join(str(v) for v in ret if isinstance(v, str) and v.strip())

    # Firma: (NumFatalErrors, NumErrorMsgs, NumWarnMsgs, NumInfoMsgs,
    #         ImportLog, ret). Errores fatales/errores => no se aplicó.
    if len(enteros) >= 2 and (enteros[0] > 0 or enteros[1] > 0):
        raise RuntimeError(
            f"SAP2000 rechazó la definición del section cut "
            f"({enteros[0]} error(es) fatal(es), {enteros[1]} error(es))."
            + (f"\nDetalle: {log}" if log else "")
        )
    return log


def listar_section_cuts(model: Any) -> list[dict[str, str]]:
    """Section cuts ya definidos en el modelo conectado: ``[{nombre, grupo}]``.

    Solo lectura (no desbloquea ni borra resultados). Port de
    ``listar_section_cuts``: lee "Section Cut Definitions" y devuelve los
    nombres (sin repetir); ``grupo`` es el valor de la columna de grupo de esa
    fila si la versión de SAP2000 la expone, ``""`` si no.
    """
    try:
        ret = _call_display_table(model, TABLA_SECTION_CUTS)
    except Exception:
        return []

    campos, filas = _parsear_tabla_bd(ret)
    if not campos or not filas:
        return []

    idx = _indice_campo(campos, ALIAS_CAMPO_NOMBRE)
    if idx is None:
        return []
    idx_grupo = _indice_campo(campos, ALIAS_CAMPO_GRUPO)

    cuts: list[dict[str, str]] = []
    vistos: set[str] = set()
    for fila in filas:
        if idx < len(fila) and fila[idx] and fila[idx] not in vistos:
            vistos.add(fila[idx])
            grupo = fila[idx_grupo] if idx_grupo is not None and idx_grupo < len(fila) else ""
            cuts.append({"nombre": fila[idx], "grupo": grupo})
    return cuts


def _escribir_definicion_section_cut(model: Any, nombre: str, nombre_grupo: str) -> None:
    """Agrega una fila a "Section Cut Definitions" (defined by group, resultados
    de análisis) y la aplica. Port literal de ``_escribir_definicion_section_cut``;
    los campos no reconocidos se dejan vacíos para que SAP2000 aplique su default."""
    try:
        ret = _llamar_get_table_editing(model, TABLA_SECTION_CUTS)
    except Exception as e:
        raise RuntimeError(
            f"No se pudo leer la tabla '{TABLA_SECTION_CUTS}' de SAP2000: {e}\n\n"
            "El OAPI de SAP2000 no permite crear section cuts de otra forma. "
            "Defínelo a mano en SAP2000 (Define > Section Cuts > Add Section "
            f"Cut > Defined by Group, grupo '{nombre_grupo}') y luego usa "
            '"Importar section cuts desde SAP2000...".'
        ) from e

    campos, filas = _parsear_tabla_bd(ret)
    if not campos:
        campos = _campos_de_tabla(model, TABLA_SECTION_CUTS)
    if not campos:
        raise RuntimeError(
            f"SAP2000 no expuso los campos de la tabla '{TABLA_SECTION_CUTS}', "
            "así que no se puede definir el section cut por código.\n\n"
            "Defínelo a mano en SAP2000 (Define > Section Cuts > Add Section "
            f"Cut > Defined by Group, grupo '{nombre_grupo}') y luego usa "
            '"Importar section cuts desde SAP2000...".'
        )

    idx_nombre = _indice_campo(campos, ALIAS_CAMPO_NOMBRE)
    idx_grupo = _indice_campo(campos, ALIAS_CAMPO_GRUPO)
    if idx_nombre is None or idx_grupo is None:
        raise RuntimeError(
            f"No se reconocieron los campos de nombre/grupo en la tabla "
            f"'{TABLA_SECTION_CUTS}' de esta versión de SAP2000 "
            f"(campos disponibles: {', '.join(campos)}).\n\n"
            "Defínelo a mano en SAP2000 (Define > Section Cuts > Add Section "
            f"Cut > Defined by Group, grupo '{nombre_grupo}') y luego usa "
            '"Importar section cuts desde SAP2000...".'
        )

    valores_por_indice: dict[int, str] = {idx_nombre: nombre, idx_grupo: nombre_grupo}
    idx_definido_por = _indice_campo(campos, ALIAS_CAMPO_DEFINIDO_POR)
    if idx_definido_por is not None:
        valores_por_indice[idx_definido_por] = "Group"
    idx_tipo = _indice_campo(campos, ALIAS_CAMPO_TIPO_RESULTADO)
    if idx_tipo is not None:
        valores_por_indice[idx_tipo] = "Analysis"

    fila_nueva = [valores_por_indice.get(i, "") for i in range(len(campos))]

    # Reemplaza la definición si ya existía con ese nombre, en vez de duplicarla.
    filas_finales = [f for f in filas if not (idx_nombre < len(f) and f[idx_nombre] == nombre)]
    filas_finales.append(fila_nueva)

    datos_planos = [celda for fila in filas_finales for celda in fila]
    version_llamada = 1
    num_registros_llamada = len(filas_finales)
    ret_set = model.DatabaseTables.SetTableForEditingArray(
        TABLA_SECTION_CUTS, version_llamada, campos, num_registros_llamada, datos_planos
    )
    # No se asume que el código de retorno esté en la última posición:
    # ``TableVersion`` y ``NumberRecords`` son ByRef de entrada que win32com
    # puede devolver de eco junto con el código real; se descartan esos dos
    # valores conocidos y se toma el entero restante.
    codigo: int | None
    if isinstance(ret_set, (list, tuple)):
        candidatos = [
            v
            for v in ret_set
            if isinstance(v, int)
            and not isinstance(v, bool)
            and v not in (version_llamada, num_registros_llamada)
        ]
        codigo = candidatos[0] if candidatos else None
    elif isinstance(ret_set, int) and not isinstance(ret_set, bool):
        codigo = ret_set
    else:
        codigo = None
    if codigo:
        raise RuntimeError(f"SetTableForEditingArray devolvió código {codigo}.")

    _llamar_apply_edited_tables(model)


def _resultado_creacion(
    *, creado: bool, requiere_desbloqueo: bool, mensaje: str, elementos: list[str]
) -> dict[str, Any]:
    return {
        "creado": creado,
        "requiere_desbloqueo": requiere_desbloqueo,
        "mensaje": mensaje,
        "elementos": elementos,
    }


def crear_section_cut(
    model: Any,
    nombre: str,
    grupo: str | None,
    frames: list[str] | None,
    desbloquear: bool,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Port de ``crear_section_cut`` (``frames`` dados: arma el grupo ``grupo``
    con ellos) y de ``crear_section_cut_desde_grupo`` (sin ``frames``: usa el
    grupo ya existente ``grupo``). Mismo manejo de bloqueo: con el modelo
    bloqueado y ``desbloquear`` falso no modifica nada y devuelve
    ``requiere_desbloqueo``. Con ``dry_run`` (ruta preview) nunca escribe: solo
    valida y informa si haría falta desbloquear."""
    if frames:
        elementos = list(frames)
        grupo_final = grupo or f"GRP_{nombre}"
    else:
        grupo_final = grupo or ""
        miembros = obtener_miembros_de_grupo(model, grupo_final)
        if not miembros["puntos"] and not miembros["shells"] and not miembros["frames"]:
            raise ValueError(
                f"El grupo '{grupo_final}' no tiene nodos, shells ni frames asignados en SAP2000."
            )
        elementos = miembros["puntos"] + miembros["shells"] + miembros["frames"]

    if model_is_locked(model):
        if not desbloquear:
            return _resultado_creacion(
                creado=False,
                requiere_desbloqueo=True,
                mensaje=MSG_MODELO_BLOQUEADO,
                elementos=elementos,
            )
        if not dry_run:
            try:
                model.SetModelIsLocked(False)
            except Exception as e:
                raise RuntimeError(f"No se pudo desbloquear el modelo: {e}") from e

    if dry_run:
        return _resultado_creacion(creado=False, requiere_desbloqueo=False, mensaje="", elementos=elementos)

    if frames:
        _asegurar_grupo(model, grupo_final, elementos)
    _escribir_definicion_section_cut(model, nombre, grupo_final)

    return _resultado_creacion(
        creado=True,
        requiere_desbloqueo=False,
        mensaje=f"Section cut '{nombre}' definido sobre el grupo '{grupo_final}'.",
        elementos=elementos,
    )


# ---------------------------------------------------------------------------
# Extracción de fuerzas
# ---------------------------------------------------------------------------


def _seleccionar_casos_output(model: Any, casos: list[str] | None) -> None:
    """Port de ``reacciones._seleccionar_casos_output``: deselecciona todo y
    marca para salida cada caso/combinación pedida (clasificada contra los
    nombres conocidos; si no matchea ninguno prueba ambos); sin ``casos``,
    todos los load cases."""
    if not casos:
        select_all_cases_for_output(model)
        return
    try:
        model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception:
        pass
    try:
        disponibles = load_cases_and_combos(model)
    except Exception:
        disponibles = {"load_cases": [], "combos": []}
    set_combos = set(disponibles.get("combos", []))
    set_cases = set(disponibles.get("load_cases", []))
    for caso in casos:
        caso_str = str(caso)
        if caso_str in set_combos:
            _try(lambda c=caso_str: model.Results.Setup.SetComboSelectedForOutput(c, True))
        elif caso_str in set_cases:
            _try(lambda c=caso_str: model.Results.Setup.SetCaseSelectedForOutput(c, True))
        else:
            _try(lambda c=caso_str: model.Results.Setup.SetComboSelectedForOutput(c, True))
            _try(lambda c=caso_str: model.Results.Setup.SetCaseSelectedForOutput(c, True))


def _try(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:
        pass


def _llamar_section_cut_analysis(model: Any, nombre: str) -> Any:
    """``Results.SectionCutAnalysis`` tolerando firmas COM cortas y largas."""
    try:
        return model.Results.SectionCutAnalysis(nombre, 0)
    except Exception:
        return model.Results.SectionCutAnalysis(nombre, 0, [], [], [], [], [], [], [], [], [], [], [])


def _parsear_section_cut_analysis(ret: Any) -> list[dict[str, Any]]:
    """Respuesta de SectionCutAnalysis -> filas normalizadas. Formato de 11
    arrays de datos (los últimos del tuple): Obj, Elm, LoadCase, StepType,
    StepNum, F1, F2, F3, M1, M2, M3. Mapeo del original: P=F3, V2=F1, V3=F2,
    M2=M2, M3=M3, T=M1."""
    arrays = [list(x) for x in ret if isinstance(x, (list, tuple))] if isinstance(ret, (list, tuple)) else []

    if len(arrays) < 11:
        raise RuntimeError(
            f"SAP2000 SectionCutAnalysis devolvio {len(arrays)} arreglos, se "
            f"esperaban al menos 11. Respuesta: {ret!r}"
        )

    _obj, _elm, load_case, _step_type, _step_num, f1, f2, f3, m1, m2, m3 = arrays[-11:]
    number_results = min(len(load_case), len(f1), len(f2), len(f3), len(m1), len(m2), len(m3))

    return [
        {
            "caso": str(load_case[i]),
            "P": float(f3[i]),
            "V2": float(f1[i]),
            "V3": float(f2[i]),
            "M2": float(m2[i]),
            "M3": float(m3[i]),
            "T": float(m1[i]),
        }
        for i in range(number_results)
    ]


def extraer_fuerzas_section_cut(model: Any, nombre: str, casos: list[str] | None) -> list[dict[str, Any]]:
    """Fuerzas (P, V2, V3, M2, M3, T) del section cut ``nombre`` para los
    casos/combinaciones dados (todos si ``casos`` es None). Port de
    ``extraer_fuerzas_section_cut`` (unidades Ton_m_C)."""

    def read() -> list[dict[str, Any]]:
        _seleccionar_casos_output(model, casos)

        # Sin esto SectionCutAnalysis devuelve vacío aunque el corte exista:
        # SAP2000 solo reporta los section cuts marcados para salida. Falla si
        # el corte no está definido, de ahí el try/except (el vacío resultante
        # lo reporta el llamador).
        _try(lambda: model.Results.Setup.SetSectionCutSelectedForOutput(nombre, True))

        ret = _llamar_section_cut_analysis(model, nombre)
        filas = _parsear_section_cut_analysis(ret)

        if casos:
            casos_str = {str(c) for c in casos}
            filas = [f for f in filas if f["caso"] in casos_str]
        return filas

    return _with_ton_m_c(model, read)


def v_total(fila: dict[str, Any]) -> float:
    return math.hypot(fila["V2"], fila["V3"])


def m_total(fila: dict[str, Any]) -> float:
    return math.hypot(fila["M2"], fila["M3"])


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


class SectionCutBody(BaseModel):
    nombre: str
    grupo: str | None = None
    frames: list[str] | None = None
    desbloquear: bool = False
    confirmed: bool = False

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, value: str) -> str:
        return _valid_name(value, "section cut name")

    @field_validator("grupo")
    @classmethod
    def grupo_valido(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else _valid_name(value, "group")

    @field_validator("frames")
    @classmethod
    def frames_validos(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > _MAX_ITEMS * 20:
            raise ValueError("frames must contain at most 10000 entries")
        return [_valid_name(item, "frame") for item in value]


class ForcesBody(BaseModel):
    cuts: list[str]
    combos: list[str]

    @field_validator("cuts")
    @classmethod
    def cuts_validos(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("cuts must contain 1–500 entries")
        return [_valid_name(item, "section cut name") for item in value]

    @field_validator("combos")
    @classmethod
    def combos_validos(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_ITEMS * 10:
            raise ValueError("combos must contain at most 5000 entries")
        return [_valid_combo_name(item, "combo") for item in value]


def _validar_modo(body: SectionCutBody) -> None:
    if not body.frames and not body.grupo:
        raise ActionValidationError("Indica frames o un grupo existente para definir el section cut.")


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas de section cuts. ``call`` es el mismo envoltorio de
    ``bridge.actions.install_routes`` (mismo actor STA, mismo mapeo de errores)."""
    from fastapi import HTTPException

    prefix = "/v1/sap/actions/foundations/section-cuts"

    @app.get(prefix)
    def section_cuts_list():
        return call(lambda model: {"cuts": listar_section_cuts(model)})

    @app.get(f"{prefix}/groups")
    def section_cuts_groups():
        # Nombres de TODOS los grupos (también los que solo tienen frames), que
        # es lo que lista el escritorio (``listar_grupos_disponibles``).
        return call(lambda model: {"groups": listar_grupos_disponibles(model)})

    @app.post(f"{prefix}/preview")
    def section_cuts_preview(body: SectionCutBody):
        def run(model: Any) -> dict[str, Any]:
            _validar_modo(body)
            return crear_section_cut(
                model, body.nombre, body.grupo, body.frames, body.desbloquear, dry_run=True
            )

        return call(run)

    @app.post(f"{prefix}/apply")
    def section_cuts_apply(body: SectionCutBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to define a section cut")

        def run(model: Any) -> dict[str, Any]:
            _validar_modo(body)
            return crear_section_cut(model, body.nombre, body.grupo, body.frames, body.desbloquear)

        return call(run)

    @app.post(f"{prefix}/forces")
    def section_cuts_forces(body: ForcesBody):
        def run(model: Any) -> dict[str, Any]:
            resultados: dict[str, list[dict[str, Any]]] = {}
            try:
                for cut in body.cuts:
                    resultados[cut] = extraer_fuerzas_section_cut(model, cut, body.combos or None)
            except Exception as exc:
                # Mismo criterio del panel original: si el modelo nunca se
                # corrió (desbloqueado) el error es "Modelo no corrido".
                raise SectionCutError(str(exc), model_not_run=not model_is_locked(model)) from exc
            return {"resultados": resultados, "model_locked": model_is_locked(model)}

        return call(run)
