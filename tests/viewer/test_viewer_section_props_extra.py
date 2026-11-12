"""``sections/props``: dimensiones y propiedades ampliadas (t2b/tfb, dis, J, As, S, Z, shapeSource, ?names=)."""

from __future__ import annotations

import math

import pytest

from tests.viewer.test_viewer_section_props import RUTA, UNIDADES, Conector, FrameProp, Modelo

PROPS = {"As2": 1200.0, "As3": 3000.0, "J": 2.5e5, "S22": 9.0e4, "S33": 6.0e5, "Z22": 1.4e5, "Z33": 6.8e5}


def _sec(tipo, **kw):
    base = {"tipo": tipo, "mat": "A992", "A": 5000.0, "I33": 1.0e8, "I22": 1.0e7}
    base.update(kw)
    return base


class FrameProp2(FrameProp):
    """FrameProp con I asimétrica, doble ángulo, doble canal, General y Section Designer."""

    def GetISection(self, nombre, *_):
        if self.secciones.get(nombre, {}).get("tipo") != 1:
            raise Exception("no es I")
        s = self.secciones[nombre]
        h, b, tf, tw, t2b, tfb = (s[c] / self.mm for c in ("h", "b", "tf", "tw", "t2b", "tfb"))
        return ("", s["mat"], h, b, tf, tw, t2b, tfb, 0, "", "", 0)

    def GetDblAngle(self, nombre, *_):
        if self.secciones.get(nombre, {}).get("tipo") != 5:
            raise Exception("no es ángulo doble")
        s = self.secciones[nombre]
        h, b, tf, tw, dis = (s[c] / self.mm for c in ("h", "b", "tf", "tw", "dis"))
        return ("", s["mat"], h, b, tf, tw, dis, 0, "", "", 0)

    def GetDblChannel(self, nombre, *_):
        s = self.secciones.get(nombre, {})
        if s.get("tipo") != 11 or s.get("sin_getter"):
            raise Exception("sin GetDblChannel")
        h, b, tf, tw, dis = (s[c] / self.mm for c in ("h", "b", "tf", "tw", "dis"))
        return ("", s["mat"], h, b, tf, tw, dis, 0, "", "", 0)

    def GetGeneral(self, nombre, *_):
        s = self.secciones.get(nombre, {})
        if s.get("tipo") != 10:  # una Section Designer no tiene GetGeneral
            raise Exception("no es General")
        t3, t2 = s["h"] / self.mm, s["b"] / self.mm
        return ("", s["mat"], t3, t2) + (0.0,) * 12 + (0, "", "", 0)

    def GetSectProps(self, nombre, *_):
        base = super().GetSectProps(nombre)
        s, mm = self.secciones[nombre], self.mm
        v = list(base)
        for campo, pos, pot in (
            ("As2", 1, 2),
            ("As3", 2, 2),
            ("J", 3, 4),
            ("S22", 6, 3),
            ("S33", 7, 3),
            ("Z22", 8, 3),
            ("Z33", 9, 3),
        ):
            if campo in s:
                v[pos] = s[campo] / mm**pot
        return tuple(v)


class Modelo2(Modelo):
    def __init__(self, secciones, codigo=12):
        super().__init__(secciones, codigo)
        self.PropFrame = FrameProp2(secciones, UNIDADES[codigo][1])


SECCIONES = {
    "IA": _sec(1, h=400.0, b=200.0, tf=16.0, tw=8.0, t2b=150.0, tfb=12.0, **PROPS),
    "DA": _sec(5, h=100.0, b=75.0, tf=8.0, tw=8.0, dis=12.0, **PROPS),
    "DC": _sec(11, h=200.0, b=75.0, tf=11.0, tw=6.0, dis=20.0, **PROPS),
    "DC_SIN_GETTER": _sec(11, sin_getter=True, h=200.0, b=75.0, tf=11.0, tw=6.0, dis=20.0),
    "GEN": _sec(10, h=500.0, b=250.0, **PROPS),
    "SD1": _sec(13, **PROPS),
}


@pytest.fixture(params=[12, 5, 1], ids=["tonf_m", "kN_mm", "lb_in"])
def j(request, montar):
    return montar(Modelo2(SECCIONES, request.param)).get(RUTA).json()


@pytest.fixture
def montar(rutas_viewer):
    def _montar(modelo):
        cliente = rutas_viewer(lambda: Conector(modelo))
        cliente.conectar_demo()
        return cliente

    return _montar


