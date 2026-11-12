"""Propiedades de las secciones de barra del modelo abierto, en mm y MPa (solo lectura).

Ruta ``GET /v1/sap/actions/viewer/sections/props``. Para cada sección de ``PropFrame`` entrega
área, inercias y radios de giro (``GetSectProps``), las dimensiones ``h/b/tf/tw`` con la misma
convención del puente de esbelteces (``bridge/esbelteces.py``) y el material (en los ángulos, ``rz`` NO se calcula aquí: ``rzCalculated`` es siempre ``false`` y el servidor lo deriva de ``h/b/tf/tw``); de cada material,
``fy``, ``fu``, ``E`` y su tipo, y en el hormigón además ``fc`` (f'c, con ``PropMaterial.GetOConcrete``).

Todo se convierte desde las unidades activas del modelo con factores fijos (como ya hace el Viewer
con las longitudes): NO se cambian las unidades de SAP2000 ni se escribe nada en el modelo. Una
sección o material que no se puede leer se omite y se explica en ``warnings``.
"""

from __future__ import annotations

import math
from typing import Any

from bridge.actions import _get_name_list, split_return
from bridge.viewer.sap2000.units import extract_result_units

# Mapeo de ``PropFrame.GetTypeOAPI`` (el mismo del Workbench, ver ``bridge/esbelteces.py``).
PROP_TYPE_NAMES = {
    1: "I_H",
    2: "Channel",
    3: "Tee",
    4: "Angle",
    5: "Double_Angle",
    6: "Box_HSS",
    7: "Pipe_HSS_Round",
    8: "Rectangular",
    9: "Circle",
    10: "General",
    11: "Double_Channel",
}

# ``PropMaterial.GetTypeOAPI`` -> eMatType.
MATERIAL_TYPE_NAMES = {
    1: "Steel",
    2: "Concrete",
    3: "NoDesign",
    4: "Aluminum",
    5: "ColdFormed",
    6: "Rebar",
    7: "Tendon",
    8: "Masonry",
}
STEEL = 1
CONCRETE = 2

# Unidad de SAP2000 -> N / mm.
_FUERZA_A_N = {
    "N": 1.0,
    "kN": 1000.0,
    "kgf": 9.80665,
    "tonf": 9806.65,
    "lb": 4.4482216152605,
    "kip": 4448.2216152605,
}
_LONGITUD_A_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}


class SeccionesError(RuntimeError):
    """No se puede leer el modelo (p. ej. unidades activas desconocidas)."""


def _num(valor: Any, factor: float = 1.0) -> float | None:
    """``valor * factor`` como float finito, o ``None``."""
    try:
        x = float(valor) * factor
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _positivo(valor: Any, factor: float) -> float | None:
    x = _num(valor, factor)
    return x if x is not None and x > 0 else None


def _factores(model: Any) -> tuple[float, float]:
    """``(mm por unidad de longitud, N por unidad de fuerza)`` de las unidades activas del modelo."""
    try:
        unidades = extract_result_units(model.GetPresentUnits())
    except Exception as exc:  # noqa: BLE001
        raise SeccionesError(f"No se pudieron leer las unidades activas del modelo: {exc}") from exc
    longitud = _LONGITUD_A_MM.get(unidades.get("length", ""))
    fuerza = _FUERZA_A_N.get(unidades.get("force", ""))
    if longitud is None or fuerza is None:
        raise SeccionesError(
            "No se reconocen las unidades activas del modelo; no se puede convertir a mm y MPa."
        )
    return longitud, fuerza


def _salidas(raw: Any, n_esperadas: int) -> list[Any] | None:
    """Salidas de una llamada COM con código de retorno 0, o ``None``."""
    if not isinstance(raw, (tuple, list)) or len(raw) != n_esperadas + 1:
        return None
    try:
        codigo, salidas = split_return(raw)
    except Exception:  # noqa: BLE001
        return None
    return list(salidas) if codigo == 0 and len(salidas) == n_esperadas else None


