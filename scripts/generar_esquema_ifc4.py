"""Genera ``bridge/ifc4_esquema.py`` (datos del esquema IFC4) a partir de ``ifcopenshell``. Solo para desarrollo.

El escritor IFC del puente (``bridge/ifc_step.py``) no depende de ``ifcopenshell``: necesita, para cada entidad que
escribe, el orden de sus atributos, cuáles son derivados, sus tipos y las enumeraciones. Esos datos se extraen UNA vez del
esquema IFC4 (ADD2 TC1) de ``ifcopenshell`` y se guardan en un módulo de datos versionado.

    python scripts/generar_esquema_ifc4.py              # reescribe bridge/ifc4_esquema.py
    python scripts/generar_esquema_ifc4.py --comprobar  # falla si el archivo versionado difiere de lo que saldría ahora

Para escribir una entidad IFC nueva: añadirla a ``ENTIDADES_USADAS`` (o un tipo definido a ``TIPOS_USADOS``), regenerar y
ejecutar las pruebas (``tests/test_ifc_esquema.py`` comprueba que todo literal ``"Ifc…"`` de ``bridge/cad_ifc.py`` está).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "bridge" / "ifc4_esquema.py"
ESQUEMA = "IFC4"

# Entidades instanciables que escribe bridge/cad_ifc.py (los supertipos entran solos por su cadena).
ENTIDADES_USADAS = (
    "IfcArbitraryClosedProfileDef",
    "IfcArbitraryProfileDefWithVoids",
    "IfcAxis2Placement2D",
    "IfcAxis2Placement3D",
    "IfcBuilding",
    "IfcBuildingElementProxy",
    "IfcBuildingStorey",
    "IfcCartesianPoint",
    "IfcCartesianPointList3D",
    "IfcCircle",
    "IfcColumn",
    "IfcDirection",
    "IfcElementAssembly",
    "IfcExtrudedAreaSolid",
    "IfcFooting",
    "IfcGeometricRepresentationContext",
    "IfcGeometricRepresentationSubContext",
    "IfcIndexedPolyCurve",
    "IfcLocalPlacement",
    "IfcMaterial",
    "IfcMechanicalFastener",
    "IfcPlate",
    "IfcPolyline",
    "IfcProductDefinitionShape",
    "IfcProject",
    "IfcPropertySet",
    "IfcPropertySingleValue",
    "IfcReinforcingBar",
    "IfcReinforcingBarType",
    "IfcRelAggregates",
    "IfcRelAssociatesMaterial",
    "IfcRelContainedInSpatialStructure",
    "IfcRelDeclares",
    "IfcRelDefinesByProperties",
    "IfcRelDefinesByType",
    "IfcSIUnit",
    "IfcShapeRepresentation",
    "IfcSite",
    "IfcSweptDiskSolid",
    "IfcUnitAssignment",
)
# Tipos definidos que se escriben envueltos (valores de un SELECT o enteros/listas con tipo).
TIPOS_USADOS = (
    "IfcArcIndex",
    "IfcBoolean",
    "IfcIdentifier",
    "IfcInteger",
    "IfcLabel",
    "IfcLineIndex",
    "IfcMassMeasure",
    "IfcPositiveLengthMeasure",
    "IfcReal",
    "IfcText",
)
AGREGADOS = {0: "ARRAY", 1: "BAG", 2: "LIST", 3: "SET"}


def _tipo(t: Any, tipos: set[str], enums: set[str], selects: set[str]) -> tuple:
    """Expresión de tipo normalizada: ENT, SELECT, ENUM, DEF, SIMPLE o LIST (min, max, elemento)."""
    nombrado = t.as_named_type()
    if nombrado is not None:
        d = nombrado.declared_type()
        if d.as_entity() is not None:
            return ("ENT", d.name())
        if d.as_enumeration_type() is not None:
            enums.add(d.name())
            return ("ENUM", d.name())
        if d.as_select_type() is not None:
            selects.add(d.name())
            return ("SELECT", d.name())
        tipos.add(d.name())
        return ("DEF", d.name())
    agregado = t.as_aggregation_type()
    if agregado is not None:
        maximo = agregado.bound2()
        return (
            AGREGADOS[agregado.type_of_aggregation()],
            agregado.bound1(),
            None if maximo < 0 else maximo,
            _tipo(agregado.type_of_element(), tipos, enums, selects),
        )
    return ("SIMPLE", t.as_simple_type().declared_type().upper())


def _miembros_select(d: Any) -> list[str]:
    """Nombres hoja de un SELECT (los SELECT anidados se aplanan)."""
    salida: list[str] = []
    for m in d.select_list():
        if m.as_select_type() is not None:
            salida.extend(_miembros_select(m.as_select_type()))
        else:
            salida.append(m.name())
    return salida


def construir() -> dict[str, Any]:
    import ifcopenshell.ifcopenshell_wrapper as iw

    esquema = iw.schema_by_name(ESQUEMA)
    tipos: set[str] = set(TIPOS_USADOS)
    enums: set[str] = set()
    selects: set[str] = set()
    entidades: dict[str, tuple] = {}

    def entidad(nombre: str, con_atributos: bool) -> None:
        if nombre in entidades and (not con_atributos or entidades[nombre][2]):
            return
        e = esquema.declaration_by_name(nombre).as_entity()
        sup = e.supertype().name() if e.supertype() is not None else None
        atributos: tuple = ()
        if con_atributos:
            derivados = list(e.derived())
            atributos = tuple(
                (a.name(), _tipo(a.type_of_attribute(), tipos, enums, selects), bool(a.optional()), bool(d))
                for a, d in zip(e.all_attributes(), derivados, strict=True)
            )
        entidades[nombre] = (sup, bool(e.is_abstract()), atributos)
        if sup is not None:
            entidad(sup, False)

    for nombre in ENTIDADES_USADAS:
        entidad(nombre, True)

    # Tipos definidos: se resuelven hasta su tipo base (un tipo puede definirse sobre otro).
    definidos: dict[str, tuple] = {}
    pendientes = sorted(tipos)
    while pendientes:
        nombre = pendientes.pop()
        if nombre in definidos:
            continue
        base = esquema.declaration_by_name(nombre).as_type_declaration().declared_type()
        nuevos: set[str] = set()
        definidos[nombre] = _tipo(base, nuevos, enums, selects)
        pendientes.extend(sorted(nuevos - set(definidos)))
    enumeraciones = {
        n: tuple(esquema.declaration_by_name(n).as_enumeration_type().enumeration_items())
        for n in sorted(enums)
    }
    miembros = {
        n: tuple(sorted(_miembros_select(esquema.declaration_by_name(n).as_select_type())))
        for n in sorted(selects)
    }
    return {
        "ENTIDADES": dict(sorted(entidades.items())),
        "TIPOS_DEFINIDOS": dict(sorted(definidos.items())),
        "ENUMERACIONES": enumeraciones,
        "SELECTS": miembros,
    }


def _formato(valor: Any) -> str:
    return repr(valor)


def generar_texto() -> str:
    import ifcopenshell

    datos = construir()
    partes = [
        '"""Datos del esquema IFC4 que necesita el escritor IFC del puente (``bridge/ifc_step.py``).\n',
        "GENERADO por scripts/generar_esquema_ifc4.py desde el esquema IFC4 (ADD2 TC1) de ifcopenshell "
        f"{ifcopenshell.version}.",
        "NO EDITAR A MANO: añada la entidad a ``ENTIDADES_USADAS`` en el script y regenere.\n",
        "* ``ENTIDADES[nombre] = (supertipo, abstracta, atributos)``; ``atributos`` son TODOS los atributos de la entidad",
        "  en el orden del esquema, como ``(nombre, tipo, opcional, derivado)``. Los supertipos solo llevan su nombre.",
        "* Un ``tipo`` es ``(\"ENT\"|\"SELECT\"|\"ENUM\"|\"DEF\", nombre)``, ``(\"SIMPLE\", \"REAL\"|\"INTEGER\"|\"STRING\"|…)`` o",
        "  ``(\"LIST\"|\"SET\"|\"ARRAY\"|\"BAG\", mínimo, máximo|None, tipo_del_elemento)``.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        'ESQUEMA = "IFC4"',
        "",
    ]
    for nombre in ("ENTIDADES", "TIPOS_DEFINIDOS", "ENUMERACIONES", "SELECTS"):
        partes.append(f"{nombre}: dict[str, tuple] = {{")
        for k, v in datos[nombre].items():
            partes.append(f"    {k!r}: {_formato(v)},")
        partes.append("}")
        partes.append("")
    return "\n".join(partes)


