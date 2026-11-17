"""Serializador STEP propio (bridge/ifc_step.py): formato, cadenas, reales, GlobalId y validación. Sin ifcopenshell."""

from __future__ import annotations

import math
import random
import re
import struct
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import ifc_step  # noqa: E402
from bridge.ifc_step import ArchivoStep, ErrorEscritor, Valor, formato_cadena, formato_real  # noqa: E402

# ---------------------------------------------------------------- reales --

_REAL_STEP = re.compile(r"^-?\d+\.\d*(E[+-]?\d+)?$")


@pytest.mark.parametrize(
    ("x", "esperado"),
    [
        (0.0, "0."),
        (-0.0, "0."),
        (1.0, "1."),
        (1000.0, "1000."),
        (0.5, "0.5"),
        (-2.25, "-2.25"),
        (1e-5, "1.E-05"),
        (1e16, "1.E+16"),
        (1.5e-7, "1.5E-07"),
        (123456.789, "123456.789"),
        (5e-324, "5.E-324"),
    ],
)
def test_formato_real(x, esperado):
    assert formato_real(x) == esperado


@pytest.mark.parametrize("malo", [math.nan, math.inf, -math.inf])
def test_real_no_finito_es_error(malo):
    with pytest.raises(ErrorEscritor):
        formato_real(malo)


def test_reales_aleatorios_vuelven_exactos():
    rng = random.Random(1234)
    vistos = 0
    while vistos < 500:
        x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if not math.isfinite(x):
            continue
        texto = formato_real(x)
        assert _REAL_STEP.match(texto), texto
        assert float(texto) == x or (x == 0 and float(texto) == 0)
        vistos += 1


def test_int_en_atributo_real_se_escribe_como_real_y_bool_no_es_numero():
    f = ArchivoStep()
    f.crear_entidad("IfcCartesianPoint", Coordinates=[1, 2, 3])
    assert "IFCCARTESIANPOINT((1.,2.,3.));" in f.a_texto()
    with pytest.raises(ErrorEscritor):
        f.crear_entidad("IfcCartesianPoint", Coordinates=[True, 0, 0])


# --------------------------------------------------------------- cadenas --


def _decodificar(literal: str) -> str:
    """Decodificador mínimo de cadenas STEP (solo para las pruebas)."""
    assert literal[0] == "'" and literal[-1] == "'"
    cuerpo, i, salida = literal[1:-1], 0, []
    while i < len(cuerpo):
        if cuerpo.startswith("''", i):
            salida.append("'")
            i += 2
        elif cuerpo.startswith("\\\\", i):
            salida.append("\\")
            i += 2
        elif cuerpo.startswith("\\X2\\", i) or cuerpo.startswith("\\X4\\", i):
            ancho = 4 if cuerpo[i + 2] == "2" else 8
            fin = cuerpo.index("\\X0\\", i)
            datos = cuerpo[i + 4 : fin]
            assert len(datos) % ancho == 0
            salida.extend(chr(int(datos[k : k + ancho], 16)) for k in range(0, len(datos), ancho))
            i = fin + 4
        else:
            assert 0x20 <= ord(cuerpo[i]) <= 0x7E and cuerpo[i] not in "'\\"
            salida.append(cuerpo[i])
            i += 1
    return "".join(salida)


CADENAS = [
    "",
    "Pilar",
    "L'angulo",
    "A\\B",
    "Ø25",
    "Perno Ø1\\",
    "ó·é",
    "linea\nnueva",
    "tab\tx",
    "😀 emoji",
    "a" * 10000,
]


@pytest.mark.parametrize("texto", CADENAS)
def test_cadenas_ascii_y_ida_y_vuelta(texto):
    literal = formato_cadena(texto)
    literal.encode("ascii")
    assert _decodificar(literal) == texto


def test_cadena_con_caracteres_especiales_exacta():
    assert formato_cadena("L'a\\") == "'L''a\\\\'"
    assert formato_cadena("Ø") == "'\\X2\\00D8\\X0\\'"
    assert formato_cadena("a\nb") == "'a\\X2\\000A\\X0\\b'"
    assert formato_cadena("😀") == "'\\X4\\0001F600\\X0\\'"
    assert formato_cadena("ñó") == "'\\X2\\00F100F3\\X0\\'"  # consecutivos agrupados


def test_cadenas_unicode_aleatorias_ida_y_vuelta():
    rng = random.Random(1234)
    for _ in range(300):
        texto = "".join(
            chr(
                rng.choice(
                    [
                        rng.randint(0x20, 0x7E),
                        rng.randint(0, 0x2FF),
                        rng.randint(0x3000, 0xD7FF),
                        rng.randint(0x10000, 0x10FFFF),
                    ]
                )
            )
            for _ in range(rng.randint(0, 30))
        )
        assert _decodificar(formato_cadena(texto)) == texto