def _llamar(funcion: Any, nombre: str, relleno: tuple, n_salidas: int) -> list[Any] | None:
    """Llama al getter con los argumentos por referencia y, si el enlace no los admite, solo con el nombre."""
    for args in ((nombre, *relleno), (nombre,)):
        try:
            salidas = _salidas(funcion(*args), n_salidas)
        except Exception:  # noqa: BLE001
            continue
        if salidas is not None:
            return salidas
    return None


# (getter, argumentos de relleno, número de salidas, campos tras ``FileName`` y ``MatProp``).
# Los mismos argumentos que usa ``bridge/esbelteces.py::extract_frame_data``.
_GETTERS: dict[int, tuple[str, tuple, int, tuple[str, ...]]] = {
    1: ("GetISection", ("", "", 0, 0, 0, 0, 0, 0, 0, "", ""), 11, ("h", "b", "tf", "tw")),
    2: ("GetChannel", ("", "", 0, 0, 0, 0, 0, "", ""), 9, ("h", "b", "tf", "tw")),
    3: ("GetTee", ("", "", 0, 0, 0, 0, 0, "", ""), 9, ("h", "b", "tf", "tw")),
    4: ("GetAngle", ("", "", 0, 0, 0, 0, 0, "", ""), 9, ("h", "b", "tf", "tw")),
    5: ("GetDblAngle", ("", "", 0, 0, 0, 0, 0, 0, "", ""), 10, ("h", "b", "tf", "tw")),
    6: ("GetTube", ("", "", 0, 0, 0, 0, 0, "", ""), 9, ("h", "b", "tf", "tw")),
    7: ("GetPipe", ("", "", 0, 0, 0, "", ""), 7, ("h", "tw")),  # h = diámetro exterior, tw = espesor
    8: ("GetRectangle", ("", "", 0, 0, 0, "", ""), 7, ("h", "b")),
    9: ("GetCircle", ("", "", 0, 0, "", ""), 6, ("h",)),
    # Doble canal: mismas salidas y convención que el ángulo doble (h = t3, b = t2, tf, tw y dis).
    11: ("GetDblChannel", ("", "", 0, 0, 0, 0, 0, 0, "", ""), 10, ("h", "b", "tf", "tw")),
}

# Tipos cuyo getter es opcional: si no existe o falla, la sección se entrega solo con ``GetSectProps``
# (``shapeSource`` = "none") en lugar de omitirse.
_GETTER_OPCIONAL = {11}

# Campos extra que entrega el getter tras ``h, b, tf, tw`` (posición en las salidas, que empiezan por
# ``FileName`` y ``MatProp``): ala inferior de la I asimétrica y separación de los dobles.
_EXTRAS = {
    1: (("t2b", 6), ("tfb", 7)),
    5: (("dis", 6),),
    11: (("dis", 6),),
}


def _dimensiones(
    model: Any, nombre: str, tipo: int, mm: float
) -> tuple[str, dict[str, float | None], dict[str, float], bool]:
    """``(material, {h, b, tf, tw}, extras, del_getter)`` de la sección.

    ``material`` vacío si no se pudo leer; ``extras`` solo lleva ``t2b``, ``tfb`` y ``dis`` cuando el getter los
    da y son positivos; ``del_getter`` es ``True`` si las dimensiones salen del getter tipado de la sección.
    """
    dims: dict[str, float | None] = {"h": None, "b": None, "tf": None, "tw": None}
    extras: dict[str, float] = {}
    definicion = _GETTERS.get(tipo)
    if definicion is not None:
        metodo, relleno, n, campos = definicion
        getter = getattr(model.PropFrame, metodo, None)
        salidas = _llamar(getter, nombre, relleno, n) if getter is not None else None
        if salidas is not None:
            for campo, valor in zip(campos, salidas[2:]):
                dims[campo] = _positivo(valor, mm)
            for campo, posicion in _EXTRAS.get(tipo, ()):
                valor = _positivo(salidas[posicion], mm) if posicion < len(salidas) else None
                if valor is not None:
                    extras[campo] = valor
            return str(salidas[1]).strip(), dims, extras, True
        return "", dims, extras, False
    # General / definida por el usuario: solo la envolvente t3 x t2 de GetGeneral (no es una forma dibujable).
    getter = getattr(model.PropFrame, "GetGeneral", None)
    if getter is not None:
        salidas = _llamar(getter, nombre, ("", "") + (0,) * 15 + ("", ""), 19)
        if salidas is not None:
            dims["h"], dims["b"] = _positivo(salidas[2], mm), _positivo(salidas[3], mm)
            return str(salidas[1]).strip(), dims, extras, False
    return "", dims, extras, False


