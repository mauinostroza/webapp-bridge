"""Lectura de SAP2000 para las rutas de Equilibrio plástico del puente.

Código propio del puente: copia literal de las funciones de lectura COM que
``bridge/equilibrio.py`` necesita (combos, Groups de diseño, miembros de un
Group, geometría de un frame y fuerza axial máxima entre estaciones). Es solo lectura de SAP2000:
el cálculo con estos datos lo hace el Workbench.

No incluye la conexión (``connect``): el ``SapModel`` llega ya conectado a
través del actor STA de la sesión, igual que en ``bridge/hormigon.py``.

Comportamiento idéntico al original: las lecturas no lanzan excepción, devuelven
``(valor, ok, mensaje)`` o listas vacías para que el diálogo muestre el error de
cada fila sin abortar la importación. Las unidades no se cambian aquí: los
valores salen en las unidades con que el modelo esté abierto.
"""

from __future__ import annotations

from typing import Any


def _clean_com_error(exc: Exception) -> str:
    if hasattr(exc, "args") and exc.args:
        args = exc.args[0]
        if isinstance(args, tuple) and len(args) >= 3:
            return f"{args[1]} (hresult={args[0]})"
        if isinstance(args, str):
            return args
    return str(exc)


# ============================================================================
#  Listados (para poblar combos del diálogo)
# ============================================================================


def _parse_name_list_result(ret: Any) -> list[str]:
    """Normaliza salidas de GetNameList/variantes COM a lista de strings.

    Formatos comunes: (names...) directo, (ret, count, names[]),
    (count, names[]), (ret, names[]).
    """
    if isinstance(ret, (list, tuple)) and ret and all(isinstance(x, str) for x in ret):
        return [str(x) for x in ret]

    if not isinstance(ret, tuple):
        return []

    if len(ret) >= 3:
        try:
            ret_code = int(ret[0])
            count = int(ret[1])
            names_raw = ret[2]
            if ret_code == 0 and isinstance(names_raw, (list, tuple)):
                return [str(n) for n in names_raw[:count]]
        except Exception:
            pass

    if len(ret) >= 2:
        names_raw = ret[1]
        if isinstance(names_raw, (list, tuple)):
            try:
                count = int(ret[0])
            except Exception:
                count = len(names_raw)
            return [str(n) for n in names_raw[:count]]

    return []


def _get_name_list(obj) -> list[str]:
    """Obtiene una lista de nombres desde un objeto COM con GetNameList(),
    tolerando firma corta (sin argumentos) o larga (ByRef)."""
    try:
        ret = obj.GetNameList()
        parsed = _parse_name_list_result(ret)
        if parsed:
            return parsed
    except Exception:
        pass

    try:
        ret = obj.GetNameList(0, [])
        parsed = _parse_name_list_result(ret)
        if parsed:
            return parsed
    except Exception:
        pass

    return []


def list_combo_names(sap_model) -> list[str]:
    return _get_name_list(sap_model.RespCombo)


def list_group_names(sap_model) -> list[str]:
    return _get_name_list(sap_model.GroupDef)


def list_group_frame_members(sap_model, group_name: str) -> list[str]:
    """Nombres de los elementos frame (ObjectType==2) asignados a un Group."""
    try:
        ret = sap_model.GroupDef.GetAssignments(group_name)
    except Exception:
        try:
            ret = sap_model.GroupDef.GetAssignments(group_name, 0, [], [])
        except Exception:
            return []
    if not isinstance(ret, tuple):
        return []
    arrays = [list(x) for x in ret if isinstance(x, (list, tuple))]
    if len(arrays) < 2:
        return []
    object_types, object_names = arrays[-2], arrays[-1]
    return [str(name) for otype, name in zip(object_types, object_names) if int(otype) == 2]


# ============================================================================
#  Extracción de fuerzas y geometría
# ============================================================================


def _max_abs_axial(p_values: list[float]) -> float:
    """Máximo |P| entre estaciones/casos — función pura, testeable sin COM."""
    if not p_values:
        return 0.0
    return max(abs(v) for v in p_values)


def _select_combo_for_output(sap_model, combo_name: str) -> bool:
    try:
        sap_model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    except Exception:
        pass
    try:
        ret = sap_model.Results.Setup.SetComboSelectedForOutput(combo_name, True)
    except Exception:
        try:
            ret = sap_model.Results.Setup.SetComboSelectedForOutput(combo_name)
        except Exception:
            return False
    return ret == 0 or ret == (0,)


def _llamar_frame_force(sap_model, frame_name: str):
    """Llama Results.FrameForce tolerando firma corta y larga (ByRef)."""
    try:
        return sap_model.Results.FrameForce(frame_name, 0)
    except Exception:
        return sap_model.Results.FrameForce(
            frame_name, 0, 0, [], [], [], [], [], [], [], [], [], [], [], [], []
        )


