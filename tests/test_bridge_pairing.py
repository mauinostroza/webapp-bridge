"""Pruebas del emparejamiento remoto de un clic (``bridge/pairing.py``).

Todo con dobles: sin bandeja de Windows, sin COM, sin ``sleep`` real (el
vencimiento se ejercita inyectando el reloj de ``bridge.pairing``).
"""

from __future__ import annotations

import json

import pytest

ALLOWED_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "http://evil.example"


@pytest.fixture(autouse=True)
def _isolated_pairing_state(tmp_path, monkeypatch):
    """Cada prueba tiene su propio directorio de datos y estado en memoria.

    ``runtime_paths.user_data_dir`` en Linux (donde corren las pruebas) usa
    ``XDG_DATA_HOME``; redirigirlo evita que una prueba lea/escriba el
    ``pairings.json`` real de la máquina o el de otra prueba.
    """
    monkeypatch.setenv("SAP2000_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SAP2000_AGENT_ORIGINS", raising=False)
    from bridge import pairing

    pairing.reset_state_for_tests()
    yield
    pairing.reset_state_for_tests()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from bridge.sap2000_agent import app

    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_request_then_poll_starts_pending(client):
    response = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 120
    request_id = body["request_id"]

    poll = client.get(f"/v1/sap/pairing/poll/{request_id}")
    assert poll.status_code == 200
    assert poll.json() == {"status": "pending"}


def test_approve_then_poll_delivers_secret_once(client):
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]

    pairing.approve(request_id)

    first = client.get(f"/v1/sap/pairing/poll/{request_id}").json()
    assert first["status"] == "approved"
    assert first["url"] == pairing.AGENT_URL
    assert first["secret"]

    second = client.get(f"/v1/sap/pairing/poll/{request_id}").json()
    assert second == {"status": "approved"}


def test_deny_then_poll_denied_without_secret(client):
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]

    pairing.deny(request_id)

    poll = client.get(f"/v1/sap/pairing/poll/{request_id}").json()
    assert poll == {"status": "denied"}


def test_expiry_with_injected_clock(monkeypatch, client):
    from bridge import pairing

    fake_time = [1000.0]
    monkeypatch.setattr(pairing, "_now", lambda: fake_time[0])

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]

    fake_time[0] += pairing.TTL_SECONDS + 1

    poll = client.get(f"/v1/sap/pairing/poll/{request_id}").json()
    assert poll == {"status": "expired"}
    assert pairing.get(request_id).status == "expired"


def test_request_response_includes_four_digit_confirm_code(client):
    response = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    confirm_code = response.json()["confirm_code"]
    assert len(confirm_code) == 4
    assert confirm_code.isdigit()


def test_confirm_code_is_ephemeral_not_in_list_pairings_or_on_disk(client):
    from bridge import pairing

    body = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()
    request_id = body["request_id"]
    confirm_code = body["confirm_code"]

    pairing.approve(request_id)

    pairings = pairing.list_pairings()
    assert len(pairings) == 1
    assert "confirm_code" not in pairings[0]
    assert confirm_code not in json.dumps(pairings[0])

    on_disk = json.loads(pairing._pairings_path().read_text("utf-8"))
    assert "confirm_code" not in on_disk[0]
    assert confirm_code not in json.dumps(on_disk)


def test_disallowed_origin_gets_403_and_creates_nothing(client):
    from bridge import pairing

    response = client.post("/v1/sap/pairing/request", headers={"Origin": DISALLOWED_ORIGIN})
    assert response.status_code == 403
    assert pairing._pending == {}


def test_missing_origin_header_gets_400(client):
    response = client.post("/v1/sap/pairing/request")
    assert response.status_code == 400


def test_sixth_pending_request_gets_429(client):
    # Five distinct allowed origins, one pending request each, fills the quota.
    allowed = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
    ]
    # ``sap2000_agent.origins`` was already computed at import time from
    # whatever SAP2000_AGENT_ORIGINS was set on first import; patch the
    # already-imported list in place so this test does not depend on import
    # order across the test session.
    from bridge import sap2000_agent

    original_origins = list(sap2000_agent.origins)
    try:
        sap2000_agent.origins[:] = [*allowed, "http://localhost:5177"]
        for origin in allowed:
            response = client.post("/v1/sap/pairing/request", headers={"Origin": origin})
            assert response.status_code == 200, response.text

        sixth = client.post("/v1/sap/pairing/request", headers={"Origin": "http://localhost:5177"})
        assert sixth.status_code == 429
    finally:
        sap2000_agent.origins[:] = original_origins


def test_same_origin_second_request_replaces_first(client):
    from bridge import pairing

    first_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()["request_id"]
    second_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]

    assert first_id != second_id
    # La primera solicitud deja de existir por completo: no queda "expirada"
    # ni recuperable por su id antiguo, sólo la última pendiente del origen.
    assert client.get(f"/v1/sap/pairing/poll/{first_id}").status_code == 404
    assert client.get(f"/v1/sap/pairing/poll/{second_id}").json() == {"status": "pending"}
    assert pairing.get(first_id) is None