def test_sustituto_suelto_no_rompe_el_archivo():
    assert formato_cadena("a\ud800b") == "'a?b'"


# ----------------------------------------------------------------- GUID ---


def test_guid_vectores_fijos():
    assert ifc_step.comprimir_guid("0" * 32) == "0" * 22
    assert ifc_step.comprimir_guid("f" * 32) == "3" + "$" * 21  # el primer carácter solo llega a 3
    g = ifc_step.comprimir_guid(uuid.UUID("12345678-1234-5678-1234-567812345678").hex)
    assert len(g) == 22 and g[0] in "0123"
    assert re.fullmatch(r"[0-9A-Za-z_$]{22}", g)


def test_guid_determinista_y_distinto_por_parte():
    ns = uuid.UUID("6f0b6c1e-4f43-5d2c-9d0e-3a8b7c1d5e21")
    a = ifc_step.guid_determinista(ns, "c1", "e1", 0)
    assert a == ifc_step.guid_determinista(ns, "c1", "e1", 0)
    assert a != ifc_step.guid_determinista(ns, "c1", "e1", 1)


def test_guid_longitud_incorrecta_es_error():
    with pytest.raises(ErrorEscritor):
        ifc_step.comprimir_guid("ab")


# ------------------------------------------------------- estructura STEP ---


def test_cabecera_y_pie():
    f = ArchivoStep()
    f.cabecera(nombre="x.ifc", marca_tiempo="2026-01-01T00:00:00", sistema="Sistema")
    t = f.a_texto().split("\n")
    assert t[0] == "ISO-10303-21;" and t[1] == "HEADER;"
    assert t[2] == "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2]'),'2;1');"
    assert t[3] == "FILE_NAME('x.ifc','2026-01-01T00:00:00',(''),(''),'bridge.ifc_step','Sistema','');"
    assert t[4] == "FILE_SCHEMA(('IFC4'));"
    assert t[-3:] == ["ENDSEC;", "END-ISO-10303-21;", ""]