def aprox(x):
    return pytest.approx(x, rel=1e-9)


def test_i_asimetrica_trae_ala_inferior_y_propiedades(j):
    s = j["sections"]["IA"]
    assert (s["h"], s["b"], s["tf"], s["tw"]) == (aprox(400.0), aprox(200.0), aprox(16.0), aprox(8.0))
    assert (s["t2b"], s["tfb"]) == (aprox(150.0), aprox(12.0))
    for campo, valor in PROPS.items():
        assert s[campo] == aprox(valor), campo
    assert s["shapeSource"] == "getter" and s["rzCalculated"] is False and "dis" not in s


def test_angulo_doble_trae_la_separacion(j):
    s = j["sections"]["DA"]
    assert s["dis"] == aprox(12.0) and s["shapeSource"] == "getter"
    assert (s["h"], s["b"], s["tf"], s["tw"]) == (aprox(100.0), aprox(75.0), aprox(8.0), aprox(8.0))
    assert "t2b" not in s and s["J"] == aprox(PROPS["J"])


def test_doble_canal_con_getter_da_dimensiones_y_dis(j):
    s = j["sections"]["DC"]
    assert s["propTypeName"] == "Double_Channel" and s["shapeSource"] == "getter"
    assert (s["h"], s["b"], s["tf"], s["tw"], s["dis"]) == tuple(map(aprox, (200.0, 75.0, 11.0, 6.0, 20.0)))


def test_doble_canal_sin_getter_se_entrega_solo_con_propiedades(j):
    s = j["sections"]["DC_SIN_GETTER"]
    assert s["shapeSource"] == "none" and s["propTypeName"] == "Double_Channel"
    assert s["h"] is None and "dis" not in s
    assert not any(w.startswith("Sección DC_SIN_GETTER") for w in j["warnings"])  # no se omite


def test_general_solo_propiedades_y_envolvente_sin_forma(j):
    s = j["sections"]["GEN"]
    assert s["shapeSource"] == "none" and s["propTypeName"] == "General"
    assert (s["h"], s["b"]) == (aprox(500.0), aprox(250.0))  # envolvente t3 x t2 de GetGeneral
    assert s["tf"] is None and s["tw"] is None and "t2b" not in s and "dis" not in s
    assert s["As2"] == aprox(PROPS["As2"]) and s["Z33"] == aprox(PROPS["Z33"])


def test_section_designer_solo_propiedades(j):
    s = j["sections"]["SD1"]
    assert s["shapeSource"] == "none" and s["propType"] == 13
    assert (s["h"], s["b"], s["tf"], s["tw"]) == (None, None, None, None)
    assert s["A"] == aprox(5000.0) and s["J"] == aprox(PROPS["J"]) and s["S33"] == aprox(PROPS["S33"])
    assert s["material"] is None


def test_campos_no_legibles_se_omiten_nunca_cero_ni_null(montar):
    secciones = {
        "W": _sec(1, h=300.0, b=150.0, tf=10.0, tw=6.0, t2b=150.0, tfb=10.0)
    }  # GetSectProps da 0 en J, S, Z
    s = montar(Modelo2(secciones)).get(RUTA).json()["sections"]["W"]
    for campo in ("As2", "As3", "J", "S22", "S33", "Z22", "Z33"):
        assert campo not in s, campo


def test_contrato_de_siempre_intacto(j):
    assert set(j) == {"units", "sections", "materials", "warnings"} and j["units"] == "mm,MPa"
    for s in j["sections"].values():
        assert {
            "propType",
            "propTypeName",
            "A",
            "I33",
            "I22",
            "r33",
            "r22",
            "rzCalculated",
            "material",
        } <= set(s)
        assert "rz" not in s and s["rzCalculated"] is False
        assert math.isfinite(s["A"])


def test_names_limita_las_secciones_y_avisa_de_las_inexistentes(montar):
    c = montar(Modelo2(SECCIONES))
    j = c.get(RUTA, params={"names": "IA, GEN,NO_EXISTE"}).json()
    assert set(j["sections"]) == {"IA", "GEN"}
    assert j["warnings"] == ["Sección NO_EXISTE: no existe en el modelo; se omite."]
    assert set(j["materials"]) == {"A992"}
    todas = c.get(RUTA).json()
    assert set(todas["sections"]) == set(SECCIONES)
    assert set(c.get(RUTA, params={"names": ""}).json()["sections"]) == set(SECCIONES)


