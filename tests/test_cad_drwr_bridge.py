"""Tests del puente CAD (bridge/cad_drwr.py) con COM FALSO.

En Linux no hay Windows ni CAD: pythoncom, win32com, win32api, win32con,
win32gui y win32process se sustituyen por módulos falsos en sys.modules, y
os.name se fuerza a "nt" para que cad_com_live._com() los use. Los objetos
del CAD (aplicación, documento, espacio modelo, capas) registran cada llamada
junto con el hilo que la hizo, para comprobar que todo pasa por el actor.
"""

import json
import os
import pathlib
import re
import sys
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

RAIZ = pathlib.Path(__file__).resolve().parents[1]  # workbench_module
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import cad_drwr  # noqa: E402
from bridge import cad_prims as ir  # noqa: E402

PREF = "/v1/sap/actions/cad"
HILO_COM = "cad-drwr-com"
CONTRATO = RAIZ / "web" / "src" / "cad_drwr" / "bridge_contracts.cad_drwr.json"
NAN = float("nan")


# ------------------------------------------------------------ COM falso --


class ErrorCOM(Exception):
    """Imita pywin32.com_error: args = (hresult, mensaje)."""


class _Variant:
    def __init__(self, vt, valor):
        self.vt = vt
        self.valor = valor


class _Objeto:
    """Objeto COM genérico: acepta cualquier propiedad."""


class _Coleccion:
    def __init__(self, m, prefijo):
        self.m, self.prefijo, self.items = m, prefijo, {}

    def Item(self, nombre):
        if nombre not in self.items:
            raise ErrorCOM(-2147024809, f"No existe {nombre}")
        return self.items[nombre]

    def Add(self, nombre):
        self.m.llamar(f"{self.prefijo}.Add", (nombre,))
        obj = _Objeto()
        self.items[nombre] = obj
        return obj


class _Lineas:
    def __init__(self, m):
        self.m = m

    def Load(self, nombre):
        self.m.llamar("Linetypes.Load", (nombre,))


class _Entidad:
    def __init__(self, metodo, args):
        self.metodo, self.args = metodo, args


class FakeModelSpace:
    def __init__(self, m):
        self.m = m
        self.items = []

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i]

    def _crear(self, metodo, *args):
        self.m.llamar(metodo, args)
        e = _Entidad(metodo, args)
        self.items.append(e)
        return e

    def AddLine(self, p1, p2):
        return self._crear("AddLine", p1, p2)

    def AddLightWeightPolyline(self, pts):
        return self._crear("AddLightWeightPolyline", pts)

    def AddCircle(self, c, r):
        return self._crear("AddCircle", c, r)

    def AddArc(self, c, r, a1, a2):
        return self._crear("AddArc", c, r, a1, a2)

    def AddSolid(self, p1, p2, p3, p4):
        return self._crear("AddSolid", p1, p2, p3, p4)

    def AddText(self, s, pt, h):
        return self._crear("AddText", s, pt, h)

    def AddDimRotated(self, p1, p2, base, ang):
        return self._crear("AddDimRotated", p1, p2, base, ang)

    def AddHatch(self, *args):
        return self._crear("AddHatch", *args)


class FakeDoc:
    def __init__(self, m):
        self.m = m
        self.Name = "Dibujo1.dwg"
        self.vars = {"USERR1": 0.0, "USERR2": 0.0, "USERR3": 0.0}
        self.Layers = _Coleccion(m, "Layers")
        self.TextStyles = _Coleccion(m, "TextStyles")
        self.Linetypes = _Lineas(m)
        self.ModelSpace = FakeModelSpace(m)

    def GetVariable(self, nombre):
        return self.vars.get(nombre, 0.0)

    def SetVariable(self, nombre, valor):
        self.m.llamar("SetVariable", (nombre, valor))
        self.vars[nombre] = valor

    def Regen(self, *args):
        self.m.llamar("Regen", args)

    def Activate(self):
        self.m.llamar("Activate")

    def SendCommand(self, comando):
        """Imita el comando AutoLISP de selección de punto de cad_com_live."""
        self.m.llamar("SendCommand", (comando,))
        marcador = int(re.search(r'setvar "USERR3" (\d+)', comando).group(1))
        self.vars["USERR3"] = float(marcador)  # el LISP fija el marcador
        modo = self.m.modo_pick
        if modo == "com":
            raise ErrorCOM(-2147417851, "El servidor lanzó una excepción")
        if modo == "ok":
            x, y = self.m.punto
            self.vars.update(USERR1=x, USERR2=y, USERR3=float(-marcador))
        elif modo == "cancel":
            self.vars["USERR3"] = 0.0
        # "silencio": el comando queda esperando el clic del usuario


