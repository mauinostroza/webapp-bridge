"""Tests for bridge/espectro.py: puerto de sap2000_bridge.py + las funciones
_calcular_ps/_calcular_qxqy/_calcular_modal/_enviar_curvas de
sap2000_dialogs.py (proyecto de escritorio Espectro NCh2369).

Fakes follow the same minimal-COM-double pattern as
tests/test_bridge_combinaciones.py and tests/test_bridge_esbelteces.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import install_routes
from bridge.espectro import (
    NO_RESULTS_HINT,
    SapEspectroError,
    calcular_modal,
    calcular_ps,
    calcular_qxqy,
    enviar_curvas,
    get_base_reaction_sums,
    get_modal_periods_at_max_mass,
    list_load_cases,
    list_response_spectrum_functions,
    push_curve_to_sap2000,
)

# ---------------------------------------------------------------------------
# Fake SAP2000 COM model
# ---------------------------------------------------------------------------


class _FuncRS:
    def __init__(self, fail_names: set[str] | None = None):
        self.fail_names = fail_names or set()
        self.calls: list[tuple[str, int, list[float], list[float], float]] = []

    def SetUser(self, func_name, n, periods, values, damp_ratio):
        self.calls.append((func_name, n, list(periods), list(values), damp_ratio))
        if func_name in self.fail_names:
            return 1
        return 0


class _Func:
    def __init__(self, names: list[str] | None = None, fail_names: set[str] | None = None):
        self.names = names or []
        self.FuncRS = _FuncRS(fail_names)
        self.get_name_list_calls: list[tuple] = []

    def GetNameList(self, start, arr, func_type):
        self.get_name_list_calls.append((start, arr, func_type))
        return (len(self.names), tuple(self.names), 0)


class _LoadCases:
    def __init__(self, names: list[str] | None = None, types: dict[str, int] | None = None):
        self.names = names or []
        self.types = types or {}
        self.get_type_calls: list[str] = []

    def GetNameList(self, start, arr):
        return (len(self.names), tuple(self.names), 0)

    def GetTypeOAPI(self, name, a, b):
        self.get_type_calls.append(name)
        return (self.types.get(name, 1), 0)


class _ResultsSetup:
    def __init__(self):
        self.deselect_calls = 0
        self.selected_case: str | None = None
        self.select_history: list[str] = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.deselect_calls += 1

    def SetCaseSelectedForOutput(self, case_name):
        self.selected_case = case_name
        self.select_history.append(case_name)
        return 0


_TON_M_C = 12
_KGF_M_C = 8
_KGF_PER_TONF = 1000.0


class _Results:
    def __init__(
        self,
        base_react: dict[str, tuple[list[float], list[float], list[float]]] | None = None,
        modal: dict[str, tuple[list[float], list[float], list[float]]] | None = None,
    ):
        self.Setup = _ResultsSetup()
        self.base_react = base_react or {}
        self.modal = modal or {}
        self.base_react_calls: list[str | None] = []
        self.modal_calls: list[str | None] = []

    def BaseReact(self, *args):
        case = self.Setup.selected_case
        self.base_react_calls.append(case)
        data = self.base_react.get(case)
        if data is None:
            return (0, 0, 0, 0, (), (), (), 0, 0, 0, 0)
        fx, fy, fz = data
        # Los resultados salen en las unidades presentes del modelo (los datos se declaran en tonf).
        k = _KGF_PER_TONF if getattr(self, "owner", None) and self.owner.units == _KGF_M_C else 1.0
        return (
            len(fx),
            0,
            0,
            0,
            tuple(v * k for v in fx),
            tuple(v * k for v in fy),
            tuple(v * k for v in fz),
            0,
            0,
            0,
            0,
        )

    def ModalParticipatingMassRatios(self, *args):
        case = self.Setup.selected_case
        self.modal_calls.append(case)
        data = self.modal.get(case)
        if data is None:
            return (0, 0, 0, 0, (), (), (), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        periods, ux, uy = data
        return (len(periods), 0, 0, 0, tuple(periods), tuple(ux), tuple(uy), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


class FakeModel:
    def __init__(
        self,
        *,
        functions=None,
        fail_send=None,
        load_cases=None,
        case_types=None,
        base_react=None,
        modal=None,
        units=_TON_M_C,
    ):
        self.units = units
        self.units_log: list[int] = []
        self.Func = _Func(functions, fail_send)
        self.LoadCases = _LoadCases(load_cases, case_types)
        self.Results = _Results(base_react, modal)
        self.Results.owner = self

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, units):
        self.units = units
        self.units_log.append(units)
        return 0


# ---------------------------------------------------------------------------
# list_response_spectrum_functions — GetNameList(0, [], FUNC_TYPE_RESPONSE_SPECTRUM=1)
# ---------------------------------------------------------------------------


def test_functions_calls_with_type_1_and_returns_names():
    model = FakeModel(functions=["NCh2369_SaH_ref", "NCh2369_Sa_design_Hx"])
    result = list_response_spectrum_functions(model)
    assert result == ["NCh2369_SaH_ref", "NCh2369_Sa_design_Hx"]
    assert model.Func.get_name_list_calls == [(0, [], 1)]


def test_functions_empty_list():
    model = FakeModel(functions=[])
    assert list_response_spectrum_functions(model) == []


# ---------------------------------------------------------------------------
# list_load_cases — GetNameList + only_modal filter via GetTypeOAPI == 3
# ---------------------------------------------------------------------------


def test_load_cases_without_filter_returns_all():
    model = FakeModel(load_cases=["D", "L", "MODAL1"], case_types={"MODAL1": 3, "D": 1, "L": 1})
    result = list_load_cases(model, only_modal=False)
    assert result == ["D", "L", "MODAL1"]
    assert model.LoadCases.get_type_calls == []  # no filtering, no GetTypeOAPI calls


def test_load_cases_only_modal_filters_type_3():
    model = FakeModel(
        load_cases=["D", "L", "MODAL1", "MODAL2"],
        case_types={"D": 1, "L": 1, "MODAL1": 3, "MODAL2": 3},
    )
    result = list_load_cases(model, only_modal=True)
    assert result == ["MODAL1", "MODAL2"]


# ---------------------------------------------------------------------------
# push_curve_to_sap2000 / enviar_curvas — overwrite semantics + per-job errors
# ---------------------------------------------------------------------------


def test_push_curve_overwrites_same_name():
    model = FakeModel()
    push_curve_to_sap2000(model, [0.0, 0.1], [0.5, 0.6], "NCh2369_SaH_ref")
    push_curve_to_sap2000(model, [0.0, 0.2], [0.7, 0.8], "NCh2369_SaH_ref")
    assert [c[0] for c in model.Func.FuncRS.calls] == ["NCh2369_SaH_ref", "NCh2369_SaH_ref"]
    assert model.Func.FuncRS.calls[-1][2] == [0.0, 0.2]
    assert model.Func.FuncRS.calls[-1][4] == 0.05  # DAMP_RATIO_DEFAULT


def test_push_curve_empty_periods_raises():
    model = FakeModel()
    with pytest.raises(SapEspectroError, match="El espectro está vacío"):
        push_curve_to_sap2000(model, [], [], "X")


def test_enviar_curvas_mixed_success_and_errors():
    model = FakeModel(fail_send={"BAD"})
    jobs = [
        ("GOOD", [0.0, 0.1], [0.1, 0.2]),
        ("BAD", [0.0, 0.1], [0.1, 0.2]),
        ("EMPTY", [], []),
    ]
    result = enviar_curvas(model, jobs)
    assert result["enviadas"] == ["GOOD"]
    assert {e["nombre"] for e in result["errores"]} == {"BAD", "EMPTY"}
    bad_error = next(e for e in result["errores"] if e["nombre"] == "BAD")
    assert "SAP2000 rechazó la función 'BAD'" in bad_error["error"]
    empty_error = next(e for e in result["errores"] if e["nombre"] == "EMPTY")
    assert "El espectro está vacío" in empty_error["error"]


# ---------------------------------------------------------------------------
# calcular_ps — Σ factor × ΣFZ per case, with output-selection calls
# ---------------------------------------------------------------------------


def test_calcular_ps_sums_factor_times_fz_with_output_selection():
    model = FakeModel(
        base_react={
            "MS1": ([10.0], [0.0], [100.0]),
            "MS2": ([0.0], [0.0], [50.0]),
        }
    )
    ps = calcular_ps(model, [{"case": "MS1", "factor": 1.0}, {"case": "MS2", "factor": 0.5}])
    assert ps == 125.0
    assert model.Results.Setup.deselect_calls == 2
    assert model.Results.Setup.select_history == ["MS1", "MS2"]
    assert model.Results.base_react_calls == ["MS1", "MS2"]


def test_calcular_ps_no_results_raises_with_hint():
    model = FakeModel(base_react={})
    with pytest.raises(SapEspectroError) as excinfo:
        calcular_ps(model, [{"case": "MISSING", "factor": 1.0}])
    assert "No hay reacciones de base para 'MISSING'" in str(excinfo.value)
    assert NO_RESULTS_HINT in str(excinfo.value)


# ---------------------------------------------------------------------------
# calcular_qxqy — FX of case_x, FY of case_y (not the same case)
# ---------------------------------------------------------------------------


def test_calcular_qxqy_uses_fx_of_x_and_fy_of_y():
    model = FakeModel(
        base_react={
            "SISMO_X": ([152.6], [999.0], [0.0]),
            "SISMO_Y": ([999.0], [151.67], [0.0]),
        }
    )
    qx, qy = calcular_qxqy(model, "SISMO_X", "SISMO_Y")
    assert qx == 152.6
    assert qy == 151.67
    assert model.Results.base_react_calls == ["SISMO_X", "SISMO_Y"]


def test_ps_y_cortes_se_leen_en_tonf_aunque_el_modelo_este_en_kgf():
    base = {
        "D": ([0.0], [0.0], [1200.0]),
        "SISMO_X": ([152.6], [0.0], [0.0]),
        "SISMO_Y": ([0.0], [151.67], [0.0]),
    }
    model = FakeModel(base_react=base, units=_KGF_M_C)
    ps = calcular_ps(model, [{"case": "D", "factor": 1.0}])
    qx, qy = calcular_qxqy(model, "SISMO_X", "SISMO_Y")
    assert ps == pytest.approx(1200.0)  # tonf, no 1 200 000 kg
    assert qx == pytest.approx(152.6)
    assert qy == pytest.approx(151.67)
    # Se restauran las unidades del modelo tras cada lectura.
    assert model.units == _KGF_M_C
    assert model.units_log == [_TON_M_C, _KGF_M_C, _TON_M_C, _KGF_M_C]


def test_si_el_modelo_ya_esta_en_ton_m_no_cambia_unidades():
    model = FakeModel(base_react={"D": ([0.0], [0.0], [10.0])})
    assert calcular_ps(model, [{"case": "D", "factor": 2.0}]) == pytest.approx(20.0)
    assert model.units_log == []


def test_las_unidades_se_restauran_aunque_falte_el_resultado():
    model = FakeModel(base_react={}, units=_KGF_M_C)
    with pytest.raises(SapEspectroError):
        calcular_ps(model, [{"case": "GHOST", "factor": 1.0}])
    assert model.units == _KGF_M_C


def test_get_base_reaction_sums_no_results():
    model = FakeModel(base_react={})
    with pytest.raises(SapEspectroError) as excinfo:
        get_base_reaction_sums(model, "GHOST")
    assert NO_RESULTS_HINT in str(excinfo.value)


# ---------------------------------------------------------------------------
# calcular_modal — argmax of UX / UY
# ---------------------------------------------------------------------------


def test_calcular_modal_argmax():
    model = FakeModel(modal={"MODAL1": ([0.3113, 0.3, 0.15], [0.05, 0.82, 0.1], [0.79, 0.03, 0.15])})
    tx, ty = calcular_modal(model, "MODAL1")
    assert tx == 0.3
    assert ty == 0.3113


def test_get_modal_periods_no_results_raises_with_hint():
    model = FakeModel(modal={})
    with pytest.raises(SapEspectroError) as excinfo:
        get_modal_periods_at_max_mass(model, "MODAL1")
    assert "No hay resultados modales para 'MODAL1'" in str(excinfo.value)
    assert NO_RESULTS_HINT in str(excinfo.value)


# ---------------------------------------------------------------------------
# Routes (installed via bridge.actions.install_routes, same fake session
# pattern as test_bridge_combinaciones.py / test_bridge_esbelteces.py)
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


def test_route_functions():
    model = FakeModel(functions=["F1"])
    response = _client(model).get("/v1/sap/actions/espectro/functions")
    assert response.status_code == 200
    assert response.json() == {"nombres": ["F1"]}


def test_route_load_cases_only_modal():
    model = FakeModel(load_cases=["D", "MODAL1"], case_types={"D": 1, "MODAL1": 3})
    response = _client(model).get("/v1/sap/actions/espectro/load-cases", params={"only_modal": True})
    assert response.status_code == 200
    assert response.json() == {"nombres": ["MODAL1"]}


def test_route_send_requires_confirmed():
    model = FakeModel()
    body = {"jobs": [{"nombre": "F1", "periods": [0.0], "values": [0.1]}]}
    response = _client(model).post("/v1/sap/actions/espectro/send", json=body)
    assert response.status_code == 422
    assert model.Func.FuncRS.calls == []


def test_route_send_confirmed_returns_enviadas_and_errores():
    model = FakeModel(fail_send={"BAD"})
    body = {
        "jobs": [
            {"nombre": "GOOD", "periods": [0.0, 0.1], "values": [0.1, 0.2]},
            {"nombre": "BAD", "periods": [0.0, 0.1], "values": [0.1, 0.2]},
        ],
        "confirmed": True,
    }
    response = _client(model).post("/v1/sap/actions/espectro/send", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["enviadas"] == ["GOOD"]
    assert result["errores"][0]["nombre"] == "BAD"


def test_route_ps():
    model = FakeModel(base_react={"MS1": ([0.0], [0.0], [100.0])})
    response = _client(model).post(
        "/v1/sap/actions/espectro/ps", json={"rows": [{"case": "MS1", "factor": 1.5}]}
    )
    assert response.status_code == 200
    assert response.json() == {"ps": 150.0}


def test_route_ps_no_results_returns_409_with_hint():
    model = FakeModel(base_react={})
    response = _client(model).post(
        "/v1/sap/actions/espectro/ps", json={"rows": [{"case": "MISSING", "factor": 1.0}]}
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "sap_error"
    assert NO_RESULTS_HINT in body["message"]


def test_route_qxqy():
    model = FakeModel(base_react={"SX": ([152.6], [0.0], [0.0]), "SY": ([0.0], [151.67], [0.0])})
    response = _client(model).post("/v1/sap/actions/espectro/qxqy", json={"case_x": "SX", "case_y": "SY"})
    assert response.status_code == 200
    assert response.json() == {"qx": 152.6, "qy": 151.67}


def test_route_modal():
    model = FakeModel(modal={"MODAL1": ([0.3113, 0.3], [0.05, 0.82], [0.79, 0.03])})
    response = _client(model).post("/v1/sap/actions/espectro/modal", json={"modal_case": "MODAL1"})
    assert response.status_code == 200
    assert response.json() == {"Tx": 0.3, "Ty": 0.3113}


def test_route_modal_no_results_returns_409_with_hint():
    model = FakeModel(modal={})
    response = _client(model).post("/v1/sap/actions/espectro/modal", json={"modal_case": "GHOST"})
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "sap_error"
    assert NO_RESULTS_HINT in body["message"]
