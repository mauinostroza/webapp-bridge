"""Tests for bridge/foundations_cuts.py: puerto de app/sap2000/section_cuts.py
(Diseño de secciones) con un modelo COM falso, mismo patrón que
tests/test_bridge_espectro.py y tests/test_bridge_actions.py."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.foundations_cuts import (
    _indice_campo,
    _parsear_section_cut_analysis,
    _parsear_tabla_bd,
    extraer_fuerzas_section_cut,
    listar_section_cuts,
)

CAMPOS = ("Name", "Group", "DefinedBy", "ResultType", "Notes")


class _Db:
    def __init__(self, filas=None, campos=CAMPOS, apply_ret=(0, 0, 0, 0, "", 0), set_ret=0):
        self.filas = [list(f) for f in (filas or [])]
        self.campos = campos
        self.apply_ret = apply_ret
        self.set_ret = set_ret
        self.set_calls = []
        self.apply_calls = 0

    def GetTableForDisplayArray(self, table, *args):
        flat = [c for f in self.filas for c in f]
        return (1, tuple(self.campos), len(self.filas), tuple(flat), 0)

    def GetTableForEditingArray(self, table, *args):
        flat = [c for f in self.filas for c in f]
        return (1, tuple(self.campos), len(self.filas), tuple(flat), 0)

    def SetTableForEditingArray(self, table, version, campos, n, datos):
        self.set_calls.append((table, version, list(campos), n, list(datos)))
        return (version, n, self.set_ret)

    def ApplyEditedTables(self, *args):
        self.apply_calls += 1
        return self.apply_ret


class _Group:
    def __init__(self, groups):
        self.groups = groups
        self.created = []

    def GetNameList(self, *_a):
        return (0, len(self.groups), tuple(self.groups))

    def GetAssignments(self, name, *_a):
        items = self.groups.get(name, [])
        return (len(items), tuple(t for t, _ in items), tuple(n for _, n in items), 0)

    def SetGroup(self, name):
        self.created.append(name)
        return 0


class _Frame:
    def __init__(self):
        self.assigned = []

    def SetGroupAssign(self, name, group):
        self.assigned.append((name, group))
        return 0


class _Setup:
    def __init__(self):
        self.log = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.log.append(("deselect",))
        return 0

    def SetCaseSelectedForOutput(self, name, flag):
        self.log.append(("case", name, flag))
        return 0

    def SetComboSelectedForOutput(self, name, flag):
        self.log.append(("combo", name, flag))
        return 0

    def SetSectionCutSelectedForOutput(self, name, flag):
        self.log.append(("cut", name, flag))
        return 0


class _Results:
    def __init__(self, rows):
        self.Setup = _Setup()
        self.rows = rows
        self.calls = []

    def SectionCutAnalysis(self, name, *args):
        self.calls.append((name, args))
        if len(args) == 1:
            raise TypeError("needs the long signature")
        cols = list(zip(*self.rows)) if self.rows else [()] * 11
        return (len(self.rows), *[tuple(c) for c in cols], 0)


# Obj, Elm, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3
ROWS = [
    ("SC", "SC", "COMB1", "", 0.0, 3.0, 4.0, -10.0, 0.5, 6.0, 8.0),
    ("SC", "SC", "COMB2", "", 0.0, 1.0, 0.0, 20.0, 0.1, 0.0, 2.0),
    ("SC", "SC", "OTRA", "", 0.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0),
]


class _Model:
    def __init__(self, *, locked=False, rows=None, groups=None, db=None):
        self.locked = locked
        self.DatabaseTables = db or _Db()
        self.GroupDef = _Group(groups if groups is not None else {"G1": [(1, "J1"), (5, "S1"), (2, "F9")]})
        self.FrameObj = _Frame()
        self.Results = _Results(ROWS if rows is None else rows)
        self.units = 6
        self.unit_log = []
        self.LoadCases = type("LC", (), {"GetNameList": lambda s, *a: (0, 2, ("DEAD", "LIVE"))})()
        self.RespCombo = type("RC", (), {"GetNameList": lambda s, *a: (0, 2, ("COMB1", "COMB2"))})()
        self.SelectObj = type("SO", (), {"GetSelected": lambda s, *a: (2, (2, 1), ("F1", "J1"), 0)})()

    def GetModelIsLocked(self):
        return self.locked

    def SetModelIsLocked(self, flag):
        self.locked = flag
        return 0

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, u):
        self.unit_log.append(u)
        self.units = u
        return 0


class _Actor:
    def call(self, fn):
        return fn()


class _Session:
    def __init__(self, model):
        self.actor = _Actor()
        self.model = model

    def _model(self):
        return self.model


def _client(model) -> TestClient:
    app = FastAPI()
    install_routes(app, _Session(model), RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


P = "/v1/sap/actions/foundations/section-cuts"


def test_parsear_tabla_bd_and_alias_index():
    campos, filas = _parsear_tabla_bd((1, CAMPOS, 2, ("A", "G", "", "", "", "B", "H", "", "", ""), 0))
    assert list(campos) == list(CAMPOS)
    assert len(filas) == 2 and filas[1][0] == "B"
    assert _indice_campo(["Section Cut", "Group"], ("name", "sectioncut")) == 0
    assert _indice_campo(["x"], ("name",)) is None


def test_list_section_cuts_reads_names_and_groups_without_duplicates():
    db = _Db([["SC_001", "G1", "", "", ""], ["SC_001", "G1", "", "", ""], ["SC_002", "G2", "", "", ""]])
    assert listar_section_cuts(_Model(db=db)) == [
        {"nombre": "SC_001", "grupo": "G1"},
        {"nombre": "SC_002", "grupo": "G2"},
    ]
    assert _client(_Model(db=db)).get(P).json() == {
        "cuts": [{"nombre": "SC_001", "grupo": "G1"}, {"nombre": "SC_002", "grupo": "G2"}]
    }
    assert listar_section_cuts(_Model(db=_Db([]))) == []


def test_groups_route_lists_all_group_names():
    r = _client(_Model(groups={"ALL": [], "G1": [(2, "F1")]})).get(f"{P}/groups")
    assert r.json() == {"groups": ["ALL", "G1"]}


def test_apply_requires_confirmed():
    r = _client(_Model()).post(f"{P}/apply", json={"nombre": "SC_001", "frames": ["F1"]})
    assert r.status_code == 422
    assert "confirmed" in r.json()["detail"]


def test_apply_requires_frames_or_group():
    r = _client(_Model()).post(f"{P}/apply", json={"nombre": "SC_001", "confirmed": True})
    assert r.status_code == 422


def test_create_from_frames_builds_group_and_writes_table_row():
    model = _Model(db=_Db([["OLD", "GX", "", "", ""]]))
    r = _client(model).post(
        f"{P}/apply",
        json={"nombre": "SC_001", "grupo": "GRP_SC_001", "frames": ["F1", "F2"], "confirmed": True},
    )
    body = r.json()
    assert r.status_code == 200, body
    assert body == {
        "creado": True,
        "requiere_desbloqueo": False,
        "mensaje": "Section cut 'SC_001' definido sobre el grupo 'GRP_SC_001'.",
        "elementos": ["F1", "F2"],
    }
    assert model.GroupDef.created == ["GRP_SC_001"]
    assert model.FrameObj.assigned == [("F1", "GRP_SC_001"), ("F2", "GRP_SC_001")]
    table, version, campos, n, datos = model.DatabaseTables.set_calls[0]
    assert (table, version, n) == ("Section Cut Definitions", 1, 2)
    assert datos[:5] == ["OLD", "GX", "", "", ""]
    assert datos[5:] == ["SC_001", "GRP_SC_001", "Group", "Analysis", ""]
    assert model.DatabaseTables.apply_calls == 1


def test_create_replaces_existing_row_with_same_name():
    model = _Model(db=_Db([["SC_001", "OLD", "", "", ""]]))
    _client(model).post(f"{P}/apply", json={"nombre": "SC_001", "grupo": "G1", "confirmed": True})
    _t, _v, _c, n, datos = model.DatabaseTables.set_calls[0]
    assert n == 1 and datos[:2] == ["SC_001", "G1"]


def test_create_from_group_lists_points_shells_frames_in_that_order():
    model = _Model()
    r = _client(model).post(f"{P}/apply", json={"nombre": "SC_2", "grupo": "G1", "confirmed": True})
    assert r.json()["elementos"] == ["J1", "S1", "F9"]
    assert model.GroupDef.created == []  # el grupo ya existía: no se toca


def test_empty_group_is_an_error():
    r = _client(_Model(groups={"E": []})).post(
        f"{P}/apply", json={"nombre": "SC", "grupo": "E", "confirmed": True}
    )
    assert r.status_code == 409
    assert "no tiene nodos, shells ni frames" in r.json()["detail"]


def test_locked_model_requires_unlock_and_never_unlocks_on_its_own():
    model = _Model(locked=True)
    client = _client(model)
    body = {"nombre": "SC_001", "frames": ["F1"], "confirmed": True}
    r = client.post(f"{P}/apply", json=body)
    assert r.json()["creado"] is False and r.json()["requiere_desbloqueo"] is True
    assert model.locked is True and model.DatabaseTables.set_calls == []
    r = client.post(f"{P}/apply", json={**body, "desbloquear": True})
    assert r.json()["creado"] is True
    assert model.locked is False and len(model.DatabaseTables.set_calls) == 1


def test_preview_never_mutates():
    model = _Model(locked=True)
    client = _client(model)
    r = client.post(f"{P}/preview", json={"nombre": "SC", "frames": ["F1"]})
    assert r.json()["requiere_desbloqueo"] is True and r.json()["creado"] is False
    r = client.post(f"{P}/preview", json={"nombre": "SC", "frames": ["F1"], "desbloquear": True})
    assert r.json()["requiere_desbloqueo"] is False and r.json()["creado"] is False
    assert model.locked is True and model.DatabaseTables.set_calls == []
    assert model.GroupDef.created == [] and model.FrameObj.assigned == []


def test_table_error_gives_manual_fallback_message():
    model = _Model(db=_Db(set_ret=5))
    r = _client(model).post(f"{P}/apply", json={"nombre": "SC", "grupo": "G1", "confirmed": True})
    assert r.status_code == 409
    assert "SetTableForEditingArray devolvió código 5" in r.json()["detail"]


def test_apply_edited_tables_fatal_errors_raise():
    model = _Model(db=_Db(apply_ret=(1, 0, 0, 0, "bad group", 0)))
    r = _client(model).post(f"{P}/apply", json={"nombre": "SC", "grupo": "G1", "confirmed": True})
    assert r.status_code == 409
    assert "rechazó la definición del section cut" in r.json()["detail"]
    assert "bad group" in r.json()["detail"]


def test_unknown_fields_are_reported_with_manual_instructions():
    model = _Model(db=_Db(campos=("Foo", "Bar")))
    r = _client(model).post(f"{P}/apply", json={"nombre": "SC", "grupo": "G1", "confirmed": True})
    assert r.status_code == 409
    assert "campos disponibles: Foo, Bar" in r.json()["detail"]
    assert 'Importar section cuts desde SAP2000...' in r.json()["detail"]


def test_parse_section_cut_analysis_maps_columns():
    ret = (3, *[tuple(c) for c in zip(*ROWS)], 0)
    filas = _parsear_section_cut_analysis(ret)
    assert filas[0] == {"caso": "COMB1", "P": -10.0, "V2": 3.0, "V3": 4.0, "M2": 6.0, "M3": 8.0, "T": 0.5}


def test_parse_section_cut_analysis_rejects_short_response():
    with pytest.raises(RuntimeError, match="al menos 11"):
        _parsear_section_cut_analysis((0, (), 0))


def test_extract_forces_selects_cases_units_and_filters():
    model = _Model()
    filas = extraer_fuerzas_section_cut(model, "SC_001", ["COMB1", "COMB2", "RARA"])
    assert [f["caso"] for f in filas] == ["COMB1", "COMB2"]
    log = model.Results.Setup.log
    assert log[0] == ("deselect",)
    assert ("combo", "COMB1", True) in log and ("combo", "COMB2", True) in log
    # un nombre desconocido se prueba como combo y como caso
    assert ("combo", "RARA", True) in log and ("case", "RARA", True) in log
    assert ("cut", "SC_001", True) in log
    # firma corta falló, se usó la larga
    assert len(model.Results.calls[0][1]) == 1 and len(model.Results.calls[1][1]) == 12
    assert model.unit_log == [12, 6] and model.units == 6


def test_extract_forces_route_returns_results_per_cut():
    model = _Model(locked=True)
    r = _client(model).post(f"{P}/forces", json={"cuts": ["SC_001", "SC_002"], "combos": ["COMB1"]})
    body = r.json()
    assert r.status_code == 200
    assert body["model_locked"] is True
    assert set(body["resultados"]) == {"SC_001", "SC_002"}
    assert body["resultados"]["SC_001"] == [
        {"caso": "COMB1", "P": -10.0, "V2": 3.0, "V3": 4.0, "M2": 6.0, "M3": 8.0, "T": 0.5}
    ]


def test_extract_forces_without_combos_uses_all_load_cases():
    model = _Model(locked=True)
    _client(model).post(f"{P}/forces", json={"cuts": ["SC"], "combos": []})
    assert ("case", "DEAD", True) in model.Results.Setup.log


def _boom(*_a):
    raise RuntimeError("section cut inexistente")


def test_extract_forces_error_flags_model_not_run():
    model = _Model(locked=False)
    model.Results.SectionCutAnalysis = _boom
    r = _client(model).post(f"{P}/forces", json={"cuts": ["SC"], "combos": ["COMB1"]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["model_not_run"] is True and "section cut inexistente" in detail["message"]
    model = _Model(locked=True)
    model.Results.SectionCutAnalysis = _boom
    r = _client(model).post(f"{P}/forces", json={"cuts": ["SC"], "combos": ["COMB1"]})
    assert r.json()["detail"]["model_not_run"] is False


def test_extract_forces_empty_result_is_not_an_error():
    r = _client(_Model(locked=True, rows=[])).post(f"{P}/forces", json={"cuts": ["SC"], "combos": ["COMB1"]})
    assert r.status_code == 200 and r.json()["resultados"] == {"SC": []}


def test_extract_forces_validates_body():
    r = _client(_Model()).post(f"{P}/forces", json={"cuts": [], "combos": []})
    assert r.status_code == 422
