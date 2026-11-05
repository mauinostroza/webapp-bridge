"""Completitud del contrato bridge<->web: toda ruta de la aplicación del puente tiene su
entrada en ``web/src/bridge_contracts.json`` o un motivo explícito en ``EXCLUIDAS``.

Compara ``app.openapi()["paths"]`` (método + ruta, con los parámetros normalizados a ``{}``)
con las claves del contrato. Así, una ruta nueva sin contrato hace fallar la prueba, y una
entrada del contrato que ya no corresponde a ninguna ruta también.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CONTRATOS = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
METODOS = ("get", "post", "put", "delete", "patch")

# Rutas de la aplicación todavía sin contrato. Cada una debe tener el motivo; al añadir su
# entrada al JSON hay que quitarla de aquí (la prueba lo exige).
EXCLUIDAS = {
    "POST /v1/sap/actions/str2fund/source": "sin contrato: esquemas pendientes de sonda con los dobles de tests/test_bridge_str2fund.py",
    "POST /v1/sap/actions/str2fund/target/preview": "sin contrato: esquemas pendientes de sonda con los dobles de tests/test_bridge_str2fund.py",
    "POST /v1/sap/actions/str2fund/target/apply": "sin contrato: esquemas pendientes de sonda con los dobles de tests/test_bridge_str2fund.py",
    "POST /v1/sap/actions/str2fund/target/materials": "sin contrato: esquemas pendientes de sonda con los dobles de tests/test_bridge_str2fund.py",
    "POST /v1/sap/actions/str2fund/target/update-loads": "sin contrato: esquemas pendientes de sonda con los dobles de tests/test_bridge_str2fund.py",
    "POST /v1/sap/analysis/run": "consumida por backend/sap2000_bridge.py:18; contrato pendiente",
    "GET /v1/sap/model": "consumida por bridge/sap_client.py:77 (ruta protegida); contrato pendiente",
    "POST /v1/sap/actions/combinations/preview": "sin consumidor en la web; documentada en docs/SAP_ACTIONS.md",
    "POST /v1/sap/actions/combinations/apply": "sin consumidor en la web; documentada en docs/SAP_ACTIONS.md",
    "POST /v1/sap/actions/spectrum/preview": "sin consumidor en la web y sin pruebas de contrato",
    "POST /v1/sap/actions/spectrum/apply": "sin consumidor en la web y sin pruebas de contrato",
    "POST /v1/sap/actions/profiles/create/preview": "sin consumidor en la web; pruebas en tests/test_bridge_actions.py",
    "POST /v1/sap/actions/profiles/section/preview": "sin consumidor en la web; pruebas en tests/test_bridge_actions.py",
    "GET /v1/sap/actions/profiles/selected-frame-properties": "sin consumidor en la web; pruebas en tests/test_bridge_actions.py",
    "GET /v1/sap/results/reactions": "sin consumidor en la web (bridge/sap2000_agent.py:603)",
}


def _norm(ruta: str) -> str:
    """``/pairing/poll/{id}`` y ``/pairing/poll/{request_id}`` son la misma ruta."""
    return re.sub(r"\{[^}]+\}", "{}", ruta)


def _clave(metodo: str, ruta: str) -> str:
    return f"{metodo.upper()} {_norm(ruta)}"


@pytest.fixture
def rutas_app(tmp_path, monkeypatch) -> set[str]:
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from bridge.sap2000_agent import app

    return {
        _clave(metodo, ruta)
        for ruta, operaciones in app.openapi()["paths"].items()
        for metodo in operaciones
        if metodo in METODOS
    }


@pytest.fixture(scope="module")
def rutas_contrato() -> set[str]:
    return {_clave(*clave.split(" ", 1)) for clave in json.loads(CONTRATOS.read_text("utf-8"))["routes"]}


def test_toda_ruta_de_la_app_tiene_contrato_o_motivo(rutas_app, rutas_contrato):
    sin_contrato = rutas_app - rutas_contrato - set(EXCLUIDAS)
    assert not sin_contrato, (
        "Rutas del puente sin entrada en web/src/bridge_contracts.json ni en EXCLUIDAS "
        f"(añádelas al contrato o justifícalas): {sorted(sin_contrato)}"
    )


def test_el_contrato_no_lista_rutas_inexistentes(rutas_app, rutas_contrato):
    inexistentes = rutas_contrato - rutas_app
    assert not inexistentes, f"El contrato lista rutas que la app no tiene: {sorted(inexistentes)}"


def test_exclusiones_son_rutas_reales_sin_contrato_y_con_motivo(rutas_app, rutas_contrato):
    assert set(EXCLUIDAS) <= rutas_app, (
        f"EXCLUIDAS nombra rutas inexistentes: {sorted(set(EXCLUIDAS) - rutas_app)}"
    )
    ya_contratadas = set(EXCLUIDAS) & rutas_contrato
    assert not ya_contratadas, (
        f"Quita de EXCLUIDAS las rutas que ya tienen contrato: {sorted(ya_contratadas)}"
    )
    vacios = [ruta for ruta, motivo in EXCLUIDAS.items() if not motivo.strip()]
    assert not vacios, f"Exclusiones sin motivo: {vacios}"
