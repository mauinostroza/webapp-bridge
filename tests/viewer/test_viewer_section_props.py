"""``GET /v1/sap/actions/viewer/sections/props``: propiedades de secciones en mm y MPa (solo lectura)."""

from __future__ import annotations

import math

import pytest

from bridge.alcances import ruta_permitida
from bridge.hosted import VIEWER_ORIGIN
from bridge.viewer.core.section_props import SeccionesError, read_section_props
from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeSapModel, _FrameProp, _NameList

RUTA = "/v1/sap/actions/viewer/sections/props"

# Código de unidades de SAP2000 -> (mm por unidad de longitud, N por unidad de fuerza).
UNIDADES = {
    12: ("tonf_m_C", 1000.0, 9806.65),
    14: ("kgf_cm_C", 10.0, 9.80665),
    5: ("kN_mm_C", 1.0, 1000.0),
    1: ("lb_in_F", 25.4, 4.4482216152605),
}

# Secciones de referencia, en mm y MPa.
W = {
    "tipo": 1,
    "mat": "A992",
    "h": 300.0,
    "b": 150.0,
    "tf": 10.0,
    "tw": 6.0,
    "A": 5000.0,
    "I33": 1.0e8,
    "I22": 1.0e7,
}
L = {
    "tipo": 4,
    "mat": "A992",
    "h": 100.0,
    "b": 75.0,
    "tf": 8.0,
    "tw": 8.0,
    "A": 1350.0,
    "I33": 1.4e6,
    "I22": 6.0e5,
}
R = {"tipo": 8, "mat": "H25", "h": 600.0, "b": 300.0, "A": 180000.0, "I33": 5.4e9, "I22": 1.35e9}
MATERIALES = {
    "A992": {"tipo": 1, "fy": 345.0, "fu": 450.0, "E": 200000.0},
    "H25": {"tipo": 2, "fy": 0.0, "fu": 0.0, "E": 25000.0, "fc": 25.0},
}


class FrameProp(_FrameProp):
    """PropFrame con firmas reales (código de retorno al final) y valores en las unidades del modelo."""

    def __init__(self, secciones: dict, mm: float) -> None:
        super().__init__(list(secciones))
        self.secciones = secciones
        self.mm = mm

    def GetTypeOAPI(self, nombre, *_):
        if nombre not in self.secciones:
            raise Exception("sección desconocida")
        return (self.secciones[nombre]["tipo"], 0)

    def _dims(self, nombre, *campos):
        s = self.secciones[nombre]
        return [s[c] / self.mm for c in campos]

    def GetISection(self, nombre, *_):
        if self.secciones.get(nombre, {}).get("tipo") != 1:
            raise Exception("no es I")
        h, b, tf, tw = self._dims(nombre, "h", "b", "tf", "tw")
        return ("", self.secciones[nombre]["mat"], h, b, tf, tw, b, tf, 0, "", "", 0)

    def GetAngle(self, nombre, *_):
        if self.secciones.get(nombre, {}).get("tipo") != 4:
            raise Exception("no es ángulo")
        h, b, tf, tw = self._dims(nombre, "h", "b", "tf", "tw")
        return ("", self.secciones[nombre]["mat"], h, b, tf, tw, 0, "", "", 0)

    def GetRectangle(self, nombre, *_):
        if self.secciones.get(nombre, {}).get("tipo") != 8:
            raise Exception("no es rectangular")
        h, b = self._dims(nombre, "h", "b")
        return ("", self.secciones[nombre]["mat"], h, b, 0, "", "", 0)

    def GetSectProps(self, nombre, *_):
        if nombre not in self.secciones:
            raise Exception("sección desconocida")
        s, mm = self.secciones[nombre], self.mm
        area, i22, i33 = s["A"] / mm**2, s["I22"] / mm**4, s["I33"] / mm**4
        r22, r33 = math.sqrt(s["I22"] / s["A"]) / mm, math.sqrt(s["I33"] / s["A"]) / mm
        return (area, 0.0, 0.0, 0.0, i22, i33, 0.0, 0.0, 0.0, 0.0, r22, r33, 0)


