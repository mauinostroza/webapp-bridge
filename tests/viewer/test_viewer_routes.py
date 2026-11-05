"""Rutas del Viewer en el puente compartido: instalación completa y contrato con la web.

Las claves de ``status``, ``results/cases`` y ``k`` se comparan con las respuestas reales
(COM simulado en modo demo) y con ``web/src/bridge_contracts.json``, la misma fuente que
usa la web.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from bridge.viewer import routes
from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeSapObject

PREFIJO = "/v1/sap/actions/viewer"
CONTRATOS = Path(__file__).resolve().parents[2] / "web" / "src" / "bridge_contracts.json"

RUTAS_ESPERADAS = {
    ("GET", "snapshot"),
    ("POST", "connect"),
    ("POST", "refresh"),
    ("GET", "status"),
    ("GET", "results/cases"),
    ("GET", "results/design"),
    ("GET", "results/area"),
    ("GET", "results/displacements"),
    ("GET", "results/frame/{frame}"),
    # Resultados en bloque (core/results_bulk.py): reacciones, barras paginadas y envolventes.
    ("GET", "results/reactions"),
    ("GET", "results/frames"),
    ("GET", "results/envelope"),
    ("GET", "k"),
    ("GET", "k/diagnose"),
    ("GET", "k/design"),
    ("POST", "k/preview"),
    ("POST", "k/apply"),
    ("GET", "k/effective"),
    ("GET", "sections/props"),
}


class Connector:
    def __init__(self):
        self.model = FakeSapObject().SapModel
        self.is_demo = True

    def connect_demo(self):
        return self.info()

    def connect_to_open_instance(self):
        return ConnectionInfo(connected=False, message="no")

    def info(self):
        return ConnectionInfo(connected=True, model_name="Demo", units="tonf_m_C", message="")

    def is_alive(self):
        return True

    def disconnect(self):
        pass


def _contrato(ruta: str) -> dict:
    return json.loads(CONTRATOS.read_text(encoding="utf-8"))["routes"][ruta]


def test_install_routes_registra_las_rutas_del_viewer():
    app = FastAPI()
    routes.install_routes(app)
    instaladas = {
        (metodo, ruta.path.removeprefix(PREFIJO + "/"))
        for ruta in app.routes
        if ruta.path.startswith(PREFIJO + "/")
        for metodo in ruta.methods
        if metodo not in {"HEAD", "OPTIONS"}
    }
    assert instaladas == RUTAS_ESPERADAS


def test_claves_de_respuesta_coinciden_con_el_contrato(rutas_viewer):
    with rutas_viewer(Connector) as client:
        estado = client.get(PREFIJO + "/status").json()
        assert set(estado) == set(_contrato("GET /v1/sap/actions/viewer/status")["keys"])

        client.conectar_demo()
        spec_casos = _contrato("GET /v1/sap/actions/viewer/results/cases")
        casos = client.get(PREFIJO + "/results/cases").json()
        assert set(casos) == set(spec_casos["keys"])
        assert casos["cases"], "el modelo simulado debe traer casos"
        for lista, claves in spec_casos["items"].items():
            assert set(casos[lista][0]) == set(claves)

        k = client.get(PREFIJO + "/k").json()
        assert set(k) == set(_contrato("GET /v1/sap/actions/viewer/k")["keys"])