# Propiedades de ``GetSectProps`` que se entregan solo si se leen y son positivas: (campo, posición, potencia de mm).
# Orden de las salidas: Area, As2, As3, Torsion, I22, I33, S22, S33, Z22, Z33, R22, R33.
_OPCIONALES = (
    ("As2", 1, 2),
    ("As3", 2, 2),
    ("J", 3, 4),
    ("S22", 6, 3),
    ("S33", 7, 3),
    ("Z22", 8, 3),
    ("Z33", 9, 3),
)


def _propiedades(model: Any, nombre: str, mm: float) -> dict[str, float | None] | None:
    """Propiedades de ``GetSectProps`` en mm (``A, I33, I22, r33, r22`` y, si se leen, J, As2, As3, S y Z)."""
    salidas = _llamar(model.PropFrame.GetSectProps, nombre, (0.0,) * 12, 12)
    if salidas is None:
        return None
    area, i22, i33 = _positivo(salidas[0], mm**2), _num(salidas[4], mm**4), _num(salidas[5], mm**4)
    if area is None or i22 is None or i33 is None:
        return None
    props: dict[str, float | None] = {
        "A": area,
        "I33": i33,
        "I22": i22,
        "r33": _num(salidas[11], mm),
        "r22": _num(salidas[10], mm),
    }
    for campo, posicion, potencia in _OPCIONALES:
        valor = _positivo(salidas[posicion], mm**potencia)
        if valor is not None:  # nunca 0 ni null inventado: si no se lee, se omite el campo
            props[campo] = valor
    return props


def _seccion(model: Any, nombre: str, mm: float, avisos: list[str]) -> tuple[dict[str, Any] | None, str]:
    """La sección en el esquema de la respuesta (o ``None`` si se omite) y el nombre de su material."""
    salida = _llamar(model.PropFrame.GetTypeOAPI, nombre, (0,), 1)
    if salida is None:
        avisos.append(f"Sección {nombre}: no se pudo leer su tipo; se omite.")
        return None, ""
    tipo = int(salida[0])
    props = _propiedades(model, nombre, mm)
    if props is None:
        avisos.append(f"Sección {nombre}: no se pudieron leer sus propiedades (GetSectProps); se omite.")
        return None, ""
    material, dims, extras, del_getter = _dimensiones(model, nombre, tipo, mm)
    if not material and tipo in _GETTERS and tipo not in _GETTER_OPCIONAL:
        avisos.append(f"Sección {nombre}: no se pudieron leer sus dimensiones ni su material; se omite.")
        return None, ""

    nombre_tipo = PROP_TYPE_NAMES.get(tipo, f"Type_{tipo}")
    sec: dict[str, Any] = {"propType": tipo, "propTypeName": nombre_tipo, **props}
    # rz de los ángulos lo calcula el servidor a partir de h, b, tf, tw: el puente no calcula.
    sec["rzCalculated"] = False
    sec.update(dims)
    sec["material"] = material or None
    sec.update(extras)
    # "getter": las dimensiones salen del getter tipado; "none": solo hay propiedades (General, Section Designer,
    # no prismática, autoselección…).
    sec["shapeSource"] = "getter" if del_getter else "none"
    return sec, material


def _fc(model: Any, nombre: str, mpa: float, avisos: list[str]) -> float | None:
    """f'c del hormigón en MPa (``GetOConcrete``), o ``None`` con aviso si no se puede leer."""
    getter = getattr(model.PropMaterial, "GetOConcrete", None)
    # fc, IsLightweight, FCsFactor, SSType, SSHysType, StrainAtFc, StrainUltimate, FinalSlope, FrictionAngle, DilatationalAngle.
    hormigon = (
        _llamar(getter, nombre, (0.0, False, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0), 10)
        if getter is not None
        else None
    )
    fc = _positivo(hormigon[0], mpa) if hormigon is not None else None
    if fc is None:
        avisos.append(f"Material {nombre}: no se pudo leer f'c (GetOConcrete); se omite fc.")
    return fc


