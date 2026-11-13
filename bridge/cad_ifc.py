"""
bridge.cad_ifc — Escritor IFC4 del modelo semántico ``estructura-v1`` (CAD-DRWR).

Recibe el diccionario JSON que produce el servidor (``backend/motor_calculo/cad_drwr/modelo``) y devuelve los
bytes de un IFC4 (vista de referencia, pensado para Revit). Es autónomo: NO importa ``backend.*`` (el puente
se publica sin motores de cálculo; lo vigila tests/test_bridge_sin_motor.py) y ``ifcopenshell`` se importa de
forma diferida, de modo que sin ella el puente arranca igual y solo esta función falla (503 en la ruta).

Estructura del archivo:

    IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey 'Fundaciones'
      IfcElementAssembly (BASE_COLUMNA | PERNO_ANCLAJE | PERFIL_REFORZADO | FUNDACION)  <- origen / angulo
        [IfcElementAssembly ZAPATA por ``meta.zapata`` (solo 'fundacion')]
          IfcFooting / IfcColumn / IfcPlate / ...        <- una entidad por instancia de cada elemento
            (hijos con ``padre`` agregados con IfcRelAggregates)

Un conjunto 'perfil' de una sola pieza no lleva ensamblaje: el IfcColumn cuelga directo de la planta.

Qué IFC sale de cada clase del modelo lo decide la tabla ``CLASES`` (clase → entidad IFC, tipo predefinido,
Pset común, extras). Añadir una clase nueva es añadir una entrada ahí (y, si trae geometría nueva, su
representación en ``_Escritor``). Los GlobalId son deterministas (uuid5 de ``conjunto.id`` + ``elemento.id`` +
índice de instancia): dos exportaciones del mismo modelo dan los mismos identificadores. Unidades: mm.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

FORMATO = "estructura-v1"
STOREY = "Fundaciones"
MAX_INSTANCIAS = 5000
MAX_JSON_BYTES = 2 * 1024 * 1024
COORD_MAX = 1e9
TOL_CONTINUIDAD = 0.05  # mm: holgura entre el fin de un tramo y el inicio del siguiente
TIPOS_BARRA = {"MAIN", "LIGATURE", "ANCHORING"}
ENSAMBLAJE_POR_TIPO = {  # tipo de conjunto → ObjectType del ensamblaje (resto: BASE_COLUMNA)
    "perno_anclaje": "PERNO_ANCLAJE",
    "perfil": "PERFIL",  # solo si el conjunto trae varias piezas; con una sola no hay ensamblaje
    "perfil_refuerzos": "PERFIL_REFORZADO",
    "fundacion": "FUNDACION",
}
SUBENSAMBLAJE_ZAPATA = "ZAPATA"  # ObjectType de la agrupación por zapata física (tipo 'fundacion')
_NS = uuid.UUID("6f0b6c1e-4f43-5d2c-9d0e-3a8b7c1d5e21")  # espacio de nombres de los GlobalId del puente


@dataclass(frozen=True)
class Clase:
    """Cómo se escribe una clase del modelo. Una fila de ``CLASES`` por clase.

    ``predef`` es el tipo predefinido deseado: si el esquema IFC4 no lo tiene en su enumeración se escribe
    ``USERDEFINED`` con ese texto en ``ObjectType`` (así Revit lo ve igualmente). ``extra(e)`` devuelve
    ``(atributos_IFC, [(nombre_pset, propiedades)])`` para lo específico de la clase.
    """

    ifc: str
    geom: str  # "extrusion" | "barrido"
    predef: str | None = None
    pset_comun: str | None = None
    comunes: dict = field(default_factory=dict)  # propiedades extra del Pset común
    pieza: bool = False  # añade Pset_WB_Pieza (marca, peso, sección)
    padre_ok: bool = False  # admite hijos (IfcRelAggregates)
    armadura: bool = False  # sigue el camino de barras (tipos IfcReinforcingBarType)
    extra: Callable[[dict], tuple[dict, list]] | None = None
    pieza_extra: Callable[[dict], dict] | None = None  # propiedades añadidas a Pset_WB_Pieza


def _extra_perno(e: dict) -> tuple[dict, list]:
    longitud = longitud_eje(e["eje"])
    props = {
        "AnchorBoltLength": ("IfcPositiveLengthMeasure", longitud),
        "AnchorBoltDiameter": ("IfcPositiveLengthMeasure", e["d"]),
    }
    return {"NominalDiameter": e["d"], "NominalLength": longitud}, [
        ("Pset_MechanicalFastenerAnchorBolt", props)
    ]


def _extra_perfil(e: dict) -> dict:
    """Propiedades del perfil (``meta``) para Pset_WB_Pieza; el área va en mm² por el nombre (el IFC lleva m²)."""
    m = e["meta"]
    return {
        "TipoPerfil": m.get("tipo_perfil"),
        "AreaMm2": m.get("area_mm2"),
        "PesoLinealKgM": m.get("peso_lineal_kg_m"),
    }


def _extra_fundacion(e: dict) -> tuple[dict, list]:
    """Trazabilidad con SAP2000 de zapatas (área, joints) y pedestales (frame, punto de pie)."""
    m = e["meta"]
    props = {
        "Zapata": m.get("zapata"),
        "Area": m.get("area"),
        "Joints": _lista(m.get("pts_nombres")),
        "Frame": m.get("frame"),
        "PuntoPie": m.get("punto_pie"),
        "Continuacion": m.get("continuacion"),
        "Aproximado": True if m.get("aproximado") else None,
    }
    return {}, [("Pset_WB_Fundacion", props)]


def _lista(x: Any) -> str:
    if isinstance(x, (list, tuple)):
        return ", ".join(str(i) for i in x)
    return "" if x is None else str(x)


_PLACA = {"LoadBearing": True, "IsExternal": False}
CLASES: dict[str, Clase] = {
    "Zapata": Clase(
        "IfcFooting",
        "extrusion",
        "PAD_FOOTING",
        "Pset_FootingCommon",
        {"LoadBearing": True},
        padre_ok=True,
        extra=_extra_fundacion,
    ),
    "Pedestal": Clase(
        "IfcColumn",
        "extrusion",
        "PEDESTAL",
        "Pset_ColumnCommon",
        {"LoadBearing": True, "IsExternal": False},
        padre_ok=True,
        extra=_extra_fundacion,
    ),
    "PlacaBase": Clase(
        "IfcPlate", "extrusion", "BASE_PLATE", "Pset_PlateCommon", _PLACA, pieza=True, padre_ok=True
    ),
    "Rigidizador": Clase("IfcPlate", "extrusion", "STIFFENER_PLATE", "Pset_PlateCommon", _PLACA, pieza=True),
    "Perfil": Clase(
        "IfcColumn",
        "extrusion",
        "COLUMN",
        "Pset_ColumnCommon",
        _PLACA,
        pieza=True,
        padre_ok=True,  # perfil_refuerzos: planchas y rigidizadores cuelgan del perfil
        pieza_extra=_extra_perfil,
    ),
    "Tuerca": Clase("IfcMechanicalFastener", "extrusion", "NUT", pieza=True),
    "Arandela": Clase("IfcMechanicalFastener", "extrusion", "WASHER", pieza=True),
    # Grout: IfcBuildingElementProxy y no IfcCovering (sus tipos son acabados: suelo, falso techo...).
    "Grout": Clase("IfcBuildingElementProxy", "extrusion", "GROUT", pieza=True, padre_ok=True),
    "PernoAnclaje": Clase("IfcMechanicalFastener", "barrido", "ANCHORBOLT", pieza=True, extra=_extra_perno),
    "BarraRefuerzo": Clase("IfcReinforcingBar", "barrido", armadura=True),
}
# Sin soporte todavía (422): Soldadura.


class ErrorModelo(ValueError):
    """El modelo ``estructura-v1`` no es válido (la ruta responde 422)."""


class FaltaIfcopenshell(RuntimeError):
    """``ifcopenshell`` no está instalada (la ruta responde 503)."""


@lru_cache(maxsize=1)
def _importable() -> bool:
    try:
        import ifcopenshell  # noqa: F401

        return True
    except ImportError:
        return False


def disponible() -> bool:
    """True si ``ifcopenshell`` se puede importar (patrón ``_ezdxf_ok`` del servidor).

    El resultado se recuerda: ``cad/status`` lo consulta a menudo y la primera importación es lenta.
    """
    return _importable()


# ------------------------------------------------------------ validación --


def _num(v: Any, donde: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ErrorModelo(f"{donde}: debe ser un número finito")
    if abs(v) > COORD_MAX:
        raise ErrorModelo(f"{donde}: valor fuera de rango")
    return float(v)


def _punto(v: Any, n: int, donde: str) -> tuple[float, ...]:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ErrorModelo(f"{donde}: debe ser una lista de {n} números")
    return tuple(_num(c, donde) for c in v)


def _texto(v: Any, donde: str, vacio: bool = False) -> str:
    if v is None and vacio:
        return ""
    if not isinstance(v, str) or (not vacio and not v.strip()):
        raise ErrorModelo(f"{donde}: debe ser un texto{'' if vacio else ' no vacío'}")
    return v


def _tramo(t: Any, donde: str) -> tuple[str, list[tuple[float, float, float]]]:
    if not isinstance(t, (list, tuple)) or not t or t[0] not in ("L", "A"):
        raise ErrorModelo(f"{donde}: tramo desconocido (se esperaba ['L',p0,p1] o ['A',p0,pm,p1])")
    n = 2 if t[0] == "L" else 3
    if len(t) != n + 1:
        raise ErrorModelo(f"{donde}: el tramo '{t[0]}' necesita {n} puntos")
    pts = [_punto(p, 3, f"{donde}[{i + 1}]") for i, p in enumerate(t[1:])]
    if t[0] == "A" and _circuncentro(*pts) is None:
        raise ErrorModelo(f"{donde}: los tres puntos del arco son colineales")
    if t[0] == "L" and _dist(pts[0], pts[1]) < 1e-9:
        raise ErrorModelo(f"{donde}: tramo recto de longitud nula")
    return t[0], pts


def _dist(a, b) -> float:
    return math.dist(a, b)


def _eje(geom: dict, donde: str) -> list[tuple[str, list]]:
    eje = geom.get("eje")
    if not isinstance(eje, (list, tuple)) or not eje:
        raise ErrorModelo(f"{donde}.eje: debe ser una lista de tramos no vacía")
    tramos = [_tramo(t, f"{donde}.eje[{i}]") for i, t in enumerate(eje)]
    for i in range(1, len(tramos)):
        if _dist(tramos[i - 1][1][-1], tramos[i][1][0]) > TOL_CONTINUIDAD:
            raise ErrorModelo(f"{donde}.eje[{i}]: el tramo no empieza donde acaba el anterior")
    return tramos


def _eje_unit(v, donde: str) -> tuple[float, float, float]:
    n = math.hypot(*v)
    if n < 1e-9:
        raise ErrorModelo(f"{donde}: vector nulo")
    return (v[0] / n, v[1] / n, v[2] / n)


def _cruz(a, b) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dist_segmento(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _dentro(p, pts) -> bool:
    c = False
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            c = not c
    return c


def _validar_extrusion(geom: dict, out: dict, donde: str) -> None:
    cont = geom.get("contorno")
    if not isinstance(cont, (list, tuple)) or len(cont) < 3:
        raise ErrorModelo(f"{donde}.geom.contorno: se necesitan al menos 3 puntos")
    out["contorno"] = [_punto(p, 2, f"{donde}.geom.contorno") for p in cont]
    h = _num(geom.get("h"), f"{donde}.geom.h")
    if h <= 0:
        raise ErrorModelo(f"{donde}.geom.h: debe ser mayor que 0")
    out["h"] = h
    if _area(out["contorno"]) < 1e-9:
        raise ErrorModelo(f"{donde}.geom.contorno: área nula")
    base = geom.get("base")
    if base is None:  # plano XY a z0, extruye en +Z
        out["base"] = {"o": (0.0, 0.0, _num(geom.get("z0", 0.0), f"{donde}.geom.z0")), "x": None, "y": None}
    else:
        if not isinstance(base, dict):
            raise ErrorModelo(f"{donde}.geom.base: debe ser un objeto {{o, x, y}}")
        bx = _eje_unit(_punto(base.get("x"), 3, f"{donde}.geom.base.x"), f"{donde}.geom.base.x")
        by = _eje_unit(_punto(base.get("y"), 3, f"{donde}.geom.base.y"), f"{donde}.geom.base.y")
        if abs(sum(i * j for i, j in zip(bx, by, strict=True))) > 1e-6:
            raise ErrorModelo(f"{donde}.geom.base: los ejes x e y deben ser perpendiculares")
        out["base"] = {"o": _punto(base.get("o", [0, 0, 0]), 3, f"{donde}.geom.base.o"), "x": bx, "y": by}
    huecos = geom.get("agujeros") or []
    if not isinstance(huecos, (list, tuple)):
        raise ErrorModelo(f"{donde}.geom.agujeros: debe ser una lista")
    out["agujeros"] = []
    for k, a in enumerate(huecos):
        if not isinstance(a, dict):
            raise ErrorModelo(f"{donde}.geom.agujeros[{k}]: debe ser un objeto {{c, d}}")
        c = _punto(a.get("c"), 2, f"{donde}.geom.agujeros[{k}].c")
        d = _num(a.get("d"), f"{donde}.geom.agujeros[{k}].d")
        if d <= 0:
            raise ErrorModelo(f"{donde}.geom.agujeros[{k}].d: debe ser mayor que 0")
        pts = out["contorno"]
        ok = (
            _dentro(c, pts)
            and min(_dist_segmento(c, p, q) for p, q in zip(pts, pts[1:] + pts[:1], strict=True))
            >= d / 2 - 1e-6
        )
        if not ok:
            raise ErrorModelo(f"{donde}.geom.agujeros[{k}]: el agujero no queda dentro del contorno")
        out["agujeros"].append((c, d))
    _validar_huecos(geom, out, donde)


def _cruzan(a, b, c, d) -> bool:
    """True si los segmentos ab y cd se cruzan en un punto interior de ambos (no solo se tocan)."""

    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return o(a, b, c) * o(a, b, d) < -1e-12 and o(c, d, a) * o(c, d, b) < -1e-12


def _validar_huecos(geom: dict, out: dict, donde: str) -> None:
    """``huecos``: polígonos [[u,v],…] pasantes (p. ej. la caja HSS); cada uno debe quedar dentro del contorno."""
    crudos = geom.get("huecos") or []
    if not isinstance(crudos, (list, tuple)):
        raise ErrorModelo(f"{donde}.geom.huecos: debe ser una lista de polígonos")
    out["huecos"] = []
    cont = out["contorno"]
    aristas = list(zip(cont, cont[1:] + cont[:1], strict=True))
    for k, h in enumerate(crudos):
        if not isinstance(h, (list, tuple)) or len(h) < 3:
            raise ErrorModelo(f"{donde}.geom.huecos[{k}]: se necesitan al menos 3 puntos")
        pts = [_punto(p, 2, f"{donde}.geom.huecos[{k}]") for p in h]
        if _area(pts) < 1e-9:
            raise ErrorModelo(f"{donde}.geom.huecos[{k}]: área nula")
        borde = zip(pts, pts[1:] + pts[:1], strict=True)
        if not all(_dentro(p, cont) for p in pts) or any(
            _cruzan(a, b, c, d) for a, b in borde for c, d in aristas
        ):
            raise ErrorModelo(f"{donde}.geom.huecos[{k}]: el hueco no queda dentro del contorno")
        out["huecos"].append(pts)


def _meta(meta: dict, donde: str) -> dict:
    """``meta`` del elemento: se conservan los escalares y listas de textos; las medidas se validan como números."""
    out = {k: v for k, v in meta.items() if isinstance(v, (str, bool)) or v is None}
    for k, v in meta.items():
        if isinstance(v, (list, tuple)) and all(isinstance(i, (str, int)) for i in v):
            out[k] = [str(i) for i in v]
    for k in ("area_mm2", "peso_lineal_kg_m"):
        if meta.get(k) is not None:
            out[k] = _num(meta[k], f"{donde}.meta.{k}")
    return out


def validar(modelo: Any) -> dict:
    """Valida el modelo y devuelve una versión normalizada (números y tuplas). Lanza ErrorModelo."""
    if not isinstance(modelo, dict):
        raise ErrorModelo("El modelo debe ser un objeto JSON")
    try:
        tam = len(json.dumps(modelo, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ErrorModelo(f"El modelo no es JSON válido: {exc}") from exc
    if tam > MAX_JSON_BYTES:
        raise ErrorModelo(f"El modelo supera el límite de {MAX_JSON_BYTES // (1024 * 1024)} MB de JSON")
    if modelo.get("formato") != FORMATO:
        raise ErrorModelo(f"Formato no admitido: {modelo.get('formato')!r} (se esperaba '{FORMATO}')")
    if modelo.get("unidades") != "mm":
        raise ErrorModelo(f"Unidades no admitidas: {modelo.get('unidades')!r} (el IFC se escribe en 'mm')")
    cid = _texto(modelo.get("id"), "id")
    origen = _punto(modelo.get("origen", [0, 0, 0]), 3, "origen")
    angulo = _num(modelo.get("angulo", 0.0), "angulo")
    vinculo = modelo.get("vinculo") or {}
    if not isinstance(vinculo, dict):
        raise ErrorModelo("vinculo: debe ser un objeto")
    avisos = modelo.get("avisos") or []
    if not isinstance(avisos, (list, tuple)):
        raise ErrorModelo("avisos: debe ser una lista de textos")
    crudos = modelo.get("elementos")
    if not isinstance(crudos, list) or not crudos:
        raise ErrorModelo("elementos: debe ser una lista no vacía")

    elementos: list[dict] = []
    vistos: set[str] = set()
    instancias_total = 0
    for i, e in enumerate(crudos):
        donde = f"elementos[{i}]"
        if not isinstance(e, dict):
            raise ErrorModelo(f"{donde}: debe ser un objeto")
        eid = _texto(e.get("id"), f"{donde}.id")
        if eid in vistos:
            raise ErrorModelo(f"{donde}.id: identificador duplicado {eid!r}")
        vistos.add(eid)
        donde = f"elemento {eid!r}"
        clase = e.get("clase")
        if clase not in CLASES:
            soportadas = ", ".join(CLASES)
            raise ErrorModelo(f"{donde}: clase no soportada {clase!r} (soportadas: {soportadas})")
        spec = CLASES[clase]
        geom = e.get("geom")
        if not isinstance(geom, dict):
            raise ErrorModelo(f"{donde}.geom: debe ser un objeto")
        mat = e.get("material") or {}
        if not isinstance(mat, dict):
            raise ErrorModelo(f"{donde}.material: debe ser un objeto")
        meta = e.get("meta") or {}
        if not isinstance(meta, dict):
            raise ErrorModelo(f"{donde}.meta: debe ser un objeto")
        peso = e.get("peso")
        out = {
            "id": eid,
            "clase": clase,
            "nombre": _texto(e.get("nombre") or eid, f"{donde}.nombre"),
            "marca": _texto(e.get("marca"), f"{donde}.marca", vacio=True),
            "padre": _texto(e.get("padre"), f"{donde}.padre", vacio=True),
            "peso": None if peso is None else _num(peso, f"{donde}.peso"),
            "seccion": _texto(meta.get("seccion"), f"{donde}.meta.seccion", vacio=True),
            "meta": _meta(meta, donde),
            "material": {
                "nombre": _texto(mat.get("nombre") or "Sin material", f"{donde}.material.nombre"),
                "tipo": _texto(mat.get("tipo"), f"{donde}.material.tipo", vacio=True),
                "fc": None if mat.get("fc") is None else _num(mat["fc"], f"{donde}.material.fc"),
                "fy": None if mat.get("fy") is None else _num(mat["fy"], f"{donde}.material.fy"),
            },
        }
        tg = geom.get("tipo")
        if tg != spec.geom:
            raise ErrorModelo(f"{donde}: geom.tipo {tg!r} no corresponde a {clase} ({spec.geom})")
        inst = geom.get("instancias")
        out["inst_explicitas"] = inst not in (None, [])
        if not out["inst_explicitas"]:
            inst = [[0.0, 0.0, 0.0]]
        if not isinstance(inst, (list, tuple)):
            raise ErrorModelo(f"{donde}.geom.instancias: debe ser una lista")
        instancias_total += len(inst)
        if instancias_total > MAX_INSTANCIAS:
            raise ErrorModelo(f"Demasiadas instancias (máximo {MAX_INSTANCIAS})")
        out["instancias"] = [_punto(p, 3, f"{donde}.geom.instancias") for p in inst]
        if tg == "extrusion":
            _validar_extrusion(geom, out, donde)
        else:
            d = _num(geom.get("d"), f"{donde}.geom.d")
            if d <= 0:
                raise ErrorModelo(f"{donde}.geom.d: debe ser mayor que 0")
            out["d"] = d
            out["eje"] = _eje(geom, f"{donde}.geom")
            if spec.armadura:
                tipo = e.get("tipo") or "NOTDEFINED"
                if tipo != "NOTDEFINED" and tipo not in TIPOS_BARRA:
                    raise ErrorModelo(f"{donde}.tipo: {tipo!r} no admitido (MAIN, LIGATURE o ANCHORING)")
                out["tipo"] = tipo
        elementos.append(out)

    por_id = {e["id"]: e for e in elementos}
    for e in elementos:
        if not e["padre"]:
            continue
        padre = por_id.get(e["padre"])
        if padre is None or not CLASES[padre["clase"]].padre_ok:
            admiten = ", ".join(c for c, s in CLASES.items() if s.padre_ok)
            raise ErrorModelo(
                f"elemento {e['id']!r}: padre {e['padre']!r} no es un elemento que admita hijos ({admiten})"
            )
        if len(padre["instancias"]) > 1:
            raise ErrorModelo(f"elemento {e['id']!r}: el padre {e['padre']!r} tiene varias instancias")
        cadena, actual = {e["id"]}, padre
        while actual is not None:
            if actual["id"] in cadena:
                raise ErrorModelo(f"elemento {e['id']!r}: ciclo en la cadena de padres")
            cadena.add(actual["id"])
            actual = por_id.get(actual["padre"])
    return {
        "id": cid,
        "nombre": _texto(modelo.get("nombre") or cid, "nombre"),
        "tipo": _texto(modelo.get("tipo"), "tipo", vacio=True),
        "modulo": _texto(modelo.get("modulo"), "modulo", vacio=True),
        "marca": _texto(modelo.get("marca") or vinculo.get("marca"), "marca", vacio=True),
        "origen": origen,
        "angulo": angulo,
        "vinculo": vinculo,
        "avisos": [str(a) for a in avisos],
        "elementos": elementos,
    }


# ------------------------------------------------------------ geometría --


def _area(pts) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))) / 2


def _circuncentro(a, b, c):
    """Centro y radio de la circunferencia por tres puntos 3D, o None si son colineales."""
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    n = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    n2 = sum(x * x for x in n)
    if n2 < 1e-12:
        return None
    # El centro es a + α·ab + β·ac con (centro - a)·ab = |ab|²/2 y (centro - a)·ac = |ac|²/2.
    ab2 = sum(x * x for x in ab)
    ac2 = sum(x * x for x in ac)
    abac = sum(x * y for x, y in zip(ab, ac, strict=True))
    det = ab2 * ac2 - abac * abac
    alfa = (ab2 / 2 * ac2 - ac2 / 2 * abac) / det
    beta = (ab2 * ac2 / 2 - abac * ab2 / 2) / det
    c0 = tuple(a[i] + alfa * ab[i] + beta * ac[i] for i in range(3))
    return c0, math.dist(c0, a)


def _ang(c, p, q) -> float:
    u = [p[i] - c[i] for i in range(3)]
    v = [q[i] - c[i] for i in range(3)]
    nu, nv = math.hypot(*u), math.hypot(*v)
    cos = max(-1.0, min(1.0, sum(x * y for x, y in zip(u, v, strict=True)) / (nu * nv)))
    return math.acos(cos)


def longitud_eje(tramos) -> float:
    """Desarrollo del eje (mm): rectas por distancia y arcos por tres puntos."""
    total = 0.0
    for tipo, pts in tramos:
        if tipo == "L":
            total += _dist(pts[0], pts[1])
        else:
            c, r = _circuncentro(*pts)
            total += r * (_ang(c, pts[0], pts[1]) + _ang(c, pts[1], pts[2]))
    return total


def _gid(*partes: object) -> str:
    """GlobalId determinista (22 caracteres) a partir de las partes que identifican la entidad."""
    import ifcopenshell.guid

    return ifcopenshell.guid.compress(uuid.uuid5(_NS, "/".join(str(p) for p in partes)).hex)


# --------------------------------------------------------------- escritor --


class _Escritor:
    def __init__(self, m: dict):
        import ifcopenshell

        self.m = m
        self.cid = m["id"]
        self.f = ifcopenshell.file(schema="IFC4")
        self.rels: dict[str, list] = defaultdict(list)
        self.objs_de: dict[str, list] = {}

    # utilidades -----------------------------------------------------------
    def ent(self, tipo: str, *args, **kw):
        return self.f.create_entity(tipo, *args, **kw)

    def pt(self, *c):
        return self.ent("IfcCartesianPoint", Coordinates=[float(x) for x in c])

    def dir(self, *c):
        return self.ent("IfcDirection", DirectionRatios=[float(x) for x in c])

    def eje2(self, loc=(0.0, 0.0, 0.0), z=None, x=None):
        return self.ent(
            "IfcAxis2Placement3D",
            Location=self.pt(*loc),
            Axis=self.dir(*z) if z else None,
            RefDirection=self.dir(*x) if x else None,
        )

    def placement(self, rel_a=None, **kw):
        return self.ent("IfcLocalPlacement", PlacementRelTo=rel_a, RelativePlacement=self.eje2(**kw))

    def raiz(self, tipo: str, clave: tuple, nombre: str, **kw):
        return self.ent(tipo, GlobalId=_gid(self.cid, *clave), Name=nombre, **kw)

    def relacion(self, tipo: str, clave: tuple, **kw):
        return self.ent(tipo, GlobalId=_gid(self.cid, "rel", *clave), **kw)

    def pset(self, obj, nombre: str, props: dict[str, Any]):
        """Pset con GlobalId determinista; ``props`` valores: str/bool/float/int (IFC según tipo)."""
        lista = []
        for k, v in props.items():
            if v is None or v == "":
                continue
            if isinstance(v, tuple):  # (tipo IFC, valor) para medidas con tipo propio
                val = self.ent(v[0], v[1])
            elif isinstance(v, bool):
                val = self.ent("IfcBoolean", v)
            elif isinstance(v, int):
                val = self.ent("IfcInteger", v)
            elif isinstance(v, float):
                val = self.ent("IfcReal", v)
            elif k in ("Reference", "Marca", "ConjuntoId"):
                val = self.ent("IfcIdentifier", str(v))
            elif k in ("Frames", "Joints", "Areas", "Huella") or k.startswith("Aviso"):
                val = self.ent("IfcText", str(v))
            else:
                val = self.ent("IfcLabel", str(v))
            lista.append(self.ent("IfcPropertySingleValue", Name=k, NominalValue=val))
        if not lista:
            return
        ps = self.ent(
            "IfcPropertySet",
            GlobalId=_gid(self.cid, "pset", obj.GlobalId, nombre),
            Name=nombre,
            HasProperties=lista,
        )
        if obj.is_a("IfcTypeObject"):  # los tipos llevan sus Psets en HasPropertySets, no en una relación
            obj.HasPropertySets = [*(obj.HasPropertySets or ()), ps]
            return
        self.ent(
            "IfcRelDefinesByProperties",
            GlobalId=_gid(self.cid, "reldp", obj.GlobalId, nombre),
            RelatedObjects=[obj],
            RelatingPropertyDefinition=ps,
        )

    # estructura ------------------------------------------------------------
    def cabecera(self):
        h = self.f.header
        try:
            h.file_description.description = ("ViewDefinition [ReferenceView_V1.2]",)
            h.file_name.name = f"{self.cid}.ifc"
            h.file_name.time_stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            h.file_name.originating_system = "Workbench puente local (bridge.cad_ifc)"
        except Exception:  # la cabecera es informativa: nunca debe impedir exportar
            pass

    def proyecto(self):
        mm = self.ent("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
        unidades = [
            mm,
            self.ent("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
            self.ent("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
            self.ent("IfcSIUnit", UnitType="MASSUNIT", Prefix="KILO", Name="GRAM"),
            self.ent("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN"),
        ]
        self.ctx = self.ent(
            "IfcGeometricRepresentationContext",
            ContextIdentifier="Model",
            ContextType="Model",
            CoordinateSpaceDimension=3,
            Precision=1e-5,
            WorldCoordinateSystem=self.eje2(),
            TrueNorth=self.dir(0, 1),
        )
        self.body = self.ent(
            "IfcGeometricRepresentationSubContext",
            ContextIdentifier="Body",
            ContextType="Model",
            ParentContext=self.ctx,
            TargetView="MODEL_VIEW",
        )
        self.proj = self.raiz(
            "IfcProject",
            ("project",),
            self.m["nombre"],
            RepresentationContexts=[self.ctx],
            UnitsInContext=self.ent("IfcUnitAssignment", Units=unidades),
        )
        self.site = self.raiz(
            "IfcSite",
            ("site",),
            "Sitio",
            CompositionType="ELEMENT",
            ObjectPlacement=self.placement(),
        )
        self.bld = self.raiz(
            "IfcBuilding",
            ("building",),
            "Edificio",
            CompositionType="ELEMENT",
            ObjectPlacement=self.placement(self.site.ObjectPlacement),
        )
        self.sto = self.raiz(
            "IfcBuildingStorey",
            ("storey",),
            STOREY,
            CompositionType="ELEMENT",
            ObjectPlacement=self.placement(self.bld.ObjectPlacement),
            Elevation=0.0,
        )
        for padre, hijo in ((self.proj, self.site), (self.site, self.bld), (self.bld, self.sto)):
            self.relacion(
                "IfcRelAggregates",
                ("agg", hijo.GlobalId),
                RelatingObject=padre,
                RelatedObjects=[hijo],
            )

    def materiales(self):
        self.mats: dict[str, Any] = {}
        self.por_material: dict[str, list] = defaultdict(list)
        for e in self.m["elementos"]:
            mt = e["material"]
            if mt["nombre"] in self.mats:
                continue
            categoria = {"hormigon": "Concrete", "acero_refuerzo": "Steel", "acero": "Steel"}.get(mt["tipo"])
            self.mats[mt["nombre"]] = self.ent("IfcMaterial", Name=mt["nombre"], Category=categoria)

    def asociar_material(self, obj, e):
        self.por_material[e["material"]["nombre"]].append(obj)

    # representaciones ------------------------------------------------------
    def rep_extrusion(self, e, desp=(0.0, 0.0, 0.0)):
        """Extrusión del contorno (con huecos poligonales y circulares) sobre ``base``; ``desp`` = desplazamiento de instancia."""
        pts = [*e["contorno"], e["contorno"][0]]
        curva = self.ent(
            "IfcPolyline", Points=[self.ent("IfcCartesianPoint", Coordinates=[p[0], p[1]]) for p in pts]
        )
        if e["agujeros"] or e["huecos"]:
            huecos = [
                self.ent(
                    "IfcPolyline",
                    Points=[self.ent("IfcCartesianPoint", Coordinates=list(p)) for p in [*h, h[0]]],
                )
                for h in e["huecos"]
            ]
            huecos += [
                self.ent(
                    "IfcCircle",
                    Position=self.ent(
                        "IfcAxis2Placement2D", Location=self.ent("IfcCartesianPoint", Coordinates=list(c))
                    ),
                    Radius=d / 2.0,
                )
                for c, d in e["agujeros"]
            ]
            perfil = self.ent(
                "IfcArbitraryProfileDefWithVoids", ProfileType="AREA", OuterCurve=curva, InnerCurves=huecos
            )
        else:
            perfil = self.ent("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=curva)
        base = e["base"]
        origen = tuple(o + d for o, d in zip(base["o"], desp, strict=True))
        if base["x"] is None:
            pos = self.eje2(origen)
        else:  # ejes locales u, v = x, y; la extrusión (+Z local) va en x × y
            pos = self.eje2(origen, z=_cruz(base["x"], base["y"]), x=base["x"])
        solido = self.ent(
            "IfcExtrudedAreaSolid",
            SweptArea=perfil,
            Position=pos,
            ExtrudedDirection=self.dir(0, 0, 1),
            Depth=e["h"],
        )
        return self.forma("SweptSolid", solido)

    def forma(self, tipo_rep: str, solido):
        rep = self.ent(
            "IfcShapeRepresentation",
            ContextOfItems=self.body,
            RepresentationIdentifier="Body",
            RepresentationType=tipo_rep,
            Items=[solido],
        )
        return self.ent("IfcProductDefinitionShape", Representations=[rep])

    def rep_barra(self, e, desp):
        # Puntos del eje desplazados; el extremo final de un tramo es el inicial del siguiente.
        puntos: list[tuple[float, float, float]] = []
        segmentos = []

        previo = None
        for tipo, pts in e["eje"]:
            idx = []
            for k, p in enumerate(pts):
                if k == 0 and previo is not None:
                    idx.append(previo)
                else:
                    q = (p[0] + desp[0], p[1] + desp[1], p[2] + desp[2])
                    puntos.append(q)
                    idx.append(len(puntos))
            previo = idx[-1]
            if tipo == "L":
                segmentos.append(self.ent("IfcLineIndex", idx))
            else:
                segmentos.append(self.ent("IfcArcIndex", idx))
        lista = self.ent("IfcCartesianPointList3D", CoordList=[list(p) for p in puntos])
        curva = self.ent("IfcIndexedPolyCurve", Points=lista, Segments=segmentos, SelfIntersect=False)
        solido = self.ent("IfcSweptDiskSolid", Directrix=curva, Radius=e["d"] / 2.0)
        return self.forma("AdvancedSweptSolid", solido)

    # elementos -------------------------------------------------------------
    def construir(self):
        self.cabecera()
        self.proyecto()
        self.materiales()
        m = self.m
        ang = math.radians(m["angulo"])
        colocacion = self.placement(
            self.sto.ObjectPlacement,
            loc=m["origen"],
            z=(0, 0, 1),
            x=(math.cos(ang), math.sin(ang), 0.0),
        )
        # Un 'perfil' de una sola pieza no necesita ensamblaje: el IfcColumn lleva el origen y los Psets.
        self.asm = None
        una_pieza = len(m["elementos"]) == 1 and not CLASES[m["elementos"][0]["clase"]].armadura
        if not (m["tipo"] == "perfil" and una_pieza):
            self.asm = self.raiz(
                "IfcElementAssembly",
                ("assembly",),
                m["nombre"],
                ObjectType=ENSAMBLAJE_POR_TIPO.get(m["tipo"], "BASE_COLUMNA"),
                ObjectPlacement=colocacion,
                AssemblyPlace="SITE",
                PredefinedType="USERDEFINED",
            )
        contenidos = [self.asm] if self.asm else []
        pl_local = colocacion
        piezas: dict[str, Any] = {}  # id de elemento -> entidad (instancia 0, la que agrega a los hijos)
        for e in m["elementos"]:
            if not CLASES[e["clase"]].armadura:
                objs = self.pieza(e, pl_local)
                piezas[e["id"]] = objs[0]
                self.objs_de[e["id"]] = objs
                contenidos.extend(objs)
        self.raiz_conjunto = self.asm or next(iter(piezas.values()))
        self.psets_conjunto(self.raiz_conjunto)
        contenidos.extend(self.agregar(piezas, pl_local))
        self.barras(piezas, contenidos, pl_local)
        for mt, objs in self.por_material.items():
            self.relacion(
                "IfcRelAssociatesMaterial",
                ("mat", mt),
                RelatedObjects=objs,
                RelatingMaterial=self.mats[mt],
            )
        self.relacion(
            "IfcRelContainedInSpatialStructure",
            ("contain",),
            RelatedElements=contenidos,
            RelatingStructure=self.sto,
        )

    def psets_conjunto(self, obj):
        """Pset_WB_Vinculo (frames/áreas/joints de SAP2000) y los avisos del conjunto, sobre su raíz."""
        m = self.m
        self.pset(
            obj,
            "Pset_WB_Vinculo",
            {
                "ConjuntoId": m["id"],
                "Marca": m["marca"],
                "Tipo": m["tipo"],
                "Modulo": m["modulo"],
                **self._vinculo(),
            },
        )
        if m["avisos"]:
            self.pset(
                obj,
                "Pset_WB_Avisos",
                {"NumAvisos": len(m["avisos"]), **{f"Aviso{i + 1}": a for i, a in enumerate(m["avisos"])}},
            )

    def pieza(self, e, pl_local) -> list:
        """Una entidad IFC por instancia del elemento (según ``CLASES``); devuelve la lista."""
        spec = CLASES[e["clase"]]
        extra_attrs, extra_psets = spec.extra(e) if spec.extra else ({}, [])
        attrs: dict[str, Any] = {}
        if spec.predef:
            if self._admite(spec.ifc, spec.predef):
                attrs["PredefinedType"] = spec.predef
            else:  # IFC4 no tiene el tipo (BASE_PLATE, NUT...): USERDEFINED y el nombre en ObjectType
                attrs.update(PredefinedType="USERDEFINED", ObjectType=spec.predef)
        if e["marca"]:
            attrs["Tag"] = e["marca"]
        n = len(e["instancias"])
        objs = []
        for i, desp in enumerate(e["instancias"]):
            clave = ("el", e["id"], i) if e["inst_explicitas"] else ("el", e["id"])
            rep = self.rep_extrusion(e, desp) if spec.geom == "extrusion" else self.rep_barra(e, desp)
            obj = self.raiz(
                spec.ifc,
                clave,
                e["nombre"] if n == 1 else f"{e['nombre']} #{i + 1}",
                ObjectPlacement=self.placement(pl_local),
                Representation=rep,
                Description=e["id"],
                **attrs,
                **extra_attrs,
            )
            if spec.pset_comun:
                self.pset(
                    obj,
                    spec.pset_comun,
                    {"Reference": e["marca"] or e["id"], "Status": "NEW", **spec.comunes},
                )
            for nombre, props in extra_psets:
                self.pset(obj, nombre, props)
            if spec.pieza:
                self.pset(
                    obj,
                    "Pset_WB_Pieza",
                    {
                        "Marca": e["marca"],
                        "Clase": e["clase"],
                        "Seccion": e["seccion"],
                        "PesoKg": None if not e["peso"] else ("IfcMassMeasure", e["peso"] / n),
                        **(spec.pieza_extra(e) if spec.pieza_extra else {}),
                    },
                )
            self.asociar_material(obj, e)
            objs.append(obj)
        return objs

    _enum_cache: dict[tuple[str, str], bool] = {}

    def _admite(self, ifc: str, predef: str) -> bool:
        """True si el tipo predefinido existe en la enumeración IFC4 de la entidad."""
        if (ifc, predef) not in self._enum_cache:
            import ifcopenshell.ifcopenshell_wrapper as iw

            ent = iw.schema_by_name("IFC4").declaration_by_name(ifc)
            tipo = next(a for a in ent.all_attributes() if a.name() == "PredefinedType").type_of_attribute()
            items = tipo.declared_type().enumeration_items()
            self._enum_cache[(ifc, predef)] = predef in items
        return self._enum_cache[(ifc, predef)]

    def agregar(self, piezas: dict[str, Any], pl_local) -> list:
        """IfcRelAggregates de las piezas: a su ``padre`` o, si no lo tienen, al ensamblaje del conjunto.

        En 'fundacion' las piezas sin padre se agrupan por ``meta.zapata`` en un IfcElementAssembly por zapata
        física (agrega sus áreas y pedestales), que cuelga del ensamblaje del conjunto. Devuelve los
        sub-ensamblajes creados (para contenerlos en la planta).
        """
        raices, hijas = [], defaultdict(list)
        zapatas: dict[str, list[dict]] = {}
        for e in self.m["elementos"]:
            if CLASES[e["clase"]].armadura:
                continue
            if e["padre"]:
                hijas[e["padre"]].extend(self.objs_de[e["id"]])
            elif self.m["tipo"] == "fundacion" and e["meta"].get("zapata"):
                zapatas.setdefault(e["meta"]["zapata"], []).append(e)
            else:
                raices.extend(self.objs_de[e["id"]])
        subs = []
        for zap, els in zapatas.items():
            sub = self.raiz(
                "IfcElementAssembly",
                ("sub", zap),
                f"Zapata {zap}",
                ObjectType=SUBENSAMBLAJE_ZAPATA,
                ObjectPlacement=self.placement(pl_local),
                AssemblyPlace="SITE",
                PredefinedType="USERDEFINED",
            )
            self.pset(sub, "Pset_WB_Vinculo", self._vinculo_zapata(zap, els))
            self.relacion(
                "IfcRelAggregates",
                ("agg-zapata", zap),
                RelatingObject=sub,
                RelatedObjects=[o for e in els for o in self.objs_de[e["id"]]],
            )
            subs.append(sub)
        raices.extend(subs)
        if raices and self.asm is not None:
            self.relacion("IfcRelAggregates", ("agg-asm",), RelatingObject=self.asm, RelatedObjects=raices)
        for padre, objs in hijas.items():
            self.relacion(
                "IfcRelAggregates", ("agg-hijas", padre), RelatingObject=piezas[padre], RelatedObjects=objs
            )
        return subs

    def _vinculo_zapata(self, zap: str, els: list[dict]) -> dict:
        def unicos(clave):
            vistos: dict[str, None] = {}
            for e in els:
                v = e["meta"].get(clave)
                for x in v if isinstance(v, list) else ([v] if v else []):
                    vistos[str(x)] = None
            return ", ".join(vistos)

        return {
            "ConjuntoId": self.cid,
            "Marca": zap,
            "Tipo": "zapata",
            "Areas": unicos("area"),
            "Frames": unicos("frame"),
            "Joints": unicos("pts_nombres"),
        }

    def _vinculo(self) -> dict[str, str]:
        v = self.m["vinculo"]

        def lista(x):
            if isinstance(x, (list, tuple)):
                return ", ".join(str(i) for i in x)
            return "" if x is None else str(x)

        out = {
            "Frames": lista(v.get("frames")),
            "Joints": lista(v.get("joints")),
            "Areas": lista(v.get("areas")),
        }
        if v.get("huella") not in (None, "", [], {}):
            out["Huella"] = json.dumps(v["huella"], ensure_ascii=False, separators=(",", ":"))
        return out

    def barras(self, piezas, contenidos, pl_local):
        tipos: dict[tuple, Any] = {}
        nombres_usados: set[str] = set()
        hijas: dict[str, list] = defaultdict(list)
        sin_padre: list = []
        por_tipo: dict[tuple, list] = defaultdict(list)
        for e in self.m["elementos"]:
            if e["clase"] != "BarraRefuerzo":
                continue
            longitud = longitud_eje(e["eje"])
            clave = (e["marca"] or e["id"], e["d"], e["tipo"])
            area = math.pi * e["d"] ** 2 / 4
            if clave not in tipos:
                nombre = clave[0]
                if nombre in nombres_usados:
                    nombre = f"{clave[0]}_{e['d']:g}_{e['tipo']}"
                nombres_usados.add(nombre)
                bt = self.ent(
                    "IfcReinforcingBarType",
                    GlobalId=_gid(self.cid, "tipo", *clave),
                    Name=nombre,
                    PredefinedType=e["tipo"],
                    NominalDiameter=e["d"],
                    CrossSectionArea=area,
                    BarLength=longitud,
                )
                self.pset(bt, "Pset_ReinforcingBarCommon", {"Reference": nombre, "Status": "NEW"})
                tipos[clave] = bt
            acero = e["material"]
            n = len(e["instancias"])
            for i, desp in enumerate(e["instancias"]):
                obj = self.ent(
                    "IfcReinforcingBar",
                    GlobalId=_gid(self.cid, "el", e["id"], i),
                    Name=e["nombre"] if n == 1 else f"{e['nombre']} #{i + 1}",
                    Description=e["id"],
                    ObjectType=e["marca"] or None,
                    ObjectPlacement=self.placement(pl_local),
                    Representation=self.rep_barra(e, desp),
                    Tag=e["marca"] or e["id"],
                    SteelGrade=acero["nombre"],
                    NominalDiameter=e["d"],
                    CrossSectionArea=area,
                    BarLength=longitud,
                    PredefinedType=e["tipo"],
                )
                self.pset(
                    obj,
                    "Pset_ReinforcingBarCommon",
                    {"Reference": e["marca"] or e["id"], "Status": "NEW"},
                )
                self.asociar_material(obj, e)
                por_tipo[clave].append(obj)
                contenidos.append(obj)
                (hijas[e["padre"]] if e["padre"] else sin_padre).append(obj)
        for clave, objs in por_tipo.items():
            self.relacion(
                "IfcRelDefinesByType",
                ("tipo", *clave),
                RelatedObjects=objs,
                RelatingType=tipos[clave],
            )
        if tipos:
            self.relacion(
                "IfcRelDeclares",
                ("declares",),
                RelatingContext=self.proj,
                RelatedDefinitions=list(tipos.values()),
            )
        for padre, objs in hijas.items():
            self.relacion(
                "IfcRelAggregates",
                ("agg-armadura", padre),
                RelatingObject=piezas[padre],
                RelatedObjects=objs,
            )
        if sin_padre:
            self.relacion(
                "IfcRelAggregates",
                ("agg-armadura-libre",),
                RelatingObject=self.raiz_conjunto,
                RelatedObjects=sin_padre,
            )

    def bytes_ifc(self) -> bytes:
        return self.f.to_string().encode("utf-8")


# ------------------------------------------------------------------ API --


def generar_ifc(modelo: Any) -> tuple[str, bytes]:
    """Valida ``modelo`` (estructura-v1) y devuelve ``(nombre_de_archivo, bytes_IFC)``.

    Lanza ``ErrorModelo`` (modelo inválido) o ``FaltaIfcopenshell`` (la librería no está instalada).
    """
    if not disponible():
        raise FaltaIfcopenshell(
            "Falta ifcopenshell en el puente: no se puede generar IFC (ver docs/CAD_IFC.md)."
        )
    m = validar(modelo)
    w = _Escritor(m)
    w.construir()
    seguro = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in m["id"]) or "modelo"
    return f"{seguro}.ifc", w.bytes_ifc()
