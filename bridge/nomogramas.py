"""Nomogramas: lectura de los frames seleccionados en SAP2000 para la vista «Columna y nudos».

Sigue el patrón de ``bridge/esbelteces.py``: sin conexión COM propia; la función recibe el
``SapModel`` del actor STA de la sesión y la ruta se instala desde ``bridge.actions.install_routes``
con el envoltorio ``call``.

Llamadas COM directas sobre el ``SapModel`` (como ``esbelteces``), SIN ``bridge.com_orden.normalizar``:
ese envoltorio sustituía por una función cualquier atributo invocable, y los objetos de win32com
(``model.SelectObj``) lo son, así que ``model.SelectObj.GetSelected`` fallaba con
``'function' object has no attribute 'GetSelected'``. El orden del código de retorno se resuelve
aquí por posición con ``split_return`` (código al inicio o al final).

Ruta (solo lectura): ``GET /v1/sap/actions/nomogramas/selected-frames``.

Unidades: se fija ``eUnits.N_mm_C`` (9) durante la lectura y se restauran siempre en un
``finally``. Nada se escribe en SAP2000.

Llamadas COM usadas:
- Reutilizadas de otros módulos del puente (ya en uso): ``SelectObj.GetSelected`` y
  ``FrameObj.GetSection`` (vía ``selected_frames``), ``FrameObj.GetPoints`` y
  ``PointObj.GetCoordCartesian`` (vía ``_frame_length_mm``), ``PropFrame.GetSectProps`` y
  ``PropMaterial.GetMPIsotropic``.
- NO VERIFICADAS contra un SAP2000 real (probadas solo con COM falso): ``PropFrame.GetMaterial``,
  ``FrameObj.GetReleases`` y ``FrameObj.GetModifiers``. Si ``GetMaterial`` falla se prueba
  ``GetISection`` como hace ``bridge/graph.py``. Un dato aislado que no se puede leer
  devuelve ``null`` y se explica en ``note``; no se falla toda la petición.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from bridge.actions import _frame_length_mm, _ret, selected_frames, split_return

logger = logging.getLogger(__name__)

N_MM_C = 9  # eUnits.N_mm_C
TIPO_FRAME = 2  # eObjectType.Frame en SelectObj.GetSelected
TOLERANCIA_ORIENTACION_DEG = 5.0

# Índices de los arrays de SAP2000 (orden U1, U2, U3, R1, R2, R3 / modificadores).
IDX_RELEASE_M2 = 4
IDX_RELEASE_M3 = 5
IDX_MOD_I22 = 4
IDX_MOD_I33 = 5
# Value(8) de FrameObj.GetModifiers: área, cortante 2, cortante 3, torsión, I22, I33, masa, peso.
MODIFICADORES_LONGITUD = 8


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _finito(valor: Any) -> float | None:
    """``float`` finito o ``None`` (NaN/inf/no numérico no son JSON válido)."""
    if not _es_numero(valor):
        return None
    try:
        numero = float(valor)
    except (OverflowError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def sanitizar_json(valor: Any) -> Any:
    """Copia recursiva donde todo ``float`` no finito pasa a ``None`` (Starlette usa allow_nan=False)."""
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else None
    if isinstance(valor, dict):
        return {k: sanitizar_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [sanitizar_json(v) for v in valor]
    return valor


def _salidas(raw: Any, operacion: str) -> list[Any]:
    """Salidas de una llamada COM sin su código; ``RuntimeError`` si el código no es 0."""
    codigo, salidas = split_return(raw)
    if codigo != 0:
        raise RuntimeError(f"SAP2000 {operacion} devolvió {codigo}")
    return salidas


def _secuencias(salidas: list[Any]) -> list[list[Any]]:
    return [list(x) for x in salidas if isinstance(x, (list, tuple))]


def _propiedades_seccion(model: Any, seccion: str) -> tuple[float | None, float | None, float | None]:
    """``(area, i33, i22)`` en mm² / mm⁴ con ``GetSectProps`` (índices de esbelteces)."""
    outs = _salidas(
        model.PropFrame.GetSectProps(seccion, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        f"PropFrame.GetSectProps({seccion!r})",
    )
    # Mismos índices que bridge.esbelteces._get_section_props: A=0, I22=4, I33=5.
    if len(outs) < 6 or not all(_es_numero(outs[i]) for i in (0, 4, 5)):
        raise RuntimeError(f"Respuesta inesperada de PropFrame.GetSectProps({seccion!r})")
    return float(outs[0]), float(outs[5]), float(outs[4])


def _material(model: Any, seccion: str) -> str | None:
    """Nombre del material. NO VERIFICADO: ``PropFrame.GetMaterial``; alternativa ``GetISection``."""
    try:
        outs = _salidas(model.PropFrame.GetMaterial(seccion), f"PropFrame.GetMaterial({seccion!r})")
        nombre = next((x for x in outs if isinstance(x, str) and x), None)
        if nombre:
            return nombre
    except Exception:
        pass
    try:
        # GetISection(Name, FileName, MatProp, ...): el material es la segunda cadena.
        outs = _salidas(model.PropFrame.GetISection(seccion), f"PropFrame.GetISection({seccion!r})")
        cadenas = [x for x in outs if isinstance(x, str)]
        if len(cadenas) >= 2 and cadenas[1]:
            return cadenas[1]
    except Exception:
        pass
    return None


def _modulo_e(model: Any, material: str) -> float | None:
    """E en MPa con ``PropMaterial.GetMPIsotropic`` (el mismo uso que ``bridge/graph.py``)."""
    outs = _salidas(model.PropMaterial.GetMPIsotropic(material), f"PropMaterial.GetMPIsotropic({material!r})")
    numeros = [float(x) for x in outs if _es_numero(x)]
    return numeros[0] if numeros else None


def _releases(model: Any, frame: str) -> dict[str, bool | None]:
    """Liberaciones de momento en i y j. NO VERIFICADO: ``FrameObj.GetReleases``."""
    vacio: dict[str, bool | None] = {"i_m2": None, "i_m3": None, "j_m2": None, "j_m3": None}
    outs = _salidas(model.FrameObj.GetReleases(frame), f"FrameObj.GetReleases({frame!r})")
    arrays = _secuencias(outs)
    if len(arrays) < 2 or len(arrays[0]) <= IDX_RELEASE_M3 or len(arrays[1]) <= IDX_RELEASE_M3:
        return vacio
    ii, jj = arrays[0], arrays[1]
    return {
        "i_m2": bool(ii[IDX_RELEASE_M2]),
        "i_m3": bool(ii[IDX_RELEASE_M3]),
        "j_m2": bool(jj[IDX_RELEASE_M2]),
        "j_m3": bool(jj[IDX_RELEASE_M3]),
    }


def _llamar_modificadores(model: Any, frame: str) -> Any:
    """``GetModifiers(Name, Value)``: ``Value`` es ByRef, así que va como lista preinicializada.

    Si el enlace rechaza la aridad (``TypeError``), se prueba la forma corta; el código de
    retorno decide después si hay datos o una nota.
    """
    try:
        return model.FrameObj.GetModifiers(frame, [0.0] * MODIFICADORES_LONGITUD)
    except TypeError:
        return model.FrameObj.GetModifiers(frame)


def _modificadores(model: Any, frame: str) -> dict[str, float | None]:
    """Modificadores de inercia I22/I33. NO VERIFICADO contra SAP2000 real: ``FrameObj.GetModifiers``.

    Código distinto de 0 → ``RuntimeError`` que ``_leer_frame_interno`` convierte en nota con null.
    """
    outs = _salidas(_llamar_modificadores(model, frame), f"FrameObj.GetModifiers({frame!r})")
    arrays = _secuencias(outs)
    if not arrays or len(arrays[0]) <= IDX_MOD_I33:
        return {"i22": None, "i33": None}
    valores = arrays[0]
    return {"i22": _finito(valores[IDX_MOD_I22]), "i33": _finito(valores[IDX_MOD_I33])}


def _puntos_y_coordenadas(
    model: Any, frame: str
) -> tuple[str, str, tuple[float, float, float], tuple[float, float, float]]:
    puntos = model.FrameObj.GetPoints(frame)
    _ret(puntos, f"FrameObj.GetPoints({frame!r})")
    nombres = [x for x in puntos if isinstance(x, str) and x]
    if len(nombres) < 2:
        raise RuntimeError(f"Respuesta inesperada de FrameObj.GetPoints({frame!r})")
    coords: list[tuple[float, float, float]] = []
    for punto in nombres[:2]:
        # _salidas quita el código de retorno en cualquier posición; antes un 0 inicial se leía como coordenada.
        salidas = _salidas(model.PointObj.GetCoordCartesian(punto), f"PointObj.GetCoordCartesian({punto!r})")
        numeros = [float(x) for x in salidas if _es_numero(x)]
        if len(numeros) < 3 or not all(math.isfinite(n) for n in numeros[:3]):
            raise RuntimeError(f"Respuesta inesperada de PointObj.GetCoordCartesian({punto!r})")
        coords.append((numeros[0], numeros[1], numeros[2]))
    return nombres[0], nombres[1], coords[0], coords[1]


def orientacion(p_i: tuple[float, float, float], p_j: tuple[float, float, float]) -> str:
    """``vertical`` (<5° con la vertical), ``horizontal`` (<5° con la horizontal) o ``inclinada``."""
    dx, dy, dz = (p_j[0] - p_i[0], p_j[1] - p_i[1], p_j[2] - p_i[2])
    largo = math.sqrt(dx * dx + dy * dy + dz * dz)
    if largo <= 0:
        return "inclinada"
    con_horizontal = math.degrees(math.asin(min(1.0, abs(dz) / largo)))
    if 90.0 - con_horizontal < TOLERANCIA_ORIENTACION_DEG:
        return "vertical"
    if con_horizontal < TOLERANCIA_ORIENTACION_DEG:
        return "horizontal"
    return "inclinada"


def _nombre_modelo(model: Any) -> str:
    try:
        raw = model.GetModelFilename()
        if isinstance(raw, str):
            nombre = raw
        else:
            nombre = next((x for x in _salidas(raw, "GetModelFilename") if isinstance(x, str)), "")
    except Exception:
        return "Modelo SAP2000"
    return nombre.replace("\\", "/").rsplit("/", 1)[-1] or "Modelo SAP2000 sin guardar"


def _leer_frame_interno(model: Any, frame: str, seccion: str) -> dict[str, Any]:
    notas: list[str] = []
    datos: dict[str, Any] = {
        "frame": frame,
        "section": seccion,
        "material": None,
        "e_mpa": None,
        "i33_mm4": None,
        "i22_mm4": None,
        "area_mm2": None,
        "length_mm": None,
        "point_i": None,
        "point_j": None,
        "orientacion": None,
        "releases": {"i_m2": None, "i_m3": None, "j_m2": None, "j_m3": None},
        "modificadores": {"i22": None, "i33": None},
        "note": None,
    }
    if not seccion:
        notas.append("El frame no tiene una sección reconocida.")
    else:
        try:
            datos["area_mm2"], datos["i33_mm4"], datos["i22_mm4"] = _propiedades_seccion(model, seccion)
        except Exception as exc:
            notas.append(f"No se pudieron leer las propiedades de la sección {seccion!r}: {exc}")
        try:
            material = _material(model, seccion)
        except Exception as exc:
            material = None
            notas.append(f"No se pudo leer el material: {exc}")
        datos["material"] = material
        if material is None:
            notas.append("No se pudo leer el material de la sección; introduzca E a mano.")
        else:
            try:
                datos["e_mpa"] = _modulo_e(model, material)
            except Exception as exc:
                notas.append(f"No se pudo leer E del material {material!r}: {exc}")
    try:
        p_i, p_j, c_i, c_j = _puntos_y_coordenadas(model, frame)
        datos["point_i"], datos["point_j"] = p_i, p_j
        datos["orientacion"] = orientacion(c_i, c_j)
        datos["length_mm"] = math.dist(c_i, c_j)
    except Exception as exc:
        notas.append(f"No se pudo determinar la orientación: {exc}")
    if datos["length_mm"] is None:
        # Sin coordenadas válidas se usa la lectura compartida: un fallo de SAP sigue siendo error 409 sap_error.
        datos["length_mm"] = _frame_length_mm(model, frame)
    try:
        datos["releases"] = _releases(model, frame)
    except Exception as exc:
        notas.append(f"No se pudieron leer las liberaciones: {exc}")
    try:
        datos["modificadores"] = _modificadores(model, frame)
    except Exception as exc:
        notas.append(f"No se pudieron leer los modificadores de inercia: {exc}")
    etiquetas = {
        "area_mm2": "el área",
        "i33_mm4": "I33",
        "i22_mm4": "I22",
        "e_mpa": "E",
        "length_mm": "la longitud",
    }
    for clave, etiqueta in etiquetas.items():
        valor = datos[clave]
        if valor is None:
            continue
        limpio = _finito(valor)
        if limpio is None:
            datos[clave] = None
            notas.append(f"SAP2000 devolvió un valor no finito para {etiqueta}; se omite.")
        else:
            datos[clave] = limpio
    for grupo in ("modificadores",):
        for clave, valor in datos[grupo].items():
            if valor is not None and _finito(valor) is None:
                datos[grupo][clave] = None
                notas.append(f"SAP2000 devolvió un valor no finito en {grupo}.{clave}; se omite.")
    datos["note"] = " ".join(notas) if notas else None
    return datos


def _leer_frame_interno_vacio(frame: str, seccion: str) -> dict[str, Any]:
    """Entrada con todas las claves del contrato a ``null`` (frame que no se pudo leer)."""
    return {
        "frame": frame,
        "section": seccion,
        "material": None,
        "e_mpa": None,
        "i33_mm4": None,
        "i22_mm4": None,
        "area_mm2": None,
        "length_mm": None,
        "point_i": None,
        "point_j": None,
        "orientacion": None,
        "releases": {"i_m2": None, "i_m3": None, "j_m2": None, "j_m3": None},
        "modificadores": {"i22": None, "i33": None},
        "note": None,
    }


def _codigo_unidades(raw: Any) -> int:
    """Código ``eUnits`` de ``GetPresentUnits`` (entero, o tupla con el código de retorno como en graph)."""
    if isinstance(raw, (tuple, list)):
        candidatos = [int(v) for v in raw if _es_numero(v) and 1 <= int(v) <= 16]
        if len(candidatos) != 1:
            raise RuntimeError("Respuesta inesperada de GetPresentUnits")
        return candidatos[0]
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Respuesta inesperada de GetPresentUnits") from exc


def _leer_frame(model: Any, frame: str, seccion: str) -> dict[str, Any]:
    """Como ``_leer_frame_interno`` pero un fallo inesperado deja la entrada con ``note``.

    Solo se propagan los errores de SAP al leer la longitud (``RuntimeError`` o similar
    con código de retorno): ``call`` los convierte en 409 legible.
    """
    try:
        return _leer_frame_interno(model, frame, seccion)
    except (ArithmeticError, TypeError, AttributeError, ValueError, IndexError, KeyError) as exc:
        vacio = _leer_frame_interno_vacio(frame, seccion)
        vacio["note"] = f"No se pudo leer el frame {frame!r}: {type(exc).__name__}: {exc}"
        return vacio


def _objetos_seleccionados(model: Any) -> list[tuple[int, str]]:
    """(tipo, nombre) de la selección. Lista vacía si no hay ningún objeto seleccionado.

    Misma llamada que ``bridge.esbelteces._selected_frame_names``: ``GetSelected(0, [], [])`` y,
    si el enlace no acepta esa aridad, la forma sin argumentos. Sin objetos SAP2000 puede
    devolver ``(0, 0, None, None)``: eso es selección vacía, no un error. El orden del código de
    retorno lo resuelve ``split_return``.
    """
    try:
        raw = model.SelectObj.GetSelected(0, [], [])
    except TypeError:
        raw = model.SelectObj.GetSelected()
    codigo, salidas = split_return(raw)
    if codigo != 0:
        raise RuntimeError(f"SAP2000 SelectObj.GetSelected devolvió {codigo}")
    arrays = [list(x) for x in salidas if isinstance(x, (list, tuple))]
    cuenta = [x for x in salidas if isinstance(x, int) and not isinstance(x, bool)]
    if (cuenta and cuenta[0] == 0) or not arrays or all(len(a) == 0 for a in arrays):
        return []
    if len(arrays) < 2:
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    tipos, nombres = arrays[-2], arrays[-1]
    if len(tipos) != len(nombres):
        raise RuntimeError("SAP2000 SelectObj.GetSelected returned mismatched arrays")
    return [(int(tipo), str(nombre)) for tipo, nombre in zip(tipos, nombres)]


def selected_frames_nomogramas(model: Any) -> dict[str, Any]:
    """Frames seleccionados en SAP2000 con lo necesario para el cálculo de K. Solo lee.

    Sin selección, o con selección sin barras, devuelve ``frames: []`` sin lanzar excepción.
    """
    objetos = _objetos_seleccionados(model)
    if any(tipo == TIPO_FRAME for tipo, _ in objetos):
        frames = selected_frames(model)
    else:
        frames = []
        if objetos:
            # El contrato no tiene un campo de nota a nivel de respuesta: queda en el log del puente.
            logger.info("Nomogramas: la selección no contiene barras (frames).")
    previas = _codigo_unidades(model.GetPresentUnits())
    cambiar = previas != N_MM_C
    try:
        if cambiar:
            _ret(model.SetPresentUnits(N_MM_C), "SetPresentUnits(N_mm_C)")
        resultado = [_leer_frame(model, f["frame"], f["section"]) for f in frames]
        nombre = _nombre_modelo(model)
    finally:
        if cambiar:
            _ret(model.SetPresentUnits(previas), "SetPresentUnits(restore)")
    return sanitizar_json({"modelo": nombre, "unidades": "N_mm_C", "frames": resultado})


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala ``/v1/sap/actions/nomogramas/*`` con el envoltorio ``call`` de ``bridge.actions``."""

    @app.get("/v1/sap/actions/nomogramas/selected-frames")
    def nomogramas_selected_frames():
        # Siempre JSON: sanitizar_json ya está aplicado en la función; los errores van por ``call``.
        return call(selected_frames_nomogramas)