class PropMaterial(_NameList):
    def __init__(self, materiales: dict, mm: float, n: float) -> None:
        super().__init__(list(materiales))
        self.m = materiales
        self.mpa_a_modelo = mm**2 / n  # MPa -> esfuerzo en unidades del modelo

    def GetTypeOAPI(self, nombre, *_):
        if nombre not in self.m:
            raise Exception("material desconocido")
        return (self.m[nombre]["tipo"], 0, 0)

    def GetOSteel_1(self, nombre, *_):
        m = self.m[nombre]
        if m["tipo"] != 1:
            raise Exception("no es acero")
        return (
            m["fy"] * self.mpa_a_modelo,
            m["fu"] * self.mpa_a_modelo,
            0.0,
            0.0,
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
        )

    def GetMPIsotropic(self, nombre, *_):
        return (self.m[nombre]["E"] * self.mpa_a_modelo, 0.3, 1.2e-5, 0.0, 0)

    def GetOConcrete(self, nombre, *_):
        m = self.m[nombre]
        if m["tipo"] != 2 or "fc" not in m:
            raise Exception("no es hormigón con f'c")
        return (m["fc"] * self.mpa_a_modelo, False, 1.0, 0, 0, 0.002, 0.003, 0.0, 0.0, 0.0, 0)


class Modelo(FakeSapModel):
    def __init__(self, secciones: dict, codigo: int = 12, materiales: dict | None = None) -> None:
        super().__init__()
        self.codigo = codigo
        _, mm, n = UNIDADES[codigo]
        self.PropFrame = FrameProp(secciones, mm)
        self.PropMaterial = PropMaterial(MATERIALES if materiales is None else materiales, mm, n)
        self.escrituras: list[str] = []

    def GetPresentUnits(self) -> int:
        return self.codigo

    def SetPresentUnits(self, code: int) -> int:  # la ruta no debe cambiar las unidades de SAP2000
        self.escrituras.append("SetPresentUnits")
        return 0


class Conector:
    def __init__(self, modelo: Modelo) -> None:
        self.model = modelo
        self.is_demo = True

    def connect_demo(self):
        return self.info()

    def connect_to_open_instance(self):
        return ConnectionInfo(connected=False, message="no")

    def info(self):
        return ConnectionInfo(
            connected=True, model_name="Demo", units=UNIDADES[self.model.codigo][0], message=""
        )

    def is_alive(self):
        return True

    def disconnect(self):
        pass


SECCIONES = {"W300": W, "L100x75x8": L, "C30x60": R}


@pytest.fixture
def montar(rutas_viewer):
    def _montar(modelo: Modelo):
        cliente = rutas_viewer(lambda: Conector(modelo))
        cliente.conectar_demo()
        return cliente

    return _montar


def test_forma_de_la_respuesta(montar):
    j = montar(Modelo(SECCIONES)).get(RUTA).json()
    assert set(j) == {"units", "sections", "materials", "warnings"}
    assert j["units"] == "mm,MPa" and j["warnings"] == []
    assert set(j["sections"]) == set(SECCIONES)
    claves = {
        "propType",
        "propTypeName",
        "A",
        "I33",
        "I22",
        "r33",
        "r22",
        "rzCalculated",
        "h",
        "b",
        "tf",
        "tw",
        "material",
    }
    assert claves <= set(j["sections"]["W300"])
    assert "rz" not in j["sections"]["W300"] and j["sections"]["W300"]["rzCalculated"] is False
    assert j["sections"]["W300"]["propTypeName"] == "I_H" and j["sections"]["W300"]["propType"] == 1
    assert j["sections"]["C30x60"]["propTypeName"] == "Rectangular"
    assert set(j["materials"]["A992"]) == {"fy", "fu", "E", "type"}


@pytest.mark.parametrize("codigo", [12, 14, 5, 1])
def test_conversion_a_mm_y_mpa_desde_cualquier_unidad(montar, codigo):
    j = montar(Modelo(SECCIONES, codigo)).get(RUTA).json()
    s = j["sections"]["W300"]
    for clave, esperado in (
        ("A", 5000.0),
        ("I33", 1e8),
        ("I22", 1e7),
        ("h", 300.0),
        ("b", 150.0),
        ("tf", 10.0),
        ("tw", 6.0),
    ):
        assert s[clave] == pytest.approx(esperado, rel=1e-6), clave
    assert s["r33"] == pytest.approx(math.sqrt(1e8 / 5000), rel=1e-6)
    assert s["r22"] == pytest.approx(math.sqrt(1e7 / 5000), rel=1e-6)
    a = j["materials"]["A992"]
    assert a["fy"] == pytest.approx(345.0, rel=1e-6) and a["E"] == pytest.approx(200000.0, rel=1e-6)


def test_angulo_entrega_dimensiones_sin_calcular_rz(montar):
    """El puente no calcula rz: lo hace el servidor a partir de h, b, tf, tw."""
    s = montar(Modelo(SECCIONES)).get(RUTA).json()["sections"]["L100x75x8"]
    assert s["propTypeName"] == "Angle" and s["rzCalculated"] is False
    assert s.get("rz") is None
    assert (s["h"], s["b"], s["tf"], s["tw"]) == pytest.approx((100.0, 75.0, 8.0, 8.0))


