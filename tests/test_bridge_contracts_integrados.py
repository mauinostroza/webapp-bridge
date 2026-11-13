"""Contratos bridge<->web de las rutas de Diagrama de interacción, más la
comprobación de que el JSON de contratos conserva intactas las rutas previas.

No modifica ``tests/test_bridge_contracts.py``: reutiliza su
``assert_matches_contract`` y el COM falso de ``tests/test_bridge_diagrama.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_bridge_contracts import CONTRACTS_PATH, assert_matches_contract
from test_bridge_diagrama import FakeModel, _client, _forces, _model_group
from test_bridge_equilibrio import FakeModel as EquilibrioModel
from test_bridge_equilibrio import _client as _equilibrio_client
from test_bridge_hormigon import FakeModel as HormigonModel
from test_bridge_hormigon import _client as _hormigon_client

REPO = CONTRACTS_PATH.parent.parent.parent
DIAGRAMA_ROUTES = [
    "GET /v1/sap/actions/diagrama/groups",
    "GET /v1/sap/actions/diagrama/combos",
    "POST /v1/sap/actions/diagrama/group-demands",
    "POST /v1/sap/actions/diagrama/selected-demands",
]
HORMIGON_ROUTES = [
    "GET /v1/sap/actions/hormigon/frames",
    "GET /v1/sap/actions/hormigon/combos",
    "POST /v1/sap/actions/hormigon/frame-forces",
]
EQUILIBRIO_ROUTES = [
    "GET /v1/sap/actions/equilibrio/combos",
    "GET /v1/sap/actions/equilibrio/groups",
    "GET /v1/sap/actions/equilibrio/group-members",
    "POST /v1/sap/actions/equilibrio/frame-data",
    "POST /v1/sap/actions/equilibrio/group-demands",
]
BASE_REF = "origin/main"


@pytest.fixture(scope="module")
def contracts() -> dict:
    return json.loads(Path(CONTRACTS_PATH).read_text("utf-8"))["routes"]


def test_diagrama_routes_declared(contracts):
    for route in DIAGRAMA_ROUTES:
        assert route in contracts


def test_diagrama_groups(contracts):
    response = _client(FakeModel(groups=["G1"])).get("/v1/sap/actions/diagrama/groups")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/diagrama/groups", response.json())


def test_diagrama_combos(contracts):
    response = _client(FakeModel(combos=["C1"])).get("/v1/sap/actions/diagrama/combos")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/diagrama/combos", response.json())


def test_diagrama_group_demands(contracts):
    response = _client(_model_group()).post(
        "/v1/sap/actions/diagrama/group-demands", json={"group": "G1", "combos": ["C1", "C2"]}
    )
    assert response.status_code == 200
    assert response.json()["rows"]
    assert_matches_contract(contracts, "POST /v1/sap/actions/diagrama/group-demands", response.json())


def test_diagrama_selected_demands(contracts):
    model = FakeModel(selected=[(2, "COL1")], forces=_forces())
    response = _client(model).post("/v1/sap/actions/diagrama/selected-demands", json={"combos": ["C1"]})
    assert response.status_code == 200
    assert response.json()["rows"]
    assert_matches_contract(contracts, "POST /v1/sap/actions/diagrama/selected-demands", response.json())


def test_hormigon_routes_declared(contracts):
    for route in HORMIGON_ROUTES:
        assert route in contracts


def test_hormigon_frames(contracts):
    response = _hormigon_client(HormigonModel()).get("/v1/sap/actions/hormigon/frames")
    assert response.status_code == 200
    assert response.json()["frames"]
    assert_matches_contract(contracts, "GET /v1/sap/actions/hormigon/frames", response.json())


def test_hormigon_combos(contracts):
    response = _hormigon_client(HormigonModel()).get("/v1/sap/actions/hormigon/combos")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/hormigon/combos", response.json())


def test_hormigon_frame_forces(contracts):
    response = _hormigon_client(HormigonModel()).post(
        "/v1/sap/actions/hormigon/frame-forces", json={"frame": "B1", "combos": ["C1", "C2"]}
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/hormigon/frame-forces", response.json())


def test_equilibrio_routes_declared(contracts):
    for route in EQUILIBRIO_ROUTES:
        assert route in contracts


def test_equilibrio_contracts(contracts):
    client = _equilibrio_client(EquilibrioModel())
    prefix = "/v1/sap/actions/equilibrio"
    cases = [
        ("GET", f"{prefix}/combos", None),
        ("GET", f"{prefix}/groups", None),
        ("GET", f"{prefix}/group-members?group=GRUPO1", None),
        ("POST", f"{prefix}/frame-data", {"frame": "D1", "combo_sismo": "SISMO", "combo_grav": "GRAV"}),
        ("POST", f"{prefix}/group-demands", {"group": "GRUPO1", "combo_sismo": "SISMO"}),
    ]
    for method, path, body in cases:
        response = client.get(path) if method == "GET" else client.post(path, json=body)
        assert response.status_code == 200, path
        assert_matches_contract(contracts, f"{method} {path.split('?')[0]}", response.json())


RUTAS_RETIRADAS = {
    "POST /v1/documentos/link",
    # Demanda calculada en el servidor: el puente solo entrega filas crudas (hormigon/frame-forces).
    "POST /v1/sap/actions/hormigon/frame-demand",
    "POST /v1/sap/actions/hormigon/frame-demand-multi",
    # Esbelteces: el puente entrega datos crudos de ángulos en las rutas -raw; el servidor calcula rz.
    "GET /v1/sap/actions/esbelteces/selected",
    "POST /v1/sap/actions/esbelteces/import-groups",
}


# Claves que una ruta ya existente gana de forma aditiva (el contrato previo sigue siendo un subconjunto, en el
# mismo orden): ``cad/status`` añade ``ifc`` (docs/CAD_IFC.md).
CLAVES_AMPLIADAS = {"POST /v1/sap/actions/cad/status": ["ifc"]}


def test_previous_contract_keys_are_intact(contracts):
    """Toda ruta que ya existía en ``origin/main`` conserva exactamente su
    contrato (sólo se permiten rutas nuevas)."""
    try:
        raw = subprocess.run(
            ["git", "show", f"{BASE_REF}:web/src/bridge_contracts.json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"{BASE_REF} no disponible")
    # Rutas retiradas a propósito: el puente ya no registra documentos por ruta pegada (sólo `pick`).
    previous = {r: s for r, s in json.loads(raw)["routes"].items() if r not in RUTAS_RETIRADAS}
    for route, spec in previous.items():
        assert route in contracts, f"ruta previa eliminada: {route}"
        if route in CLAVES_AMPLIADAS:
            nuevas = [
                k for k in CLAVES_AMPLIADAS[route] if k not in spec["keys"]
            ]  # ya fusionadas: no se repiten
            ampliado = {**spec, "keys": [*spec["keys"], *nuevas]}
            assert contracts[route] == ampliado, f"contrato previo modificado: {route}"
            continue
        assert contracts[route] == spec, f"contrato previo modificado: {route}"
    assert list(contracts)[: len(previous) - 2] == list(previous)[: len(previous) - 2]
