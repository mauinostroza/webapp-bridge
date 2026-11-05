"""Tests de bridge/placa_base.py: port de las acciones de Placa Base
(bridge/tests/test_acciones_sap.py de placa_base_simple) con un COM falso propio, con el
mismo patrón de tests/test_bridge_hormigon.py.

El doble sigue la convención de comtypes (código de retorno AL FINAL). Cada prueba se
ejecuta también con el proxy ``_RetFirst`` (código AL INICIO, forma pywin32): ambos
órdenes deben dar el mismo resultado.

Verifican el contrato JSON y el flujo de la GUI con COM falso; no demuestran el
comportamiento de SAP2000 real.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import ActionValidationError, install_routes
from bridge.hosted import PLACA_BASE_ORIGIN
from bridge.placa_base import (
    PlacaBaseError,
    _filtrar_nombres,
    columna_en_nodo,
    listar_cargas,
    listar_nodos,
    reacciones,
    resaltar_nodos,
    seleccionar_cargas,
)

CONTRATOS = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
PREFIJO = "/v1/sap/actions/placa-base"
UNIDADES_TONF_M = 12


class _RetFirst:
    """Proxy que mueve el código de retorno al inicio de toda tupla (forma pywin32)."""

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


class _Lista:
    """Objeto con ``GetNameList`` -> ``(n, nombres, código)``."""

    def __init__(self, modelo, nombres_de):
        self._modelo = modelo
        self._nombres_de = nombres_de

    def GetNameList(self, *_args):
        nombres = self._nombres_de(self._modelo)
        return (len(nombres), tuple(nombres), self._modelo.codigo("GetNameList"))


class _Setup:
    def __init__(self, modelo):
        self._m = modelo

    def _marca(self, conjunto, nombre):
        codigo = 1 if nombre in self._m.ilegibles else 0
        return (nombre in conjunto, codigo)

    def GetCaseSelectedForOutput(self, nombre):
        return self._marca(self._m.salida_casos, nombre)

    def GetComboSelectedForOutput(self, nombre):
        return self._marca(self._m.salida_combos, nombre)

    def DeselectAllCasesAndCombosForOutput(self):
        self._m.llamadas.append(("deseleccionar",))
        self._m.salida_casos.clear()
        self._m.salida_combos.clear()
        return self._m.codigo("DeselectAll")

    def SetCaseSelectedForOutput(self, nombre):
        self._m.llamadas.append(("caso", nombre))
        codigo = self._m.codigo("SetCaseSelectedForOutput")
        if codigo == 0:
            self._m.salida_casos.add(nombre)
        return codigo

    def SetComboSelectedForOutput(self, nombre):
        self._m.llamadas.append(("combo", nombre))
        codigo = self._m.codigo("SetComboSelectedForOutput")
        if codigo == 0:
            self._m.salida_combos.add(nombre)
        return codigo


class _Resultados:
    def __init__(self, modelo):
        self.Setup = _Setup(modelo)
        self._m = modelo

    def JointReact(self, nodo, _tipo):
        return self._m.joint_react(nodo)


class _Puntos:
    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_args):
        return (len(self._m.nodos), tuple(self._m.nodos), 0)

    def GetRestraint(self, nodo):
        return ([self._m.restringidos[nodo]] * 3 + [False] * 3, self._m.codigo("GetRestraint"))

    def GetConnectivity(self, nodo):
        asociadas = [f for f, (a, _b) in self._m.frames.items() if a == nodo]
        return (
            len(asociadas),
            tuple([2] * len(asociadas)),
            tuple(asociadas),
            self._m.codigo("GetConnectivity"),
        )

    def GetCoordCartesian(self, nodo):
        coords = self._m.coords[nodo]
        if self._m.coords_enteras:
            coords = tuple(int(v) for v in coords)
        return (*coords, self._m.codigo("GetCoordCartesian"))

    def SetSelected(self, nodo, _valor):
        codigo = self._m.codigo("SetSelected")
        if codigo == 0:
            self._m.seleccion.append((1, nodo))
        return codigo


class _Seleccion:
    def __init__(self, modelo):
        self._m = modelo

    def GetSelected(self):
        tipos = tuple(t for t, _n in self._m.seleccion)
        nombres = tuple(n for _t, n in self._m.seleccion)
        return (len(nombres), tipos, nombres, self._m.codigo("GetSelected"))

    def ClearSelection(self):
        self._m.seleccion.clear()
        return self._m.codigo("ClearSelection")


class _Frames:
    def __init__(self, modelo):
        self._m = modelo

    def GetPoints(self, frame):
        return (*self._m.frames[frame], self._m.codigo("GetPoints"))

    def GetSection(self, _frame):
        return ("W14x90", False, self._m.codigo("GetSection"))

    def GetLocalAxes(self, _frame):
        return (self._m.angulo, False, self._m.codigo("GetLocalAxes"))


class _PropFrame:
    def __init__(self, modelo):
        self._m = modelo

    def GetISection(self, _seccion):
        # CSI: FileName, MatProp, t3, t2, tf, tw, t2b, tfb, color, notes, guid
        return (
            "",
            "A992Fy50",
            0.356,
            0.369,
            0.018,
            0.011,
            0.369,
            0.018,
            -1,
            "",
            "g",
            self._m.codigo("GetISection"),
        )


class _Vista:
    def __init__(self, modelo):
        self._m = modelo

    def RefreshView(self):
        return self._m.codigo("RefreshView")


class ModeloFalso:
    """SapModel falso: dos columnas (nodos 1 y 2, empotrados), un nodo libre (3), una
    viga horizontal (B1, entre 1 y 2) que no es columna y dos casos y dos combinaciones.

    ``codigos`` fuerza un código de retorno distinto de 0 en el método indicado.
    ``ilegibles`` son casos o combinaciones cuya marca de salida SAP no informa.
    """

    def __init__(
        self,
        *,
        unidades=6,
        con_reacciones=True,
        angulo=0.0,
        codigos=None,
        ilegibles=(),
        seleccion=((1, "1"), (1, "3")),
        fallo_lectura=False,
        arrays_desiguales=False,
        coords_enteras=False,
        archivo="C:/proyectos/edificio.sdb",
    ):
        self.codigos = dict(codigos or {})
        self.coords_enteras = coords_enteras
        self.ilegibles = set(ilegibles)
        self.unidades = unidades
        self.historial_unidades: list[int] = []
        self.salida_casos = {"DEAD"}
        self.salida_combos = {"COMB1"}
        self.seleccion = list(seleccion)
        self.llamadas: list[tuple] = []
        self.lecturas_joint_react: list[str] = []
        self.casos = ["DEAD", "LIVE", "SISMO"]
        self.combinaciones = ["COMB1", "COMB2"]
        self.nodos = ["1", "2", "3"]
        self.restringidos = {"1": True, "2": True, "3": False}
        self.coords = {
            "1": (0.0, 0.0, 0.0),
            "2": (5.0, 0.0, 0.0),
            "3": (9.0, 0.0, 0.0),
            "T1": (0.0, 0.0, 4.0),
            "T2": (5.0, 0.0, 4.0),
        }
        self.frames = {"C1": ("1", "T1"), "C2": ("2", "T2"), "B1": ("1", "2")}
        self.angulo = angulo
        self.con_reacciones = con_reacciones
        self.fallo_lectura = fallo_lectura
        self.arrays_desiguales = arrays_desiguales
        self.archivo = archivo

        self.LoadPatterns = _Lista(self, lambda _m: ["DEAD"])
        self.LoadCases = _Lista(self, lambda m: m.casos)
        self.RespCombo = _Lista(self, lambda m: m.combinaciones)
        self.Results = _Resultados(self)
        self.PointObj = _Puntos(self)
        self.SelectObj = _Seleccion(self)
        self.FrameObj = _Frames(self)
        self.PropFrame = _PropFrame(self)
        self.View = _Vista(self)

    def codigo(self, clave: str) -> int:
        return self.codigos.get(clave, 0)

    # --- unidades y archivo ----------------------------------------------
    def GetPresentUnits(self):
        # CSI: eUnits GetPresentUnits() devuelve el valor directamente, sin código.
        return self.unidades

    def SetPresentUnits(self, unidades):
        self.historial_unidades.append(unidades)
        if unidades != UNIDADES_TONF_M and self.codigo("RestaurarUnidades"):
            return 1
        self.unidades = unidades
        return 0

    def GetModelFilename(self):
        return (self.archivo, 0)

    # --- reacciones --------------------------------------------------------
    def joint_react(self, nodo):
        self.lecturas_joint_react.append(nodo)
        if self.fallo_lectura:
            raise RuntimeError("COM caído al leer JointReact")
        codigo = self.codigo("JointReact")
        if not self.con_reacciones:
            return (0, *(() for _ in range(11)), codigo)
        base = float(nodo)
        casos = ["COMB1", "COMB2"]
        n = len(casos)
        f1 = [base * 1.0, base * 2.0]
        if self.arrays_desiguales:
            f1 = f1[:1]
        return (
            n,
            [nodo] * n,  # Obj
            [nodo] * n,  # Elm
            casos,  # LoadCase
            ["", ""],  # StepType
            [0.0] * n,  # StepNum
            f1,  # F1 = Vx
            [base * 3.0, base * 4.0],  # F2 = Vy
            [base * 5.0, base * 6.0],  # F3 = P
            [base * 7.0, base * 8.0],  # M1 = Mx
            [base * 9.0, base * 10.0],  # M2 = My
            [base * 11.0, base * 12.0],  # M3 = Mz
            codigo,
        )


def _sap(orden: str, **opciones):
    """Devuelve ``(modelo, sap)``: ``modelo`` es el doble para leer el estado; ``sap`` es el
    objeto que se pasa a las funciones, con el código al final o al inicio según ``orden``."""
    modelo = ModeloFalso(**opciones)
    return modelo, (modelo if orden == "codigo_al_final" else _RetFirst(modelo))


@pytest.fixture(params=["codigo_al_final", "codigo_al_inicio"])
def orden(request):
    return request.param


# ---------------------------------------------------------------------------
# Cargas y selección de salida
# ---------------------------------------------------------------------------


def test_listar_cargas_devuelve_casos_combinaciones_y_seleccion(orden):
    _, sap = _sap(orden)
    assert listar_cargas(sap) == {
        "modelo": "edificio.sdb",
        "casos": ["DEAD", "LIVE", "SISMO"],
        "combinaciones": ["COMB1", "COMB2"],
        "seleccion": {"casos": ["DEAD"], "combinaciones": ["COMB1"]},
        "seleccion_desconocida": False,
    }


def test_marca_ilegible_se_avisa_y_no_figura_como_seleccionada(orden):
    _, sap = _sap(orden, ilegibles=("DEAD",))
    r = listar_cargas(sap)
    assert r["seleccion_desconocida"] is True
    assert r["seleccion"] == {"casos": [], "combinaciones": ["COMB1"]}


def test_modelo_sin_guardar_se_nombra_asi(orden):
    _, sap = _sap(orden, archivo="")
    assert listar_cargas(sap)["modelo"] == "Modelo SAP2000 sin guardar"


def test_seleccionar_cargas_marca_en_sap_y_descarta_inexistentes(orden):
    modelo, sap = _sap(orden)
    r = seleccionar_cargas(sap, ["LIVE", "FANTASMA"], ["COMB2", "COMB2"])
    assert r == {
        "modelo": "edificio.sdb",
        "casos": ["LIVE"],
        "combinaciones": ["COMB2"],
        "ignorados": {"casos": ["FANTASMA"], "combinaciones": []},
    }
    assert modelo.salida_casos == {"LIVE"} and modelo.salida_combos == {"COMB2"}
    assert modelo.llamadas[0] == ("deseleccionar",)


def test_seleccionar_cargas_exige_al_menos_una_y_que_exista(orden):
    modelo, sap = _sap(orden)
    with pytest.raises(ActionValidationError, match="Seleccione al menos un estado de carga"):
        seleccionar_cargas(sap, [], [])
    with pytest.raises(ActionValidationError, match="Ninguno"):
        seleccionar_cargas(sap, ["NOEXISTE"], [])
    assert modelo.llamadas == []  # no se tocó SAP2000


def test_seleccionar_cargas_informa_el_rechazo_de_sap(orden):
    _, sap = _sap(orden, codigos={"SetCaseSelectedForOutput": 5})
    with pytest.raises(PlacaBaseError, match="código 5") as excinfo:
        seleccionar_cargas(sap, ["LIVE"], [])
    assert excinfo.value.detail["code"] == "sap_error"
    assert "puede haber quedado incompleta" in excinfo.value.detail["message"]


def test_filtrar_nombres_copia_de_la_gui():
    assert _filtrar_nombres([" A ", "B", "A", "Z"], ["A", "B", "C"]) == (["A", "B"], ["Z"])
    assert _filtrar_nombres([], ["A"]) == ([], [])


# ---------------------------------------------------------------------------
# Nodos
# ---------------------------------------------------------------------------


def test_listar_nodos_distingue_seleccionados_restringidos_y_para_diseno(orden):
    modelo, sap = _sap(orden)
    assert listar_nodos(sap) == {
        "modelo": "edificio.sdb",
        "seleccionados": ["1", "3"],
        "restringidos": ["1", "2"],
        "para_diseno": ["1"],  # seleccionados con restricción (el 3 está libre)
    }
    modelo.seleccion.clear()
    assert listar_nodos(sap)["para_diseno"] == ["1", "2"]


def test_listar_nodos_ignora_objetos_que_no_son_puntos(orden):
    _, sap = _sap(orden, seleccion=((2, "C1"), (1, "1"), (5, "S1")))
    assert listar_nodos(sap)["seleccionados"] == ["1"]


def test_restriccion_ilegible_no_descarta_el_nodo(orden):
    """Como el original: si SAP no informa la restricción, el nodo se conserva."""
    _, sap = _sap(orden, codigos={"GetRestraint": 1})
    assert listar_nodos(sap)["restringidos"] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Reacciones
# ---------------------------------------------------------------------------


def test_reacciones_en_tonf_m_con_el_orden_de_columnas_de_la_gui(orden):
    modelo, sap = _sap(orden, unidades=6)
    r = reacciones(sap, ["1"])
    assert r["unidades"] == "tonf-m" and r["ejes"] == "globales" and r["angulo_deg"] is None
    primera, segunda = r["reacciones"]
    assert primera == {
        "node": "1",
        "load_name": "COMB1",
        "step_type": "",
        "step_num": 0.0,
        "P": 5.0,
        "Vx": 1.0,
        "Vy": 3.0,
        "Mx": 7.0,
        "My": 9.0,
        "Mz": 11.0,
    }
    assert segunda["load_name"] == "COMB2" and segunda["P"] == 6.0
    # Fija tonf-m (12) mientras lee y restaura las unidades del usuario (6).
    assert modelo.historial_unidades == [12, 6]
    assert modelo.unidades == 6


def test_reacciones_solo_de_nodos_restringidos(orden):
    _, sap = _sap(orden)
    r = reacciones(sap, ["1", "3", "2"])
    assert {fila["node"] for fila in r["reacciones"]} == {"1", "2"}


def test_reacciones_no_leen_dos_veces_un_nodo_repetido(orden):
    modelo, sap = _sap(orden)
    reacciones(sap, ["1", "1"])
    assert modelo.lecturas_joint_react == ["1"]


def test_reacciones_rotan_al_eje_local_pero_no_mz(orden):
    _, sap = _sap(orden)
    sin = reacciones(sap, ["1"])["reacciones"][0]
    con = reacciones(sap, ["1"], angulo_deg=90)
    assert con["ejes"] == "locales" and con["angulo_deg"] == 90.0
    fila = con["reacciones"][0]
    theta = math.radians(-90)
    assert fila["Vx"] == pytest.approx(sin["Vx"] * math.cos(theta) + sin["Vy"] * math.sin(theta))
    assert fila["Vy"] == pytest.approx(-sin["Vx"] * math.sin(theta) + sin["Vy"] * math.cos(theta))
    assert fila["Mx"] == pytest.approx(sin["Mx"] * math.cos(theta) + sin["My"] * math.sin(theta))
    assert fila["P"] == sin["P"] and fila["Mz"] == sin["Mz"]


def test_angulo_menor_a_medio_grado_no_rota(orden):
    _, sap = _sap(orden)
    r = reacciones(sap, ["1"], angulo_deg=0.3)
    assert r["ejes"] == "globales"
    assert r["reacciones"][0]["Vx"] == 1.0


def test_reacciones_marca_la_seleccion_solo_si_se_envia(orden):
    modelo, sap = _sap(orden)
    reacciones(sap, ["1"])
    assert modelo.llamadas == []
    reacciones(sap, ["1"], combinaciones=["COMB1", "COMB2"])
    assert modelo.salida_casos == set() and modelo.salida_combos == {"COMB1", "COMB2"}


def test_reacciones_sin_resultados_es_error_y_restaura_unidades(orden):
    modelo, sap = _sap(orden, con_reacciones=False)
    with pytest.raises(PlacaBaseError, match="no devolvió reacciones"):
        reacciones(sap, ["1"])
    assert modelo.unidades == 6


def test_unidades_se_restauran_aunque_falle_la_lectura(orden):
    modelo, sap = _sap(orden, fallo_lectura=True)
    with pytest.raises(RuntimeError, match="COM caído"):
        reacciones(sap, ["1"])
    assert modelo.historial_unidades == [12, 6]
    assert modelo.unidades == 6


def test_error_al_restaurar_las_unidades_se_eleva(orden):
    modelo, sap = _sap(orden, codigos={"RestaurarUnidades": 1})
    with pytest.raises(PlacaBaseError, match="No se pudieron restaurar las unidades"):
        reacciones(sap, ["1"])
    assert modelo.unidades == UNIDADES_TONF_M  # la restauración no llegó a aplicarse


def test_reacciones_con_arrays_de_distinto_tamano_es_error(orden):
    _, sap = _sap(orden, arrays_desiguales=True)
    with pytest.raises(PlacaBaseError, match="distinto tamaño"):
        reacciones(sap, ["1"])


def test_reacciones_con_codigo_de_error_en_joint_react_es_error(orden):
    _, sap = _sap(orden, codigos={"JointReact": 3})
    with pytest.raises(PlacaBaseError, match="JointReact.*código 3"):
        reacciones(sap, ["1"])


# ---------------------------------------------------------------------------
# Columna en nodo
# ---------------------------------------------------------------------------


def test_columna_en_nodo_devuelve_la_seccion_i_real_y_null_si_no_hay(orden):
    modelo, sap = _sap(orden, angulo=15.0)
    col, sin_columna = columna_en_nodo(sap, ["1", "3"])["columnas"]
    assert col["nodo"] == "1"
    info = col["columna"]
    assert info["frame"] == "C1" and info["section"] == "W14x90"  # la viga B1 horizontal se descarta
    assert info["length_m"] == pytest.approx(4.0)
    assert info["local_angle_deg"] == 15.0
    assert info["depth_cm"] == pytest.approx(35.6)
    assert info["width_cm"] == pytest.approx(36.9)
    assert info["flange_thickness_cm"] == pytest.approx(1.8)
    assert info["web_thickness_cm"] == pytest.approx(1.1)
    assert sin_columna == {"nodo": "3", "columna": None}
    assert modelo.historial_unidades == [12, 6]


def test_coordenadas_enteras_con_codigo_al_inicio_se_leen_bien(orden):
    """Con el código al inicio y coordenadas enteras, la heurística de ``split_return``
    confunde un 0 de coordenada con el código: sólo ``normalizar`` lo resuelve."""
    _, sap = _sap(orden, coords_enteras=True)
    info = columna_en_nodo(sap, ["2"])["columnas"][0]["columna"]
    assert info["frame"] == "C2" and info["length_m"] == pytest.approx(4.0)


def test_columna_fallo_de_connectivity_es_error_y_no_null(orden):
    _, sap = _sap(orden, codigos={"GetConnectivity": 1})
    with pytest.raises(PlacaBaseError, match="GetConnectivity"):
        columna_en_nodo(sap, ["1"])


def test_columna_fallo_de_getpoints_es_error_y_no_null(orden):
    _, sap = _sap(orden, codigos={"GetPoints": 1})
    with pytest.raises(PlacaBaseError, match="GetPoints"):
        columna_en_nodo(sap, ["1"])


def test_columna_con_seccion_que_no_es_i_deja_las_dimensiones_nulas(orden):
    _, sap = _sap(orden, codigos={"GetISection": 1})
    info = columna_en_nodo(sap, ["1"])["columnas"][0]["columna"]
    assert info["section"] == "W14x90" and info["length_m"] == pytest.approx(4.0)
    assert info["depth_cm"] is None and info["web_thickness_cm"] is None


def test_columna_restaura_unidades_aunque_falle_la_lectura(orden):
    modelo, sap = _sap(orden, codigos={"GetConnectivity": 1})
    with pytest.raises(PlacaBaseError):
        columna_en_nodo(sap, ["1"])
    assert modelo.historial_unidades == [12, 6] and modelo.unidades == 6


# ---------------------------------------------------------------------------
# Resaltado
# ---------------------------------------------------------------------------


def test_resaltar_nodos_selecciona_solo_esos_puntos(orden):
    modelo, sap = _sap(orden)
    assert resaltar_nodos(sap, ["1", "2"]) == {"resaltado": True}
    assert modelo.seleccion == [(1, "1"), (1, "2")]


@pytest.mark.parametrize("clave", ["ClearSelection", "SetSelected", "RefreshView"])
def test_resaltar_rechaza_codigo_distinto_de_cero(orden, clave):
    _, sap = _sap(orden, codigos={clave: 1})
    with pytest.raises(PlacaBaseError, match="código 1"):
        resaltar_nodos(sap, ["1"])


def test_las_respuestas_son_json_estricto(orden):
    _, sap = _sap(orden)
    for respuesta in (
        listar_cargas(sap),
        listar_nodos(sap),
        reacciones(sap, ["1", "2"]),
        columna_en_nodo(sap, ["1"]),
    ):
        json.dumps(respuesta, allow_nan=False)


# ---------------------------------------------------------------------------
# Rutas HTTP (envoltorio call de bridge.actions.install_routes)
# ---------------------------------------------------------------------------


class _SesionFalsa:
    def __init__(self, modelo):
        self.actor = self
        self._modelo = modelo

    def call(self, fn):
        return fn()

    def _model(self):
        return self._modelo


def _cliente(sap) -> TestClient:
    app = FastAPI()
    install_routes(app, _SesionFalsa(sap), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


CASOS_CONTRATO = [
    ("GET", "cargas", None),
    ("POST", "seleccionar-cargas", {"casos": ["LIVE"]}),
    ("GET", "nodos", None),
    ("POST", "reacciones", {"nodos": ["1"]}),
    ("POST", "columna-en-nodo", {"nodos": ["1"]}),
    ("POST", "resaltar-nodos", {"nodos": ["1"]}),
]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), CASOS_CONTRATO)
def test_respuestas_coinciden_con_bridge_contracts(orden, metodo, ruta, cuerpo):
    contratos = json.loads(CONTRATOS.read_text("utf-8"))["routes"]
    _, sap = _sap(orden)
    cliente = _cliente(sap)
    url = f"{PREFIJO}/{ruta}"
    respuesta = cliente.get(url) if metodo == "GET" else cliente.post(url, json=cuerpo)
    assert respuesta.status_code == 200, respuesta.text
    cuerpo_json = respuesta.json()
    spec = contratos[f"{metodo} {url}"]
    assert set(cuerpo_json) == set(spec["keys"])
    for lista, claves in spec.get("items", {}).items():
        assert set(cuerpo_json[lista][0]) == set(claves)


def test_ruta_seleccionar_con_rechazo_de_sap_da_409(orden):
    _, sap = _sap(orden, codigos={"SetCaseSelectedForOutput": 5})
    respuesta = _cliente(sap).post(f"{PREFIJO}/seleccionar-cargas", json={"casos": ["LIVE"]})
    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"]
    assert detalle["code"] == "sap_error" and "código 5" in detalle["message"]


def test_ruta_seleccionar_sin_nombres_validos_da_422(orden):
    modelo, sap = _sap(orden)
    cliente = _cliente(sap)
    assert cliente.post(f"{PREFIJO}/seleccionar-cargas", json={}).status_code == 422
    assert cliente.post(f"{PREFIJO}/seleccionar-cargas", json={"casos": ["NOEXISTE"]}).status_code == 422
    assert modelo.llamadas == []


def test_ruta_resaltar_con_rechazo_de_sap_da_409(orden):
    _, sap = _sap(orden, codigos={"RefreshView": 2})
    respuesta = _cliente(sap).post(f"{PREFIJO}/resaltar-nodos", json={"nodos": ["1"]})
    assert respuesta.status_code == 409
    assert "RefreshView" in respuesta.json()["detail"]["message"]


@pytest.mark.parametrize(
    ("ruta", "cuerpo"),
    [
        ("reacciones", {}),
        ("reacciones", {"nodos": []}),
        ("reacciones", {"nodos": "1"}),
        ("reacciones", {"nodos": [1]}),
        ("reacciones", {"nodos": [""]}),
        ("reacciones", {"nodos": ["1"], "angulo_deg": "90"}),
        ("reacciones", {"nodos": ["1"], "angulo_deg": True}),
        ("reacciones", {"nodos": ["x" * 101]}),
        ("reacciones", {"nodos": [str(i) for i in range(501)]}),
        ("reacciones", {"nodos": ["1"], "casos": "DEAD"}),
        ("reacciones", {"nodos": ["a/b"]}),
        ("columna-en-nodo", {}),
        ("columna-en-nodo", {"nodos": []}),
        ("resaltar-nodos", {"nodos": []}),
        ("seleccionar-cargas", {"casos": ["x" * 101]}),
        ("seleccionar-cargas", {"combinaciones": ["a?b"]}),
    ],
)
def test_rutas_rechazan_payloads_invalidos_con_422_sin_tocar_sap(ruta, cuerpo):
    modelo = ModeloFalso()
    respuesta = _cliente(modelo).post(f"{PREFIJO}/{ruta}", json=cuerpo)
    assert respuesta.status_code == 422, respuesta.text
    assert modelo.llamadas == [] and modelo.historial_unidades == [] and modelo.lecturas_joint_react == []


# ---------------------------------------------------------------------------
# Autenticación y alcance (aplicación real, como tests/test_bridge_alcances.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_real(tmp_path, monkeypatch):
    # El token se lee al importar sap2000_agent: primero el entorno, después el import.
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    from bridge import pairing
    from bridge.sap2000_agent import app

    pairing.reset_state_for_tests()
    yield TestClient(app, base_url="http://127.0.0.1:8765")
    pairing.reset_state_for_tests()


def _emparejar(client, origen: str) -> str:
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": origen}).json()["request_id"]
    pairing.approve(request_id)
    return client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]


def test_sin_token_las_rutas_de_placa_base_dan_401(app_real):
    assert app_real.get(f"{PREFIJO}/nodos").status_code == 401
    assert app_real.post(f"{PREFIJO}/resaltar-nodos", json={"nodos": ["1"]}).status_code == 401


def test_origen_de_placa_base_llega_a_sus_rutas_y_no_al_grafo(app_real):
    secreto = _emparejar(app_real, PLACA_BASE_ORIGIN)
    cabeceras = {"Authorization": f"Bearer {secreto}", "Origin": PLACA_BASE_ORIGIN}
    dentro = app_real.get(f"{PREFIJO}/nodos", headers=cabeceras)
    assert dentro.status_code not in (401, 403)  # la capa de autenticación la deja pasar
    fuera = app_real.get("/v1/sap/actions/graph/model", headers=cabeceras)
    assert fuera.status_code == 403
    assert "alcance" in fuera.json()["detail"]