def test_la_lectura_es_de_solo_lectura(montar):
    modelo = Modelo2(SECCIONES)
    montar(modelo).get(RUTA)
    assert modelo.escrituras == []


# ---------------------------------------------------------------------------
# GetNameList de PropFrame con respuestas de otra forma o ilegibles
# ---------------------------------------------------------------------------


class FrameProp3(FrameProp2):
    """PropFrame cuya GetNameList responde con la forma que se le indique (por firma)."""

    def __init__(self, secciones, mm, respuesta):
        super().__init__(secciones, mm)
        self.respuesta = respuesta
        self.llamadas = []

    def GetNameList(self, *args):
        self.llamadas.append(len(args))
        r = self.respuesta(*args) if callable(self.respuesta) else self.respuesta
        if isinstance(r, Exception):
            raise r
        return r


def _modelo3(respuesta, secciones=SECCIONES):
    m = Modelo2(secciones)
    m.PropFrame = FrameProp3(secciones, UNIDADES[12][1], respuesta)
    return m


NOMBRES = tuple(sorted(SECCIONES))


@pytest.mark.parametrize(
    "respuesta",
    [
        (len(NOMBRES), NOMBRES, 0),  # (count, names, ret)
        (0, len(NOMBRES), NOMBRES),  # (ret, count, names)
        (NOMBRES, 0),  # (names, ret)
        NOMBRES,  # tupla de str sin ret: antes lanzaba RuntimeError
        list(NOMBRES),  # lista de str sin ret
    ],
    ids=["count_names_ret", "ret_count_names", "names_ret", "tupla_sin_ret", "lista_sin_ret"],
)
def test_la_lista_de_secciones_se_lee_con_cualquier_forma_de_respuesta(montar, respuesta):
    j = montar(_modelo3(respuesta)).get(RUTA).json()
    assert set(j["sections"]) == set(SECCIONES) and j["warnings"] == []


def test_si_una_firma_responde_raro_se_prueba_la_siguiente(montar):
    modelo = _modelo3(lambda *a: None if len(a) == 0 else (len(NOMBRES), NOMBRES, 0))
    j = montar(modelo).get(RUTA).json()
    assert set(j["sections"]) == set(SECCIONES)
    assert modelo.PropFrame.llamadas[-2:] == [0, 2]  # GetNameList() y GetNameList(0, [])


def test_lista_ilegible_sin_names_responde_vacio_con_aviso_en_vez_de_fallar(montar):
    r = montar(_modelo3(lambda *a: object())).get(RUTA)
    assert r.status_code == 200
    j = r.json()
    assert j["sections"] == {} and j["materials"] == {} and set(j) == {"units", "sections", "materials", "warnings"}
    assert len(j["warnings"]) == 1 and "No se pudo leer la lista de secciones" in j["warnings"][0]
    assert "?names=A,B" in j["warnings"][0] and "última respuesta: object" in j["warnings"][0]


def test_lista_con_error_com_sin_names_tambien_responde_vacio_con_aviso(montar):
    j = montar(_modelo3(TypeError("firma no admitida"))).get(RUTA).json()
    assert j["sections"] == {} and "No se pudo leer la lista de secciones" in j["warnings"][0]


def test_names_lee_cada_seccion_directamente_sin_la_lista(montar):
    modelo = _modelo3(lambda *a: None)  # ninguna firma de GetNameList sirve
    j = montar(modelo).get(RUTA, params={"names": "IA,GEN,FANTASMA"}).json()
    assert set(j["sections"]) == {"IA", "GEN"}
    assert j["sections"]["IA"]["t2b"] == pytest.approx(150.0) and j["sections"]["GEN"]["shapeSource"] == "none"
    assert set(j["materials"]) == {"A992"}
    assert any("No se pudo leer la lista de secciones" in w for w in j["warnings"])
    assert "Sección FANTASMA: no se pudo leer su tipo; se omite." in j["warnings"]


def test_names_con_lista_legible_sigue_avisando_de_las_inexistentes(montar):
    j = montar(_modelo3((len(NOMBRES), NOMBRES, 0))).get(RUTA, params={"names": "IA,NO_EXISTE"}).json()
    assert set(j["sections"]) == {"IA"}
    assert j["warnings"] == ["Sección NO_EXISTE: no existe en el modelo; se omite."]
