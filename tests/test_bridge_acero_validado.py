"""``validado_con_sap`` por operación y tipo: True en preview; en apply (y parcial) I, canal, ángulo, tubo y pipe; False en revertir.

Reutiliza los dobles COM de ``tests/test_bridge_acero_enviar.py`` y ``tests/test_bridge_acero_fuerzas.py``.
La sonda v3 (docs/SONDA_ACERO_AISC360.md) validó preview, creación y asignación, y la geometría de la
sección I; no validó revertir. Las claves de las respuestas no cambian (contrato de origin/main).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.acero_aisc360_enviar import validacion
from bridge.acero_aisc360_fuerzas import leer_fuerzas
from bridge.actions import install_routes
from tests.test_bridge_acero_enviar import RAIZ, _Modelo, _perfil, _Sesion
from tests.test_bridge_acero_fuerzas import FakeAcero


@pytest.fixture
def cliente():
    def make(modelo):
        app = FastAPI()
        install_routes(app, _Sesion(modelo), RuntimeError)
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


def _cuerpo_envio(perfil, barras=("V1", "V2")):
    return {"perfil": perfil, "material": "A36", "barras": list(barras)}


def test_validacion_por_operacion():
    assert validacion("preview") == {
        "validado_con_sap": True,
        "validado_con": "SAP2000 23.3.1 (pywin32), sonda v3",
        "validado_fecha": "2026-10-14",
    }
    assert validacion("apply", "I")["validado_con_sap"] is True
    assert validacion("revertir")["validado_con_sap"] is False
    # Una operación que no está en la lista no se da por validada.
    assert validacion("otra")["validado_con_sap"] is False


def test_preview_es_validado_y_no_escribe(cliente):
    modelo = _Modelo()
    respuesta = cliente(modelo).post(f"{RAIZ}/preview", json=_cuerpo_envio(_perfil()))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["validado_con_sap"] is True
    assert modelo.llamadas_setsection == []


@pytest.mark.parametrize(
    ("tipo", "dims"),
    [
        ("I", {"d": 300, "bf": 150, "tf": 12, "tw": 8}),
        ("canal", {"d": 200, "bf": 75, "tf": 10, "tw": 6}),
        ("angulo", {"d": 101.6, "bf": 101.6, "tf": 12.7, "tw": 12.7}),
        ("tubo_rect", {"d": 200, "bf": 100, "tf": 6, "tw": 6}),
        ("tubo_circ", {"D": 165.2, "t": 6}),
        ("pipe", {"D": 114.3, "t": 4}),
    ],
)
def test_apply_es_validado_para_cada_tipo(cliente, tipo, dims):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo_envio(_perfil(tipo, dims)))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True
    assert cuerpo["validado_con_sap"] is True


def test_apply_parcial_sigue_validado(cliente):
    modelo = _Modelo(frames={"V1": "W1", "V2": "W1", "V3": "W1"})
    modelo.fallar_barra = "V2"
    respuesta = cliente(modelo).post(
        f"{RAIZ}/apply", json=_cuerpo_envio(_perfil(), barras=["V1", "V2", "V3"])
    )
    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"]
    assert detalle["partial"] is True
    assert detalle["validado_con_sap"] is True


def test_revertir_no_es_validado_y_conserva_la_version(cliente):
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/revertir", json={"revertir": {"V1": "W1"}})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["validado_con_sap"] is False
    assert cuerpo["validado_con"] == "SAP2000 23.3.1 (pywin32), sonda v3"
    assert cuerpo["validado_fecha"] == "2026-10-14"


def test_lectura_de_fuerzas_sigue_validada():
    res = leer_fuerzas(FakeAcero(), ["V1"], ["U1"])
    assert res["validado_con_sap"] is True