def test_lineas_exactas_de_entidades_y_valores_envueltos():
    f = ArchivoStep()
    p = f.crear_entidad("IfcCartesianPoint", Coordinates=[0.0, 1e-5, -2.5])
    ax = f.crear_entidad("IfcAxis2Placement3D", Location=p)
    f.crear_entidad("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    f.crear_entidad("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    f.crear_entidad("IfcPropertySingleValue", Name="Marca", NominalValue=f.crear_entidad("IfcLabel", "A'1"))
    f.crear_entidad("IfcPropertySingleValue", Name="N", NominalValue=f.crear_entidad("IfcInteger", 7))
    f.crear_entidad("IfcPropertySingleValue", Name="B", NominalValue=f.crear_entidad("IfcBoolean", True))
    f.crear_entidad(
        "IfcIndexedPolyCurve",
        Points=f.crear_entidad("IfcCartesianPointList3D", CoordList=[[0, 0, 0], [1, 2, 3]]),
        Segments=[f.crear_entidad("IfcLineIndex", [1, 2])],
        SelfIntersect=False,
    )
    assert (
        ax.Location is p and p.is_a() == "IfcCartesianPoint" and p.is_a("IfcPoint") and not p.is_a("IfcLine")
    )
    lineas = f.a_texto().split("\n")[7:-3]
    assert lineas == [
        "#1=IFCCARTESIANPOINT((0.,1.E-05,-2.5));",
        "#2=IFCAXIS2PLACEMENT3D(#1,$,$);",
        "#3=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);",
        "#4=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);",
        "#5=IFCPROPERTYSINGLEVALUE('Marca',$,IFCLABEL('A''1'),$);",
        "#6=IFCPROPERTYSINGLEVALUE('N',$,IFCINTEGER(7),$);",
        "#7=IFCPROPERTYSINGLEVALUE('B',$,IFCBOOLEAN(.T.),$);",
        "#8=IFCCARTESIANPOINTLIST3D(((0.,0.,0.),(1.,2.,3.)));",
        "#9=IFCINDEXEDPOLYCURVE(#8,(IFCLINEINDEX((1,2))),.F.);",
    ]


def test_valor_inmutable_y_repr():
    v = ArchivoStep().crear_entidad("IfcLabel", "x")
    assert isinstance(v, Valor) and v.tipo == "IfcLabel" and v.dato == "x"
    with pytest.raises(AttributeError):
        v.dato = "y"


def test_atributos_asignables_despues_de_crear():
    f = ArchivoStep()
    tipo = f.crear_entidad("IfcReinforcingBarType", GlobalId="g" * 22, PredefinedType="MAIN")
    assert tipo.HasPropertySets is None
    ps = f.crear_entidad(
        "IfcPropertySet",
        GlobalId="h" * 22,
        HasProperties=[f.crear_entidad("IfcPropertySingleValue", Name="n")],
    )
    tipo.HasPropertySets = [ps]
    assert tipo.HasPropertySets == [ps]
    with pytest.raises(ErrorEscritor):  # HasProperties exige al menos una propiedad
        f.crear_entidad("IfcPropertySet", GlobalId="i" * 22, HasProperties=[])
    with pytest.raises(ErrorEscritor):
        tipo.NoExiste = 1


# --------------------------------------------------------------- errores ---


def test_errores_de_validacion():
    f = ArchivoStep()
    p = f.crear_entidad("IfcCartesianPoint", Coordinates=[0, 0, 0])
    casos = [
        lambda: f.crear_entidad("IfcNoExiste"),
        lambda: f.crear_entidad("IfcRoot", GlobalId="x"),  # abstracta / sin atributos propios
        lambda: f.crear_entidad("IfcCartesianPoint", Coordenadas=[0, 0, 0]),  # atributo inexistente
        lambda: f.crear_entidad("IfcCartesianPoint", [0, 0], Coordinates=[0, 0]),  # posición y nombre
        lambda: f.crear_entidad(
            "IfcCartesianPoint", Coordinates=[0]
        ),  # rango [1:3] ok pero cuenta 1 -> válido; ver abajo
        lambda: f.crear_entidad("IfcCartesianPoint", Coordinates=[0, 0, 0, 0]),  # más de 3
        lambda: f.crear_entidad("IfcCartesianPoint", Coordinates="abc"),  # no es lista
        lambda: f.crear_entidad("IfcSIUnit", Dimensions=p, UnitType="LENGTHUNIT", Name="METRE"),  # derivado
        lambda: f.crear_entidad("IfcSIUnit", UnitType="LONGITUD", Name="METRE"),  # enumeración errónea
        lambda: f.crear_entidad("IfcAxis2Placement3D", Location="no es entidad"),
        lambda: f.crear_entidad(
            "IfcAxis2Placement3D", Location=f.crear_entidad("IfcLabel", "x")
        ),  # valor, no entidad
        lambda: f.crear_entidad("IfcLabel", 5),  # tipo base errónea
        lambda: f.crear_entidad("IfcLabel"),
        lambda: f.crear_entidad(
            "IfcPropertySingleValue", Name="n", NominalValue="texto suelto"
        ),  # SELECT sin envolver
        lambda: f.crear_entidad("IfcLineIndex", [1]),  # [2:?]
        lambda: ArchivoStep().crear_entidad("IfcAxis2Placement3D", Location=p),  # entidad de otro archivo
    ]
    for caso in casos:
        if caso is casos[4]:
            caso()  # un solo valor en [1:3] es válido
            continue
        with pytest.raises(ErrorEscritor):
            caso()


def test_atributo_obligatorio_vacio_se_detecta_al_serializar():
    f = ArchivoStep()
    f.crear_entidad("IfcCartesianPoint")  # Coordinates es obligatorio
    with pytest.raises(ErrorEscritor, match="Coordinates"):
        f.a_texto()


def test_admite_enum():
    f = ArchivoStep()
    assert f.admite_enum("IfcFooting", "PredefinedType", "PAD_FOOTING")
    assert not f.admite_enum("IfcColumn", "PredefinedType", "PEDESTAL")  # es de IFC4X3
    assert not f.admite_enum("IfcPlate", "PredefinedType", "BASE_PLATE")
    with pytest.raises(ErrorEscritor):
        f.admite_enum("IfcNoExiste", "PredefinedType", "X")
    with pytest.raises(ErrorEscritor):
        f.admite_enum("IfcColumn", "Tag", "X")


def test_el_modulo_solo_usa_la_biblioteca_estandar_y_el_esquema():
    import ast

    permitidos = {"__future__", "math", "time", "uuid", "typing", "bridge"}
    arbol = ast.parse((RAIZ / "bridge" / "ifc_step.py").read_text(encoding="utf-8"))
    raices = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            raices.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            raices.add((n.module or "").split(".")[0])
    assert raices <= permitidos, raices


def test_el_empaquetado_no_lleva_ifcopenshell():
    """Guarda: el .exe no instala ni recoge ifcopenshell; solo es dependencia de desarrollo."""
    script = (RAIZ / "scripts" / "build_bridge.ps1").read_text(encoding="utf-8")
    assert "ifcopenshell" not in script and "requirements-puente-ifc" not in script
    produccion = [RAIZ / "backend" / "requirements.txt", RAIZ / "backend" / "requirements-integrados.txt"]
    for ruta in produccion:
        assert "ifcopenshell" not in ruta.read_text(encoding="utf-8").lower(), ruta.name
    assert "ifcopenshell==" in (RAIZ / "backend" / "requirements-dev.txt").read_text(encoding="utf-8")
    assert not (RAIZ / "backend" / "requirements-puente-ifc.txt").exists()