class FakeApp:
    def __init__(self, m, doc):
        self.m = m
        self.Name = "ZWCAD"
        self.Version = "25.0"
        self.HWnd = 4242
        self.ActiveDocument = doc
        self.Visible = True
        self.WindowState = 0

    def ZoomExtents(self):
        self.m.llamar("ZoomExtents")


class Mundo:
    """Estado del CAD falso y registro de llamadas."""

    def __init__(self):
        self.llamadas = []  # (hilo, método, args)
        self.fallos = {}  # método -> excepción que lanza
        self.apps = {}  # ProgID -> aplicación falsa
        self.modo_pick = "ok"  # ok | cancel | com | silencio
        self.punto = (0.0, 0.0)
        self.coinit = []  # (hilo, flag) de CoInitializeEx
        self.doc = None
        self._lock = threading.Lock()

    def llamar(self, metodo, args=()):
        with self._lock:
            self.llamadas.append((threading.current_thread().name, metodo, args))
        if metodo in self.fallos:
            raise self.fallos[metodo]

    def metodos(self):
        return [met for _, met, _ in self.llamadas]

    def argumentos(self, metodo):
        return [args for _, met, args in self.llamadas if met == metodo]

    def conectar_zwcad(self):
        self.doc = FakeDoc(self)
        self.apps["ZWCAD.Application"] = FakeApp(self, self.doc)


def _instalar_com(monkeypatch, m):
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.VT_ARRAY = 0x2000
    pythoncom.VT_R8 = 5
    pythoncom.VT_DISPATCH = 9
    pythoncom.COINIT_APARTMENTTHREADED = 2

    def CoInitializeEx(flag):
        m.coinit.append((threading.current_thread().name, flag))

    pythoncom.CoInitializeEx = CoInitializeEx
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    pythoncom.PumpWaitingMessages = lambda: None

    def GetActiveObject(progid):
        if progid in m.apps:
            return m.apps[progid]
        raise ErrorCOM(-2147221021, "Operación no disponible")

    client = types.ModuleType("win32com.client")
    client.GetActiveObject = GetActiveObject
    client.VARIANT = _Variant
    win32com = types.ModuleType("win32com")
    win32com.client = client

    win32api = types.ModuleType("win32api")
    win32api.GetCurrentThreadId = lambda: 1
    win32con = types.ModuleType("win32con")
    win32con.SW_RESTORE = 9
    win32gui = types.ModuleType("win32gui")
    win32gui.IsIconic = lambda h: False
    win32gui.GetForegroundWindow = lambda: 0
    win32gui.BringWindowToTop = lambda h: None
    win32gui.SetForegroundWindow = lambda h: None
    win32process = types.ModuleType("win32process")
    win32process.GetWindowThreadProcessId = lambda h: (2, 3)
    win32process.AttachThreadInput = lambda a, b, c: True

    modulos = {
        "pythoncom": pythoncom,
        "win32com": win32com,
        "win32com.client": client,
        "win32api": win32api,
        "win32con": win32con,
        "win32gui": win32gui,
        "win32process": win32process,
    }
    for nombre, mod in modulos.items():
        monkeypatch.setitem(sys.modules, nombre, mod)
    monkeypatch.setattr(os, "name", "nt")


@pytest.fixture
def mundo(monkeypatch):
    m = Mundo()
    _instalar_com(monkeypatch, m)
    return m


@pytest.fixture
def cliente(mundo):
    app = FastAPI()
    cad_drwr.install_routes(app)
    return TestClient(app), mundo


# ------------------------------------------------------------- ayudas --


def post(cli, ruta, cuerpo):
    return cli.post(PREF + ruta, json=cuerpo)


def abrir(cli, n_total=10, origen=None, th=3.5):
    cuerpo = {"confirmed": True, "n_total": n_total, "th": th}
    if origen is not None:
        cuerpo["origen"] = origen
    r = post(cli, "/draw/begin", cuerpo)
    assert r.status_code == 200, r.text
    return r.json()["sesion"]


def linea(a, b, l=ir.L_CONC):
    return {"t": "line", "a": list(a), "b": list(b), "l": l}


def circulo(c, r, l=ir.L_ACERO):
    return {"t": "circle", "c": list(c), "r": r, "l": l}


