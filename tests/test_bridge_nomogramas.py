"""Tests de bridge/nomogramas.py con COM falso (no demuestran el comportamiento de SAP2000 real).

Siguen el patrón de tests/test_bridge_placa_base.py: código de retorno al final (comtypes)
y también al inicio (pywin32) con el proxy ``_RetFirst``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.nomogramas import orientacion, selected_frames_nomogramas

CONTRATOS = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
RUTA = "/v1/sap/actions/nomogramas/selected-frames"


class _RetFirst:
    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return _RetFirst(attr)

        def call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if isinstance(result, tuple) and result and isinstance(result[-1], int):
                return (result[-1], *result[:-1])
            return result

        return call


class _Ns:
    pass


class _Sap:
    def __init__(self, frames=None, units=12):
        # frames: nombre -> dict(section, i, j, coords)
        self.frames = frames or {}
        self.units = units
        self.set_units: list[int] = []
        self.fallan: set[str] = set()
        self.secciones_malas: set[str] = set()
        self.SelectObj = _Ns()
        self.SelectObj.GetSelected = self._get_selected
        self.FrameObj = _Ns()
        self.FrameObj.GetSection = lambda n, *a: (self.frames[n]["section"], "", 0)
        self.FrameObj.GetPoints = lambda n: (self.frames[n]["i"], self.frames[n]["j"], 0)
        self.FrameObj.GetReleases = self._releases
        self.FrameObj.GetModifiers = self._modificadores
        self.LoadPatterns = _Ns()
        self.LoadPatterns.GetNameList = lambda *a: (1, ("DEAD",), 0)
        self.PointObj = _Ns()
        self.PointObj.GetCoordCartesian = lambda p: (*self.coords[p], 0)
        self.coords = {}
        for f in self.frames.values():
            self.coords[f["i"]], self.coords[f["j"]] = f["coords"]
        self.PropFrame = _Ns()
        self.PropFrame.GetSectProps = self._sect_props
        self.PropFrame.GetMaterial = self._material
        self.PropFrame.GetISection = lambda n: (0, "", "S355", 0.4, 0.2, 0.02, 0.01, 0)
        self.PropMaterial = _Ns()
        self.PropMaterial.GetMPIsotropic = lambda m: (210000.0, 0.3, 1.2e-5, 0.0, 0)

    def _get_selected(self, *a):
        return (len(self.frames), [2] * len(self.frames), list(self.frames), 0)

    def _sect_props(self, nombre, *a):
        if nombre in self.secciones_malas:
            return 1
        return (5000.0, 0.0, 0.0, 0.0, 2.0e7, 9.0e7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    def _material(self, nombre):
        if "material" in self.fallan:
            raise RuntimeError("GetMaterial no disponible")
        return ("S355", 0)

    def _releases(self, n):
        if "releases" in self.fallan:
            raise RuntimeError("GetReleases no disponible")
        return ((False,) * 4 + (False, True), (False,) * 6, (0.0,) * 6, (0.0,) * 6, 0)

    def _modificadores(self, n):
        if "mod" in self.fallan:
            return 1
        return ((1.0, 1.0, 1.0, 1.0, 0.7, 0.35, 1.0, 1.0), 0)

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, u):
        self.set_units.append(u)
        self.units = u
        return 0

    def GetModelFilename(self, *a):
        return "C:\\modelos\\torre.sdb"


def _columna():
    return {"section": "W360", "i": "1", "j": "2", "coords": ((0.0, 0.0, 0.0), (0.0, 0.0, 4000.0))}


def _viga():
    return {"section": "W410", "i": "2", "j": "3", "coords": ((0.0, 0.0, 4000.0), (6000.0, 0.0, 4100.0))}


@pytest.fixture(params=[False, True], ids=["ret-ultimo", "ret-primero"])
def envolver(request):
    return (lambda m: _RetFirst(m)) if request.param else (lambda m: m)


def test_sin_frames(envolver):
    sap = _Sap()
    out = selected_frames_nomogramas(envolver(sap))
    assert out == {"modelo": "torre.sdb", "unidades": "N_mm_C", "frames": []}
    assert sap.units == 12


def test_varios_frames_y_unidades_restauradas(envolver):
    sap = _Sap({"C1": _columna(), "V1": _viga()})
    out = selected_frames_nomogramas(envolver(sap))
    c, v = out["frames"]
    assert (c["frame"], c["section"], c["material"], c["e_mpa"]) == ("C1", "W360", "S355", 210000.0)
    assert (c["i33_mm4"], c["i22_mm4"], c["area_mm2"]) == (9.0e7, 2.0e7, 5000.0)
    assert c["length_mm"] == pytest.approx(4000.0)
    assert (c["point_i"], c["point_j"], c["orientacion"]) == ("1", "2", "vertical")
    assert v["orientacion"] == "horizontal"
    assert c["releases"] == {"i_m2": False, "i_m3": True, "j_m2": False, "j_m3": False}
    assert c["modificadores"] == {"i22": 0.7, "i33": 0.35}
    assert c["note"] is None
    assert sap.set_units == [9, 12]
    assert sap.units == 12


def test_no_cambia_unidades_si_ya_son_n_mm_c(envolver):
    sap = _Sap({"C1": _columna()}, units=9)
    selected_frames_nomogramas(envolver(sap))
    assert sap.set_units == []


def test_unidades_restauradas_con_excepcion():
    sap = _Sap({"C1": _columna()})
    sap.PointObj.GetCoordCartesian = lambda p: (0.0, 0.0, 0.0, 5)  # código de error
    with pytest.raises(RuntimeError):
        selected_frames_nomogramas(sap)
    assert sap.units == 12
    assert sap.set_units == [9, 12]


def test_seccion_no_reconocida_no_falla_toda_la_peticion():
    sap = _Sap({"C1": _columna(), "V1": _viga()})
    sap.secciones_malas.add("W410")
    c, v = selected_frames_nomogramas(sap)["frames"]
    assert c["note"] is None
    assert v["i33_mm4"] is None and v["area_mm2"] is None
    assert "W410" in v["note"]
    assert v["length_mm"] > 0


def test_datos_aislados_ausentes_son_null_con_nota():
    sap = _Sap({"C1": _columna()})
    sap.fallan = {"material", "releases", "mod"}
    (c,) = selected_frames_nomogramas(sap)["frames"]
    # GetMaterial falla: se usa el material de GetISection.
    assert c["material"] == "S355"
    assert c["releases"] == {"i_m2": None, "i_m3": None, "j_m2": None, "j_m3": None}
    assert c["modificadores"] == {"i22": None, "i33": None}
    assert "liberaciones" in c["note"] and "modificadores" in c["note"]


@pytest.mark.parametrize(
    ("p_j", "esperada"),
    [
        ((0, 0, 100), "vertical"),
        ((100, 0, 5), "horizontal"),
        ((100, 0, 100), "inclinada"),
        ((5, 0, 100), "vertical"),
        ((0, 0, 0), "inclinada"),
    ],
)
def test_orientacion(p_j, esperada):
    assert orientacion((0.0, 0.0, 0.0), tuple(float(x) for x in p_j)) == esperada


def _cliente(sap):
    class _Sesion:
        def __init__(self):
            self.actor = self

        def call(self, fn):
            return fn()

        def _model(self):
            return sap

    app = FastAPI()
    install_routes(app, _Sesion(), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_ruta_cumple_contrato_json():
    contrato = json.loads(CONTRATOS.read_text("utf-8"))["routes"]["GET " + RUTA]
    r = _cliente(_Sap({"C1": _columna()})).get(RUTA)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert set(cuerpo) == set(contrato["keys"])
    assert set(cuerpo["frames"][0]) == set(contrato["items"]["frames"])


def test_ruta_error_com_es_409_sap_error():
    sap = _Sap({"C1": _columna()})
    sap.FrameObj.GetPoints = lambda n: ("1", "2", 7)
    r = _cliente(sap).get(RUTA)
    assert r.status_code == 409
    assert sap.units == 12


# --- Endurecimiento: nada escapa, nada no finito, siempre JSON ---------------------------


class _PyWinTypesLike(Exception):  # imita pywintypes.com_error sin depender de pywin32
    pass


def _json_estricto(obj):
    return json.dumps(obj, allow_nan=False)


@pytest.mark.parametrize("exc", [TypeError("x"), AttributeError("y"), _PyWinTypesLike(-2147352567, "boom")])
def test_excepciones_raras_en_lecturas_aisladas_son_null_con_nota(exc, envolver):
    def lanza(*a, **k):
        raise exc

    sap = _Sap({"C1": _columna()})
    sap.FrameObj.GetReleases = lanza
    sap.FrameObj.GetModifiers = lanza
    sap.PropFrame.GetMaterial = lanza
    sap.PropFrame.GetISection = lanza
    (c,) = selected_frames_nomogramas(envolver(sap))["frames"]
    assert c["material"] is None and c["e_mpa"] is None
    assert c["releases"]["i_m2"] is None and c["modificadores"]["i22"] is None
    assert c["note"]
    assert c["length_mm"] == pytest.approx(4000.0)
    _json_estricto(c)


def test_excepcion_rara_en_seccion_y_material_e():
    sap = _Sap({"C1": _columna()})
    sap.PropFrame.GetSectProps = lambda *a: (_ for _ in ()).throw(TypeError("raro"))
    sap.PropMaterial.GetMPIsotropic = lambda m: (_ for _ in ()).throw(AttributeError("raro"))
    (c,) = selected_frames_nomogramas(sap)["frames"]
    assert c["area_mm2"] is None and c["i33_mm4"] is None and c["e_mpa"] is None
    assert "sección" in c["note"] and "E del material" in c["note"]


@pytest.mark.parametrize("malo", [float("nan"), float("inf"), float("-inf")])
def test_valores_no_finitos_son_null_con_nota(malo, envolver):
    sap = _Sap({"C1": _columna()})
    sap.PropFrame.GetSectProps = lambda n, *a: (
        malo,
        0.0,
        0.0,
        0.0,
        malo,
        malo,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0,
    )
    sap.PropMaterial.GetMPIsotropic = lambda m: (malo, 0.3, 1.2e-5, 0.0, 0)
    sap.FrameObj.GetModifiers = lambda n: ((1.0, 1.0, 1.0, 1.0, malo, 1.0, 1.0, 1.0), 0)
    out = selected_frames_nomogramas(envolver(sap))
    (c,) = out["frames"]
    assert c["area_mm2"] is None and c["i33_mm4"] is None and c["i22_mm4"] is None and c["e_mpa"] is None
    assert c["modificadores"] == {"i22": None, "i33": 1.0}
    assert c["note"]
    _json_estricto(out)


def test_longitud_no_finita_es_null_con_nota():
    sap = _Sap({"C1": _columna()})
    sap.PointObj.GetCoordCartesian = lambda p: (0.0, 0.0, float("nan"), 0)
    (c,) = selected_frames_nomogramas(sap)["frames"]
    assert c["length_mm"] is None
    assert c["orientacion"] is None
    assert "longitud" in c["note"] or "orientación" in c["note"]


def test_un_frame_que_falla_entre_varios_no_afecta_a_los_demas():
    sap = _Sap({"C1": _columna(), "V1": _viga(), "C2": _columna()})
    original = sap._sect_props

    def sect(nombre, *a):
        if nombre == "W410":
            raise TypeError("fallo raro")
        return original(nombre, *a)

    sap.PropFrame.GetSectProps = sect
    c1, v1, c2 = selected_frames_nomogramas(sap)["frames"]
    assert c1["note"] is None and c2["note"] is None
    assert v1["frame"] == "V1" and v1["area_mm2"] is None and v1["note"]
    assert v1["length_mm"] > 0


def test_sanitizar_json_recursivo():
    from bridge.nomogramas import sanitizar_json

    sucio = {"a": float("nan"), "b": [1.0, float("inf"), {"c": float("-inf")}], "d": "x", "e": True}
    limpio = sanitizar_json(sucio)
    assert limpio == {"a": None, "b": [1.0, None, {"c": None}], "d": "x", "e": True}
    _json_estricto(limpio)


def test_ruta_con_nan_responde_200_json_estricto():
    sap = _Sap({"C1": _columna()})
    sap.PropFrame.GetSectProps = lambda n, *a: (float("nan"),) * 12 + (0,)
    sap.PropMaterial.GetMPIsotropic = lambda m: (float("inf"), 0.3, 1.2e-5, 0.0, 0)
    r = _cliente(sap).get(RUTA)
    assert r.status_code == 200, r.text
    json.loads(r.text, parse_constant=lambda c: pytest.fail(f"constante no JSON: {c}"))
    assert r.json()["frames"][0]["area_mm2"] is None


def test_unidades_restauradas_si_excepcion_rara_en_frame():
    sap = _Sap({"C1": _columna()})
    sap.FrameObj.GetPoints = lambda n: (_ for _ in ()).throw(TypeError("raro"))
    (c,) = selected_frames_nomogramas(sap)["frames"]
    assert c["frame"] == "C1" and c["length_mm"] is None and c["note"]
    assert sap.units == 12 and sap.set_units == [9, 12]


def test_get_present_units_como_tupla(envolver):
    sap = _Sap({"C1": _columna()})
    sap.GetPresentUnits = lambda: (12, 0)
    out = selected_frames_nomogramas(envolver(sap))
    assert out["frames"][0]["note"] is None
    assert sap.set_units == [9, 12]


def test_get_present_units_tupla_ya_en_n_mm_c():
    sap = _Sap({"C1": _columna()}, units=9)
    sap.GetPresentUnits = lambda: (9, 0)
    selected_frames_nomogramas(sap)
    assert sap.set_units == []


# ---------------------------------------------------------------------------
# Regresión: sub-objetos invocables (como CDispatch de win32com) y código de retorno al inicio.
# Con normalizar() el modelo se envolvía y model.SelectObj pasaba a ser una función:
# «'function' object has no attribute 'GetSelected'». Forma real de SAP2000 (pywin32): código primero.
# ---------------------------------------------------------------------------

_SUBOBJETOS = ("SelectObj", "FrameObj", "PointObj", "PropFrame", "PropMaterial", "LoadPatterns")


class _CDispatchSim:
    """Imita un CDispatch de win32com: es invocable (método por defecto) y expone sus métodos como atributos."""

    def __init__(self, atributos):
        self.__dict__.update(atributos)

    def __call__(self, *args, **kwargs):
        return None


def _modelo_como_win32com(frames=None):
    """Modelo con sub-objetos invocables y código de retorno al inicio en la selección y en los patrones."""
    sap = _Sap(frames)
    for nombre in _SUBOBJETOS:
        sap.__dict__[nombre] = _CDispatchSim(vars(getattr(sap, nombre)))
    sap.SelectObj.GetSelected = lambda *a: (0, len(sap.frames), [2] * len(sap.frames), list(sap.frames))
    sap.LoadPatterns.GetNameList = lambda *a: (0, 2, ("DEAD",))  # código primero: detectado como «primero»
    return sap


def test_seleccion_con_objeto_invocable_no_se_envuelve():
    sap = _modelo_como_win32com({"C1": _columna(), "V1": _viga()})
    out = selected_frames_nomogramas(sap)
    assert [f["frame"] for f in out["frames"]] == ["C1", "V1"]
    assert [f["section"] for f in out["frames"]] == ["W360", "W410"]
    assert sap.units == 12  # unidades restauradas tras leer


def test_ruta_http_con_objeto_invocable_responde_200():
    sap = _modelo_como_win32com({"C1": _columna()})
    r = _cliente(sap).get(RUTA)
    assert r.status_code == 200, r.text
    assert r.json()["frames"][0]["frame"] == "C1"


def test_coordenadas_con_codigo_primero_dan_la_longitud_correcta():
    sap = _modelo_como_win32com({"C1": _columna()})
    sap.PointObj.GetCoordCartesian = lambda p: (0, *sap.coords[p])
    frame = selected_frames_nomogramas(sap)["frames"][0]
    assert frame["length_mm"] == pytest.approx(4000.0)
    assert frame["orientacion"] == "vertical"


def test_coordenadas_con_codigo_primero_y_sap_error_es_nota_o_409():
    sap = _modelo_como_win32com({"C1": _columna()})
    sap.PointObj.GetCoordCartesian = lambda p: (5, 0.0, 0.0, 0.0)
    with pytest.raises(RuntimeError, match=r"GetCoordCartesian.*5"):
        selected_frames_nomogramas(sap)


# Respuesta real de PropFrame.GetSectProps (IN20X30.6, A36): código al inicio y 12 salidas.
# Orden de SAP2000: Area, As2, As3, Torsion, I22, I33, S22, S33, Z22, Z33, R22, R33. Solo se usan
# los índices 0, 4 y 5; la cola (S33 y siguientes) no está en el JSON y se rellena con 0.0.
_SECTPROPS_REAL = (
    3900.0,
    1000.0,
    2500.0,
    103168.75,
    5626874.99,
    29530000.0,
    75025.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
)


@pytest.mark.parametrize("ret_primero", [True, False])
def test_propiedades_seccion_reales(ret_primero):
    from bridge.nomogramas import _propiedades_seccion

    sap = _Sap({})
    sap.PropFrame.GetSectProps = lambda *a: (0, *_SECTPROPS_REAL) if ret_primero else (*_SECTPROPS_REAL, 0)
    area, i33, i22 = _propiedades_seccion(sap, "IN20X30.6")
    assert area == 3900.0
    assert i22 == 5626874.99
    assert i33 == 29530000.0
    assert i33 > i22  # el eje fuerte tiene la mayor inercia


@pytest.mark.parametrize("ret_primero", [True, False])
def test_material_y_modulo_e_reales(ret_primero):
    from bridge.nomogramas import _material, _modulo_e

    sap = _Sap({})
    sap.PropFrame.GetMaterial = lambda n: (0, "A36") if ret_primero else ("A36", 0)
    sap.PropMaterial.GetMPIsotropic = (
        (lambda m: (0, 199947.98, 0.3, 1.17e-5, 76903.07))
        if ret_primero
        else (lambda m: (199947.98, 0.3, 1.17e-5, 76903.07, 0))
    )
    assert _material(sap, "IN20X30.6") == "A36"
    assert _modulo_e(sap, "A36") == 199947.98


@pytest.mark.parametrize("ret_primero", [True, False])
def test_releases_reales_orden_i_j_m2_m3(ret_primero):
    from bridge.nomogramas import _releases

    ii = [False, False, False, False, True, False]  # i: M2 liberado
    jj = [False, False, False, False, False, True]  # j: M3 liberado
    sap = _Sap({})
    sap.FrameObj.GetReleases = lambda n: (
        (0, ii, jj, [0.0] * 6, [0.0] * 6) if ret_primero else (ii, jj, [0.0] * 6, [0.0] * 6, 0)
    )
    assert _releases(sap, "C1") == {"i_m2": True, "i_m3": False, "j_m2": False, "j_m3": True}


def test_get_modifiers_pasa_value_de_8_ceros_y_lee_i22_i33():
    from bridge.nomogramas import _modificadores

    llamadas = []
    sap = _Sap({})

    def mod(nombre, valor=None):
        llamadas.append((nombre, valor))
        return (0, (1.0, 1.0, 1.0, 1.0, 0.7, 0.35, 1.0, 1.0))

    sap.FrameObj.GetModifiers = mod
    assert _modificadores(sap, "C1") == {"i22": 0.7, "i33": 0.35}
    assert llamadas == [("C1", [0.0] * 8)]


def test_get_modifiers_con_codigo_distinto_de_cero_es_null_con_nota():
    sap = _modelo_como_win32com({"C1": _columna()})
    sap.FrameObj.GetModifiers = lambda *a: (1, None)
    frame = selected_frames_nomogramas(sap)["frames"][0]
    assert frame["modificadores"] == {"i22": None, "i33": None}
    assert "modificadores" in frame["note"]
    assert frame["length_mm"] == pytest.approx(4000.0)  # el resto de la lectura sigue


# --- Selección vacía o sin barras: frames [] y nunca 409 ---------------------------------

_SIN_OBJETOS = [
    pytest.param((0, 0, None, None), id="sin-objetos-codigo-primero"),
    pytest.param((None, None, 0), id="sin-objetos-codigo-ultimo"),
    pytest.param((0, 0, [], []), id="arrays-vacios-codigo-primero"),
    pytest.param(([], [], 0), id="arrays-vacios-codigo-ultimo"),
    pytest.param((0, [], [], 0), id="arrays-vacios-codigo-en-ambos-extremos"),
]


def _sap_con_seleccion(raw, frames=None):
    sap = _Sap(frames)
    sap.SelectObj.GetSelected = lambda *a: raw
    return sap


@pytest.mark.parametrize("raw", _SIN_OBJETOS)
def test_seleccion_vacia_devuelve_frames_vacio(envolver, raw):
    sap = _sap_con_seleccion(raw)
    sap.FrameObj.GetSection = lambda *a: pytest.fail("sin objetos no debe leerse ningún frame")
    out = selected_frames_nomogramas(envolver(sap))
    assert out == {"modelo": "torre.sdb", "unidades": "N_mm_C", "frames": []}
    assert sap.units == 12  # la lectura de unidades y su restauración también se ejecutan


@pytest.mark.parametrize("raw", _SIN_OBJETOS)
def test_ruta_http_seleccion_vacia_responde_200_sin_409(raw):
    sap = _sap_con_seleccion(raw)
    r = _cliente(sap).get(RUTA)
    assert r.status_code == 200, r.text
    assert r.json() == {"modelo": "torre.sdb", "unidades": "N_mm_C", "frames": []}
    assert sap.units == 12


def test_seleccion_vacia_se_lee_con_la_forma_de_esbelteces():
    llamadas = []

    def get_selected(*args):
        llamadas.append(args)
        return (0, 0, None, None)

    sap = _sap_con_seleccion(None)
    sap.SelectObj.GetSelected = get_selected
    assert selected_frames_nomogramas(sap)["frames"] == []
    assert llamadas == [(0, [], [])]


def test_seleccion_sin_barras_devuelve_frames_vacio_y_lo_registra(caplog):
    sap = _sap_con_seleccion((0, 1, [1], ["J1"]))  # código primero; un nudo seleccionado
    sap.FrameObj.GetSection = lambda *a: pytest.fail("sin barras no debe leerse ningún frame")
    with caplog.at_level(logging.INFO, logger="bridge.nomogramas"):
        out = selected_frames_nomogramas(sap)
    assert out == {"modelo": "torre.sdb", "unidades": "N_mm_C", "frames": []}
    assert "la selección no contiene barras (frames)" in caplog.text
    assert sap.units == 12


def test_seleccion_mixta_solo_devuelve_las_barras(envolver):
    sap = _sap_con_seleccion((0, 2, [1, 2], ["J1", "V1"]), {"V1": _viga()})
    out = selected_frames_nomogramas(envolver(sap))
    assert [f["frame"] for f in out["frames"]] == ["V1"]
    assert out["frames"][0]["section"] == "W410"
    assert sap.units == 12
