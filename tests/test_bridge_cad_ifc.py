"""Exportación IFC4 del puente (bridge/cad_ifc.py y ruta POST /v1/sap/actions/cad/ifc).

Entrada real: tests/fixtures/estructura_v1_pedestal.json (lo que produce el servidor). Las pruebas reabren el
IFC con ifcopenshell, lo validan (esquema + reglas EXPRESS; ``lark`` viene con ifcopenshell) y comprueban
clases, relaciones, Psets, GlobalId deterministas, longitudes y, con ``ifcopenshell.geom``, el volumen.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import cad_drwr, cad_ifc  # noqa: E402

FIXTURE = RAIZ / "tests" / "fixtures" / "estructura_v1_pedestal.json"
RUTA = "/v1/sap/actions/cad/ifc"


# Las únicas pruebas que no necesitan ifcopenshell (503, importaciones del módulo y geometría pura); el resto se
# salta si la librería no está instalada (se instala con backend/requirements-puente-ifc.txt).
SIN_LIBRERIA = {
    "test_ruta_503_sin_ifcopenshell_y_estado",
    "test_el_modulo_no_importa_ifcopenshell_ni_el_motor_al_cargar",
    "test_longitud_de_arcos_por_tres_puntos",
}


@pytest.fixture(autouse=True)
def _requiere_ifcopenshell(request):
    if request.node.originalname not in SIN_LIBRERIA and not cad_ifc.disponible():
        pytest.skip("ifcopenshell no está instalada")


@pytest.fixture(scope="module")
def modelo() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def cliente() -> TestClient:
    app = FastAPI()
    cad_drwr.install_routes(app)
    return TestClient(app)


def _instancias(m: dict) -> int:
    return sum(len(e["geom"]["instancias"]) for e in m["elementos"] if e["clase"] == "BarraRefuerzo")


def _abrir(tmp_path: Path, datos: bytes):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    ruta = tmp_path / "salida.ifc"
    ruta.write_bytes(datos)
    return ifcopenshell.open(str(ruta))


@pytest.fixture(scope="module")
def ifc_bytes(modelo) -> bytes:
    pytest.importorskip("ifcopenshell")
    nombre, datos = cad_ifc.generar_ifc(modelo)
    assert nombre == "pedestal.ifc"
    return datos


@pytest.fixture
def f(tmp_path, ifc_bytes):
    return _abrir(tmp_path, ifc_bytes)


# ------------------------------------------------------------ esquema --


def test_el_ifc_valida_contra_el_esquema_y_las_reglas_express(f):
    import ifcopenshell.validate

    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert log.statements == []
    assert f.schema == "IFC4"


def test_cabecera_vista_de_referencia_y_unidades_mm(f):
    assert "ReferenceView" in f.header.file_description.description[0]
    proyecto = f.by_type("IfcProject")[0]
    unidades = proyecto.UnitsInContext.Units
    longitud = next(u for u in unidades if u.UnitType == "LENGTHUNIT")
    assert (longitud.Prefix, longitud.Name) == ("MILLI", "METRE")
    contextos = {
        (c.ContextType, getattr(c, "ContextIdentifier", None))
        for c in f.by_type("IfcGeometricRepresentationContext")
    }
    assert ("Model", "Model") in contextos and ("Model", "Body") in contextos


def test_jerarquia_espacial(f):
    proyecto = f.by_type("IfcProject")[0]
    assert proyecto.IsDecomposedBy[0].RelatedObjects[0].is_a("IfcSite")
    sitio = f.by_type("IfcSite")[0]
    assert sitio.IsDecomposedBy[0].RelatedObjects[0].is_a("IfcBuilding")
    edificio = f.by_type("IfcBuilding")[0]
    piso = edificio.IsDecomposedBy[0].RelatedObjects[0]
    assert piso.is_a("IfcBuildingStorey") and piso.Name == "Fundaciones"


# ----------------------------------------------------------- elementos --


def test_clases_y_cantidades(f, modelo):
    cuenta = Counter(e.is_a() for e in f)
    assert cuenta["IfcReinforcingBar"] == _instancias(modelo) == 34
    assert cuenta["IfcElementAssembly"] == cuenta["IfcFooting"] == cuenta["IfcColumn"] == 1
    assert cuenta["IfcReinforcingBarType"] == len(
        {e["marca"] for e in modelo["elementos"] if e["clase"] == "BarraRefuerzo"}
    )
    asm = f.by_type("IfcElementAssembly")[0]
    assert (asm.PredefinedType, asm.ObjectType) == ("USERDEFINED", "BASE_COLUMNA")
    assert f.by_type("IfcFooting")[0].PredefinedType == "PAD_FOOTING"
    col = f.by_type("IfcColumn")[0]
    assert (col.PredefinedType, col.ObjectType) == ("USERDEFINED", "PEDESTAL")
    for el in (f.by_type("IfcFooting")[0], col):
        assert el.Representation.Representations[0].Items[0].is_a("IfcExtrudedAreaSolid")
        assert el.Representation.Representations[0].RepresentationIdentifier == "Body"


def test_barras_atributos_y_curva(f, modelo):
    tipos_ok = {e["id"]: e for e in modelo["elementos"] if e["clase"] == "BarraRefuerzo"}
    por_tipo = Counter(b.PredefinedType for b in f.by_type("IfcReinforcingBar"))
    esperado = Counter()
    for e in tipos_ok.values():
        esperado[e["tipo"]] += len(e["geom"]["instancias"])
    assert por_tipo == esperado
    for b in f.by_type("IfcReinforcingBar"):
        e = tipos_ok[b.Description]
        assert b.NominalDiameter == pytest.approx(e["geom"]["d"])
        assert b.SteelGrade == e["material"]["nombre"]
        solido = b.Representation.Representations[0].Items[0]
        assert solido.is_a("IfcSweptDiskSolid") and solido.Radius == pytest.approx(e["geom"]["d"] / 2)
        assert solido.Directrix.is_a("IfcIndexedPolyCurve")
    # B2a: dos tramos rectos y un arco (IfcArcIndex con los tres puntos)
    b2a = next(b for b in f.by_type("IfcReinforcingBar") if b.Description == "B2a")
    segmentos = [s.is_a() for s in b2a.Representation.Representations[0].Items[0].Directrix.Segments]
    assert segmentos == ["IfcLineIndex", "IfcArcIndex", "IfcLineIndex"]
    arco = b2a.Representation.Representations[0].Items[0].Directrix.Segments[1]
    assert len(arco[0]) == 3


def test_bar_length_igual_a_la_longitud_del_eje(f, modelo):
    norm = {e["id"]: e for e in cad_ifc.validar(modelo)["elementos"]}
    por_id = {e["id"]: e for e in modelo["elementos"] if e["clase"] == "BarraRefuerzo"}
    for b in f.by_type("IfcReinforcingBar"):
        assert b.BarLength == pytest.approx(cad_ifc.longitud_eje(norm[b.Description]["eje"]))
        # concuerda con el desarrollo que declara el servidor (con la tolerancia de su redondeo)
        assert b.BarLength == pytest.approx(por_id[b.Description]["longitud"], rel=0.02)


def test_instancias_desplazan_el_eje(f, modelo):
    b1 = [b for b in f.by_type("IfcReinforcingBar") if b.Description == "B1"]
    assert len(b1) == 8
    xs = set()
    for b in b1:
        pts = b.Representation.Representations[0].Items[0].Directrix.Points.CoordList
        xs.add((round(pts[0][0], 3), round(pts[0][1], 3)))
    desp = {
        (round(-93.0 + d[0], 3), round(93.0 + d[1], 3)) for d in modelo["elementos"][2]["geom"]["instancias"]
    }
    assert xs == desp


def test_armadura_agregada_al_padre_y_contenida_en_la_planta(f, modelo):
    for e in modelo["elementos"]:
        if e["clase"] != "BarraRefuerzo":
            continue
        padre = next(p for p in f.by_type("IfcRoot") if p.Name and p.Description == e["padre"])
        hijas = {h.Description for r in padre.IsDecomposedBy for h in r.RelatedObjects}
        assert e["id"] in hijas
    asm = f.by_type("IfcElementAssembly")[0]
    partes = {p.is_a() for r in asm.IsDecomposedBy for p in r.RelatedObjects}
    assert partes == {"IfcFooting", "IfcColumn"}
    contenidos = f.by_type("IfcRelContainedInSpatialStructure")[0].RelatedElements
    assert len(contenidos) == 1 + 2 + 34
    assert all(b.ContainedInStructure for b in f.by_type("IfcReinforcingBar"))


def test_tipos_por_marca_y_materiales(f):
    for tipo in f.by_type("IfcReinforcingBarType"):
        assert tipo.Types[0].RelatedObjects
        assert tipo.NominalDiameter and tipo.BarLength
    materiales = {m.Name: m for m in f.by_type("IfcMaterial")}
    assert len(materiales) == 2
    for el in f.by_type("IfcReinforcingBar") + f.by_type("IfcFooting") + f.by_type("IfcColumn"):
        assert el.HasAssociations and el.HasAssociations[0].is_a("IfcRelAssociatesMaterial")
    assert {m.Category for m in materiales.values()} == {"Concrete", "Steel"}


def test_psets_estandar_y_propio(f):
    def psets(obj):
        return {
            r.RelatingPropertyDefinition.Name: r.RelatingPropertyDefinition
            for r in obj.IsDefinedBy
            if r.is_a("IfcRelDefinesByProperties")
        }

    assert "Pset_FootingCommon" in psets(f.by_type("IfcFooting")[0])
    assert "Pset_ColumnCommon" in psets(f.by_type("IfcColumn")[0])
    assert all("Pset_ReinforcingBarCommon" in psets(b) for b in f.by_type("IfcReinforcingBar"))
    vinculo = psets(f.by_type("IfcElementAssembly")[0])["Pset_WB_Vinculo"]
    props = {p.Name: p.NominalValue.wrappedValue for p in vinculo.HasProperties}
    assert props["ConjuntoId"] == "pedestal"


# --------------------------------------------------------- GlobalId --


def test_globalid_deterministas_y_unicos(modelo, tmp_path):
    pytest.importorskip("ifcopenshell")
    _, a = cad_ifc.generar_ifc(modelo)
    _, b = cad_ifc.generar_ifc(copy.deepcopy(modelo))
    fa, fb = _abrir(tmp_path, a), _abrir(tmp_path, b)
    ids_a = [e.GlobalId for e in fa.by_type("IfcRoot")]
    ids_b = [e.GlobalId for e in fb.by_type("IfcRoot")]
    assert ids_a == ids_b
    assert len(ids_a) == len(set(ids_a))
    assert all(len(g) == 22 for g in ids_a)


def test_globalid_cambian_si_cambia_el_conjunto(modelo, tmp_path):
    pytest.importorskip("ifcopenshell")
    otro = copy.deepcopy(modelo)
    otro["id"] = "pedestal2"
    ids = {e.GlobalId for e in _abrir(tmp_path, cad_ifc.generar_ifc(modelo)[1]).by_type("IfcRoot")}
    ids2 = {e.GlobalId for e in _abrir(tmp_path, cad_ifc.generar_ifc(otro)[1]).by_type("IfcRoot")}
    assert not ids & ids2


def test_origen_y_angulo_colocan_el_ensamblaje(modelo, tmp_path):
    pytest.importorskip("ifcopenshell")
    m = copy.deepcopy(modelo)
    m["origen"] = [1000.0, 2000.0, 50.0]
    m["angulo"] = 90.0
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    p = f.by_type("IfcElementAssembly")[0].ObjectPlacement.RelativePlacement
    assert tuple(p.Location.Coordinates) == (1000.0, 2000.0, 50.0)
    assert p.RefDirection.DirectionRatios[0] == pytest.approx(0.0, abs=1e-12)
    assert p.RefDirection.DirectionRatios[1] == pytest.approx(1.0)


# ------------------------------------------------------------ geometría --


def test_volumen_de_barras_pi_d2_l_4(f):
    pytest.importorskip("numpy")
    geom = pytest.importorskip("ifcopenshell.geom")
    util_shape = pytest.importorskip("ifcopenshell.util.shape")
    ajustes = geom.settings()
    comprobadas = 0
    for desc in ("B1", "B2a", "B3"):
        b = next(b for b in f.by_type("IfcReinforcingBar") if b.Description == desc)
        forma = geom.create_shape(ajustes, b)
        volumen = util_shape.get_volume(forma.geometry)
        esperado = math.pi * b.NominalDiameter**2 / 4 * b.BarLength
        assert volumen == pytest.approx(esperado * 1e-9, rel=0.03), desc  # la malla sale en m (mm³ -> m³)
        comprobadas += 1
    assert comprobadas == 3


# -------------------------------------------------------------- ruta --


def test_ruta_responde_200_con_el_ifc_en_base64(cliente, modelo, tmp_path):
    pytest.importorskip("ifcopenshell")
    r = cliente.post(RUTA, json={"modelo": modelo})
    assert r.status_code == 200, r.text
    d = r.json()
    assert sorted(d) == ["contenido_base64", "nombre", "tamano"]
    datos = base64.b64decode(d["contenido_base64"])
    assert d["nombre"] == "pedestal.ifc" and d["tamano"] == len(datos)
    assert datos.startswith(b"ISO-10303-21")
    assert len(_abrir(tmp_path, datos).by_type("IfcReinforcingBar")) == 34


def _422(cliente, modelo_malo) -> str:
    r = cliente.post(RUTA, json={"modelo": modelo_malo})
    assert r.status_code == 422, r.text
    return json.dumps(r.json(), ensure_ascii=False)


def test_ruta_422_modelo_invalido(cliente, modelo):
    pytest.importorskip("ifcopenshell")
    m = copy.deepcopy(modelo)
    m["formato"] = "estructura-v2"
    assert "estructura-v1" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["unidades"] = "m"
    assert "mm" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["elementos"][0]["geom"]["tipo"] = "esfera"
    assert "geom.tipo" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["elementos"][2]["padre"] = "no_existe"
    assert "padre" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["elementos"][2]["geom"]["eje"] = [["A", [0, 0, 0], [1, 1, 1], [2, 2, 2]]]
    assert "colineales" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["elementos"][2]["geom"]["instancias"] = [[0.0, 0.0, 0.0]] * 5001
    assert "5000" in _422(cliente, m)

    m = copy.deepcopy(modelo)
    m["avisos"] = ["x" * 1000] * 2500
    assert "MB" in _422(cliente, m)

    assert cliente.post(RUTA, json={}).status_code == 422
    assert cliente.post(RUTA, json={"modelo": modelo, "otro": 1}).status_code == 422


def test_ruta_503_sin_ifcopenshell_y_estado(cliente, modelo, monkeypatch):
    monkeypatch.setattr(cad_ifc, "disponible", lambda: False)
    r = cliente.post(RUTA, json={"modelo": modelo})
    assert r.status_code == 503
    assert "ifcopenshell" in r.json()["detail"]
    assert cliente.post("/v1/sap/actions/cad/status", json={}).json()["ifc"] is False
    monkeypatch.setattr(cad_ifc, "disponible", lambda: True)
    assert cliente.post("/v1/sap/actions/cad/status", json={}).json()["ifc"] is True


def test_el_modulo_no_importa_ifcopenshell_ni_el_motor_al_cargar():
    import ast

    arbol = ast.parse((RAIZ / "bridge" / "cad_ifc.py").read_text(encoding="utf-8"))
    for nodo in arbol.body:  # solo imports de nivel de módulo
        if isinstance(nodo, ast.Import):
            nombres = [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom):
            nombres = [nodo.module or ""]
        else:
            continue
        assert not any(n.startswith(("ifcopenshell", "backend")) for n in nombres), nombres


def test_longitud_de_arcos_por_tres_puntos():
    r = 50.0
    cuarto = [("A", [(r, 0, 0), (r * math.cos(math.pi / 4), r * math.sin(math.pi / 4), 0), (0, r, 0)])]
    assert cad_ifc.longitud_eje(cuarto) == pytest.approx(math.pi * r / 2)
    # 270° en un plano inclinado (la pasada por el punto medio decide el lado del arco)
    t = 3 * math.pi / 4
    tres_cuartos = [("A", [(r, 0, 0), (r * math.cos(t), 0, r * math.sin(t)), (0, 0, -r)])]
    assert cad_ifc.longitud_eje(tres_cuartos) == pytest.approx(1.5 * math.pi * r)


# ------------------------------------------- placa base y perno de anclaje --

FIXTURES = RAIZ / "tests" / "fixtures"
NUEVOS = ["placa_base", "perno_pg", "perno_l"]


def _cargar(nombre: str) -> dict:
    return json.loads((FIXTURES / f"estructura_v1_{nombre}.json").read_text(encoding="utf-8"))


def _psets(obj) -> dict:
    return {
        r.RelatingPropertyDefinition.Name: {
            p.Name: p.NominalValue.wrappedValue for p in r.RelatingPropertyDefinition.HasProperties
        }
        for r in obj.IsDefinedBy
        if r.is_a("IfcRelDefinesByProperties")
    }


def _por_desc(f, desc: str) -> list:
    return [p for p in f.by_type("IfcProduct") if p.Description == desc]


def _esperadas(m: dict) -> int:
    return sum(len(e["geom"].get("instancias") or [1]) for e in m["elementos"])


@pytest.fixture(scope="module", params=NUEVOS)
def caso(request):
    pytest.importorskip("ifcopenshell")
    m = _cargar(request.param)
    return request.param, m, cad_ifc.generar_ifc(m)[1]


def test_nuevos_valida_y_reabre(caso, tmp_path):
    import ifcopenshell.validate

    _, m, datos = caso
    f = _abrir(tmp_path, datos)
    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert log.statements == []
    # una entidad IFC por instancia de cada elemento + el ensamblaje
    asm = f.by_type("IfcElementAssembly")[0]
    piezas = [p for p in f.by_type("IfcElement") if not p.is_a("IfcElementAssembly")]
    assert len(piezas) == _esperadas(m)
    assert asm.ObjectType == ("PERNO_ANCLAJE" if m["tipo"] == "perno_anclaje" else "BASE_COLUMNA")


def test_nuevos_globalid_estables(caso, tmp_path):
    nombre, m, datos = caso
    otro = cad_ifc.generar_ifc(copy.deepcopy(m))[1]
    ids = [e.GlobalId for e in _abrir(tmp_path, datos).by_type("IfcRoot")]
    assert ids == [e.GlobalId for e in _abrir(tmp_path, otro).by_type("IfcRoot")]
    assert len(ids) == len(set(ids))


def test_placa_base_clases_agujeros_y_relaciones(tmp_path):
    m = _cargar("placa_base")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    cuenta = Counter(e.is_a() for e in f.by_type("IfcElement"))
    assert cuenta["IfcPlate"] == 1 + 4  # placa + 4 cartelas
    assert cuenta["IfcMechanicalFastener"] == 4 + 4 + 4  # pernos + tuercas + arandelas
    assert cuenta["IfcColumn"] == 2  # pedestal + perfil
    assert cuenta["IfcBuildingElementProxy"] == 1  # grout
    placa = _por_desc(f, "placa")[0]
    assert (placa.PredefinedType, placa.ObjectType) == (
        "USERDEFINED",
        "BASE_PLATE",
    )  # IFC4 no tiene BASE_PLATE
    perfil = placa.Representation.Representations[0].Items[0].SweptArea
    assert perfil.is_a("IfcArbitraryProfileDefWithVoids") and len(perfil.InnerCurves) == 4
    assert all(c.is_a("IfcCircle") and c.Radius == pytest.approx(12.5) for c in perfil.InnerCurves)
    cartelas = _por_desc(f, "cartela1") + _por_desc(f, "cartela2") + _por_desc(f, "cartela3")
    assert {c.ObjectType for c in cartelas} == {"STIFFENER_PLATE"}
    assert len(_por_desc(f, "tuerca")) == len(_por_desc(f, "arandela")) == 4
    assert {t.ObjectType for t in _por_desc(f, "tuerca")} == {"NUT"}
    assert {t.ObjectType for t in _por_desc(f, "arandela")} == {"WASHER"}
    pernos = _por_desc(f, "perno_a") + _por_desc(f, "perno_b")
    assert len(pernos) == 4 and {p.PredefinedType for p in pernos} == {"ANCHORBOLT"}
    assert {p.NominalDiameter for p in pernos} == {19.0}
    assert all(p.NominalLength == pytest.approx(707.535, rel=1e-3) for p in pernos)
    assert pernos[0].Representation.Representations[0].Items[0].is_a("IfcSweptDiskSolid")
    # jerarquía: pernos y grout al hormigón; perfil y cartelas a la placa; placa y hormigón al ensamblaje
    hijos = {
        padre.Description: {h.Description for r in padre.IsDecomposedBy for h in r.RelatedObjects}
        for padre in f.by_type("IfcElement")
        if padre.IsDecomposedBy
    }
    assert hijos["hormigon"] == {"grout", "perno_a", "perno_b"}
    assert hijos["placa"] == {"columna", "cartela1", "cartela2", "cartela3", "cartela4"}
    raiz = {
        h.Description for r in f.by_type("IfcElementAssembly")[0].IsDecomposedBy for h in r.RelatedObjects
    }
    assert {"hormigon", "placa", "tuerca", "arandela"} <= raiz
    # materiales, Psets
    assert {m.Category for m in f.by_type("IfcMaterial")} == {"Concrete", "Steel"}
    assert all(p.HasAssociations for p in f.by_type("IfcElement") if not p.is_a("IfcElementAssembly"))
    assert "Pset_PlateCommon" in _psets(placa)
    pieza = _psets(placa)["Pset_WB_Pieza"]
    assert pieza["Marca"] == "PB1" and pieza["PesoKg"] == pytest.approx(24.27)
    assert _psets(_por_desc(f, "columna")[0])["Pset_WB_Pieza"]["Seccion"] == "W250X25"
    anc = _psets(pernos[0])["Pset_MechanicalFastenerAnchorBolt"]
    assert anc["AnchorBoltDiameter"] == 19.0
    assert _psets(_por_desc(f, "tuerca")[0])["Pset_WB_Pieza"]["PesoKg"] == pytest.approx(0.48 / 4)


def test_instancias_de_extrusion_desplazan_la_base(tmp_path):
    m = _cargar("placa_base")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    tuercas = _por_desc(f, "tuerca")
    esperado = {(d[0], d[1], 37.0 + d[2]) for d in m["elementos"][-1]["geom"]["instancias"]}
    pos = {tuple(t.Representation.Representations[0].Items[0].Position.Location.Coordinates) for t in tuercas}
    assert pos == esperado
    assert len({t.GlobalId for t in tuercas}) == 4


def test_cartela_extruye_en_x_cruz_y(tmp_path):
    f = _abrir(tmp_path, cad_ifc.generar_ifc(_cargar("placa_base"))[1])
    s = _por_desc(f, "cartela1")[0].Representation.Representations[0].Items[0]
    # base x=[1,0,0], y=[0,0,1] -> x × y = [0,-1,0]
    assert tuple(round(c, 9) for c in s.Position.Axis.DirectionRatios) == (0.0, -1.0, 0.0)
    assert tuple(s.Position.RefDirection.DirectionRatios) == (1.0, 0.0, 0.0)


def test_pernos_perno_pg_y_l(tmp_path):
    for nombre, tuercas in (("perno_pg", 3), ("perno_l", 1)):
        f = _abrir(tmp_path, cad_ifc.generar_ifc(_cargar(nombre))[1])
        assert len(_por_desc(f, "perno")) == 1
        assert Counter(p.ObjectType for p in f.by_type("IfcMechanicalFastener") if p.ObjectType) == Counter(
            {"NUT": tuercas, "WASHER": 2 if nombre == "perno_pg" else 1}
        )
        assert f.by_type("IfcElementAssembly")[0].ObjectType == "PERNO_ANCLAJE"


def _volumen(obj) -> float:
    geom = pytest.importorskip("ifcopenshell.geom")
    util_shape = pytest.importorskip("ifcopenshell.util.shape")
    return util_shape.get_volume(geom.create_shape(geom.settings(), obj).geometry) * 1e9  # m³ -> mm³


def test_volumen_de_la_placa_con_agujeros(tmp_path):
    pytest.importorskip("numpy")
    m = _cargar("placa_base")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    esp = 440 * 590 * 12 - 4 * math.pi * 12.5**2 * 12
    assert _volumen(_por_desc(f, "placa")[0]) == pytest.approx(esp, rel=0.03)
    # cartela: triángulo [[−50.5,0],[−50.5,150],[−130.5,0]] · 12 (y extruida en −Y, fuera del plano XY)
    assert _volumen(_por_desc(f, "cartela1")[0]) == pytest.approx(0.5 * 80 * 150 * 12, rel=0.03)
    # tuerca hexagonal: entre caras 1.8·19 = 34.2 mm
    assert _volumen(_por_desc(f, "tuerca")[0]) == pytest.approx(math.sqrt(3) / 2 * 34.2**2 * 15.2, rel=0.03)


@pytest.mark.parametrize(
    "nombre,idp", [("placa_base", "perno_a"), ("perno_pg", "perno"), ("perno_l", "perno")]
)
def test_volumen_del_perno_pi_d2_l_4(nombre, idp, tmp_path):
    pytest.importorskip("numpy")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(_cargar(nombre))[1])
    for p in _por_desc(f, idp):
        assert _volumen(p) == pytest.approx(math.pi * p.NominalDiameter**2 / 4 * p.NominalLength, rel=0.03)


def test_hormigon_con_base_se_coloca_en_su_cota(tmp_path):
    f = _abrir(tmp_path, cad_ifc.generar_ifc(_cargar("placa_base"))[1])
    s = _por_desc(f, "hormigon")[0].Representation.Representations[0].Items[0]
    assert tuple(s.Position.Location.Coordinates) == (0.0, 0.0, -500.0)
    assert _volumen(_por_desc(f, "hormigon")[0]) == pytest.approx(640 * 640 * 500, rel=1e-6)


def test_422_nuevas_validaciones(cliente):
    pytest.importorskip("ifcopenshell")
    base = _cargar("placa_base")

    def con(cambio) -> str:
        m = copy.deepcopy(base)
        cambio(m)
        return _422(cliente, m)

    assert "clase no soportada" in con(lambda m: m["elementos"][0].update(clase="Soldadura"))
    assert "perpendiculares" in con(lambda m: m["elementos"][2]["geom"]["base"].update(y=[1, 0, 0.2]))
    assert "dentro del contorno" in con(
        lambda m: m["elementos"][2]["geom"]["agujeros"].append({"c": [500.0, 0.0], "d": 25.0})
    )
    assert "admita hijos" in con(
        lambda m: m["elementos"][4].update(padre="tuerca")
    )  # la tuerca no admite hijos
    assert "ciclo" in con(lambda m: m["elementos"][2].update(padre="columna"))  # placa ⇄ perfil
    assert "ciclo" in con(lambda m: m["elementos"][3].update(padre="columna"))  # perfil hijo de sí mismo
    assert "ciclo" in con(lambda m: m["elementos"][0].update(padre="grout"))
    assert "geom.tipo" in con(lambda m: m["elementos"][-3]["geom"].update(tipo="extrusion"))
    assert "5000" in con(lambda m: m["elementos"][-1]["geom"].update(instancias=[[0, 0, 0]] * 5001))


# ------------------------------------------- perfil, perfil_refuerzos y fundación --


def _valida(f) -> None:
    import ifcopenshell.validate

    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log, express_rules=True)
    assert log.statements == []


def _padre(obj):
    rel = obj.Decomposes
    return rel[0].RelatingObject if rel else None


CONJUNTOS = {
    "perfil_hss": {"IfcColumn": 1},
    "perfil_refuerzos": {"IfcElementAssembly": 1, "IfcColumn": 1, "IfcPlate": 8},
    "fundacion": {"IfcElementAssembly": 2, "IfcFooting": 1, "IfcColumn": 2},
}


@pytest.fixture(scope="module", params=list(CONJUNTOS))
def conj(request):
    pytest.importorskip("ifcopenshell")
    m = _cargar(request.param)
    return request.param, m, cad_ifc.generar_ifc(m)[1]


def test_conjuntos_valida_reabre_y_cuenta_por_clase(conj, tmp_path):
    nombre, _, datos = conj
    f = _abrir(tmp_path, datos)
    _valida(f)
    assert Counter(e.is_a() for e in f.by_type("IfcElement")) == CONJUNTOS[nombre]


def test_conjuntos_globalid_estables(conj, tmp_path):
    _, m, datos = conj
    otro = cad_ifc.generar_ifc(copy.deepcopy(m))[1]
    ids = [e.GlobalId for e in _abrir(tmp_path, datos).by_type("IfcRoot")]
    assert ids == [e.GlobalId for e in _abrir(tmp_path, otro).by_type("IfcRoot")]
    assert len(ids) == len(set(ids))


def test_perfil_hss_sin_ensamblaje_con_hueco_y_psets(tmp_path):
    pytest.importorskip("numpy")
    m = _cargar("perfil_hss")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    assert len(f.by_type("IfcElementAssembly")) == 0
    perfil = f.by_type("IfcColumn")[0]
    assert (perfil.PredefinedType, _padre(perfil)) == ("COLUMN", None)
    assert perfil.ContainedInStructure[0].RelatingStructure.is_a("IfcBuildingStorey")
    sec = perfil.Representation.Representations[0].Items[0].SweptArea
    assert sec.is_a("IfcArbitraryProfileDefWithVoids")
    assert [c.is_a() for c in sec.InnerCurves] == ["IfcPolyline"]
    ps = _psets(perfil)
    meta = m["elementos"][0]["meta"]
    assert ps["Pset_WB_Pieza"]["TipoPerfil"] == meta["tipo_perfil"] == "Caja HSS"
    assert ps["Pset_WB_Pieza"]["Seccion"] == meta["seccion"]
    assert ps["Pset_WB_Pieza"]["AreaMm2"] == pytest.approx(meta["area_mm2"])
    assert ps["Pset_WB_Pieza"]["PesoLinealKgM"] == pytest.approx(meta["peso_lineal_kg_m"])
    assert ps["Pset_WB_Vinculo"]["Tipo"] == "perfil"
    assert "ilustrativo" in ps["Pset_WB_Avisos"]["Aviso1"]
    # volumen = área neta (exterior - hueco) × longitud del tramo
    g = m["elementos"][0]["geom"]
    neta = cad_ifc._area(g["contorno"]) - sum(cad_ifc._area(h) for h in g["huecos"])
    assert neta == pytest.approx(meta["area_mm2"], rel=1e-3)
    assert _volumen(perfil) == pytest.approx(neta * g["h"], rel=0.03)


def test_huecos_y_agujeros_circulares_se_combinan(tmp_path):
    m = _cargar("perfil_hss")
    m["elementos"][0]["geom"]["agujeros"] = [{"c": [0.0, 0.0], "d": 20.0}]
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    sec = f.by_type("IfcColumn")[0].Representation.Representations[0].Items[0].SweptArea
    assert sorted(c.is_a() for c in sec.InnerCurves) == ["IfcCircle", "IfcPolyline"]


def test_perfil_refuerzos_jerarquia_y_volumen_de_plancha(tmp_path):
    pytest.importorskip("numpy")
    m = _cargar("perfil_refuerzos")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    asm = f.by_type("IfcElementAssembly")[0]
    assert (asm.PredefinedType, asm.ObjectType) == ("USERDEFINED", "PERFIL_REFORZADO")
    perfil = _por_desc(f, "perfil")[0]
    assert _padre(perfil) == asm
    hijos = [e for e in f.by_type("IfcPlate")]
    assert len(hijos) == 8 and all(_padre(p) == perfil for p in hijos)
    assert {p.ObjectType for p in hijos} == {"STIFFENER_PLATE"}
    ps = _psets(perfil)["Pset_WB_Pieza"]
    assert ps["TipoPerfil"] == "Perfil I / W" and ps["Seccion"] == "W250X25"
    por_id = {e["id"]: e for e in m["elementos"]}
    for pid in ("plancha1", "plancha3"):
        meta = por_id[pid]["meta"]
        esperado = meta["ancho"] * meta["espesor"] * por_id[pid]["geom"]["h"]
        assert _volumen(_por_desc(f, pid)[0]) == pytest.approx(esperado, rel=0.03)
    # el perfil I: área de la sección × longitud
    assert _volumen(perfil) == pytest.approx(
        por_id["perfil"]["meta"]["area_mm2"] * por_id["perfil"]["geom"]["h"], rel=0.03
    )


def test_fundacion_subensamblaje_por_zapata_y_vinculo(tmp_path):
    pytest.importorskip("numpy")
    m = _cargar("fundacion")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    asm = next(a for a in f.by_type("IfcElementAssembly") if a.ObjectType == "FUNDACION")
    sub = next(a for a in f.by_type("IfcElementAssembly") if a.ObjectType == "ZAPATA")
    assert sub.Name == "Zapata F1" and _padre(sub) == asm
    assert {_padre(e) for e in f.by_type("IfcFooting") + f.by_type("IfcColumn")} == {sub}
    # el origen del conjunto (coordenadas grandes) va en el ensamblaje
    loc = asm.ObjectPlacement.RelativePlacement.Location.Coordinates
    assert tuple(loc) == (1500.0, 1750.0, -1000.0)
    ps = _psets(asm)
    assert ps["Pset_WB_Vinculo"]["Frames"] == "C1, C2" and ps["Pset_WB_Vinculo"]["Joints"] == "1, 2, 3, 4"
    assert ps["Pset_WB_Vinculo"]["Areas"] == "A1"
    assert ps["Pset_WB_Avisos"]["Aviso1"] == m["avisos"][0] and ps["Pset_WB_Avisos"]["NumAvisos"] == 1
    sp = _psets(sub)["Pset_WB_Vinculo"]
    assert (sp["Areas"], sp["Frames"], sp["Joints"]) == ("A1", "C1, C2", "1, 2, 3, 4")
    zap = f.by_type("IfcFooting")[0]
    assert _psets(zap)["Pset_WB_Fundacion"]["Area"] == "A1"
    assert _psets(zap)["Pset_WB_Fundacion"]["Joints"] == "1, 2, 3, 4"
    ped = _por_desc(f, "z1_p2")[0]
    pf = _psets(ped)["Pset_WB_Fundacion"]
    assert (pf["Frame"], pf["PuntoPie"], pf["Aproximado"]) == ("C2", "6", True)


def test_fundacion_varias_zapatas_y_volumen(tmp_path):
    pytest.importorskip("numpy")
    m = _cargar("fundacion")
    z2 = copy.deepcopy(m["elementos"][0])
    z2["id"], z2["marca"], z2["geom"]["h"] = "z2_a2", "F2", 500.0
    z2["geom"]["contorno"] = [[0.0, 0.0], [2000.0, 0.0], [2000.0, 1000.0], [0.0, 1000.0]]
    z2["meta"] = {**z2["meta"], "area": "A2", "zapata": "F2", "pts_nombres": ["7", "8"]}
    z3 = copy.deepcopy(z2)  # segunda área de la misma zapata física F2
    z3["id"], z3["meta"] = "z2_a3", {**z2["meta"], "area": "A3", "pts_nombres": ["8", "9"]}
    ped = copy.deepcopy(m["elementos"][1])
    ped["id"], ped["meta"] = "z2_p1", {**ped["meta"], "frame": "C3", "zapata": "F2"}
    m["elementos"] += [z2, z3, ped]
    f = _abrir(tmp_path, cad_ifc.generar_ifc(m)[1])
    _valida(f)
    subs = {a.Name: a for a in f.by_type("IfcElementAssembly") if a.ObjectType == "ZAPATA"}
    assert sorted(subs) == ["Zapata F1", "Zapata F2"]
    f2 = {e.Description for e in subs["Zapata F2"].IsDecomposedBy[0].RelatedObjects}
    assert f2 == {"z2_a2", "z2_a3", "z2_p1"}
    assert _psets(subs["Zapata F2"])["Pset_WB_Vinculo"]["Areas"] == "A2, A3"
    esperado = sum(
        cad_ifc._area(e["geom"]["contorno"]) * e["geom"]["h"]
        for e in m["elementos"]
        if e["clase"] == "Zapata"
    )
    assert sum(_volumen(z) for z in f.by_type("IfcFooting")) == pytest.approx(esperado, rel=0.03)


def test_fundacion_zapata_un_area(tmp_path):
    pytest.importorskip("numpy")
    f = _abrir(tmp_path, cad_ifc.generar_ifc(_cargar("fundacion"))[1])
    assert _volumen(f.by_type("IfcFooting")[0]) == pytest.approx(3000 * 3500 * 400, rel=0.03)


def test_422_huecos(cliente):
    pytest.importorskip("ifcopenshell")
    base = _cargar("perfil_hss")

    def con(cambio) -> str:
        m = copy.deepcopy(base)
        cambio(m)
        return _422(cliente, m)

    g = lambda m: m["elementos"][0]["geom"]  # noqa: E731
    assert "dentro del contorno" in con(lambda m: g(m)["huecos"].append([[0, 0], [500, 0], [500, 50]]))
    assert "dentro del contorno" in con(lambda m: g(m).update(huecos=[[[-500, -50], [50, -50], [50, 50]]]))
    assert "al menos 3" in con(lambda m: g(m)["huecos"].append([[0, 0], [1, 1]]))
    assert "área nula" in con(lambda m: g(m)["huecos"].append([[0, 0], [1, 1], [2, 2]]))
    assert "lista de polígonos" in con(lambda m: g(m).update(huecos={"a": 1}))
