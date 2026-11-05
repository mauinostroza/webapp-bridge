"""Pruebas de la parte pura de ``bridge/tray.py`` (sin Win32/COM).

Como en el resto del puente, todo lo que dependa de ``win32gui`` real se
salta en este entorno Linux con ``pytest.skip`` (ver ``tests/test_bridge.py``
para el mismo patrón). Aquí sólo se prueba lo que sí es Python puro.
"""

from __future__ import annotations

import sys

import pytest

import bridge.pairing as pairing
import bridge.tray as tray


def test_tray_module_imports_without_windows():
    """Demuestra que la capa Win32 está bien separada: importar no revienta."""
    assert hasattr(tray, "TrayApp")
    assert hasattr(tray, "PairingRequestQueue")


# --- Cola de solicitudes de emparejamiento ---------------------------------


def _request(
    request_id: str, origin: str = "http://localhost:5173", confirm_code: str = "0000"
) -> pairing.PairingRequest:
    return pairing.PairingRequest(
        id=request_id, origin=origin, created_at=0.0, expires_at=120.0, confirm_code=confirm_code
    )


def test_pairing_queue_processes_in_order():
    queue = tray.PairingRequestQueue()
    queue.put(_request("a"))
    queue.put(_request("b"))
    queue.put(_request("c"))
    assert [queue.get_nowait().id for _ in range(3)] == ["a", "b", "c"]
    assert queue.get_nowait() is None


def test_pairing_queue_notify_helper_feeds_the_queue():
    queue = tray.PairingRequestQueue()
    tray.notify_pairing_request(queue, _request("x"))
    got = queue.get_nowait()
    assert got is not None and got.id == "x"


def test_pairing_queue_drops_oldest_beyond_documented_limit():
    queue = tray.PairingRequestQueue(maxsize=3)
    for i in range(5):
        queue.put(_request(str(i)))
    assert queue.qsize() == 3
    # Se descartaron las más antiguas (0, 1); quedan las 3 últimas en orden.
    assert [queue.get_nowait().id for _ in range(3)] == ["2", "3", "4"]


# --- Filtrado de list_pairings() para pintar --------------------------------


def test_visible_pairings_strips_secret_hash_and_keeps_display_fields():
    raw = [
        {
            "id": "p1",
            "origin": "http://localhost:5173",
            "label": "Mi equipo",
            "secret_hash": "deadbeef",
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_used_at": None,
        }
    ]
    rows = tray.visible_pairings(raw)
    assert rows == [
        {
            "id": "p1",
            "label": "Mi equipo",
            "origin": "http://localhost:5173",
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_used_at": None,
        }
    ]
    assert "secret_hash" not in rows[0]


def test_visible_pairings_handles_empty_list():
    assert tray.visible_pairings([]) == []


# --- Texto de la ventana de estado ------------------------------------------


def test_status_text_not_connected():
    assert tray.status_text({"connected": False}) == "SAP2000: sin conexión"


def test_status_text_connected_with_model_path():
    text = tray.status_text({"connected": True, "model_path": "C:\\models\\edificio.sdb"})
    assert text == "SAP2000: conectado — C:\\models\\edificio.sdb"


def test_status_text_com_down_with_reason():
    text = tray.status_text({"connected": False, "reason": "COM connection lost"})
    assert text == "SAP2000: sin conexión (COM connection lost)"


def test_status_text_on_exception():
    from bridge.sap2000_agent import SapComError

    text = tray.status_text(None, error=SapComError("SAP2000 COM agent requires Windows."))
    assert text == "SAP2000: error al consultar el estado (SAP2000 COM agent requires Windows.)"


# --- Texto del aviso de emparejamiento ---------------------------------------


def test_pairing_dialog_text_includes_origin_and_confirm_code():
    request = _request("r1", origin="http://localhost:5173", confirm_code="4821")
    assert tray.pairing_dialog_text(request) == (
        "Un sitio web solicita emparejarse con este puente SAP2000.\n\n"
        "Origen: http://localhost:5173\n"
        "Código: 4821\n\n"
        "Acéptalo sólo si el código coincide con el que muestra la página web."
    )


# --- Aprobar / denegar -------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pairing_state(tmp_path, monkeypatch):
    """Aísla ``pairings.json`` en un directorio temporal (mismo patrón que
    ``tests/test_bridge_pairing.py``): ``user_data_dir`` sigue ``XDG_DATA_HOME``
    en Linux, y sin redirigirlo las pruebas leerían/escribirían el archivo
    real de la máquina."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    pairing.reset_state_for_tests()
    yield
    pairing.reset_state_for_tests()


def test_resolve_approve_unknown_id_is_a_noop():
    tray.resolve_pairing_request(pairing, "no-existe", approve=True)  # no debe lanzar


def test_resolve_approve_already_expired_is_a_noop():
    request = pairing.create_request("http://localhost:5173")
    request_obj = pairing._pending[request.id]
    request_obj.status = "expired"
    tray.resolve_pairing_request(pairing, request.id, approve=True)  # no debe lanzar
    assert pairing._pending[request.id].status == "expired"


def test_resolve_approve_pending_request_actually_approves():
    request = pairing.create_request("http://localhost:5173")
    tray.resolve_pairing_request(pairing, request.id, approve=True)
    assert pairing._pending[request.id].status == "approved"
    pairings = pairing.list_pairings()
    assert len(pairings) == 1
    assert pairings[0]["origin"] == "http://localhost:5173"


def test_resolve_deny_unknown_id_is_a_noop():
    tray.resolve_pairing_request(pairing, "no-existe", approve=False)  # no debe lanzar


def test_resolve_deny_already_resolved_does_not_raise():
    """A diferencia de approve, deny sobre un id ya no pendiente no lanza nada."""
    request = pairing.create_request("http://localhost:5173")
    pairing.deny(request.id)
    assert pairing._pending[request.id].status == "denied"
    tray.resolve_pairing_request(pairing, request.id, approve=False)  # no debe lanzar
    assert pairing._pending[request.id].status == "denied"


def test_resolve_deny_pending_request_actually_denies():
    request = pairing.create_request("http://localhost:5173")
    tray.resolve_pairing_request(pairing, request.id, approve=False)
    assert pairing._pending[request.id].status == "denied"


# --- Capa Win32: sólo se puede ejercer en Windows real -----------------------


def test_tray_app_requires_windows():
    if sys.platform == "win32":
        pytest.skip("Sólo aplica fuera de Windows")
    with pytest.raises(RuntimeError, match="requires Windows"):
        tray.TrayApp(
            port=8765,
            get_status=lambda: {"connected": False},
            get_pairings=lambda: [],
            on_revoke=lambda _pid: None,
            on_exit=lambda: None,
        )


def test_reject_on_close_denies_a_pending_request():
    request = pairing.create_request("http://localhost:5173")
    assert tray.reject_on_close(pairing, request) is True
    assert pairing._pending[request.id].status == "denied"


def test_reject_on_close_keeps_an_already_approved_request():
    request = pairing.create_request("http://localhost:5173")
    pairing.approve(request.id)
    tray.reject_on_close(pairing, request)
    assert pairing._pending[request.id].status == "approved"


def test_reject_on_close_without_request_does_nothing():
    assert tray.reject_on_close(pairing, None) is False