def get_frame_axial_force(sap_model, frame_name: str, combo_name: str) -> tuple[float | None, bool, str]:
    """Máxima fuerza axial |P| (tonf) del frame para un combo, entre estaciones.

    Devuelve (valor, ok, mensaje_error) — nunca lanza excepción, para que
    el diálogo pueda mostrar errores por fila sin abortar la importación.
    """
    try:
        if not _select_combo_for_output(sap_model, combo_name):
            return None, False, f"No se pudo activar el combo '{combo_name}'."
        ret = _llamar_frame_force(sap_model, frame_name)
        if not isinstance(ret, tuple):
            return None, False, f"FrameForce devolvió un formato inesperado: {ret!r}."
        # Los arrays de datos (P entre ellos) son los últimos elementos tipo
        # lista/tupla de la respuesta; se identifican por tipo, no por índice
        # fijo, porque su posición varía entre la firma corta y la larga.
        arrays = [list(x) for x in ret if isinstance(x, (list, tuple))]
        if len(arrays) < 13:
            return (
                None,
                False,
                (
                    f"FrameForce devolvió {len(arrays)} arreglos, se esperaban "
                    f"13 (Obj, ObjSta, Elm, ElmSta, LoadCase, StepType, StepNum, "
                    f"P, V2, V3, T, M2, M3; formato: {ret!r})."
                ),
            )
        # Orden fijo de los 13 arreglos de FrameForce: P es el 8vo, es decir el
        # 6to contando desde el final (independiente de si NumberResults/ret_code
        # aparecen como escalares antes o después).
        p_values = arrays[-6]
        if not p_values:
            return None, False, f"Sin resultados para '{frame_name}' / '{combo_name}'."
        return _max_abs_axial([float(v) for v in p_values]), True, ""
    except Exception as exc:  # pragma: no cover - solo con COM real
        return None, False, _clean_com_error(exc)


def get_frame_axial_force_gravity(
    sap_model, frame_name: str, combo_name: str
) -> tuple[float | None, bool, str]:
    """Igual que get_frame_axial_force, para el combo de gravedad.

    Aproximación: se usa la fuerza axial del elemento como carga concentrada
    equivalente si el modelo no trae un patrón de carga distribuido explícito
    por nivel.
    """
    return get_frame_axial_force(sap_model, frame_name, combo_name)


def _call_get_coord_cartesian(sap_model, joint_name: str) -> tuple[tuple[float, float, float] | None, str]:
    """Llama PointObj.GetCoordCartesian tolerando firma directa o ByRef.

    SAP2000 OAPI moderno: GetCoordCartesian(name) -> (ret, x, y, z).
    Fallback con argumentos ByRef para versiones que lo requieren.
    """
    try:
        ret = sap_model.PointObj.GetCoordCartesian(str(joint_name))
        if isinstance(ret, tuple) and len(ret) >= 4:
            ret_code = int(ret[0])
            if ret_code == 0:
                return (float(ret[1]), float(ret[2]), float(ret[3])), ""
    except Exception:
        pass

    try:
        raw = sap_model.PointObj.GetCoordCartesian(str(joint_name), 0.0, 0.0, 0.0, "")
        if isinstance(raw, tuple) and len(raw) >= 4:
            ret_code = raw[-1]
            if ret_code == 0 or ret_code == (0,):
                return (float(raw[0]), float(raw[1]), float(raw[2])), ""
    except Exception as exc:
        return None, _clean_com_error(exc)

    return None, f"GetCoordCartesian falló para '{joint_name}'."


def _call_get_points(sap_model, frame_name: str) -> tuple[tuple[str, str] | None, str]:
    """Llama FrameObj.GetPoints tolerando firma directa o ByRef."""
    try:
        ret = sap_model.FrameObj.GetPoints(str(frame_name))
        if isinstance(ret, tuple) and len(ret) >= 3:
            ret_code = int(ret[0])
            if ret_code == 0:
                return (str(ret[1]), str(ret[2])), ""
    except Exception:
        pass

    try:
        raw = sap_model.FrameObj.GetPoints(str(frame_name), "", "")
        ret_code = raw[-1]
        if ret_code == 0 or ret_code == (0,):
            return (str(raw[0]), str(raw[1])), ""
    except Exception as exc:
        return None, _clean_com_error(exc)

    return None, f"GetPoints falló para '{frame_name}'."


def get_frame_geometry(sap_model, frame_name: str):
    """(H, L) de una diagonal a partir de sus dos joints: H = |Δz|,
    L = distancia horizontal (Δx, Δy) entre ambos — válido cuando el
    frame va de esquina a esquina del vano (caso típico de diagonal en
    X o Chevron)."""
    points, msg = _call_get_points(sap_model, frame_name)
    if points is None:
        return (None, None), False, msg
    p1, p2 = points

    coord1, msg1 = _call_get_coord_cartesian(sap_model, p1)
    coord2, msg2 = _call_get_coord_cartesian(sap_model, p2)
    if coord1 is None or coord2 is None:
        return (None, None), False, msg1 or msg2 or "GetCoordCartesian falló."

    x1, y1, z1 = coord1
    x2, y2, z2 = coord2
    h = abs(z2 - z1)
    l_val = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return (h, l_val), True, ""
