"""Red de seguridad: cada ruta del puente consumida por la web se ejecuta con
COM falso (mismos patrones de doble que ``test_bridge_actions.py``,
``test_bridge_esbelteces.py``, ``test_bridge_combinaciones.py`` y
``test_bridge_espectro.py``) y las claves de nivel superior (y de cada item,
para listas) de la respuesta real se comparan contra
``web/src/bridge_contracts.json`` — la misma fuente que
``web/src/bridge_contracts.test.ts`` usa del lado de la web.

Esta prueba nació del mismo bug que arregló el commit ``e346141``: el puente
devolvía ``joints`` en ``/v1/sap/actions/foundations/groups`` mientras la web
leía ``nodes``. Frontend y puente se escriben en paralelo y ninguna otra
prueba cruza ambos lados; ésta sí.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes

CONTRACTS_PATH = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"


@pytest.fixture(scope="module")
def contracts() -> dict:
    return json.loads(CONTRACTS_PATH.read_text("utf-8"))["routes"]


def assert_matches_contract(contracts: dict, route: str, body: dict) -> None:
    """Compara las claves de nivel superior (y de cada item de una lista) de
    ``body`` contra la entrada ``route`` del contrato. Falla con un mensaje
    que nombra la ruta y el conjunto de claves esperado vs. el real, para que
    un desajuste sea obvio sin tener que abrir el JSON.
    """
    spec = contracts[route]
    expected_keys = set(spec["keys"])
    actual_keys = set(body.keys())
    assert actual_keys == expected_keys, (
        f"{route}: claves de nivel superior no coinciden con el contrato.\n"
        f"esperado={sorted(expected_keys)} real={sorted(actual_keys)}"
    )
    for list_key, item_keys in spec.get("items", {}).items():
        items = body[list_key]
        if isinstance(
            items, dict
        ):  # columnas paralelas (p. ej. hormigon/frame-forces): se comparan las claves
            assert set(items) == set(item_keys), (
                f"{route}: claves de {list_key} no coinciden con el contrato.\n"
                f"esperado={sorted(item_keys)} real={sorted(items)}"
            )
            continue
        assert isinstance(items, list), f"{route}: {list_key!r} debería ser una lista"
        if not items:
            continue
        first = items[0]
        assert isinstance(first, dict), f"{route}: los items de {list_key!r} deberían ser objetos"
        actual_item_keys = set(first.keys())
        expected_item_keys = set(item_keys)
        assert actual_item_keys == expected_item_keys, (
            f"{route}: claves de {list_key}[] no coinciden con el contrato.\n"
            f"esperado={sorted(expected_item_keys)} real={sorted(actual_item_keys)}"
        )


# ---------------------------------------------------------------------------
# bridge.actions.install_routes: una sola sesión falsa (mismo patrón que
# test_bridge_esbelteces.py/test_bridge_combinaciones.py/test_bridge_espectro.py)
# instala TODAS las rutas de acciones (profiles/foundations/esbelteces/
# combinaciones/espectro) sobre un único modelo falso que expone todo lo que
# cada ruta ejercitada aquí necesita.
# ---------------------------------------------------------------------------


class _PropMaterial:
    def GetNameList(self, *_args):
        return (0, 1, ("A36",))

    def GetOSteel_1(self, _mat_prop, *_args):
        return (250.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0)


class _PropFrame:
    def GetNameList(self, *_args):
        return (0, 1, ("W310",))

    def GetTypeOAPI(self, _name, *_args):
        return (10, 0)  # General: ningún Get* de familia conocida lo acepta

    def GetGeneral(self, name, *_args):
        return (name, "A36", 300.0, 150.0) + (0,) * 14 + (0,)

    def GetSectProps(self, _name, *_args):
        return (100.0, 0, 0, 0, 400.0, 900.0, 0, 0, 0, 0, 0, 0, 0)


class _SelectObj:
    def GetSelected(self, *_args):
        # J1/J2 = puntos (1), S1/S2 = shells (5), F1 = frame (2)
        return (5, (1, 1, 5, 5, 2), ("J1", "J2", "S1", "S2", "F1"), 0)

    def ClearSelection(self):
        return 0


class _FrameObj:
    def GetSection(self, name, *_args):
        return ("W310", "", 0)

    def GetPoints(self, name, *_args):
        return ("J1", "J2", 0)

    def SetSection(self, *_args):
        return 0

    def SetSelected(self, *_args):
        return 0


class _PointObj:
    _coords = {"J1": (0.0, 0.0, 0.0), "J2": (1.0, 0.0, 0.0)}
    _loads: dict[str, list] = {}

    def GetCoordCartesian(self, name, *_args):
        x, y, z = self._coords.get(name, (0.0, 0.0, 0.0))
        return (0, x, y, z)

    def GetLoadForce(self, joint, *_args):
        return (0, (joint,), (), (), (), (), (), (), (), (), (), 0)

    def GetNameList(self, *_args):
        return (0, 2, ("J1", "J2"))

    def SetLoadForce(self, *_args):
        return 0


class _LoadCases:
    def GetNameList(self, *args):
        if not args:
            # bridge.actions/bridge.combinaciones: (ret, count, names).
            return (0, 2, ("DEAD", "LIVE"))
        # bridge.espectro siempre llama con (0, []): (count, names, ret).
        return (2, ("DEAD", "LIVE"), 0)

    def GetTypeOAPI(self, _name, *_args):
        return (3, 0)  # CASE_TYPE_MODAL


class _RespCombo:
    def GetNameList(self, *_args):
        return (0, 1, ("COMB1",))

    def GetCaseList(self, _name, *_args):
        return (0, (), (), ())

    def Add(self, *_args):
        return 0

    def SetCaseList(self, *_args):
        return 0

    def Delete(self, *_args):
        return 0


class _LoadPatterns:
    def GetNameList(self, *_args):
        return (0, 2, ("DEAD", "LIVE"))


class _GroupDef:
    def GetNameList(self, *_args):
        return (0, 1, ("G1",))

    def GetAssignments(self, name, *_args):
        return (2, (1, 5), ("J1", "S1"), 0)


class _AreaObj:
    def GetElm(self, area):
        return (0, 1, ("E1",))


class _AreaElm:
    def GetPoints(self, elm):
        return (0, 3, ("J1", "J2", "J1"))


class _ResultsSetup:
    def DeselectAllCasesAndCombosForOutput(self):
        return 0

    def SetCaseSelectedForOutput(self, *_args):
        return 0

    def SetComboSelectedForOutput(self, *_args):
        return 0


class _Results:
    Setup = _ResultsSetup()


class _DatabaseTables:
    def GetTableForDisplayArray(self, *_args):
        return (0, ("Area", "OutputCase", "Pressure"), 1, ("E1", "COMB1", "-1.5"))


class _AISC360_16:
    def SetOverwrite(self, *_args):
        return 0


class _DesignSteel:
    AISC360_16 = _AISC360_16()

    def GetCode(self, _name):
        return ("AISC 360-16", 0)


class _Func:
    def GetNameList(self, *_args):
        # bridge.espectro: model.Func.GetNameList(0, [], FUNC_TYPE_RESPONSE_SPECTRUM) -> (count, names, ret)
        return (0, (), 0)

    class FuncRS:
        @staticmethod
        def SetUser(*_args):
            return 0


class MegaModel:
    """Un solo modelo COM falso, suficiente para todas las rutas GET/POST
    de acciones ejercitadas por ``assert_matches_contract`` más abajo — no
    pretende ser un doble completo de SAP2000 (ver los archivos test_bridge_*
    citados en el docstring del módulo para dobles más realistas por función)."""

    def __init__(self):
        self.PropMaterial = _PropMaterial()
        self.PropFrame = _PropFrame()
        self.SelectObj = _SelectObj()
        self.FrameObj = _FrameObj()
        self.PointObj = _PointObj()
        self.LoadCases = _LoadCases()
        self.RespCombo = _RespCombo()
        self.LoadPatterns = _LoadPatterns()
        self.GroupDef = _GroupDef()
        self.AreaObj = _AreaObj()
        self.AreaElm = _AreaElm()
        self.Results = _Results()
        self.DatabaseTables = _DatabaseTables()
        self.DesignSteel = _DesignSteel()
        self.Func = _Func()

    def GetPresentUnits(self):
        return 6

    def SetPresentUnits(self, _value):
        return 0

    def GetModelFilename(self):
        return "fake.sdb"


class _FakeActor:
    def call(self, fn):
        return fn()


class _FakeSession:
    def __init__(self, model):
        self.actor = _FakeActor()
        self.model = model

    def _model(self):
        return self.model


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_routes(app, _FakeSession(MegaModel()), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_profiles_catalog(client, contracts):
    response = client.get("/v1/sap/actions/profiles/catalog")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/profiles/catalog", response.json())


def test_profiles_selected_frames(client, contracts):
    response = client.get("/v1/sap/actions/profiles/selected-frames")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/profiles/selected-frames", response.json())


def test_profiles_section_apply(client, contracts):
    response = client.post(
        "/v1/sap/actions/profiles/section/apply",
        json={"frames": ["F1"], "section": "W310", "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/profiles/section/apply", response.json())


def test_foundations_cases(client, contracts):
    response = client.get("/v1/sap/actions/foundations/cases")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/cases", response.json())


def test_foundations_selection(client, contracts):
    response = client.get("/v1/sap/actions/foundations/selection?kind=joints")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/selection", response.json())


def test_foundations_groups(client, contracts):
    """El bug real: el puente devolvía ``joints``, la web (FoundationGroup) lee ``nodes``."""
    response = client.get("/v1/sap/actions/foundations/groups")
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/groups", body)
    assert body["groups"][0]["nodes"] == ["J1"]


def test_foundations_load_patterns(client, contracts):
    response = client.get("/v1/sap/actions/foundations/load-patterns")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/load-patterns", response.json())


def test_foundations_loads_existing(client, contracts):
    response = client.get("/v1/sap/actions/foundations/loads/existing?joints=J1")
    assert response.status_code == 200
    body = response.json()
    # El modelo falso no devuelve filas (GetLoadForce vacío); igual se
    # verifica la forma de nivel superior — ver test_bridge_actions.py para
    # una prueba con filas reales y comprobación de sus claves.
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/loads/existing", body)
    spec = contracts["GET /v1/sap/actions/foundations/loads/existing"]
    row = {
        "joint": "J1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": 0.0,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }
    assert set(row.keys()) == set(spec["items"]["rows"])


def test_foundations_loads_preview(client, contracts):
    row = {"joint": "J1", "load_pat": "DEAD", "coord_sys": "Global", "f1": 1.0}
    response = client.post(
        "/v1/sap/actions/foundations/loads/preview", json={"rows": [row], "mode": "replace"}
    )
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/loads/preview", body)


def test_foundations_loads_apply(client, contracts):
    row = {"joint": "J1", "load_pat": "DEAD", "coord_sys": "Global", "f1": 1.0}
    response = client.post(
        "/v1/sap/actions/foundations/loads/apply",
        json={"rows": [row], "mode": "replace", "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/loads/apply", response.json())


def test_foundations_shell_diagram(client, contracts):
    response = client.post(
        "/v1/sap/actions/foundations/shell-diagram", json={"shells": ["S1"], "combinacion": "COMB1"}
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/shell-diagram", response.json())


def test_foundations_shell_results(client, contracts):
    response = client.post(
        "/v1/sap/actions/foundations/shell-results",
        json={"shells": ["S1"], "combinacion": "COMB1", "tipo": "forces", "componente": "M11", "paso": "max"},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/shell-results", response.json())


def test_foundations_shell_results_multi(client, contracts):
    franja = {"centro_x": 0.5, "centro_y": 0.0, "largo": 1.0, "ancho": 1.0, "largo_en_x": True}
    response = client.post(
        "/v1/sap/actions/foundations/shell-results-multi",
        json={
            "shells": ["S1"],
            "combos": [{"combinacion": "COMB1", "paso": "min"}],
            "tipo": "forces",
            "franjas": [franja],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/shell-results-multi", body)
    assert body["filas"], "una fila por franja x combo, aunque no haya datos"


def test_foundations_pedestal_candidates(client, contracts):
    response = client.post("/v1/sap/actions/foundations/pedestal-candidates", json={"joints": ["J1"]})
    assert response.status_code == 200
    assert_matches_contract(
        contracts, "POST /v1/sap/actions/foundations/pedestal-candidates", response.json()
    )


def test_esbelteces_selected_raw(client, contracts):
    response = client.get("/v1/sap/actions/esbelteces/selected-raw")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/esbelteces/selected-raw", response.json())


def test_esbelteces_groups(client, contracts):
    response = client.get("/v1/sap/actions/esbelteces/groups")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/esbelteces/groups", response.json())


def test_esbelteces_import_groups_raw(client, contracts):
    response = client.post(
        "/v1/sap/actions/esbelteces/import-groups-raw", json={"map": {"Columna_SMF": "G1"}}
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/esbelteces/import-groups-raw", response.json())


def test_esbelteces_sections(client, contracts):
    response = client.get("/v1/sap/actions/esbelteces/sections")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/esbelteces/sections", response.json())


def test_esbelteces_section_apply(client, contracts):
    response = client.post(
        "/v1/sap/actions/esbelteces/section/apply",
        json={"frame": "F1", "section": "W310", "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/esbelteces/section/apply", response.json())


def test_esbelteces_highlight(client, contracts):
    response = client.post("/v1/sap/actions/esbelteces/highlight", json={"frames": ["F1"]})
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/esbelteces/highlight", response.json())


def test_esbelteces_k_factors_apply(client, contracts):
    response = client.post(
        "/v1/sap/actions/esbelteces/k-factors/apply",
        json={"items": [{"frame": "F1", "kx": 1.0, "ky": 1.0}], "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/esbelteces/k-factors/apply", response.json())


def test_combinaciones_snapshot(client, contracts):
    response = client.get("/v1/sap/actions/combinaciones/snapshot")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/combinaciones/snapshot", response.json())


def test_combinaciones_existing(client, contracts):
    response = client.get("/v1/sap/actions/combinaciones/existing")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/combinaciones/existing", response.json())


def test_combinaciones_delete(client, contracts):
    response = client.post(
        "/v1/sap/actions/combinaciones/delete", json={"names": ["COMB1"], "confirmed": True}
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/combinaciones/delete", response.json())


def test_combinaciones_send(client, contracts):
    matrix = {"headers": [], "combinations": [{"name": "C1", "factors_by_case": {"DEAD": "1.0"}}]}
    response = client.post("/v1/sap/actions/combinaciones/send", json={"matrix": matrix, "confirmed": True})
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/combinaciones/send", response.json())


def test_espectro_functions(client, contracts):
    response = client.get("/v1/sap/actions/espectro/functions")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/espectro/functions", response.json())


def test_espectro_spectra(client, contracts):
    response = client.get("/v1/sap/actions/espectro/spectra")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/espectro/spectra", response.json())


def test_espectro_load_cases(client, contracts):
    response = client.get("/v1/sap/actions/espectro/load-cases")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/espectro/load-cases", response.json())


def test_espectro_send(client, contracts):
    response = client.post(
        "/v1/sap/actions/espectro/send",
        json={"jobs": [{"nombre": "F1", "periods": [0.1], "values": [0.2]}], "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(contracts, "POST /v1/sap/actions/espectro/send", response.json())


# ---------------------------------------------------------------------------
# bridge/sap2000_agent.py: status/snapshot no pasan por install_routes de
# arriba; se ejercitan sus funciones puras directamente (mismo patrón que
# test_bridge.py), sin STA real ni COM de Windows.
# ---------------------------------------------------------------------------


def test_status_shape(monkeypatch, contracts):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import SapSession

    session = SapSession()
    assert_matches_contract(contracts, "GET /v1/sap/status", session._status_action())

    # Conectado: la respuesta agrega model_path (y, si se puede leer,
    # version) sobre las claves siempre presentes del contrato — se
    # comprueba por separado en vez de con assert_matches_contract (que exige
    # igualdad exacta), porque esas dos son opcionales según lo que SAP2000
    # logre reportar.
    class StatusModel:
        def GetModelFilename(self):
            return "fake.sdb"

    session.model = StatusModel()
    session.sap_object = object()
    connected_body = session._status_action()
    assert set(contracts["GET /v1/sap/status"]["keys"]) <= set(connected_body.keys())
    assert connected_body["model_path"] == "fake.sdb"


def test_snapshot_shape(monkeypatch, contracts):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    from bridge.sap2000_agent import _snapshot_from_model

    class Setup:
        def DeselectAllCasesAndCombosForOutput(self):
            return 0

        def SetCaseSelectedForOutput(self, *_args):
            return 0

        def SetComboSelectedForOutput(self, *_args):
            return 0

    class Results:
        def __init__(self):
            self.Setup = Setup()

        def JointReact(self, name, item_type):
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (1.0,),
                (2.0,),
                (0.0,),
                (0.0,),
                (0.0,),
                (0.0,),
                0,
            )

        def JointDispl(self, name, item_type):
            return (
                1,
                (name,),
                (name,),
                ("DEAD",),
                ("Max",),
                (0.0,),
                (0.1,),
                (0.2,),
                (0.0,),
                (0.0,),
                (0.0,),
                (0.0,),
                0,
            )

    class DatabaseTables:
        @staticmethod
        def GetTableForDisplayArray(*_args):
            return (0, (), 0, ("Area", "OutputCase", "Pressure"), 1, ("A1", "DEAD", "-2.5"))

    class SnapshotModel:
        def __init__(self):
            self.PointObj = type(
                "P",
                (),
                {
                    "GetNameList": lambda self: (0, 1, ("1",)),
                    "GetCoordCartesian": lambda self, n: (0, 0.0, 0.0, 0.0),
                },
            )()
            self.Results = Results()
            self.DatabaseTables = DatabaseTables()
            self.LoadCases = type("L", (), {"GetNameList": lambda self: (0, 0, ())})()

        def GetPresentUnits(self):
            return 99

        def SetPresentUnits(self, _v):
            return 0

        def GetModelFilename(self):
            return "fake.sdb"

        def GetModelIsLocked(self):
            return True

    body = _snapshot_from_model(
        SnapshotModel(), groups=[{"id": "G1", "name": "G1", "nodes": [], "shells": []}]
    )
    assert_matches_contract(contracts, "POST /v1/sap/snapshot", body)


# ---------------------------------------------------------------------------
# bridge/foundations_cuts.py (Diseño de secciones): modelo falso propio, con
# una tabla "Section Cut Definitions" no vacía y un SectionCutAnalysis.
# ---------------------------------------------------------------------------


class _CutsDb:
    def GetTableForDisplayArray(self, *_args):
        return (1, ("Name", "Group"), 1, ("SC_001", "G1"), 0)

    def GetTableForEditingArray(self, *_args):
        return (1, ("Name", "Group"), 0, (), 0)

    def SetTableForEditingArray(self, _t, version, _c, n, _d):
        return (version, n, 0)

    def ApplyEditedTables(self, *_args):
        return (0, 0, 0, 0, "", 0)


class _CutsResults:
    Setup = _ResultsSetup()

    def SectionCutAnalysis(self, _name, *_args):
        row = [("SC",), ("SC",), ("COMB1",), ("",), (0.0,)] + [(1.0,)] * 6
        return (1, *row, 0)


class _CutsModel(MegaModel):
    def __init__(self):
        super().__init__()
        self.DatabaseTables = _CutsDb()
        self.Results = _CutsResults()

    def GetModelIsLocked(self):
        return False


@pytest.fixture
def cuts_client() -> TestClient:
    app = FastAPI()
    install_routes(app, _FakeSession(_CutsModel()), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_foundations_selection_frames(client, contracts):
    response = client.get("/v1/sap/actions/foundations/selection?kind=frames")
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/selection", body)
    assert body["names"] == ["F1"]


def test_foundations_section_cuts_list_and_groups(cuts_client, contracts):
    response = cuts_client.get("/v1/sap/actions/foundations/section-cuts")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/section-cuts", response.json())
    response = cuts_client.get("/v1/sap/actions/foundations/section-cuts/groups")
    assert response.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/actions/foundations/section-cuts/groups", response.json())


@pytest.mark.parametrize("action", ["preview", "apply"])
def test_foundations_section_cuts_create(cuts_client, contracts, action):
    response = cuts_client.post(
        f"/v1/sap/actions/foundations/section-cuts/{action}",
        json={"nombre": "SC_002", "frames": ["F1"], "grupo": "GRP_SC_002", "confirmed": True},
    )
    assert response.status_code == 200
    assert_matches_contract(
        contracts, f"POST /v1/sap/actions/foundations/section-cuts/{action}", response.json()
    )


def test_foundations_section_cuts_forces(cuts_client, contracts):
    response = cuts_client.post(
        "/v1/sap/actions/foundations/section-cuts/forces", json={"cuts": ["SC_001"], "combos": ["COMB1"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "POST /v1/sap/actions/foundations/section-cuts/forces", body)
    assert set(body["resultados"]["SC_001"][0]) == {"caso", "P", "V2", "V3", "M2", "M3", "T"}


# ---------------------------------------------------------------------------
# bridge/pairing.py: las dos rutas exentas del token, mismo patrón de
# aislamiento de estado que test_bridge_pairing.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_pairing_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    from bridge import pairing

    pairing.reset_state_for_tests()
    yield
    pairing.reset_state_for_tests()


def test_pairing_request_and_poll_shape(contracts):
    from bridge.sap2000_agent import app

    client = TestClient(app, base_url="http://127.0.0.1:8765")
    origin = "http://localhost:5173"
    response = client.post("/v1/sap/pairing/request", headers={"Origin": origin})
    assert response.status_code == 200
    body = response.json()
    assert_matches_contract(contracts, "POST /v1/sap/pairing/request", body)

    poll = client.get(f"/v1/sap/pairing/poll/{body['request_id']}")
    assert poll.status_code == 200
    assert_matches_contract(contracts, "GET /v1/sap/pairing/poll/{id}", poll.json())
