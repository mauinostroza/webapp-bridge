"""GET /k/effective: K y L efectivos (overwrite numérico o valor de diseño si el overwrite es automático)."""
from __future__ import annotations

import pytest

from tests.viewer.test_viewer_web_k import Connector

PREFIJO = "/v1/sap/actions/viewer"
CAMPOS = {"K1 Major", "K1 Minor", "K2 Major", "K2 Minor", "K LTB", "L Major", "L Minor", "L LTB"}


@pytest.fixture
def client(rutas_viewer):
    holder = {}

    def factory():
        holder["c"] = Connector()
        return holder["c"]

    with rutas_viewer(factory) as c:
        c.holder = holder
        c.conectar_demo()
        yield c


def _db(client):
    return client.holder["c"].model.DatabaseTables


def _efectivo(client):
    r = client.get(PREFIJO + "/k/effective")
    assert r.status_code == 200
    return r.json()


def test_requiere_conexion(rutas_viewer):
    with rutas_viewer(Connector) as c:
        assert c.get(PREFIJO + "/k/effective").status_code == 409


def test_forma_exacta_http_200(client):
    body = _efectivo(client)
    assert set(body) == {"available", "reason", "tableKey", "designTableKey", "notes", "frames", "governing"}
    assert body["available"] is True and body["reason"] == ""
    assert body["tableKey"] and body["designTableKey"]
    assert set(body["frames"]["F1"]) == CAMPOS
    for celda in body["frames"]["F1"].values():
        assert set(celda) == {"value", "source", "overwriteAuto", "designValue"}
    assert set(body["governing"]["F1"]) == {"combo", "station", "ratio", "designSect", "rows"}
    textos = " ".join(body["notes"])
    assert "fila gobernante" in textos and "hipótesis" in textos


def test_overwrite_numerico(client):
    c = _efectivo(client)["frames"]["F1"]["L Minor"]
    assert c == {"value": 0.5, "source": "overwrite", "overwriteAuto": False, "designValue": 0.5}


def test_overwrite_numerico_prevalece_sobre_el_diseno(client):
    k = _efectivo(client)["frames"]["F3"]["K1 Major"]
    assert k["source"] == "overwrite" and k["value"] == 0.8 and k["overwriteAuto"] is False


def test_automatico_con_diseno(client):
    c = _efectivo(client)["frames"]["F1"]["K2 Major"]
    assert c == {"value": 1.0, "source": "design", "overwriteAuto": True, "designValue": 1.0}


def test_automatico_con_diseno_overwrites_reales_cero(client):
    _db(client).use_real_overwrites()
    f = _efectivo(client)["frames"]["F1"]
    assert f["K2 Major"]["source"] == "design" and f["K2 Major"]["overwriteAuto"] is True
    assert f["K2 Major"]["value"] == f["K2 Major"]["designValue"] is not None
    assert f["K1 Major"]["source"] == "overwrite" and f["K1 Major"]["value"] == 1.0


def test_automatico_sin_diseno(client):
    _db(client).design_run = False
    body = _efectivo(client)
    c = body["frames"]["F1"]["K2 Major"]
    assert c == {"value": None, "source": "none", "overwriteAuto": True, "designValue": None}
    assert "K LTB" not in body["frames"]["F1"]
    assert body["designTableKey"] is None and body["governing"] == {}
    assert any("ejecute el diseño de acero" in n for n in body["notes"])
    assert body["frames"]["F1"]["L Minor"]["source"] == "overwrite"


def test_diseno_sin_overwrites(client):
    _db(client).steel_key = "Nada - Inexistente"
    body = _efectivo(client)
    assert body["available"] is True and body["tableKey"] is None and body["designTableKey"]
    c = body["frames"]["F1"]["K2 Major"]
    assert c == {"value": 1.0, "source": "design", "overwriteAuto": None, "designValue": 1.0}
    assert set(body["frames"]["F1"]) == CAMPOS
    assert any("Overwrites no disponibles" in n for n in body["notes"])


def test_diseno_basura_con_overwrites_no_rompe(client):
    _db(client).design_garbage = True
    body = _efectivo(client)
    assert body["available"] is True and body["designTableKey"] is None
    assert body["frames"]["F1"]["K2 Major"]["source"] == "none"


def test_nada_disponible(client):
    _db(client).steel_key = "Nada - Inexistente"
    _db(client).design_run = False
    r = client.get(PREFIJO + "/k/effective")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["reason"]
    assert body["frames"] == {} and body["governing"] == {}
    assert set(body) == {"available", "reason", "tableKey", "designTableKey", "notes", "frames", "governing"}


def test_k_y_k_design_identicos_antes_y_despues(client):
    k_antes = client.get(PREFIJO + "/k").json()
    d_antes = client.get(PREFIJO + "/k/design").json()
    _efectivo(client)
    assert client.get(PREFIJO + "/k").json() == k_antes
    assert client.get(PREFIJO + "/k/design").json() == d_antes
    assert set(k_antes["frames"]["F1"]) == {"K1 Major", "K1 Minor", "K2 Major", "K2 Minor",
                                            "L Major", "L Minor", "L LTB"}