def texto(p, s, h=3.5, l=ir.L_TXT):
    return {"t": "text", "p": list(p), "s": s, "h": h, "l": l}


def cota(a, b, base, txt="100", th=3.0):
    return {
        "t": "dim",
        "a": list(a),
        "b": list(b),
        "base": list(base),
        "v": False,
        "l": ir.L_ACOT,
        "txt": txt,
        "th": th,
    }


def contrato():
    return json.loads(CONTRATO.read_text(encoding="utf-8"))["routes"]


def claves_contrato(ruta):
    return sorted(contrato()[f"POST {PREF}{ruta}"]["keys"])


def solo_msg(r):
    detalle = r.json()["detail"]
    assert isinstance(detalle, list) and detalle, r.text
    return detalle[0]["msg"]


# ------------------------------------------------------------- status --


def test_status_sin_cad(cliente):
    cli, _ = cliente
    r = post(cli, "/status", {})
    assert r.status_code == 200
    d = r.json()
    assert d["conectado"] is False
    assert d["programa"] is None and d["version"] is None and d["documento"] is None
    assert "No se encontró AutoCAD, ZWCAD o BricsCAD" in d["detalle"]
    assert sorted(d) == claves_contrato("/status")


def test_status_con_cad(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    r = post(cli, "/status", {})
    assert r.status_code == 200
    assert r.json() == {
        "conectado": True,
        "programa": "ZWCAD",
        "version": "25.0",
        "documento": "Dibujo1.dwg",
        "detalle": "ZWCAD | versión 25.0 | documento: Dibujo1.dwg",
    }
    assert sorted(r.json()) == claves_contrato("/status")


def test_status_fuera_de_windows_informa_el_motivo(cliente, monkeypatch):
    cli, _ = cliente
    monkeypatch.setattr(os, "name", "posix")
    d = post(cli, "/status", {}).json()
    assert d["conectado"] is False
    assert "solo está disponible en Windows" in d["detalle"]


def test_status_rechaza_campos_extra(cliente):
    cli, _ = cliente
    r = post(cli, "/status", {"x": 1})
    assert r.status_code == 422
    assert solo_msg(r) == "Campo no admitido"


# --------------------------------------------------------------- pick --


def test_pick_feliz(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.punto = (123.5, -4.25)
    r = post(cli, "/pick", {"mensaje": "Indique el punto"})
    assert r.status_code == 200
    assert r.json() == {"x": 123.5, "y": -4.25}
    assert sorted(r.json()) == claves_contrato("/pick")
    comandos = [args[0] for args in m.argumentos("SendCommand")]
    assert any("Indique el punto" in c for c in comandos)


def test_pick_cancelado_409(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.modo_pick = "cancel"
    r = post(cli, "/pick", {})
    assert r.status_code == 409
    assert r.json()["detail"] == "Selección de punto cancelada."


def test_pick_error_com_409_con_detalle_real(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.modo_pick = "com"
    r = post(cli, "/pick", {})
    assert r.status_code == 409
    assert "No se pudo iniciar la selección de punto" in r.json()["detail"]
    assert "El servidor lanzó una excepción" in r.json()["detail"]


def test_pick_timeout_409_y_el_actor_se_recupera(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.modo_pick = "silencio"
    r = post(cli, "/pick", {"timeout_s": 5})
    assert r.status_code == 409
    assert "no respondió en 5 s" in r.json()["detail"]
    # Simula que el usuario pulsa Esc en el CAD: el comando termina y el
    # actor queda libre. Una llamada posterior debe responder.
    m.doc.vars["USERR3"] = 0.0
    r2 = post(cli, "/status", {})
    assert r2.status_code == 200 and r2.json()["conectado"] is True


def test_pick_rechaza_timeout_fuera_de_rango(cliente):
    cli, _ = cliente
    r = post(cli, "/pick", {"timeout_s": 4})
    assert r.status_code == 422
    assert solo_msg(r) == "Debe ser mayor o igual que 5"


# -------------------------------------------------------------- begin --


def test_begin_sin_confirmed_422(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    r = post(cli, "/draw/begin", {"confirmed": False, "n_total": 3, "th": 3.5})
    assert r.status_code == 422
    assert "confirmed=true" in solo_msg(r)
    assert m.metodos() == []  # no se tocó el CAD


def test_begin_sin_cad_409(cliente):
    cli, _ = cliente
    r = post(cli, "/draw/begin", {"confirmed": True, "n_total": 3, "th": 3.5})
    assert r.status_code == 409
    assert "No se encontró AutoCAD" in r.json()["detail"]


def test_begin_reemplaza_la_sesion_anterior(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    s1 = abrir(cli)
    s2 = abrir(cli)
    assert s1 != s2
    assert post(cli, "/draw/batch", {"sesion": s1, "ents": [linea((0, 0), (1, 1))]}).status_code == 409
    assert post(cli, "/draw/batch", {"sesion": s2, "ents": [linea((0, 0), (1, 1))]}).status_code == 200


# ---------------------------------------------------- flujo completo --


def test_flujo_completo_begin_batch_end(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    st = post(cli, "/status", {}).json()
    r = post(cli, "/draw/begin", {"confirmed": True, "origen": [1000, 500], "n_total": 4, "th": 3.5})
    assert r.status_code == 200
    begin = r.json()
    sid = begin["sesion"]

    b1 = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (100, 0)), circulo((10, 10), 5)]})
    b2 = post(
        cli,
        "/draw/batch",
        {"sesion": sid, "ents": [texto((5, 5), "HOLA"), cota((0, 0), (100, 0), (50, -20))]},
    )
    assert b1.status_code == 200 and b2.status_code == 200
    assert b1.json() == {"creadas": 2, "omitidas": 0, "errores": []}
    assert b2.json() == {"creadas": 2, "omitidas": 0, "errores": []}

    fin = post(cli, "/draw/end", {"sesion": sid})
    assert fin.status_code == 200
    d = fin.json()
    assert d["creadas"] == 4 and d["omitidas"] == 0
    assert d["documento"] == "Dibujo1.dwg"
    assert d["resumen"] == "ZWCAD — documento: Dibujo1.dwg — 4 entidades creadas, 0 omitidas"

    # Claves reales de cada respuesta == contrato web.
    assert sorted(st) == claves_contrato("/status")
    assert sorted(begin) == claves_contrato("/draw/begin")
    assert sorted(b1.json()) == claves_contrato("/draw/batch")
    assert sorted(d) == claves_contrato("/draw/end")

    # Capas y variables de cota, luego geometría, luego regeneración.
    assert ("DIMTXT", 3.5) in m.argumentos("SetVariable")
    assert "Layers.Add" in m.metodos()
    assert m.metodos().index("AddLine") < m.metodos().index("Regen")
    assert m.metodos()[-2:] == ["Regen", "ZoomExtents"]

    # Todo el COM se ejecutó en el hilo del actor, con apartamento STA.
    assert {hilo for hilo, _, _ in m.llamadas} == {HILO_COM}
    assert m.coinit == [(HILO_COM, 2)]

    # La sesión se libera al terminar.
    assert post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1))]}).status_code == 409