def test_material_que_no_es_acero(montar):
    j = montar(Modelo(SECCIONES)).get(RUTA).json()
    h25 = j["materials"]["H25"]
    assert h25["type"] == "Concrete" and h25["fy"] is None and h25["fu"] is None
    assert h25["E"] == pytest.approx(25000.0, rel=1e-6)
    assert j["materials"]["A992"]["type"] == "Steel"


@pytest.mark.parametrize("codigo", [12, 14, 5, 1])
def test_hormigon_envia_fc_en_mpa_desde_cualquier_unidad(montar, codigo):
    j = montar(Modelo(SECCIONES, codigo)).get(RUTA).json()
    assert j["materials"]["H25"]["fc"] == pytest.approx(25.0, rel=1e-6)
    assert j["warnings"] == []


def test_fc_solo_en_hormigon_y_con_las_claves_de_antes(montar):
    j = montar(Modelo(SECCIONES)).get(RUTA).json()
    assert "fc" not in j["materials"]["A992"]
    assert set(j["materials"]["A992"]) == {"fy", "fu", "E", "type"}
    assert set(j["materials"]["H25"]) == {"fy", "fu", "E", "type", "fc"}


def test_hormigon_sin_fc_legible_se_omite_con_aviso(montar):
    materiales = {"A992": MATERIALES["A992"], "HX": {"tipo": 2, "fy": 0.0, "fu": 0.0, "E": 25000.0}}
    modelo = Modelo({"C": {**R, "mat": "HX"}}, materiales=materiales)
    j = montar(modelo).get(RUTA).json()
    assert j["sections"]["C"]["material"] == "HX"
    assert "fc" not in j["materials"]["HX"] and j["materials"]["HX"]["type"] == "Concrete"
    assert j["materials"]["HX"]["E"] == pytest.approx(25000.0, rel=1e-6)
    assert any("HX" in w and "f'c" in w for w in j["warnings"])


def test_puente_sin_getoconcrete_responde_sin_fc(montar):
    modelo = Modelo(SECCIONES)
    modelo.PropMaterial.GetOConcrete = None  # enlace antiguo: el método no existe
    r = montar(modelo).get(RUTA)
    assert r.status_code == 200
    j = r.json()
    assert "fc" not in j["materials"]["H25"] and j["materials"]["H25"]["type"] == "Concrete"
    assert any("H25" in w and "f'c" in w for w in j["warnings"])


def test_seccion_ilegible_se_omite_con_aviso(montar):
    modelo = Modelo({**SECCIONES, "ROTA": {**W, "tipo": 1}})
    del modelo.PropFrame.secciones["ROTA"]["A"]  # GetSectProps falla para esta sección
    j = montar(modelo).get(RUTA).json()
    assert "ROTA" not in j["sections"] and "W300" in j["sections"]
    assert any("ROTA" in w for w in j["warnings"])


def test_material_ilegible_se_omite_con_aviso(montar):
    j = montar(Modelo(SECCIONES, materiales={"A992": MATERIALES["A992"]})).get(RUTA).json()
    assert "H25" not in j["materials"]
    assert any("H25" in w for w in j["warnings"])


def test_sin_modelo_abierto_responde_404(rutas_viewer):
    cliente = rutas_viewer(lambda: Conector(Modelo(SECCIONES)))
    assert cliente.get(RUTA).status_code == 404


def test_no_escribe_ni_cambia_unidades(montar):
    modelo = Modelo(SECCIONES, 14)
    assert montar(modelo).get(RUTA).status_code == 200
    assert modelo.escrituras == []
    assert modelo.GetPresentUnits() == 14


def test_unidades_desconocidas_responden_409(montar):
    modelo = Modelo(SECCIONES)
    cliente = montar(modelo)  # conecta con unidades válidas y luego el modelo pasa a unidades no reconocidas
    modelo.GetPresentUnits = lambda: 99  # type: ignore[method-assign]
    assert cliente.get(RUTA).status_code == 409
    with pytest.raises(SeccionesError):
        read_section_props(modelo)


def test_alcance_del_visor_solo_el_prefijo_viewer():
    assert ruta_permitida(VIEWER_ORIGIN, RUTA)
    assert not ruta_permitida(VIEWER_ORIGIN, "/v1/sap/actions/esbelteces/section")
    assert not ruta_permitida(VIEWER_ORIGIN, "/v1/sap/actions/esbelteces/")
