"""Tests for bridge/combinaciones.py: matriz de Response Combinations.

Fakes follow the same minimal-COM-double pattern as tests/test_bridge_actions.py
and tests/test_bridge_esbelteces.py. Several fakes only accept the SECOND
GetNameList/GetCaseList calling convention (``(0, [])`` etc.), forcing the
module's multi-signature fallback loop to actually run instead of always
matching the empty-args attempt.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.combinaciones import (
    Sap2000ComError,
    delete_response_combinations_from_sap2000,
    get_load_cases_from_sap2000,
    get_model_snapshot_from_sap2000,
    get_response_combinations_from_sap2000,
    send_combinations_to_sap2000,
)

# ---------------------------------------------------------------------------
# Fake SAP2000 COM model
# ---------------------------------------------------------------------------


class _LoadCases:
    def __init__(self, names: list[str]):
        self.names = names

    def GetNameList(self, *args):
        # Only the second attempted signature, (0, []), is accepted -
        # exercises the _call_get_name_list fallback loop.
        if args == ():
            raise TypeError("GetNameList() takes no arguments")
        return (0, len(self.names), tuple(self.names))


class _RespCombo:
    def __init__(self, combos: dict[str, list[tuple[int, str, float]]] | None = None):
        # combo name -> list of (item_type, case/member name, factor)
        self.combos: dict[str, list[tuple[int, str, float]]] = combos or {}
        self.added: list[tuple[str, int]] = []
        self.deleted: list[str] = []
        self.set_case_list_calls: list[tuple[str, int, str, float]] = []
        self.delete_fail: set[str] = set()
        self.set_case_fail: set[tuple[str, str]] = set()

    def GetNameList(self, *args):
        if args == ():
            raise TypeError("GetNameList() takes no arguments")
        names = tuple(self.combos.keys())
        return (0, len(names), names)

    def GetCaseList(self, name, *args):
        if args == ():
            raise TypeError("GetCaseList() takes no arguments")
        items = self.combos.get(name, [])
        item_types = tuple(i[0] for i in items)
        names = tuple(i[1] for i in items)
        factors = tuple(i[2] for i in items)
        return (0, len(items), item_types, names, factors)

    def Add(self, name, combo_type):
        self.added.append((name, combo_type))
        if name in self.combos:
            return 1  # already exists in the model
        self.combos[name] = []
        return 0

    def Delete(self, name):
        self.deleted.append(name)
        if name in self.delete_fail:
            # Simulate a Delete SAP2000 rejects: the combo stays in the model,
            # so a retried Add() below will also fail with "already exists".
            return 1
        self.combos.pop(name, None)
        return 0

    def SetCaseList(self, name, kind, member_or_case, factor):
        self.set_case_list_calls.append((name, kind, member_or_case, factor))
        if (name, member_or_case) in self.set_case_fail:
            return 1
        self.combos.setdefault(name, []).append((kind, member_or_case, factor))
        return 0


class FakeModel:
    def __init__(self, *, load_case_names=None, combos=None):
        self.LoadCases = _LoadCases(load_case_names or [])
        self.RespCombo = _RespCombo(combos)


# ---------------------------------------------------------------------------
# _call_get_name_list fallback / get_load_cases_from_sap2000
# ---------------------------------------------------------------------------


def test_name_list_signature_fallback_reads_load_cases():
    model = FakeModel(load_case_names=["D", "L", "W"])
    result = get_load_cases_from_sap2000(model)
    assert result == [
        {"name": "D", "sap_type": None},
        {"name": "L", "sap_type": None},
        {"name": "W", "sap_type": None},
    ]


# ---------------------------------------------------------------------------
# snapshot / existing
# ---------------------------------------------------------------------------


def test_snapshot_shape():
    model = FakeModel(
        load_case_names=["D", "L"],
        combos={"C1": [(0, "D", 1.0), (0, "L", 1.2)]},
    )
    snapshot = get_model_snapshot_from_sap2000(model)
    assert snapshot == {
        "load_cases": [{"name": "D", "sap_type": None}, {"name": "L", "sap_type": None}],
        "combinations": [
            {
                "name": "C1",
                "items": [
                    {"item_type": 0, "name": "D", "factor": 1.0},
                    {"item_type": 0, "name": "L", "factor": 1.2},
                ],
            }
        ],
    }


def test_get_response_combinations_reads_every_combo():
    model = FakeModel(combos={"C1": [(0, "D", 1.0)], "C2": [(0, "L", 1.5)]})
    result = get_response_combinations_from_sap2000(model)
    assert {c["name"] for c in result} == {"C1", "C2"}


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_returns_failed_names():
    model = FakeModel(combos={"C1": [], "C2": []})
    model.RespCombo.delete_fail = {"C2"}
    failed = delete_response_combinations_from_sap2000(model, ["C1", "C2"])
    assert failed == ["C2"]
    assert model.RespCombo.deleted == ["C1", "C2"]


# ---------------------------------------------------------------------------
# send: happy path / existing combo replace / envelope / warnings
# ---------------------------------------------------------------------------


def _matrix(*combinations: dict) -> dict:
    return {"headers": [], "combinations": list(combinations)}


def test_send_happy_path():
    model = FakeModel(load_case_names=["D", "L"])
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1", "L": "1.2"}, "envelope_members": None})
    result = send_combinations_to_sap2000(model, matrix)
    assert result.created == 1
    assert result.warnings == []
    assert model.RespCombo.added == [("C1", 0)]
    assert ("C1", 0, "D", 1.0) in model.RespCombo.set_case_list_calls
    assert ("C1", 0, "L", 1.2) in model.RespCombo.set_case_list_calls


def test_send_existing_combo_deletes_and_retries():
    model = FakeModel(load_case_names=["D"], combos={"C1": [(0, "OLD", 1.0)]})
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1"}, "envelope_members": None})
    result = send_combinations_to_sap2000(model, matrix)
    assert result.created == 1
    assert model.RespCombo.added == [("C1", 0), ("C1", 0)]
    assert model.RespCombo.deleted == ["C1"]
    # the old term is gone; only the new one remains.
    assert model.RespCombo.combos["C1"] == [(0, "D", 1.0)]


def test_send_envelope_combo_uses_type_1_and_factor_1():
    model = FakeModel(combos={"MEMBER1": [], "MEMBER2": []})
    matrix = _matrix({"name": "ENV1", "factors_by_case": {}, "envelope_members": ["MEMBER1", "MEMBER2"]})
    result = send_combinations_to_sap2000(model, matrix)
    assert result.created == 1
    assert model.RespCombo.added[-1] == ("ENV1", 1)
    assert model.RespCombo.set_case_list_calls == [
        ("ENV1", 1, "MEMBER1", 1.0),
        ("ENV1", 1, "MEMBER2", 1.0),
    ]


def test_send_missing_load_case_warns_without_com_call():
    model = FakeModel(load_case_names=["D"])
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1", "GHOST": "1"}, "envelope_members": None})
    result = send_combinations_to_sap2000(model, matrix)
    assert result.created == 1
    assert any(
        "GHOST" in w and "revisar que el estado de carga se encuentre creado en SAP2000" in w
        for w in result.warnings
    )
    assert all(call[2] != "GHOST" for call in model.RespCombo.set_case_list_calls)


def test_send_skips_zero_factor():
    model = FakeModel(load_case_names=["D", "L"])
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1", "L": "0"}, "envelope_members": None})
    send_combinations_to_sap2000(model, matrix)
    assert model.RespCombo.set_case_list_calls == [("C1", 0, "D", 1.0)]


def test_send_duplicate_name_warns_and_last_definition_wins():
    model = FakeModel(load_case_names=["D", "L"])
    matrix = _matrix(
        {"name": "C1", "factors_by_case": {"D": "1"}, "envelope_members": None},
        {"name": "C1", "factors_by_case": {"L": "1"}, "envelope_members": None},
    )
    result = send_combinations_to_sap2000(model, matrix)
    assert any("'C1' aparece 2 veces en la matriz" in w for w in result.warnings)
    assert model.RespCombo.combos["C1"] == [(0, "L", 1.0)]


def test_send_total_failure_raises_structured_error():
    model = FakeModel(load_case_names=["D"], combos={"C1": [(0, "OLD", 1.0)]})
    model.RespCombo.delete_fail = {"C1"}
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1"}, "envelope_members": None})
    with pytest.raises(Sap2000ComError) as excinfo:
        send_combinations_to_sap2000(model, matrix)
    assert excinfo.value.detail["code"] == "send_failed"
    assert "No se pudo enviar ninguna combinación a SAP2000" in excinfo.value.detail["message"]
    assert "No se pudo crear la combinación 'C1'." in excinfo.value.detail["message"]


# ---------------------------------------------------------------------------
# Routes (installed via bridge.actions.install_routes, same fake session
# pattern as test_bridge_esbelteces.py)
# ---------------------------------------------------------------------------


class _FakeActor:
    def call(self, fn):
        return fn()


class _FakeSession:
    def __init__(self, model):
        self.actor = _FakeActor()
        self.model = model

    def _model(self):
        return self.model


def _client(model) -> TestClient:
    app = FastAPI()
    install_routes(app, _FakeSession(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_route_snapshot():
    model = FakeModel(load_case_names=["D"], combos={"C1": [(0, "D", 1.0)]})
    response = _client(model).get("/v1/sap/actions/combinaciones/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["load_cases"] == [{"name": "D", "sap_type": None}]
    assert body["combinations"][0]["name"] == "C1"


def test_route_existing():
    model = FakeModel(combos={"C1": [(0, "D", 1.0)]})
    response = _client(model).get("/v1/sap/actions/combinaciones/existing")
    assert response.status_code == 200
    assert response.json()["combinations"][0]["name"] == "C1"


def test_route_delete_requires_confirmed():
    model = FakeModel(combos={"C1": []})
    response = _client(model).post("/v1/sap/actions/combinaciones/delete", json={"names": ["C1"]})
    assert response.status_code == 422
    assert model.RespCombo.deleted == []


def test_route_delete_confirmed_returns_failed():
    model = FakeModel(combos={"C1": [], "C2": []})
    model.RespCombo.delete_fail = {"C2"}
    response = _client(model).post(
        "/v1/sap/actions/combinaciones/delete", json={"names": ["C1", "C2"], "confirmed": True}
    )
    assert response.status_code == 200
    assert response.json() == {"failed": ["C2"]}


def test_route_send_requires_confirmed():
    model = FakeModel(load_case_names=["D"])
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1"}, "envelope_members": None})
    response = _client(model).post("/v1/sap/actions/combinaciones/send", json={"matrix": matrix})
    assert response.status_code == 422
    assert model.RespCombo.added == []


def test_route_send_confirmed_returns_created_and_warnings():
    model = FakeModel(load_case_names=["D"])
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1", "GHOST": "2"}, "envelope_members": None})
    response = _client(model).post(
        "/v1/sap/actions/combinaciones/send", json={"matrix": matrix, "confirmed": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert any("GHOST" in w for w in body["warnings"])


def test_route_send_total_failure_returns_409_with_send_failed_body():
    model = FakeModel(load_case_names=["D"], combos={"C1": [(0, "OLD", 1.0)]})
    model.RespCombo.delete_fail = {"C1"}
    matrix = _matrix({"name": "C1", "factors_by_case": {"D": "1"}, "envelope_members": None})
    response = _client(model).post(
        "/v1/sap/actions/combinaciones/send", json={"matrix": matrix, "confirmed": True}
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "send_failed"
    assert "No se pudo enviar ninguna combinación a SAP2000" in body["message"]