def test_validate_secret_accepts_approved_rejects_invented_and_revoked(client):
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    secret = client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]

    assert pairing.validate_secret(f"Bearer {secret}") is True
    assert pairing.validate_secret("Bearer made-up-secret") is False
    assert pairing.validate_secret("") is False

    pairings = pairing.list_pairings()
    assert len(pairings) == 1
    pairing.revoke(pairings[0]["id"])

    assert pairing.validate_secret(f"Bearer {secret}") is False


def test_mutating_route_accepts_boot_token_or_paired_secret_rejects_else(client):
    from bridge import pairing

    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    secret = client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]

    with_boot_token = client.get("/v1/sap/status", headers={"Authorization": "Bearer test-token"})
    assert with_boot_token.status_code in (200, 503)  # 503 on non-Windows: reaches the handler

    with_paired_secret = client.get("/v1/sap/status", headers={"Authorization": f"Bearer {secret}"})
    assert with_paired_secret.status_code in (200, 503)

    rejected = client.get("/v1/sap/status", headers={"Authorization": "Bearer nonsense"})
    assert rejected.status_code == 401

    rejected_missing = client.get("/v1/sap/status")
    assert rejected_missing.status_code == 401


def test_pairing_routes_do_not_require_authorization(client):
    request = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN})
    assert request.status_code == 200

    poll = client.get(f"/v1/sap/pairing/poll/{request.json()['request_id']}")
    assert poll.status_code == 200


# --- S-07: vida útil, vencimiento y revocación del secreto emparejado ------


def _paired_secret(client):
    from bridge import pairing, sap2000_agent

    sap2000_agent._init_policy()  # lo que hace el evento de arranque del agente
    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    return client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]


def _advance(monkeypatch, hours):
    from datetime import timedelta

    from bridge import sap2000_agent

    real = sap2000_agent._utcnow
    monkeypatch.setattr(sap2000_agent, "_utcnow", lambda: real() + timedelta(hours=hours))


def test_paired_secret_expires_with_clear_401(client, monkeypatch):
    monkeypatch.delenv("SAP2000_AGENT_TOKEN_TTL_HOURS", raising=False)
    secret = _paired_secret(client)
    headers = {"Authorization": f"Bearer {secret}"}
    _advance(monkeypatch, 11)
    assert client.get("/v1/sap/status", headers=headers).status_code != 401
    _advance(monkeypatch, 2)  # acumulado: 13 h > 12 h
    expired = client.get("/v1/sap/status", headers=headers)
    assert expired.status_code == 401
    assert expired.json()["detail"] == "El token del puente venció; vuelva a conectar"


def test_ttl_is_configurable_by_env(client, monkeypatch):
    monkeypatch.setenv("SAP2000_AGENT_TOKEN_TTL_HOURS", "1")
    headers = {"Authorization": f"Bearer {_paired_secret(client)}"}
    _advance(monkeypatch, 2)
    assert client.get("/v1/sap/status", headers=headers).status_code == 401


def test_boot_token_never_expires(client, monkeypatch):
    _advance(monkeypatch, 24 * 365)
    response = client.get("/v1/sap/status", headers={"Authorization": "Bearer test-token"})
    assert response.status_code != 401


def test_legacy_pairing_without_expiry_keeps_working(client, monkeypatch):
    from bridge import pairing, sap2000_agent

    # Emparejamiento hecho por el puente anterior: ya existe al crearse la política.
    request_id = client.post("/v1/sap/pairing/request", headers={"Origin": ALLOWED_ORIGIN}).json()[
        "request_id"
    ]
    pairing.approve(request_id)
    secret = client.get(f"/v1/sap/pairing/poll/{request_id}").json()["secret"]
    sap2000_agent._init_policy()  # primer arranque del agente nuevo: lo anota como anterior
    _advance(monkeypatch, 24 * 30)
    headers = {"Authorization": f"Bearer {secret}"}
    assert client.get("/v1/sap/status", headers=headers).status_code != 401


def test_revoke_invalidates_only_current_secret(client):
    first = {"Authorization": f"Bearer {_paired_secret(client)}"}
    second = {"Authorization": f"Bearer {_paired_secret(client)}"}

    response = client.post("/v1/sap/revoke", headers=first)
    assert response.status_code == 200
    assert response.json() == {"revoked": True}
    assert client.get("/v1/sap/status", headers=first).status_code == 401
    assert client.get("/v1/sap/status", headers=second).status_code != 401


def test_revoke_requires_auth_and_does_not_revoke_boot_token(client):
    assert client.post("/v1/sap/revoke").status_code == 401
    boot = {"Authorization": "Bearer test-token"}
    assert client.post("/v1/sap/revoke", headers=boot).json() == {"revoked": False}
    assert client.get("/v1/sap/status", headers=boot).status_code != 401
