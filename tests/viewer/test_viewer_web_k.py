"""API de factores K del Viewer en el puente compartido (sin SAP2000: datos simulados).

Adaptado de SAP2000_AI_ASSISTANT@572b684 (tests/test_web_k.py): imports, rutas
``/v1/sap/actions/viewer/k*`` y puente sustituido por dobles. La lógica de cada prueba
no cambia. El control de Host/Origin pasa al puente compartido (omitido aquí).
"""

from __future__ import annotations

import json

import pytest

from bridge.viewer.core import k_factors as kf
from bridge.viewer.sap2000.connector import ConnectionInfo
from bridge.viewer.sap2000.fake_model import FakeSapObject

PREFIJO = "/v1/sap/actions/viewer"


class Connector:
    def __init__(self):
        self.model = FakeSapObject().SapModel
        self.is_demo = True
        self.unlock_calls = 0

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

    def unlock_if_needed(self, enabled=True):
        self.unlock_calls += 1
        was = bool(self.model.locked)
        self.model.locked = False
        self.model.DatabaseTables.locked_rejects_edits = False
        return was


@pytest.fixture
def client(rutas_viewer):
    holder = {}

    def factory():
        holder["c"] = Connector()
        return holder["c"]

    with rutas_viewer(factory) as c:
        c.holder = holder
        yield c


def _connect(client):
    client.conectar_demo()


def _audit(client):
    path = client.tmp / "logs" / "audit.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_k_requires_connection(client):
    assert client.get(PREFIJO + "/k").status_code == 409
    assert (
        client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.8}}).status_code
        == 409
    )


def test_read_returns_only_steel_frames_and_distinguishes_auto(client):
    _connect(client)
    body = client.get(PREFIJO + "/k").json()
    assert body["available"] and set(body["frames"]) == {"F1", "F3"}  # F2 es de hormigón
    assert body["frames"]["F1"]["K2 Major"] == {"value": None, "auto": True}
    assert body["frames"]["F1"]["K1 Major"] == {"value": 1.0, "auto": False}


def test_preview_does_not_touch_the_model(client):
    _connect(client)
    r = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}})
    assert r.status_code == 200
    body = r.json()
    assert body["diff"][0]["text"] == "K1 mayor: 1.000 → 0.700"
    assert body["nonce"] and body["modelLocked"] is False
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["K1 Major"]["value"] == 1.0
    assert _audit(client) == []


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"K1 Major": 50.0}, 422),
        ({"K9": 1.0}, 422),
        ({}, 422),
    ],
)
def test_preview_rejects_invalid_values(client, changes, status):
    _connect(client)
    assert client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": changes}).status_code == status


def test_preview_of_a_frame_without_steel_overwrites_creates_nothing(client):
    _connect(client)
    r = client.post(PREFIJO + "/k/preview", json={"frame": "F2", "changes": {"K1 Major": 0.7}})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "frame_without_k"


def test_apply_writes_verifies_and_audits_and_nonce_is_single_use(client):
    _connect(client)
    nonce = client.post(
        PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7, "K2 Minor": "auto"}}
    ).json()["nonce"]
    r = client.post(PREFIJO + "/k/apply", json={"nonce": nonce})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["designResultsStale"] and body["values"]["K1 Major"]["value"] == 0.7
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["K1 Major"]["value"] == 0.7
    assert client.get(PREFIJO + "/status").json()["designResultsStale"] is True
    assert [e["event"] for e in _audit(client)] == ["write_executed"]
    assert _audit(client)[0]["source"] == "web"
    again = client.post(PREFIJO + "/k/apply", json={"nonce": nonce})
    assert again.status_code == 410 and again.json()["detail"]["code"] == "nonce_invalid"


def test_apply_without_a_preview_or_with_extra_fields_is_rejected(client):
    _connect(client)
    assert client.post(PREFIJO + "/k/apply", json={"nonce": "inventado"}).status_code == 410
    assert client.post(PREFIJO + "/k/apply", json={"nonce": "x", "frame": "F1"}).status_code == 422
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["K1 Major"]["value"] == 1.0


def test_locked_model_needs_explicit_second_confirmation(client):
    _connect(client)
    model = client.holder["c"].model
    model.locked = True
    model.DatabaseTables.locked_rejects_edits = True
    nonce = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}}).json()[
        "nonce"
    ]
    r = client.post(PREFIJO + "/k/apply", json={"nonce": nonce})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "model_locked"
    assert "DESCARTA" in r.json()["detail"]["message"]
    assert client.holder["c"].unlock_calls == 0 and model.locked is True  # no desbloqueó solo
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["K1 Major"]["value"] == 1.0
    ok = client.post(PREFIJO + "/k/apply", json={"nonce": nonce, "unlock": True})
    assert ok.status_code == 200 and ok.json()["unlocked"] is True
    assert any("descartaron" in w for w in ok.json()["warnings"])
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["K1 Major"]["value"] == 0.7
    events = [e["event"] for e in _audit(client)]
    assert events == ["write_blocked_locked", "write_executed"]


