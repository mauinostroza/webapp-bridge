"""Fixture compartido de las pruebas del Viewer: app mínima con ``install_routes``.

Sustituye ``web_bridge.server.create_app`` del original. El puente COM (``LocalBridge``)
se crea con la fábrica de cada prueba y se instala en ``bridge.viewer.routes`` por
``monkeypatch``, así que no toca SAP2000 ni el singleton del proceso.

El endpoint ``connect`` no admite ``demo`` (corre en la web), así que el cliente
expone ``conectar_demo()``, que llama directamente al puente del Viewer.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def rutas_viewer(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bridge.viewer import routes
    from bridge.viewer.core.bridge import LocalBridge
    from bridge.viewer.core.k_factors import KFactorService

    puentes: list[LocalBridge] = []

    def montar(fabrica):
        puente = LocalBridge(fabrica)
        puentes.append(puente)
        monkeypatch.setattr(routes, "_puente", puente)
        # Auditoría y copias de seguridad aisladas en tmp_path (logs/ y backups/ bajo ella).
        monkeypatch.setattr(routes, "_servicio_k", KFactorService(tmp_path))
        app = FastAPI()
        routes.install_routes(app)
        cliente = TestClient(app, base_url="http://127.0.0.1:8765")
        cliente.puente = puente
        cliente.tmp = tmp_path
        cliente.conectar_demo = lambda: asyncio.run(puente.connect("demo"))
        return cliente

    yield montar
    for puente in puentes:
        puente.close()