def test_origen_desplaza_todas_las_coordenadas(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli, origen=[-50, 25])
    post(
        cli,
        "/draw/batch",
        {
            "sesion": sid,
            "ents": [
                linea((0, 0), (100, 0)),
                circulo((10, 10), 5),
                cota((0, 0), (100, 0), (50, -20)),
            ],
        },
    )
    ln = m.argumentos("AddLine")[0]
    assert ln[0].valor == (-50.0, 25.0, 0.0)
    assert ln[1].valor == (50.0, 25.0, 0.0)
    assert m.argumentos("AddCircle")[0][0].valor == (-40.0, 35.0, 0.0)
    cot = m.argumentos("AddDimRotated")[0]
    assert cot[2].valor == (0.0, 5.0, 0.0)  # base (50, -20) desplazada


# ------------------------------------------------- validación de lotes --

ENTIDADES_INVALIDAS = [
    ("tipo_desconocido", {"t": "triangle", "l": ir.L_CONC}, "Tipo de entidad no admitido"),
    (
        "capa_desconocida",
        {"t": "line", "a": [0, 0], "b": [1, 1], "l": "NO_EXISTE"},
        "Capa desconocida: NO_EXISTE",
    ),
    (
        "demasiados_puntos",
        {"t": "poly", "p": [[i, i] for i in range(2001)], "l": ir.L_CONC},
        "Lista demasiado larga (máximo 2000 elementos)",
    ),
    (
        "texto_largo",
        {"t": "text", "p": [0, 0], "s": "x" * 501, "h": 3.5, "l": ir.L_TXT},
        "Texto demasiado largo (máximo 500 caracteres)",
    ),
    (
        "coordenada_excesiva",
        {"t": "line", "a": [1e10, 0], "b": [1, 1], "l": ir.L_CONC},
        "menor o igual que 1000000000",
    ),
]


