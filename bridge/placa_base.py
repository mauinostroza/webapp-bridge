"""Placa Base: lectura y selección de resultados de SAP2000 para el puente compartido.

Puerto de las acciones de ``bridge/actions.py`` y de los métodos de
``bridge/sap_client.py`` de la aplicación de escritorio Placa Base (commit
``01b7330`` de ``placa_base_simple``, rama ``web-auditoria``), al patrón de
``bridge/hormigon.py``: sin conexión COM propia. Cada función recibe el ``SapModel``
ya conectado a través del actor STA de la sesión y las rutas se instalan desde
``bridge.actions.install_routes`` con el mismo envoltorio ``call``.

Rutas (``/v1/sap/actions/placa-base/``, ver docs/PUENTE_COMPARTIDO.md):
- ``GET cargas`` y ``GET nodos``: sólo lectura.
- ``POST seleccionar-cargas`` y ``POST reacciones`` (con ``casos``/``combinaciones``
  opcionales): cambian sólo la selección de salida de resultados.
- ``POST columna-en-nodo``: sólo lectura.
- ``POST resaltar-nodos``: cambia sólo la selección de nodos de la vista.

Diferencias respecto al original (ver docs/MODULES_SOURCE.md):
- Los códigos de retorno distintos de 0 son error (409 con mensaje). El original los
  ignora en la selección de salida, en el resaltado y en la lectura de reacciones.
- Las unidades se restauran siempre; si la restauración falla, la ruta responde error.
  El original traga ese fallo.
- Un tamaño de arrays de ``JointReact`` que no cuadra es error. El original trunca.
- Un fallo de COM en ``GetConnectivity``, ``GetPoints`` o en la lectura de la columna
  es error; ``columna`` es ``null`` sólo si el nodo no tiene una columna vertical.
- Una marca de salida que SAP no puede leer se indica con ``seleccion_desconocida``
  (no se da por no seleccionada en silencio).
- El orden del código de retorno lo fija ``bridge.com_orden.normalizar`` (código al
  final). No se usa la heurística ``_payload`` del original, que confunde booleanos
  con códigos.
- Nombres repetidos en ``nodos`` de ``reacciones`` se leen una sola vez.

Probadas sólo con COM falso (``tests/test_bridge_placa_base.py``); no se han validado
contra un SAP2000 real.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from bridge.actions import ActionValidationError, _valid_combo_name, _valid_name, split_return
from bridge.com_orden import normalizar
from bridge.str2fund import Str2FundError, _parse_selection_flag

UNIDADES_TONF_M = 12  # eUnits.tonf_m: fuerza tonf, longitud m, temperatura C (placa_base_gui.py:1523)
MAX_NOMBRES = 500
MAX_LARGO_NOMBRE = 100
UMBRAL_ROTACION_DEG = 0.5
VERTICALIDAD_MINIMA = 0.50
TOLERANCIA_XY_MINIMA_M = 0.02
TOLERANCIA_XY_RELATIVA = 0.03
TIPO_PUNTO = 1  # eItemType.Point en SelectObj.GetSelected
TIPO_FRAME = 2  # eItemType.Frame en PointObj.GetConnectivity
COLUMNAS_JOINT_REACT = 11  # Obj, Elm, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3
CAMPOS_DIMENSION_I = ("depth_cm", "width_cm", "flange_thickness_cm", "web_thickness_cm")


class PlacaBaseError(RuntimeError):
    """Error de SAP2000 en una acción de Placa Base.

    ``detail`` es el cuerpo del 409 que escribe el envoltorio ``call`` de
    ``bridge.actions.install_routes``.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


# ---------------------------------------------------------------------------
# Lectura de salidas COM
# ---------------------------------------------------------------------------


def _es_entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _salida(raw: Any, operacion: str) -> list[Any]:
    """Salidas de una llamada COM sin su código de retorno; error si el código no es 0.

    El código puede ir al inicio o al final (``bridge.actions.split_return``). ``None``
    es un método sin código de retorno.
    """
    if raw is None:
        return []
    try:
        codigo, salidas = split_return(raw)
    except RuntimeError as exc:
        raise PlacaBaseError(f"SAP2000 {operacion} devolvió una respuesta inesperada.") from exc
    if codigo != 0:
        raise PlacaBaseError(f"SAP2000 {operacion} falló (código {codigo}).")
    return salidas