def _material(
    model: Any, nombre: str, mm: float, fuerza_n: float, avisos: list[str]
) -> dict[str, Any] | None:
    """``{fy, fu, E, type}`` en MPa; ``fy`` y ``fu`` solo para acero; ``fc`` solo para hormigón y si se puede leer. ``None`` si no se puede leer el material."""
    mpa = fuerza_n / (mm**2)  # unidad de esfuerzo del modelo -> MPa
    salida = _llamar(model.PropMaterial.GetTypeOAPI, nombre, (0, 0), 2)
    if salida is None:
        avisos.append(f"Material {nombre}: no se pudo leer su tipo; se omite.")
        return None
    codigo = int(salida[0])
    tipo = MATERIAL_TYPE_NAMES.get(codigo, f"Type_{codigo}")
    fy = fu = None
    if codigo == STEEL:
        acero = _llamar(model.PropMaterial.GetOSteel_1, nombre, (0.0,) * 4 + (0, 0) + (0.0,) * 4, 10)
        if acero is None:
            avisos.append(f"Material {nombre}: no se pudo leer fy y fu (GetOSteel_1).")
        else:
            fy, fu = _positivo(acero[0], mpa), _positivo(acero[1], mpa)
    modulo = _llamar(model.PropMaterial.GetMPIsotropic, nombre, (0.0,) * 4, 4)
    e = _positivo(modulo[0], mpa) if modulo is not None else None
    if e is None:
        avisos.append(f"Material {nombre}: no se pudo leer E (GetMPIsotropic).")
    material: dict[str, Any] = {"fy": fy, "fu": fu, "E": e, "type": tipo}
    if codigo == CONCRETE:
        fc = _fc(model, nombre, mpa, avisos)
        if fc is not None:
            material["fc"] = fc
    return material


def read_section_props(model: Any, names: list[str] | None = None) -> dict[str, Any]:
    """Propiedades de las secciones de barra del modelo, en mm y MPa.

    Con ``names`` solo se leen esas secciones (las que no existen se avisan en ``warnings``); sin él, todas.
    """
    mm, fuerza_n = _factores(model)
    avisos: list[str] = []
    try:
        nombres: list[str] | None = sorted(_get_name_list(model.PropFrame, "PropFrame.GetNameList"))
    except Exception as exc:  # noqa: BLE001
        nombres = None
        if names is None:
            # Sin la lista no se puede recorrer el modelo: respuesta vacía con aviso, no un error de toda la ruta.
            avisos.append(
                f"No se pudo leer la lista de secciones ({exc}); pida las secciones con ?names=A,B."
            )
            return {"units": "mm,MPa", "sections": {}, "materials": {}, "warnings": avisos}
    if names is not None:
        pedidas = list(dict.fromkeys(n for n in names if n))
        if nombres is None:
            # Sin la lista completa cada nombre se lee directamente con PropFrame.GetTypeOAPI.
            avisos.append(
                "No se pudo leer la lista de secciones; se leen directamente las secciones pedidas "
                "(las que no existan se avisarán como ilegibles)."
            )
            nombres = sorted(pedidas)
        else:
            existentes = set(nombres)
            for n in pedidas:
                if n not in existentes:
                    avisos.append(f"Sección {n}: no existe en el modelo; se omite.")
            nombres = sorted(n for n in pedidas if n in existentes)

    secciones: dict[str, Any] = {}
    usados: set[str] = set()
    for nombre in nombres:
        sec, material = _seccion(model, nombre, mm, avisos)
        if sec is None:
            continue
        secciones[nombre] = sec
        if material:
            usados.add(material)

    materiales: dict[str, Any] = {}
    for nombre in sorted(usados):
        mat = _material(model, nombre, mm, fuerza_n, avisos)
        if mat is not None:
            materiales[nombre] = mat
    return {"units": "mm,MPa", "sections": secciones, "materials": materiales, "warnings": avisos}
