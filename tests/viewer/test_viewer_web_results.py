"""API de resultados del Viewer en el puente compartido (datos simulados, solo lectura).

Adaptado de SAP2000_AI_ASSISTANT@572b684 (tests/test_web_results.py): imports, rutas
``/v1/sap/actions/viewer/*`` y puente sustituido por dobles. La lógica de cada prueba
no cambia. El control de Host/Origin pasa al puente compartido (omitido aquí).
"""

from __future__ import annotations

import pytest

from bridge.viewer.core import results as res
from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeSapObject

# Lista de casos real del modelo simulado, antes de que el fixture la sustituya.
_list_cases_real = res.list_cases

PREFIJO = "/v1/sap/actions/viewer"


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


@pytest.fixture
def client(rutas_viewer):
    with rutas_viewer(Connector) as c:
        yield c


@pytest.fixture(autouse=True)
def fake_case_names(monkeypatch):
    # El simulador etiqueta sus filas con 'Ds' / 'ASD1.01', distinto de su lista de casos.
    monkeypatch.setattr(
        res,
        "list_cases",
        lambda model: {
            "cases": [
                {"value": "CASE:Ds", "name": "Ds", "kind": "case"},
                {"value": "COMBO:ASD1.01", "name": "ASD1.01", "kind": "combo"},
            ]
        },
    )


@pytest.fixture
def connected(client):
    client.conectar_demo()
    return client


def test_results_need_a_connection(client):
    assert client.get(PREFIJO + "/results/cases").status_code == 409
    assert client.get(PREFIJO + "/results/design").status_code == 409


def test_cases_list_cases_and_combos_only(connected, monkeypatch):
    # Esta prueba usa la lista real del modelo, no la del fixture.
    monkeypatch.setattr(res, "list_cases", _list_cases_real)
    cases = connected.get(PREFIJO + "/results/cases").json()["cases"]
    kinds = {c["kind"] for c in cases}
    assert kinds == {"case", "combo"}
    assert {"DEAD", "ASD1.01"} <= {c["name"] for c in cases}


def test_design_ratios_report_what_was_skipped(connected):
    body = connected.get(PREFIJO + "/results/design").json()
    assert body["ratios"]["F1"] == 0.85 and "C2" not in body["ratios"]  # C2 es N/A
    assert body["max"] == pytest.approx(max(body["ratios"].values()))
    assert isinstance(body["warnings"], list)


def test_area_stress_has_unit_and_honest_note(connected):
    r = connected.get(PREFIJO + "/results/area", params={"case": "Ds", "component": "M11"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit"] == "tonf" and body["component"] == "M11"
    assert body["values"]["A1"] == pytest.approx(3.175)  # media de 3.2, 3.6, 2.9 y 3.0 (esquinas)
    assert any("Promedio" in w for w in body["warnings"])
    r2 = connected.get(PREFIJO + "/results/area", params={"case": "Ds", "component": "F11"})
    assert r2.json()["unit"] == "tonf/m"


@pytest.mark.parametrize(
    "params",
    [
        {"case": "Ds", "component": "XX"},  # componente desconocido
        {"case": "NO_EXISTE", "component": "M11"},  # caso inventado: no llega al lector
        {"case": "ALL", "component": "M11"},  # nada de "todos": un caso concreto
        {"case": "Ds:OUTPUT:x", "component": "M11"},
    ],
)
def test_area_rejects_unknown_component_or_case(connected, params):
    assert connected.get(PREFIJO + "/results/area", params=params).status_code == 422


def test_case_is_required(connected):
    assert connected.get(PREFIJO + "/results/displacements").status_code == 422
    assert connected.get(PREFIJO + "/results/frame/F1").status_code == 422


def test_displacements_keep_units_and_the_largest_step(connected):
    body = connected.get(PREFIJO + "/results/displacements", params={"case": "Ds"}).json()
    assert body["unit"] == "m" and body["toMeters"] == 1.0
    assert set(body["nodes"]) == {"J1"}  # el simulador solo trae J1 para 'Ds'
    assert len(body["nodes"]["J1"]) == 6
    assert body["max"] > 0


def test_frame_forces_stations(connected):
    body = connected.get(PREFIJO + "/results/frame/F1", params={"case": "Ds"}).json()
    assert body["frame"] == "F1" and body["units"]["force"] == "tonf"
    s = body["stations"][0]
    assert {"x", "step", "P", "V2", "V3", "T", "M2", "M3"} <= set(s)


def test_case_without_rows_is_reported_not_invented(connected):
    r = connected.get(PREFIJO + "/results/displacements", params={"case": "ASD1.01"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_results"
    r = connected.get(PREFIJO + "/results/frame/F1", params={"case": "ASD1.01"})
    assert r.status_code == 409


@pytest.mark.skip(
    reason="Host/Origin y CORS los comprueba el puente compartido (bridge/sap2000_agent.py), no las rutas del Viewer"
)
def test_foreign_origin_rejected(connected):
    assert (
        connected.get(PREFIJO + "/results/cases", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
