"""Tabla operación × tipo de ``validado_con_sap`` (bridge/acero_aisc360_enviar.py, ``validacion``).

Regla: preview es True para todos los tipos; apply y apply parcial son True para I, Channel, Angle, Tube y
Pipe (geometría comparada posición a posición en la sonda v4, D4-1); revertir es False (la sonda v4 lo probó
con una I y la operación no recibe el tipo). Los tipos son los nombres de SAP2000 (``tipo_sap`` de
``TIPO_A_SAP``), no los de la web. Ver docs/SONDA_ACERO_AISC360_V4.md y docs/SAP_ACTIONS.md, sección de enviar.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.acero_aisc360_enviar import (
    ADVERTENCIA_GEOMETRIA,
    ADVERTENCIA_GEOMETRIA_I,
    ADVERTENCIA_GEOMETRIA_SONDA,
    TIPO_A_SAP,
    TIPOS_VALIDADOS_POSICIONAL,
    validacion,
)
from bridge.actions import install_routes
from tests.test_bridge_acero_enviar import DIM_I, RAIZ, _Modelo, _perfil, _Sesion

TIPOS_SAP = ["I", "Channel", "Angle", "Tube", "Pipe"]
DIMS_POR_TIPO_SAP = {
    "I": ("I", DIM_I),
    "Channel": ("canal", DIM_I),
    "Angle": ("angulo", DIM_I),
    "Tube": ("tubo_rect", {"d": 200, "bf": 100, "tf": 6, "tw": 6}),
    "Pipe": ("pipe", {"D": 114.3, "t": 4}),
}


@pytest.fixture
def cliente():
    def make(modelo):
        app = FastAPI()
        install_routes(app, _Sesion(modelo), RuntimeError)
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


def _cuerpo(perfil, barras=("V1", "V2")):
    return {"perfil": perfil, "material": "A36", "barras": list(barras)}


def test_los_tipos_de_la_lista_blanca_son_nombres_sap_reales():
    assert set(TIPO_A_SAP.values()) == set(TIPOS_SAP)
    assert TIPOS_VALIDADOS_POSICIONAL == set(TIPOS_SAP)


@pytest.mark.parametrize("tipo_sap", TIPOS_SAP)
def test_preview_es_validado_para_todos_los_tipos(tipo_sap):
    assert validacion("preview", tipo_sap)["validado_con_sap"] is True


@pytest.mark.parametrize("tipo_sap", TIPOS_SAP)
def test_apply_validado_para_los_cinco_tipos(tipo_sap):
    assert validacion("apply", tipo_sap)["validado_con_sap"] is True


def test_tipo_fuera_de_la_lista_no_se_da_por_validado(monkeypatch):
    import bridge.acero_aisc360_enviar as enviar

    monkeypatch.setattr(enviar, "TIPOS_VALIDADOS_POSICIONAL", frozenset({"I"}))
    assert validacion("apply", "Channel")["validado_con_sap"] is False
    assert validacion("apply", "I")["validado_con_sap"] is True


@pytest.mark.parametrize("tipo_sap", TIPOS_SAP + [None, "Tee", "W"])
def test_revertir_nunca_es_validado(tipo_sap):
    assert validacion("revertir", tipo_sap)["validado_con_sap"] is False


def test_apply_sin_tipo_no_se_da_por_validado():
    assert validacion("apply")["validado_con_sap"] is False


def test_campos_de_version_no_cambian_con_el_tipo():
    res = validacion("apply", "Channel")
    assert res["validado_con"] == "SAP2000 23.3.1 (pywin32), sonda v3"
    assert res["validado_fecha"] == "2026-10-14"


@pytest.mark.parametrize(("tipo_sap", "perfil"), [(s, DIMS_POR_TIPO_SAP[s]) for s in TIPOS_SAP])
def test_preview_por_http_es_validado_para_cada_tipo(cliente, tipo_sap, perfil):
    tipo, dims = perfil
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/preview", json=_cuerpo(_perfil(tipo, dims)))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["validado_con_sap"] is True


@pytest.mark.parametrize(("tipo_sap", "perfil"), [(s, DIMS_POR_TIPO_SAP[s]) for s in TIPOS_SAP])
def test_apply_por_http_y_aviso_de_geometria_segun_el_tipo(cliente, tipo_sap, perfil):
    tipo, dims = perfil
    respuesta = cliente(_Modelo()).post(f"{RAIZ}/apply", json=_cuerpo(_perfil(tipo, dims)))
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True
    assert cuerpo["validado_con_sap"] is True
    esperado = ADVERTENCIA_GEOMETRIA_I if tipo_sap == "I" else ADVERTENCIA_GEOMETRIA_SONDA
    assert esperado in cuerpo["warnings"]
    assert ADVERTENCIA_GEOMETRIA not in cuerpo["warnings"]


@pytest.mark.parametrize(("tipo_sap", "perfil"), [(s, DIMS_POR_TIPO_SAP[s]) for s in TIPOS_SAP])
def test_apply_parcial_por_http_sigue_la_regla_del_tipo(cliente, tipo_sap, perfil):
    tipo, dims = perfil
    modelo = _Modelo(frames={"V1": "W1", "V2": "W1", "V3": "W1"})
    modelo.fallar_barra = "V2"
    respuesta = cliente(modelo).post(
        f"{RAIZ}/apply", json=_cuerpo(_perfil(tipo, dims), barras=["V1", "V2", "V3"])
    )
    assert respuesta.status_code == 409, respuesta.text
    detalle = respuesta.json()["detail"]
    assert detalle["partial"] is True
    assert detalle["validado_con_sap"] is True
    assert detalle["revertir"] == {"V1": "W1"}