def _arreglos(salidas: Sequence[Any]) -> list[Any]:
    return [parte for parte in salidas if isinstance(parte, (list, tuple))]


def _nombres(raw: Any, operacion: str) -> list[str]:
    """Nombres de un ``GetNameList``: la única lista de textos de la respuesta."""
    for parte in _salida(raw, operacion):
        if isinstance(parte, (list, tuple)) and all(isinstance(x, str) for x in parte):
            return [str(x) for x in parte]
    raise PlacaBaseError(f"SAP2000 {operacion} devolvió una respuesta inesperada.")


def _nombre_modelo(model: Any) -> str:
    try:
        raw = model.GetModelFilename()
        if isinstance(raw, str):
            nombre = raw
        else:
            nombre = next((x for x in _salida(raw, "GetModelFilename") if isinstance(x, str)), "")
    except Exception:
        return "Modelo SAP2000"
    return Path(nombre).name or "Modelo SAP2000 sin guardar"


def _unidades_actuales(model: Any) -> int:
    raw = model.GetPresentUnits()
    if isinstance(raw, (tuple, list)):
        enteros = [x for x in _salida(raw, "GetPresentUnits") if _es_entero(x)]
        if len(enteros) == 1:
            return enteros[0]
    elif _es_entero(raw):
        # CSI: eUnits GetPresentUnits() devuelve el valor directamente, sin código.
        return raw
    raise PlacaBaseError("SAP2000 GetPresentUnits devolvió una respuesta inesperada.")