@pytest.mark.parametrize("nombre,ent,mensaje", ENTIDADES_INVALIDAS, ids=[c[0] for c in ENTIDADES_INVALIDAS])
def test_entidad_invalida_422(cliente, nombre, ent, mensaje):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli)
    r = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1)), ent]})
    assert r.status_code == 422, r.text
    assert mensaje in solo_msg(r)
    assert not [x for x in m.metodos() if x.startswith("Add")]  # lote entero rechazado


def test_nan_en_coordenada_422(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli)
    cuerpo = f'{{"sesion": "{sid}", "ents": [{{"t": "line", "a": [NaN, 0], "b": [1, 1], "l": "CONCRETO"}}]}}'
    r = cli.post(PREF + "/draw/batch", content=cuerpo, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert "finito" in solo_msg(r)
    assert not [x for x in m.metodos() if x.startswith("Add")]


def test_lote_mayor_de_1000_422(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli, n_total=5000)
    r = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1))] * 1001})
    assert r.status_code == 422
    assert "Lista demasiado larga (máximo 1000 elementos)" in solo_msg(r)


def test_lote_que_supera_n_total_422(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli, n_total=1)
    r = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1)), linea((0, 0), (2, 2))]})
    assert r.status_code == 422
    assert "supera el total declarado" in solo_msg(r)
    # Un lote que sí cabe se acepta después del rechazo.
    ok = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1))]})
    assert ok.status_code == 200


# ------------------------------------------------- fallos y registros --


def test_fallo_com_en_una_entidad_se_registra(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.fallos["AddCircle"] = ErrorCOM(-2147352567, "Fallo simulado al crear el círculo")
    sid = abrir(cli)
    r = post(
        cli,
        "/draw/batch",
        {"sesion": sid, "ents": [linea((0, 0), (1, 1)), circulo((5, 5), 2), linea((2, 2), (3, 3))]},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["creadas"] == 2 and d["omitidas"] == 1
    assert len(d["errores"]) == 1
    error = d["errores"][0]
    assert error["indice"] == 1 and error["tipo"] == "circle"
    assert "Fallo simulado al crear el círculo" in error["motivo"]
    fin = post(cli, "/draw/end", {"sesion": sid}).json()
    assert fin["creadas"] == 2 and fin["omitidas"] == 1


def test_errores_de_lote_se_limitan_a_50(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    m.fallos["AddLine"] = ErrorCOM(-2147352567, "Fallo masivo")
    sid = abrir(cli, n_total=200)
    r = post(cli, "/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1))] * 60})
    d = r.json()
    assert d["omitidas"] == 60
    assert len(d["errores"]) == 50


# ------------------------------------------------------- sesión vencida --


def test_sesion_vencida_409(cliente, monkeypatch):
    cli, m = cliente
    m.conectar_zwcad()
    sid = abrir(cli)
    monkeypatch.setattr(cad_drwr, "TTL_SESION_S", 0.0)
    time.sleep(0.01)
    for ruta, cuerpo in (
        ("/draw/batch", {"sesion": sid, "ents": [linea((0, 0), (1, 1))]}),
        ("/draw/end", {"sesion": sid}),
    ):
        r = post(cli, ruta, cuerpo)
        assert r.status_code == 409
        assert r.json()["detail"] == "La sesión de dibujo venció; vuelva a enviar"


def test_sesion_desconocida_409(cliente):
    cli, m = cliente
    m.conectar_zwcad()
    r = post(cli, "/draw/end", {"sesion": "inventada"})
    assert r.status_code == 409
    assert r.json()["detail"] == "La sesión de dibujo venció; vuelva a enviar"


# ------------------------------------------------------------ contrato --


def test_contrato_cubre_exactamente_las_rutas_montadas(cliente):
    cli, _ = cliente
    # openapi es estable entre versiones de FastAPI (include_router cambió de forma)
    rutas = {
        f"POST {ruta}"
        for ruta, ops in cli.app.openapi()["paths"].items()
        if ruta.startswith(PREF) and "post" in ops
    }
    # Las rutas SAP solo se montan si se pasa `call`; se verifican aparte
    # en tests/test_bridge_cad_drwr_sap.py.
    assert {r for r in contrato() if "/cad/sap/" not in r} == rutas
    for ruta, spec in contrato().items():
        assert spec["keys"], ruta
        assert len(set(spec["keys"])) == len(spec["keys"]), ruta