def test_silent_ignore_by_sap_is_reported_with_the_real_value(client):
    _connect(client)
    client.holder["c"].model.DatabaseTables.apply_silently_ignores = True
    nonce = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}}).json()[
        "nonce"
    ]
    r = client.post(PREFIJO + "/k/apply", json={"nonce": nonce})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "verify_failed"
    assert r.json()["detail"]["actual"]["K1 Major"]["value"] == 1.0
    assert _audit(client)[-1]["event"] == "write_failed"


def test_apply_refuses_if_the_open_model_changed_since_preview(client):
    _connect(client)
    nonce = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}}).json()[
        "nonce"
    ]
    model = client.holder["c"].model
    model.GetModelFilename = lambda: "otro.sdb"
    r = client.post(PREFIJO + "/k/apply", json={"nonce": nonce})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "model_changed"


def test_expired_nonce_is_rejected(client, monkeypatch):
    _connect(client)
    nonce = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}}).json()[
        "nonce"
    ]
    monkeypatch.setattr(kf, "NONCE_TTL_SECONDS", -1)
    assert client.post(PREFIJO + "/k/apply", json={"nonce": nonce}).status_code == 410


@pytest.mark.skip(
    reason="Host/Origin y CORS los comprueba el puente compartido (bridge/sap2000_agent.py), no las rutas del Viewer"
)
def test_foreign_origin_cannot_use_k_endpoints(client):
    _connect(client)
    h = {"Origin": "https://evil.example"}
    assert client.get(PREFIJO + "/k", headers=h).status_code == 403
    assert client.post(PREFIJO + "/k/apply", json={"nonce": "x"}, headers=h).status_code == 403


def test_diagnose_returns_text(client):
    _connect(client)
    assert "text" in client.get(PREFIJO + "/k/diagnose").json()


def test_get_k_devuelve_siete_claves_con_l_de_solo_lectura(client):
    _connect(client)
    body = client.get(PREFIJO + "/k").json()
    assert set(body["frames"]["F1"]) == {
        "K1 Major",
        "K1 Minor",
        "K2 Major",
        "K2 Minor",
        "L Major",
        "L Minor",
        "L LTB",
    }
    assert body["frames"]["F1"]["L Major"] == {"value": 1.0, "auto": False}
    assert body["frames"]["F1"]["L LTB"] == {"value": None, "auto": True}
    assert body["frames"]["F3"]["L Minor"] == {"value": None, "auto": False}


def test_get_k_sin_l_devuelve_cuatro_y_nota(client):
    _connect(client)
    client.holder["c"].model.DatabaseTables.include_length_columns = False
    body = client.get(PREFIJO + "/k").json()
    assert body["available"] is True
    assert set(body["frames"]["F1"]) == {"K1 Major", "K1 Minor", "K2 Major", "K2 Minor"}
    assert "Esta tabla no trae relaciones de longitud no arriostrada" in body["notes"]


def test_preview_y_apply_solo_k_no_llevan_l(client):
    _connect(client)
    prev = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"K1 Major": 0.7}}).json()
    assert all(not d["field"].startswith("L ") for d in prev["diff"])
    body = client.post(PREFIJO + "/k/apply", json={"nonce": prev["nonce"]}).json()
    assert set(body["values"]) == {"K1 Major"}
    assert client.get(PREFIJO + "/k").json()["frames"]["F1"]["L Major"]["value"] == 1.0


def test_preview_y_apply_con_k_y_l(client):
    _connect(client)
    prev = client.post(
        PREFIJO + "/k/preview",
        json={"frame": "F1", "changes": {"K1 Major": 0.7, "L Major": 2.5, "L LTB": "auto"}},
    ).json()
    por = {d["field"]: d for d in prev["diff"]}
    assert por["L Major"]["label"] == "L mayor" and por["L Major"]["before"] == {"value": 1.0, "auto": False}
    assert por["L Major"]["after"] == {"value": 2.5, "auto": False}
    assert por["L LTB"]["after"] == {"value": None, "auto": True}
    body = client.post(PREFIJO + "/k/apply", json={"nonce": prev["nonce"]}).json()
    assert set(body["values"]) == {"K1 Major", "L Major", "L LTB"}
    assert body["values"]["L Major"] == {"value": 2.5, "auto": False}
    leido = client.get(PREFIJO + "/k").json()["frames"]["F1"]
    assert leido["L Major"]["value"] == 2.5 and leido["L LTB"]["auto"] is True


def test_preview_l_invalido_y_columna_ausente(client):
    _connect(client)
    r = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"L Major": -1}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_changes"
    client.holder["c"].model.DatabaseTables.include_length_columns = False
    r = client.post(PREFIJO + "/k/preview", json={"frame": "F1", "changes": {"L Major": 2.0}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "column_missing"