@contextmanager
def _unidades_tonf_m(model: Any) -> Iterator[None]:
    """Fija tonf-m mientras se lee y restaura las unidades del usuario al salir.

    Si la restauración falla, el error se eleva: las unidades del modelo quedarían
    cambiadas sin que nadie lo sepa.
    """
    anteriores = _unidades_actuales(model)
    try:
        _salida(model.SetPresentUnits(UNIDADES_TONF_M), "SetPresentUnits(tonf-m)")
        yield
    finally:
        try:
            _salida(model.SetPresentUnits(anteriores), f"SetPresentUnits({anteriores}) al restaurar")
        except Exception as exc:
            raise PlacaBaseError(
                f"No se pudieron restaurar las unidades de SAP2000 (eUnits {anteriores}). "
                f"Revise las unidades del modelo. Detalle: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Cargas y selección de salida
# ---------------------------------------------------------------------------


def _nombres_de_carga(model: Any) -> tuple[list[str], list[str]]:
    casos = _nombres(model.LoadCases.GetNameList(), "LoadCases.GetNameList")
    combinaciones = _nombres(model.RespCombo.GetNameList(), "RespCombo.GetNameList")
    return casos, combinaciones


def _marca_de_salida(setup: Any, tipo: str, nombre: str) -> bool | None:
    """Marca de salida de un caso o combinación. ``None`` si SAP no la informa."""
    metodo = "GetCaseSelectedForOutput" if tipo == "caso" else "GetComboSelectedForOutput"
    descripcion = f"Results.Setup.{metodo}({nombre!r})"
    raw = getattr(setup, metodo)(nombre)
    try:
        return _parse_selection_flag(raw, descripcion)
    except Str2FundError as exc:
        raise PlacaBaseError(str(exc)) from exc


def _seleccion_de_salida(
    model: Any, casos: list[str], combinaciones: list[str]
) -> tuple[list[str], list[str], bool]:
    """Casos y combinaciones marcados para salida.

    Devuelve ``(casos, combinaciones, desconocida)``. ``desconocida`` es ``True`` si
    alguna marca no se pudo leer; esos nombres no aparecen como seleccionados.
    """
    setup = model.Results.Setup
    desconocida = False
    marcados: dict[str, list[str]] = {"caso": [], "combinacion": []}
    for tipo, nombres in (("caso", casos), ("combinacion", combinaciones)):
        for nombre in nombres:
            marca = _marca_de_salida(setup, tipo, nombre)
            if marca is None:
                desconocida = True
            elif marca:
                marcados[tipo].append(nombre)
    return marcados["caso"], marcados["combinacion"], desconocida


def _fijar_seleccion_de_salida(model: Any, casos: list[str], combinaciones: list[str]) -> None:
    setup = model.Results.Setup
    try:
        _salida(
            setup.DeselectAllCasesAndCombosForOutput(), "Results.Setup.DeselectAllCasesAndCombosForOutput"
        )
        for nombre in casos:
            _salida(
                setup.SetCaseSelectedForOutput(nombre), f"Results.Setup.SetCaseSelectedForOutput({nombre!r})"
            )
        for nombre in combinaciones:
            _salida(
                setup.SetComboSelectedForOutput(nombre),
                f"Results.Setup.SetComboSelectedForOutput({nombre!r})",
            )
    except PlacaBaseError as exc:
        raise PlacaBaseError(
            f"{exc} La selección de salida puede haber quedado incompleta; revísela en SAP2000."
        ) from exc


def _filtrar_nombres(guardados: Sequence[str], disponibles: Sequence[str]) -> tuple[list[str], list[str]]:
    """Copia de ``filtrar_nombres`` de Placa Base: conserva, sin repetir y en orden, los
    nombres que existen en el modelo. Devuelve ``(existentes, ignorados)``."""
    indice = {str(n).strip(): str(n) for n in disponibles}
    existentes: list[str] = []
    ignorados: list[str] = []
    vistos: set[str] = set()
    for nombre in guardados:
        clave = str(nombre).strip()
        if clave in vistos:
            continue
        vistos.add(clave)
        if clave in indice:
            existentes.append(indice[clave])
        else:
            ignorados.append(clave)
    return existentes, ignorados


def listar_cargas(model: Any) -> dict[str, Any]:
    """Casos y combinaciones del modelo y los marcados para salida."""
    model = normalizar(model, destino_primero=False)
    casos, combinaciones = _nombres_de_carga(model)
    sel_casos, sel_combinaciones, desconocida = _seleccion_de_salida(model, casos, combinaciones)
    return {
        "modelo": _nombre_modelo(model),
        "casos": casos,
        "combinaciones": combinaciones,
        "seleccion": {"casos": sel_casos, "combinaciones": sel_combinaciones},
        "seleccion_desconocida": desconocida,
    }


def seleccionar_cargas(model: Any, casos: Sequence[str], combinaciones: Sequence[str]) -> dict[str, Any]:
    """Marca para salida los casos y combinaciones indicados (``set_output_selection``).

    Exige al menos uno y descarta los que no existen en el modelo. Si SAP rechaza una
    marca, la respuesta es error (el original lo ignora).
    """
    model = normalizar(model, destino_primero=False)
    if not casos and not combinaciones:
        raise ActionValidationError("Seleccione al menos un estado de carga o combinación.")
    disp_casos, disp_combinaciones = _nombres_de_carga(model)
    ok_casos, ign_casos = _filtrar_nombres(casos, disp_casos)
    ok_combinaciones, ign_combinaciones = _filtrar_nombres(combinaciones, disp_combinaciones)
    if not ok_casos and not ok_combinaciones:
        raise ActionValidationError(
            "Ninguno de los estados de carga o combinaciones indicados existe en el modelo."
        )
    _fijar_seleccion_de_salida(model, ok_casos, ok_combinaciones)
    return {
        "modelo": _nombre_modelo(model),
        "casos": ok_casos,
        "combinaciones": ok_combinaciones,
        "ignorados": {"casos": ign_casos, "combinaciones": ign_combinaciones},
    }


# ---------------------------------------------------------------------------
# Nodos y restricciones
# ---------------------------------------------------------------------------


def _nodos_seleccionados(model: Any) -> list[str]:
    arreglos = _arreglos(_salida(model.SelectObj.GetSelected(), "SelectObj.GetSelected"))
    if len(arreglos) < 2:
        raise PlacaBaseError("SAP2000 SelectObj.GetSelected devolvió una respuesta inesperada.")
    tipos, nombres = arreglos[-2], arreglos[-1]
    if len(tipos) != len(nombres):
        raise PlacaBaseError("SAP2000 SelectObj.GetSelected devolvió listas de distinto tamaño.")
    return [str(nombre) for tipo, nombre in zip(tipos, nombres) if int(tipo) == TIPO_PUNTO]


def _estado_restriccion(model: Any, nodo: str) -> bool | None:
    """``True`` con restricción, ``False`` sin ella, ``None`` si no se puede leer.

    Como el original, un nodo cuya restricción no se lee se trata como posiblemente
    restringido (``is not False``) y no se descarta.
    """
    try:
        arreglos = _arreglos(_salida(model.PointObj.GetRestraint(nodo), f"PointObj.GetRestraint({nodo})"))
    except Exception:
        return None
    if not arreglos:
        return None
    return any(bool(x) for x in arreglos[-1])


def listar_nodos(model: Any) -> dict[str, Any]:
    """Nodos seleccionados, nodos restringidos y los que se proponen para el diseño."""
    model = normalizar(model, destino_primero=False)
    seleccionados = _nodos_seleccionados(model)
    todos = _nombres(model.PointObj.GetNameList(), "PointObj.GetNameList")
    estados = {nodo: _estado_restriccion(model, nodo) for nodo in dict.fromkeys([*todos, *seleccionados])}
    restringidos = [nodo for nodo in todos if estados[nodo] is not False]
    if seleccionados:
        para_diseno = [nodo for nodo in seleccionados if estados[nodo] is not False]
    else:
        para_diseno = restringidos
    return {
        "modelo": _nombre_modelo(model),
        "seleccionados": seleccionados,
        "restringidos": restringidos,
        "para_diseno": para_diseno,
    }


# ---------------------------------------------------------------------------
# Reacciones
# ---------------------------------------------------------------------------

_CAMPOS_REACCION = ("node", "load_name", "step_type", "step_num", "P", "Vx", "Vy", "Mx", "My", "Mz")


def _reacciones_de_nodo(model: Any, nodo: str) -> list[dict[str, Any]]:
    """Filas de ``Results.JointReact`` de un nodo, en las unidades que tenga fijadas SAP."""
    arreglos = _arreglos(_salida(model.Results.JointReact(nodo, 0), f"Results.JointReact({nodo})"))
    if len(arreglos) < COLUMNAS_JOINT_REACT:
        raise PlacaBaseError(
            f"SAP2000 Results.JointReact({nodo}) devolvió {len(arreglos)} columnas; se esperaban "
            f"{COLUMNAS_JOINT_REACT}."
        )
    obj, elm, caso, tipo, paso, f1, f2, f3, m1, m2, m3 = (
        list(columna) for columna in arreglos[-COLUMNAS_JOINT_REACT:]
    )
    if len({len(columna) for columna in (obj, elm, caso, tipo, paso, f1, f2, f3, m1, m2, m3)}) != 1:
        raise PlacaBaseError(f"SAP2000 Results.JointReact({nodo}) devolvió columnas de distinto tamaño.")
    filas = []
    for i in range(len(caso)):
        filas.append(
            {
                "node": str(nodo),
                "load_name": str(caso[i]),
                "step_type": str(tipo[i]),
                "step_num": _finito(float(paso[i]), f"step_num del nodo {nodo}"),
                "P": _finito(float(f3[i]), f"P del nodo {nodo}"),
                "Vx": _finito(float(f1[i]), f"Vx del nodo {nodo}"),
                "Vy": _finito(float(f2[i]), f"Vy del nodo {nodo}"),
                "Mx": _finito(float(m1[i]), f"Mx del nodo {nodo}"),
                "My": _finito(float(m2[i]), f"My del nodo {nodo}"),
                "Mz": _finito(float(m3[i]), f"Mz del nodo {nodo}"),
            }
        )
    return filas


def _finito(valor: float, donde: str) -> float:
    if not math.isfinite(valor):
        raise PlacaBaseError(f"SAP2000 devolvió un valor no finito ({donde}).")
    return valor


def _rotar_al_eje_local(fila: dict[str, Any], angulo_deg: float) -> dict[str, Any]:
    """Vx, Vy, Mx y My al eje local del perfil (de global a local: giro de -ángulo).
    P y Mz no se rotan. Mismas fórmulas que ``JointReaction`` en ``reactions_local``."""
    theta = math.radians(-angulo_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return {
        **fila,
        "Vx": fila["Vx"] * cos_t + fila["Vy"] * sin_t,
        "Vy": -fila["Vx"] * sin_t + fila["Vy"] * cos_t,
        "Mx": fila["Mx"] * cos_t + fila["My"] * sin_t,
        "My": -fila["Mx"] * sin_t + fila["My"] * cos_t,
    }


def reacciones(
    model: Any,
    nodos: Sequence[str],
    casos: Sequence[str] = (),
    combinaciones: Sequence[str] = (),
    angulo_deg: float | None = None,
) -> dict[str, Any]:
    """Reacciones en los nodos restringidos, en tonf y m (``_importar_reacciones_sap``).

    Si se indican casos o combinaciones, primero se marcan para salida. Con
    ``angulo_deg`` de módulo >= 0.5, Vx, Vy, Mx y My se giran al eje local del perfil.
    """
    model = normalizar(model, destino_primero=False)
    if casos or combinaciones:
        _fijar_seleccion_de_salida(model, list(casos), list(combinaciones))
    candidatos = list(dict.fromkeys(nodos))
    with _unidades_tonf_m(model):
        filas: list[dict[str, Any]] = []
        for nodo in candidatos:
            if _estado_restriccion(model, nodo) is False:
                continue
            filas.extend(_reacciones_de_nodo(model, nodo))
    if not filas:
        raise PlacaBaseError(
            "SAP2000 no devolvió reacciones. Ejecute el análisis y revise la selección de resultados."
        )
    rotadas = angulo_deg is not None and abs(angulo_deg) >= UMBRAL_ROTACION_DEG
    if rotadas:
        filas = [_rotar_al_eje_local(fila, angulo_deg) for fila in filas]
    return {
        "unidades": "tonf-m",
        "ejes": "locales" if rotadas else "globales",
        "angulo_deg": angulo_deg,
        "reacciones": [{campo: fila[campo] for campo in _CAMPOS_REACCION} for fila in filas],
    }


# ---------------------------------------------------------------------------
# Columna en nodo
# ---------------------------------------------------------------------------


def _frames_conectados(model: Any, nodo: str) -> list[str]:
    arreglos = _arreglos(_salida(model.PointObj.GetConnectivity(nodo), f"PointObj.GetConnectivity({nodo})"))
    if len(arreglos) < 2:
        raise PlacaBaseError(f"SAP2000 PointObj.GetConnectivity({nodo}) devolvió una respuesta inesperada.")
    tipos, nombres = arreglos[-2], arreglos[-1]
    if len(tipos) != len(nombres):
        raise PlacaBaseError(f"SAP2000 PointObj.GetConnectivity({nodo}) devolvió listas de distinto tamaño.")
    frames: list[str] = []
    for tipo, nombre in zip(tipos, nombres):
        try:
            es_frame = int(tipo) == TIPO_FRAME
        except (TypeError, ValueError):
            es_frame = str(tipo).strip().lower() in {"2", "frame", "frameobj", "object"}
        if es_frame and str(nombre):
            frames.append(str(nombre))
    return frames


def _coordenadas(model: Any, punto: str) -> tuple[float, float, float]:
    numeros = [
        float(x)
        for x in _salida(model.PointObj.GetCoordCartesian(punto), "PointObj.GetCoordCartesian")
        if _es_numero(x)
    ]
    if len(numeros) < 3:
        raise PlacaBaseError(f"SAP2000 PointObj.GetCoordCartesian({punto}) devolvió menos de 3 coordenadas.")
    return numeros[0], numeros[1], numeros[2]


def _extremos_frame(model: Any, frame: str) -> list[str]:
    return [
        str(x)
        for x in _salida(model.FrameObj.GetPoints(frame), f"FrameObj.GetPoints({frame})")
        if isinstance(x, str) and x
    ]


def _seccion_frame(model: Any, frame: str) -> str:
    salidas = _salida(model.FrameObj.GetSection(frame), f"FrameObj.GetSection({frame})")
    return next((x for x in salidas if isinstance(x, str) and x), "")


def _angulo_local_frame(model: Any, frame: str) -> float | None:
    numeros = [
        float(x)
        for x in _salida(model.FrameObj.GetLocalAxes(frame), f"FrameObj.GetLocalAxes({frame})")
        if _es_numero(x)
    ]
    return numeros[0] if numeros else None


def _dimensiones_en_i(model: Any, seccion: str) -> dict[str, float | None]:
    """Dimensiones de un perfil I en cm. Nulas si la sección no es I o SAP no la informa
    (como el original: no es un fallo de la columna)."""
    vacio: dict[str, float | None] = dict.fromkeys(CAMPOS_DIMENSION_I)
    if not seccion:
        return vacio
    try:
        salidas = _salida(model.PropFrame.GetISection(seccion), f"PropFrame.GetISection({seccion})")
    except Exception:
        return vacio
    # CSI: FileName, MatProp, t3, t2, tf, tw, t2b, tfb, color, notes, guid (los m se pasan a cm)
    numeros = [float(x) for x in salidas if _es_numero(x)]
    if len(numeros) < 4:
        return vacio
    return {campo: valor * 100.0 for campo, valor in zip(CAMPOS_DIMENSION_I, numeros[:4])}


def _columna_en_nodo(model: Any, nodo: str) -> dict[str, Any] | None:
    frames = _frames_conectados(model, nodo)
    if not frames:
        return None
    xyz_nodo = _coordenadas(model, nodo)
    candidatos: list[tuple[float, int, float, float, str]] = []
    for frame in frames:
        extremos = _extremos_frame(model, frame)
        if len(extremos) < 2:
            continue
        coords = [_coordenadas(model, punto) for punto in extremos[:2]]
        dx, dy, dz = (coords[1][i] - coords[0][i] for i in range(3))
        longitud = math.sqrt(dx * dx + dy * dy + dz * dz)
        if not longitud:
            continue
        vertical = abs(dz) / longitud
        if vertical < VERTICALIDAD_MINIMA:
            continue
        if extremos[0] == nodo:
            indice, coincide = 0, 1
        elif extremos[1] == nodo:
            indice, coincide = 1, 1
        else:
            distancias = [sum((coords[k][i] - xyz_nodo[i]) ** 2 for i in range(3)) for k in (0, 1)]
            indice, coincide = (0 if distancias[0] <= distancias[1] else 1), 0
        otro = coords[1 - indice]
        desfase_xy = math.hypot(otro[0] - xyz_nodo[0], otro[1] - xyz_nodo[1])
        if desfase_xy > max(TOLERANCIA_XY_MINIMA_M, TOLERANCIA_XY_RELATIVA * longitud):
            continue
        # Mismo orden que el original: manda la verticalidad y después la coincidencia de extremo.
        candidatos.append((vertical, coincide, -desfase_xy, longitud, frame))
    if not candidatos:
        return None
    _vertical, _coincide, _desfase, longitud_m, frame = max(candidatos)
    seccion = _seccion_frame(model, frame)
    return {
        "node": nodo,
        "frame": frame,
        "section": seccion,
        "length_m": longitud_m,
        "local_angle_deg": _angulo_local_frame(model, frame),
        **_dimensiones_en_i(model, seccion),
    }


def columna_en_nodo(model: Any, nodos: Sequence[str]) -> dict[str, Any]:
    """Columna vertical que llega a cada nodo, con su sección y dimensiones en cm."""
    model = normalizar(model, destino_primero=False)
    with _unidades_tonf_m(model):
        columnas = [{"nodo": nodo, "columna": _columna_en_nodo(model, nodo)} for nodo in nodos]
    return {"columnas": columnas}


# ---------------------------------------------------------------------------
# Resaltado
# ---------------------------------------------------------------------------


def resaltar_nodos(model: Any, nodos: Sequence[str]) -> dict[str, Any]:
    """Selecciona los nodos en SAP2000 y refresca la vista (botón «Resaltar en SAP2000»)."""
    model = normalizar(model, destino_primero=False)
    _salida(model.SelectObj.ClearSelection(), "SelectObj.ClearSelection")
    for nodo in nodos:
        _salida(model.PointObj.SetSelected(nodo, True), f"PointObj.SetSelected({nodo!r})")
    _salida(model.View.RefreshView(), "View.RefreshView")
    return {"resaltado": True}


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


def _nombre_de_sap(valor: str, etiqueta: str) -> str:
    combinacion = etiqueta == "combinaciones"  # SAP2000 admite «:» en combinaciones («ASD1: D + L»)
    try:
        nombre = _valid_combo_name(valor, etiqueta) if combinacion else _valid_name(valor, etiqueta)
    except ActionValidationError as exc:
        prohibidos = "/ \\ * ? \" < > |" if combinacion else "/ \\ : * ? \" < > |"
        raise ValueError(
            f"'{etiqueta}' admite nombres de 1 a 128 caracteres sin {prohibidos} ni caracteres de control."
        ) from exc
    if len(nombre) > MAX_LARGO_NOMBRE:
        raise ValueError(f"Un nombre de '{etiqueta}' supera {MAX_LARGO_NOMBRE} caracteres.")
    return nombre


def _lista_de_nombres(valor: Sequence[str], etiqueta: str, minimo: int) -> list[str]:
    if len(valor) < minimo:
        raise ValueError(f"'{etiqueta}' debe contener al menos {minimo} nombre(s).")
    if len(valor) > MAX_NOMBRES:
        raise ValueError(f"'{etiqueta}' admite como máximo {MAX_NOMBRES} nombres.")
    return [_nombre_de_sap(item, etiqueta) for item in valor]


class SeleccionarCargasBody(BaseModel):
    casos: list[str] | None = None
    combinaciones: list[str] | None = None

    @field_validator("casos")
    @classmethod
    def _casos(cls, valor: list[str] | None) -> list[str]:
        return _lista_de_nombres(valor or [], "casos", minimo=0)

    @field_validator("combinaciones")
    @classmethod
    def _combinaciones(cls, valor: list[str] | None) -> list[str]:
        return _lista_de_nombres(valor or [], "combinaciones", minimo=0)


class NodosBody(BaseModel):
    nodos: list[str]

    @field_validator("nodos")
    @classmethod
    def _nodos(cls, valor: list[str]) -> list[str]:
        return _lista_de_nombres(valor, "nodos", minimo=1)


class ReaccionesBody(BaseModel):
    nodos: list[str]
    casos: list[str] | None = None
    combinaciones: list[str] | None = None
    angulo_deg: float | None = None

    @field_validator("nodos")
    @classmethod
    def _nodos(cls, valor: list[str]) -> list[str]:
        return _lista_de_nombres(valor, "nodos", minimo=1)

    @field_validator("casos")
    @classmethod
    def _casos(cls, valor: list[str] | None) -> list[str]:
        return _lista_de_nombres(valor or [], "casos", minimo=0)

    @field_validator("combinaciones")
    @classmethod
    def _combinaciones(cls, valor: list[str] | None) -> list[str]:
        return _lista_de_nombres(valor or [], "combinaciones", minimo=0)

    @field_validator("angulo_deg", mode="before")
    @classmethod
    def _angulo(cls, valor: Any) -> float | None:
        if valor is None:
            return None
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError("'angulo_deg' debe ser un número.")
        if not math.isfinite(valor):
            raise ValueError("'angulo_deg' debe ser un número finito.")
        return float(valor)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/placa-base/*`` con el envoltorio ``call`` de
    ``bridge.actions.install_routes`` (actor STA, token y mapeo de errores a HTTP)."""
    prefix = "/v1/sap/actions/placa-base"

    @app.get(f"{prefix}/cargas")
    def placa_base_cargas():
        return call(listar_cargas)

    @app.post(f"{prefix}/seleccionar-cargas")
    def placa_base_seleccionar_cargas(body: SeleccionarCargasBody):
        return call(lambda model: seleccionar_cargas(model, body.casos or [], body.combinaciones or []))

    @app.get(f"{prefix}/nodos")
    def placa_base_nodos():
        return call(listar_nodos)

    @app.post(f"{prefix}/reacciones")
    def placa_base_reacciones(body: ReaccionesBody):
        return call(
            lambda model: reacciones(
                model,
                body.nodos,
                body.casos or [],
                body.combinaciones or [],
                body.angulo_deg,
            )
        )

    @app.post(f"{prefix}/columna-en-nodo")
    def placa_base_columna_en_nodo(body: NodosBody):
        return call(lambda model: columna_en_nodo(model, body.nodos))

    @app.post(f"{prefix}/resaltar-nodos")
    def placa_base_resaltar_nodos(body: NodosBody):
        return call(lambda model: resaltar_nodos(model, body.nodos))