def _formatear(texto: str) -> str:
    """Pasa el texto por ruff format (el formato del repositorio), para que ``--comprobar`` sea estable."""
    ruff = [shutil.which("ruff")] if shutil.which("ruff") else [sys.executable, "-m", "ruff"]
    r = subprocess.run(
        [*ruff, "format", "--stdin-filename", str(DESTINO), "-"],
        input=texto,
        capture_output=True,
        text=True,
        cwd=RAIZ,
        check=True,
    )
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--comprobar", action="store_true", help="falla si bridge/ifc4_esquema.py no está al día")
    args = ap.parse_args()
    try:
        texto = _formatear(generar_texto())
    except ImportError:
        print(
            "Falta ifcopenshell (dependencia de desarrollo: backend/requirements-dev.txt).", file=sys.stderr
        )
        return 2
    if args.comprobar:
        if not DESTINO.exists() or DESTINO.read_text(encoding="utf-8") != texto:
            print(
                f"{DESTINO.relative_to(RAIZ)} no coincide con el esquema: ejecute scripts/generar_esquema_ifc4.py",
                file=sys.stderr,
            )
            return 1
        print("El esquema versionado está al día.")
        return 0
    DESTINO.write_text(texto, encoding="utf-8")
    print(f"Escrito {DESTINO.relative_to(RAIZ)} ({len(texto) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
