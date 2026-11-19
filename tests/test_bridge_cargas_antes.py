"""``foundations/loads/preview`` y ``loads/existing``: el «antes» se compara sin distinguir mayúsculas y no se traga errores."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge.actions import existing_loads, install_routes, preview_loads
from tests.test_bridge_actions import _LoadsModel

P = "/v1/sap/actions/foundations/loads"


def _fila(joint="J1", pat="DEAD", csys="Global", f1=10.0):
    return {
        "joint": joint,
        "load_pat": pat,
        "coord_sys": csys,
        "f1": f1,
        "f2": 0.0,
        "f3": 0.0,
        "m1": 0.0,
        "m2": 0.0,
        "m3": 0.0,
    }


def _carga(pat="DEAD", csys="Global", f1=1.0):
    return (pat, csys, f1, 0.0, 0.0, 0.0, 0.0, 0.0)


def _cambio(model, fila, mode="replace"):
    return preview_loads(model, [fila], mode)["changes"][0]


# --- coincidencia ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pat", "csys"),
    [("dead", "GLOBAL"), ("  Dead ", " global "), ("DEAD", "global"), ("DEAD", ""), ("DEAD", "   ")],
)
def test_coincide_con_otras_mayusculas_espacios_y_csys_vacio(pat, csys):
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Global", 1.0)]})
    cambio = _cambio(modelo, _fila(pat=pat, csys=csys))
    assert cambio["before_status"] == "matched"
    assert cambio["before"]["f1"] == 1.0
    assert cambio["after"]["f1"] == 10.0


def test_modo_add_suma_sobre_el_antes_encontrado_con_otras_mayusculas():
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Global", 1.0)]})
    assert _cambio(modelo, _fila(pat="dead", csys="GLOBAL"), "add")["after"]["f1"] == 11.0


def test_patron_con_cargas_en_otro_sistema_de_coordenadas_es_no_match_con_existing():
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Local", 3.0), _carga("LIVE", "Global", 5.0)]})
    cambio = _cambio(modelo, _fila(pat="DEAD", csys="Global"))
    assert cambio["before_status"] == "no_match" and cambio["before"] is None
    assert {(e["load_pat"], e["coord_sys"], e["f1"]) for e in cambio["existing"]} == {
        ("DEAD", "Local", 3.0),
        ("LIVE", "Global", 5.0),
    }
    assert all("joint" not in e for e in cambio["existing"])


def test_nodo_sin_cargas_es_none():
    cambio = _cambio(_LoadsModel({}), _fila(joint="J9"))
    assert cambio["before_status"] == "none" and cambio["before"] is None and cambio["existing"] == []


def test_lo_que_ya_funcionaba_no_cambia():
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Global", 1.0), _carga("DEAD", "Global", 2.0)]})
    resultado = preview_loads(modelo, [_fila()], "replace")
    cambio = resultado["changes"][0]
    assert cambio["before"] == {"f1": 3.0, "f2": 0.0, "f3": 0.0, "m1": 0.0, "m2": 0.0, "m3": 0.0}
    assert cambio["after"]["f1"] == 10.0 and cambio["before_status"] == "matched"
    assert set(resultado) == {"changes"}  # sin errores no aparecen read_errors ni warnings
    assert modelo.unit_calls == [12, 6]  # unidades forzadas y restauradas una vez


# --- errores de lectura ---------------------------------------------------------------------------------------------------


def _con_respuesta(respuesta):
    modelo = _LoadsModel({"J1": [_carga()]})
    modelo.PointObj.GetLoadForce = lambda joint, *a: respuesta(joint)
    return modelo


def test_respuesta_con_menos_arreglos_es_error_de_lectura_no_sin_cargas():
    modelo = _con_respuesta(lambda j: (1, (j,), ("DEAD",), 0))
    resultado = preview_loads(modelo, [_fila()], "replace")
    cambio = resultado["changes"][0]
    assert cambio["before_status"] == "read_error" and cambio["before"] is None
    assert [e["joint"] for e in resultado["read_errors"]] == ["J1"]
    assert "arreglos" in resultado["read_errors"][0]["message"]
    assert any("J1" in w for w in resultado["warnings"])
    assert modelo.unit_calls == [12, 6]


def test_codigo_de_retorno_distinto_de_cero_es_error_de_lectura():
    vacio = ((),) * 10
    modelo = _con_respuesta(lambda j: (0, *vacio, 1))
    resultado = preview_loads(modelo, [_fila()], "replace")
    assert resultado["changes"][0]["before_status"] == "read_error"
    assert "código 1" in resultado["read_errors"][0]["message"]


def test_respuesta_vacia_con_ceros_sigue_siendo_sin_cargas():
    modelo = _con_respuesta(lambda j: (0, None, None, None, None, None, None, None, None, None, None, 0))
    resultado = preview_loads(modelo, [_fila()], "replace")
    assert resultado["changes"][0]["before_status"] == "none"
    assert "read_errors" not in resultado


def test_un_nodo_con_error_no_afecta_a_los_demas():
    modelo = _LoadsModel({"J1": [_carga()], "J2": [_carga(f1=4.0)]})
    original = modelo.PointObj.GetLoadForce

    def lectura(joint, *a):
        return (1, (joint,), ("DEAD",), 0) if joint == "J1" else original(joint, *a)

    modelo.PointObj.GetLoadForce = lectura
    r = preview_loads(modelo, [_fila("J1"), _fila("J2")], "replace")
    assert [c["before_status"] for c in r["changes"]] == ["read_error", "matched"]
    assert r["changes"][1]["before"]["f1"] == 4.0
    assert [e["joint"] for e in r["read_errors"]] == ["J1"]


# --- loads/existing ---------------------------------------------------------------------------------------------------------


def test_existing_loads_acumula_errores_y_sin_lista_conserva_el_comportamiento_anterior():
    modelo = _con_respuesta(lambda j: (1, (j,), ("DEAD",), 0))
    errores: list[dict[str, str]] = []
    assert existing_loads(modelo, ["J1"], errores) == []
    assert errores and errores[0]["joint"] == "J1"
    assert existing_loads(modelo, ["J1"]) == []  # sin lista: lectura tolerante de siempre


class _Actor:
    def call(self, fn):
        return fn()


class _Session:
    def __init__(self, model):
        self.actor = _Actor()
        self._model = model

    def _model_(self):
        return self._model


def _client(model):
    app = FastAPI()
    sesion = _Session(model)
    sesion.__dict__["_model"] = lambda: model
    install_routes(app, sesion, RuntimeError)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_ruta_existing_devuelve_filas_y_read_errors():
    modelo = _LoadsModel({"J1": [_carga()]})
    original = modelo.PointObj.GetLoadForce
    modelo.PointObj.GetLoadForce = lambda joint, *a: (
        (1, (joint,), ("DEAD",), 0) if joint == "J2" else original(joint, *a)
    )
    cuerpo = _client(modelo).get(f"{P}/existing", params={"joints": "J1,J2"}).json()
    assert [r["joint"] for r in cuerpo["rows"]] == ["J1"]
    assert [e["joint"] for e in cuerpo["read_errors"]] == ["J2"]
    assert cuerpo["warnings"]


def test_ruta_existing_sin_errores_conserva_la_forma_de_siempre():
    cuerpo = _client(_LoadsModel({"J1": [_carga()]})).get(f"{P}/existing", params={"joints": "J1"}).json()
    assert set(cuerpo) == {"rows"}


def test_ruta_preview_devuelve_los_campos_nuevos():
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Global", 1.0)]})
    cuerpo = (
        _client(modelo)
        .post(f"{P}/preview", json={"rows": [_fila(pat="dead", csys="")], "mode": "add"})
        .json()
    )
    cambio = cuerpo["changes"][0]
    assert cambio["before_status"] == "matched" and cambio["after"]["f1"] == 11.0
    assert cambio["existing"][0]["load_pat"] == "DEAD"


def test_ruta_acepta_coord_sys_vacio_como_global():
    modelo = _LoadsModel({"J1": [_carga("DEAD", "Global", 1.0)]})
    r = _client(modelo).post(f"{P}/preview", json={"rows": [_fila(csys="")], "mode": "replace"})
    assert r.status_code == 200 and r.json()["changes"][0]["before_status"] == "matched"
