"""Tests de bridge/acero_aisc360_enviar.py: enviar perfiles como sección y asignarlos a barras.

COM falso con el mismo patrón que tests/test_bridge_acero_fuerzas.py. La forma pywin32 (código de
retorno al inicio) se prueba con el proxy de tests/test_bridge_esbelteces.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.acero_aisc360_enviar import EnviarBody, preview_envio
from bridge.actions import install_routes
from tests.test_bridge_contracts import assert_matches_contract
from tests.test_bridge_esbelteces import _RetFirst

RAIZ = "/v1/sap/actions/acero-aisc360/enviar"
ORIGINAL = 6  # kN_m_C: unidades del modelo de prueba (la restauración debe devolverlas aquí)
DIM_I = {"d": 300, "bf": 150, "tf": 12, "tw": 8}


class _Prop:
    """PropFrame: nombres de sección y las Set* de creación."""

    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (list(self._m.secciones), 0)

    def SetISection(self, *args):
        return self._m.crear("SetISection", args)

    def GetISection(self, nombre, *_):
        """Forma real de SAP2000 23.3.1 (pywin32): código al inicio y 11 salidas; unidades presentes."""
        if nombre not in self._m.geometrias or self._m.geometria_no_legible:
            return (1, "", "", 0, 0, 0, 0, 0, 0, 0, "", "")
        t3, t2, tf, tw = self._m.geometrias[nombre]
        f = 1.0 if self._m.units == 9 else 0.001  # 9 = N_mm_C; si no, metros
        extra = 1.0 if not self._m.geometria_distinta else 1.5
        return (0, "", "A36", t3 * f * extra, t2 * f, tf * f, tw * f, t2 * f, tf * f, 16711680, "", "")

    def SetChannel(self, *args):
        return self._m.crear("SetChannel", args)

    def SetAngle(self, *args):
        return self._m.crear("SetAngle", args)

    def SetTube(self, *args):
        return self._m.crear("SetTube", args)

    def SetPipe(self, *args):
        return self._m.crear("SetPipe", args)


class _Materiales:
    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (list(self._m.materiales), 0)


class _Barras:
    """FrameObj: nombres, sección actual y SetSection (con fallos y cambios silenciosos programables)."""

    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (list(self._m.frames), 0)

    def GetSection(self, nombre, *_):
        if nombre not in self._m.frames:
            return ("", "", 1)
        return (self._m.frames[nombre], "", 0)

    def SetSection(self, nombre, seccion, *_):
        self._m.llamadas_setsection.append((nombre, seccion))
        if nombre == self._m.fallar_barra:
            raise RuntimeError("boom barra")
        if nombre not in self._m.frames:
            return 1
        if nombre != self._m.barra_silenciosa:
            self._m.frames[nombre] = seccion
        return 0


class _Seleccion:
    def __init__(self, modelo):
        self._m = modelo

    def GetSelected(self, *_):
        nombres = self._m.seleccion
        return (0, len(nombres), tuple(2 for _ in nombres), tuple(nombres))


class _Modelo:
    def __init__(self, *, frames=None, secciones=("W1",), materiales=("A36",), bloqueado=False, seleccion=()):
        self.frames = dict(frames if frames is not None else {"V1": "W1", "V2": "W1", "V3": "W1"})
        self.secciones = list(secciones)
        self.materiales = list(materiales)
        self.seleccion = list(seleccion)
        self.units = ORIGINAL
        self.unit_calls: list[int] = []
        self.fallar_restaurar = False
        self.locked = bloqueado
        self.bloqueo_tupla = False  # GetModelIsLocked como (ret, bool)
        self.unidades_tupla = False  # GetPresentUnits como (ret, unidades)
        self.geometrias: dict[str, tuple[float, float, float, float]] = {}
        self.geometria_distinta = False  # GetISection devuelve otra geometría (relectura que difiere)
        self.geometria_no_legible = False  # GetISection responde con error
        self.lock_calls: list[bool] = []
        self.calls: list[tuple[str, tuple]] = []  # creaciones de sección
        self.llamadas_setsection: list[tuple[str, str]] = []
        self.fallar_barra: str | None = None
        self.barra_silenciosa: str | None = None
        self.fallar_crear = False
        self.PropFrame = _Prop(self)
        self.PropMaterial = _Materiales(self)
        self.FrameObj = _Barras(self)
        self.SelectObj = _Seleccion(self)

    def crear(self, metodo, args):
        if self.fallar_crear:
            raise RuntimeError("boom crear")
        self.calls.append((metodo, args))
        if metodo == "SetISection":
            self.geometrias[args[0]] = (args[2], args[3], args[4], args[5])
        if args[0] not in self.secciones:
            self.secciones.append(args[0])
        return 0

    def GetPresentUnits(self):
        return (0, self.units) if self.unidades_tupla else self.units

    def SetPresentUnits(self, unidad):
        self.unit_calls.append(unidad)
        if unidad == ORIGINAL and self.fallar_restaurar:
            return 1
        self.units = unidad
        return 0

    def GetModelIsLocked(self):
        return (0, self.locked) if self.bloqueo_tupla else self.locked

    def SetModelIsLocked(self, bloqueo):
        self.lock_calls.append(bloqueo)
        if not bloqueo:
            self.locked = False
        return 0


class _Actor:
    def call(self, fn):
        return fn()


class _Sesion:
    def __init__(self, modelo):
        self.actor = _Actor()
        self.modelo = modelo

    def _model(self):
        return self.modelo


@pytest.fixture
def cliente():
    def make(modelo):
        app = FastAPI()
        install_routes(app, _Sesion(modelo), RuntimeError)
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


@pytest.fixture(scope="module")
def contratos():
    raiz = Path(__file__).resolve().parent.parent
    return json.loads((raiz / "web" / "src" / "bridge_contracts.json").read_text("utf-8"))["routes"]


def _perfil(tipo="I", dims=None, nombre="W-NUEVA"):
    return {"nombre": nombre, "tipo": tipo, "dimensiones_mm": DIM_I if dims is None else dims}


def _cuerpo(**cambios):
    cuerpo = {"perfil": _perfil(), "material": "A36", "barras": ["V1", "V2"]}
    cuerpo.update(cambios)
    return cuerpo


# ---------------------------------------------------------------------------
# Tipos de perfil -> llamada COM
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "dims", "metodo", "args"),
    [
        ("I", DIM_I, "SetISection", ("W-NUEVA", "A36", 300, 150, 12, 8, 150, 12, -1, "", "")),
        ("canal", DIM_I, "SetChannel", ("W-NUEVA", "A36", 300, 150, 12, 8, -1, "", "")),
        ("angulo", DIM_I, "SetAngle", ("W-NUEVA", "A36", 300, 150, 12, 8, -1, "", "")),
        (
            "tubo_rect",
            {"t3": 200, "t2": 100, "tf": 6, "tw": 5},
            "SetTube",
            ("W-NUEVA", "A36", 200, 100, 6, 5, -1, "", ""),
        ),
        ("tubo_circ", {"D": 165.2, "t": 6}, "SetPipe", ("W-NUEVA", "A36", 165.2, 6, -1, "", "")),
        ("pipe", {"D": 114.3, "t": 4}, "SetPipe", ("W-NUEVA", "A36", 114.3, 4, -1, "", "")),
    ],
)
def test_cada_tipo_llama_a_su_set_con_los_argumentos_de_create_section(cliente, tipo, dims, metodo, args):
    modelo = _Modelo()
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(perfil=_perfil(tipo, dims)))
    assert respuesta.status_code == 200, respuesta.text
    assert modelo.calls == [(metodo, args)]
    assert modelo.unit_calls == [9, ORIGINAL]
    assert modelo.units == ORIGINAL
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True
    assert cuerpo["validado_con_sap"] is True  # I, canal, ángulo, tubo y pipe (sonda v4)
    assert cuerpo["validado_con"] == "SAP2000 23.3.1 (pywin32), sonda v3"
    assert cuerpo["validado_fecha"] == "2026-10-14"
    assert cuerpo["resultados_invalidados"] is False
    assert [modelo.frames[b] for b in ("V1", "V2")] == ["W-NUEVA", "W-NUEVA"]


def test_tee_se_rechaza_con_422_y_mensaje_claro(cliente):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(perfil=_perfil("tee")))
    assert respuesta.status_code == 422
    assert "tees" in respuesta.text.lower()


@pytest.mark.parametrize(
    ("perfil", "motivo"),
    [
        (_perfil("cajon"), "no soportado"),
        (_perfil("pipe", {"D": 114.3, "t": 4, "d": 114.3}), "no aplicables"),
        (_perfil("I", {"d": 300, "bf": 150, "tf": 12}), "Faltan dimensiones"),
        (_perfil("I", {"d": 300, "bf": 150, "tf": 12, "tw": 8, "dd": 1}), "dd"),
        (_perfil("I", {"d": -300, "bf": 150, "tf": 12, "tw": 8}), "mayores que cero"),
        (_perfil("tubo_rect", {"d": 200, "t3": 150, "bf": 100, "tf": 6, "tw": 5}), "alias"),
        (_perfil("I", {"d": 300, "bf": 150, "tf": 12, "tw": 8}, nombre="W/NUEVA"), "perfil.nombre"),
    ],
)
def test_entradas_invalidas_son_422(cliente, perfil, motivo):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(perfil=perfil))
    assert respuesta.status_code == 422, motivo
    assert motivo in respuesta.text


def test_barras_501_es_422_y_lista_vacia_tambien(cliente):
    nombres = [f"B{i}" for i in range(501)]
    assert cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(barras=nombres)).status_code == 422
    assert cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo(barras=[])).status_code == 422


# ---------------------------------------------------------------------------
# Sección existente, material y bloqueo
# ---------------------------------------------------------------------------


def test_seccion_existente_sin_sobrescribir_es_409_y_no_escribe(cliente):
    modelo = _Modelo(secciones=("W1", "W-NUEVA"))
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "section_exists"
    assert modelo.calls == [] and modelo.unit_calls == [] and modelo.llamadas_setsection == []


def test_seccion_existente_con_sobrescribir_la_reemplaza(cliente):
    modelo = _Modelo(secciones=("W1", "W-NUEVA"))
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(sobrescribir=True))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["seccion"]["accion"] == "sobrescrita"
    assert len(modelo.calls) == 1 and modelo.calls[0][0] == "SetISection"
    assert modelo.unit_calls == [9, ORIGINAL]


def test_material_inexistente_es_422_con_la_lista_de_materiales(cliente):
    modelo = _Modelo(materiales=("A36", "A572"))
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(material="A992"))
    assert respuesta.status_code == 422
    assert "no existe" in respuesta.text and "A572" in respuesta.text
    assert modelo.calls == [] and modelo.unit_calls == []


def test_modelo_bloqueado_sin_desbloquear_es_409_sin_cambios(cliente):
    modelo = _Modelo(bloqueado=True)
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "modelo_bloqueado"
    assert modelo.lock_calls == [] and modelo.calls == [] and modelo.unit_calls == []
    assert modelo.locked is True


def test_modelo_bloqueado_con_desbloquear_lo_desbloquea_y_avisa(cliente):
    modelo = _Modelo(bloqueado=True)
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(desbloquear=True))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert modelo.lock_calls == [False]
    assert cuerpo["modelo_desbloqueado"] is True
    assert cuerpo["resultados_invalidados"] is True
    assert any("desbloqueado" in aviso and "BORRA" in aviso for aviso in cuerpo["warnings"])


def test_bloqueo_como_tupla_con_codigo_se_lee_igual(cliente):
    """GetModelIsLocked con código de retorno en tupla (forma pywin32) bloquea igual que el escalar."""
    modelo = _Modelo(bloqueado=True)
    modelo.bloqueo_tupla = True
    vista = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo())
    assert vista.status_code == 200 and vista.json()["modelo_bloqueado"] is True
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "modelo_bloqueado"
    assert modelo.calls == [] and modelo.lock_calls == []


def test_bloqueo_escalar_falso_no_bloquea(cliente):
    vista = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo())
    assert vista.json()["modelo_bloqueado"] is False


def test_geometria_i_releida_coincide_y_se_avisa_que_se_relee(cliente):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo())
    cuerpo = respuesta.json()
    assert respuesta.status_code == 200 and cuerpo["ok"] is True
    assert not any("no coincide" in aviso for aviso in cuerpo["warnings"])
    assert any("GetISection" in aviso and "se relee" in aviso for aviso in cuerpo["warnings"])


def test_geometria_i_distinta_da_ok_false_con_los_valores(cliente):
    modelo = _Modelo()
    modelo.geometria_distinta = True  # SAP2000 devuelve el alma de otra medida
    cuerpo = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo()).json()
    assert cuerpo["ok"] is False
    assert any("no coincide con lo enviado" in aviso and "300" in aviso for aviso in cuerpo["warnings"])
    assert all(item["ok"] for item in cuerpo["verificacion"])  # las barras sí tienen la sección


def test_geometria_i_no_legible_da_ok_false(cliente):
    modelo = _Modelo()
    modelo.geometria_no_legible = True  # GetISection responde con error tras crear la sección
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 200
    assert respuesta.json()["ok"] is False
    assert any("No se pudo releer la geometría" in aviso for aviso in respuesta.json()["warnings"])


def test_canal_no_relee_geometria_y_avisa_como_antes(cliente):
    cuerpo = (
        cliente(_Modelo())
        .post(
            f"{RAIZ}/apply",
            json=_cuerpo(perfil=_perfil(tipo="canal", dims=DIM_I)),
        )
        .json()
    )
    assert cuerpo["ok"] is True
    assert any("no se relee" in aviso for aviso in cuerpo["warnings"])


# ---------------------------------------------------------------------------
# Fallo a mitad, reversión y relectura
# ---------------------------------------------------------------------------


def test_fallo_en_la_tercera_barra_devuelve_partial_sin_seguir(cliente):
    modelo = _Modelo(frames={"V1": "W1", "V2": "W1", "V3": "W1", "V4": "W1"})
    modelo.fallar_barra = "V3"
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(barras=["V1", "V2", "V3", "V4"]))
    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"]
    assert detalle["partial"] is True
    assert detalle["fallida"] == "V3"
    assert detalle["aplicadas"] == [
        {"frame": "V1", "seccion_previa": "W1"},
        {"frame": "V2", "seccion_previa": "W1"},
    ]
    assert detalle["revertir"] == {"V1": "W1", "V2": "W1"}
    assert detalle["validado_con_sap"] is True
    assert any("no se revierten automáticamente" in w for w in detalle["warnings"])
    assert "V4" not in [barra for barra, _ in modelo.llamadas_setsection]
    assert modelo.frames["V4"] == "W1"
    assert modelo.units == ORIGINAL  # las unidades se restauran aunque falle a mitad


def test_revertir_reasigna_las_secciones_previas(cliente):
    modelo = _Modelo(frames={"V1": "W1", "V2": "W1", "V3": "W1", "V4": "W1"})
    modelo.fallar_barra = "V3"
    cliente_api = cliente(modelo)
    previo = cliente_api.post(f"{RAIZ}/apply", json=_cuerpo(barras=["V1", "V2", "V3"])).json()["detail"]
    modelo.fallar_barra = None
    respuesta = cliente_api.post(f"{RAIZ}/revertir", json={"revertir": previo["revertir"]})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True
    assert modelo.frames["V1"] == "W1" and modelo.frames["V2"] == "W1"
    assert all(item["ok"] for item in cuerpo["verificacion"])


def test_revertir_con_fallo_a_mitad_es_partial(cliente):
    modelo = _Modelo(frames={"V1": "W-NUEVA", "V2": "W-NUEVA"}, secciones=("W1", "W-NUEVA"))
    modelo.fallar_barra = "V2"
    respuesta = cliente(modelo).post(f"{RAIZ}/revertir", json={"revertir": {"V1": "W1", "V2": "W1"}})
    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"]
    assert detalle["partial"] is True and detalle["fallida"] == "V2"
    assert detalle["revertir"] == {"V1": "W-NUEVA"}


def test_revertir_con_seccion_previa_borrada_es_422(cliente):
    modelo = _Modelo(frames={"V1": "W-NUEVA"}, secciones=("W-NUEVA",))
    respuesta = cliente(modelo).post(f"{RAIZ}/revertir", json={"revertir": {"V1": "W1"}})
    assert respuesta.status_code == 422
    assert "W1" in respuesta.text
    assert modelo.llamadas_setsection == []


def test_relectura_que_difiere_da_ok_false(cliente):
    modelo = _Modelo()
    modelo.barra_silenciosa = "V2"  # SetSection devuelve 0 pero no cambia la sección
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is False
    malas = [item for item in cuerpo["verificacion"] if not item["ok"]]
    assert [(item["barra"], item["leido"]) for item in malas] == [("V2", "W1")]
    assert any("no coincide" in aviso for aviso in cuerpo["warnings"])


# ---------------------------------------------------------------------------
# Unidades, orden de retorno y errores genéricos
# ---------------------------------------------------------------------------


def test_error_al_crear_restaura_las_unidades(cliente):
    modelo = _Modelo()
    modelo.fallar_crear = True
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 409
    assert modelo.unit_calls == [9, ORIGINAL]
    assert modelo.units == ORIGINAL
    assert modelo.llamadas_setsection == []


def test_fallo_al_restaurar_unidades_se_avisa(cliente):
    modelo = _Modelo()
    modelo.fallar_restaurar = True
    respuesta = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo())
    assert respuesta.status_code == 200
    assert any("No se pudieron restaurar las unidades" in aviso for aviso in respuesta.json()["warnings"])


def test_codigo_de_retorno_al_inicio_da_el_mismo_resultado(cliente):
    normal = cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo())
    invertido = cliente(_RetFirst(_Modelo())).post(f"{RAIZ}/apply", json=_cuerpo())
    assert normal.status_code == invertido.status_code == 200
    assert normal.json() == invertido.json()
    vista = cliente(_RetFirst(_Modelo())).post(f"{RAIZ}/preview", json=_cuerpo())
    assert vista.status_code == 200 and vista.json()["cambios"] == 2


# ---------------------------------------------------------------------------
# Vista previa: solo lectura
# ---------------------------------------------------------------------------


def test_preview_no_escribe_nada(cliente):
    modelo = _Modelo()
    respuesta = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo())
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["preview"] is True and cuerpo["will_mutate"] is False
    assert cuerpo["cambios"] == 2 and cuerpo["sin_cambio"] == 0
    assert cuerpo["seccion"]["accion"] == "crear"
    assert modelo.calls == [] and modelo.llamadas_setsection == []
    assert modelo.unit_calls == [] and modelo.lock_calls == []
    assert modelo.secciones == ["W1"]


def test_preview_avisa_de_bloqueo_inexistentes_y_asterisco(cliente):
    modelo = _Modelo(bloqueado=True)
    cuerpo = (
        cliente(modelo)
        .post(
            f"{RAIZ}/preview",
            json=_cuerpo(perfil=_perfil("I", DIM_I, nombre="H414x405*"), barras=["V1", "NOEXISTE"]),
        )
        .json()
    )
    textos = " | ".join(cuerpo["warnings"])
    assert cuerpo["modelo_bloqueado"] is True
    assert "NOEXISTE" in textos and "bloqueado" in textos
    assert "'*'" not in textos and "no está verificado" not in textos  # validado con SAP2000 23.3.1
    assert cuerpo["barras"] == [{"frame": "V1", "seccion_actual": "W1", "cambia": True}]


def test_preview_con_seleccion_resuelve_las_barras_seleccionadas(cliente):
    modelo = _Modelo(seleccion=("V2",))
    cuerpo = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo(barras="selected")).json()
    assert cuerpo["barras"] == [{"frame": "V2", "seccion_actual": "W1", "cambia": True}]


def test_seleccion_vacia_es_409(cliente):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(barras="selected"))
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "sap_error"


def test_sobrescribir_avisa_de_las_barras_fuera_de_la_lista(cliente):
    modelo = _Modelo(frames={"V1": "W1", "V2": "W1", "V3": "W-NUEVA"}, secciones=("W1", "W-NUEVA"))
    cuerpo = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo(barras=["V1"], sobrescribir=True)).json()
    assert cuerpo["seccion"]["accion"] == "sobrescribir"
    assert any("cambia también 1 barra" in aviso for aviso in cuerpo["warnings"])


def test_barra_inexistente_se_omite_con_aviso(cliente):
    modelo = _Modelo()
    cuerpo = cliente(modelo).post(f"{RAIZ}/apply", json=_cuerpo(barras=["V1", "NOEXISTE"])).json()
    assert [a["frame"] for a in cuerpo["aplicadas"]] == ["V1"]
    assert any("NOEXISTE" in aviso for aviso in cuerpo["warnings"])


def test_preview_sin_barras_existentes_es_409(cliente):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(barras=["X1", "X2"]))
    assert respuesta.status_code == 409


def test_preview_directo_no_llama_a_escritura():
    modelo = _Modelo()
    cuerpo = preview_envio(modelo, EnviarBody(**_cuerpo()))
    assert cuerpo["seccion"]["tipo_sap"] == "I"
    assert modelo.calls == [] and modelo.llamadas_setsection == [] and modelo.unit_calls == []


# ---------------------------------------------------------------------------
# Contrato bridge <-> web
# ---------------------------------------------------------------------------


def test_contratos_de_preview_apply_y_revertir(cliente, contratos):
    modelo = _Modelo()
    vista = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo()).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/acero-aisc360/enviar/preview", vista)
    aplicado = cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo()).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/acero-aisc360/enviar/apply", aplicado)
    revertido = cliente(_Modelo()).post(f"{RAIZ}/revertir", json={"revertir": {"V1": "W1"}}).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/acero-aisc360/enviar/revertir", revertido)
