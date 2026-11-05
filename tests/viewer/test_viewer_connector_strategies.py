"""
Fase 25: cobertura de la capa de conexión COM (sap2000/connector.py).

Hasta ahora la ruta COM real no tenía NINGÚN test: nadie llamaba a
connect_to_open_instance() ni simulaba un proxy caído. Como `comtypes` no
es importable fuera de Windows ("COM technology not available"), estos
tests inyectan módulos falsos `comtypes`/`comtypes.client`/
`comtypes.gen.SAP2000v1` en sys.modules — el mismo patrón que el resto de
la suite ya usa para falsear pyvista/pyvistaqt.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import sys
import types

import pytest

from bridge.viewer.sap2000.connector import Sap2000Connector
from bridge.viewer.sap2000.fake_model import FakeSapObject


class _FakeHelper:
    """Imita SAP2000v1.Helper: QueryInterface(cHelper) + GetObject(ProgID)."""

    def __init__(self, sap_object=None, get_object_error: Exception | None = None) -> None:
        self._sap_object = sap_object
        self._get_object_error = get_object_error
        self.query_interface_calls: list[object] = []

    def QueryInterface(self, interface):  # noqa: N802 - nombre impuesto por COM
        self.query_interface_calls.append(interface)
        return self

    def GetObject(self, prog_id):  # noqa: N802 - nombre impuesto por COM
        if self._get_object_error is not None:
            raise self._get_object_error
        return self._sap_object

    def CreateObject(self, exe_path):  # noqa: N802 - nombre impuesto por COM
        return self._sap_object


class _DeadProxy:
    """Proxy COM cuyo servidor murió: toda llamada lanza."""

    def __getattr__(self, name):
        raise OSError("-2147417848 The object invoked has disconnected from its clients.")


def _install_fake_comtypes(
    monkeypatch,
    *,
    create_object=None,
    get_module=None,
    get_active_object=None,
) -> types.ModuleType:
    comtypes_mod = types.ModuleType("comtypes")
    client_mod = types.ModuleType("comtypes.client")
    gen_mod = types.ModuleType("comtypes.gen")
    sap_mod = types.ModuleType("comtypes.gen.SAP2000v1")

    class _cHelper:  # noqa: N801 - imita el nombre real de la interfaz
        pass

    sap_mod.cHelper = _cHelper

    def _unavailable(*_a, **_kw):
        raise OSError("-2147221021 Operation unavailable (MK_E_UNAVAILABLE)")

    client_mod.CreateObject = create_object or _unavailable
    client_mod.GetModule = get_module or (lambda *_a, **_kw: sap_mod)
    client_mod.GetActiveObject = get_active_object or _unavailable

    comtypes_mod.client = client_mod
    comtypes_mod.gen = gen_mod
    gen_mod.SAP2000v1 = sap_mod
    comtypes_mod.CoInitialize = lambda *_a, **_kw: None
    comtypes_mod.CoUninitialize = lambda *_a, **_kw: None

    for name, module in (
        ("comtypes", comtypes_mod),
        ("comtypes.client", client_mod),
        ("comtypes.gen", gen_mod),
        ("comtypes.gen.SAP2000v1", sap_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return client_mod


# ── Cadena de estrategias ────────────────────────────────────────────────


def test_connects_via_typelib_and_chelper_first(monkeypatch) -> None:
    """Estrategia 1: GetModule + cHelper (early binding, patrón oficial CSI)."""
    sap_object = FakeSapObject()
    helper = _FakeHelper(sap_object=sap_object)
    generated: list[str] = []

    _install_fake_comtypes(
        monkeypatch,
        create_object=lambda prog_id: helper,
        get_module=lambda dll: generated.append(dll),
    )
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: r"C:\SAP2000 25\SAP2000v1.dll")

    connector = Sap2000Connector()
    info = connector.connect_to_open_instance()

    assert info.connected is True
    assert connector.model is sap_object.SapModel
    # Se generaron los wrappers de la typelib y se pidió la interfaz cHelper.
    assert generated == [r"C:\SAP2000 25\SAP2000v1.dll"]
    assert helper.query_interface_calls


def test_falls_back_to_chelper_without_typelib(monkeypatch) -> None:
    """Si no se encuentra el DLL, la estrategia 2 usa el Helper sin typelib."""
    sap_object = FakeSapObject()
    helper = _FakeHelper(sap_object=sap_object)

    _install_fake_comtypes(monkeypatch, create_object=lambda prog_id: helper)
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    connector = Sap2000Connector()
    info = connector.connect_to_open_instance()

    assert info.connected is True
    # Sin typelib no se pide QueryInterface.
    assert helper.query_interface_calls == []


def test_falls_back_to_get_active_object_when_helper_unavailable(monkeypatch) -> None:
    """Estrategia 3: la ruta histórica del repo sigue siendo el último recurso."""
    sap_object = FakeSapObject()

    def _no_helper(prog_id):
        raise OSError("-2147221164 REGDB_E_CLASSNOTREG")

    _install_fake_comtypes(
        monkeypatch,
        create_object=_no_helper,
        get_active_object=lambda prog_id: sap_object,
    )
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    connector = Sap2000Connector()
    info = connector.connect_to_open_instance()

    assert info.connected is True
    assert connector.model is sap_object.SapModel


def test_rejects_object_without_sapmodel(monkeypatch) -> None:
    """Un ProgID colisionado que devuelve algo sin SapModel no cuenta."""

    class _NotSap:
        pass

    _install_fake_comtypes(
        monkeypatch,
        create_object=lambda prog_id: _FakeHelper(sap_object=_NotSap()),
        get_active_object=lambda prog_id: _NotSap(),
    )
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    connector = Sap2000Connector()
    info = connector.connect_to_open_instance()

    assert info.connected is False
    assert connector.model is None


# ── Diagnóstico accionable ───────────────────────────────────────────────


def test_failure_message_is_actionable_not_a_raw_repr(monkeypatch) -> None:
    """Antes se volcaban dos repr de excepción; ahora se explica qué hacer."""
    _install_fake_comtypes(monkeypatch)
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    connector = Sap2000Connector()
    info = connector.connect_to_open_instance()

    assert info.connected is False
    message = info.message
    # MK_E_UNAVAILABLE => SAP2000 cerrado o desajuste de privilegios.
    assert "administrador" in message
    assert "Allow API Access" in message
    # El detalle técnico sigue disponible, pero ya no es lo único.
    assert "Detalle técnico" in message
    # Ya no se da por hecho que la versión es la v23.
    assert "v23" not in message.split("Detalle técnico")[0]


def test_failure_message_flags_unregistered_api(monkeypatch) -> None:
    def _not_registered(*_a, **_kw):
        raise OSError("-2147221164 REGDB_E_CLASSNOTREG Class not registered")

    _install_fake_comtypes(
        monkeypatch, create_object=_not_registered, get_active_object=_not_registered
    )
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    info = Sap2000Connector().connect_to_open_instance()

    assert "no está registrada" in info.message


# ── Salud de la conexión, el bug de seguridad y la reconexión ────────────


def test_has_open_model_is_false_on_a_dead_proxy() -> None:
    """Bug de seguridad corregido: antes la excepción que prueba que la
    conexión murió se convertía en 'sí, hay un modelo abierto', y
    safety/validator.py usa este predicado para autorizar escrituras."""
    connector = Sap2000Connector()
    connector.model = _DeadProxy()
    connector.sap_object = object()

    assert connector.has_open_model() is False
    # Y además deja de afirmar que sigue conectado.
    assert connector.is_connected is False
    assert connector.model is None


def test_is_connected_reflects_a_live_probe_not_just_a_non_none_attribute() -> None:
    connector = Sap2000Connector()
    assert connector.is_connected is False

    connector.connect_demo()
    assert connector.is_connected is True

    connector.model = _DeadProxy()
    assert connector.is_connected is False


def test_info_reports_a_lost_connection_instead_of_blank_fields() -> None:
    connector = Sap2000Connector()
    connector.sap_object = object()
    connector.model = _DeadProxy()

    info = connector.info()

    assert info.connected is False
    assert "perdió la conexión" in info.message


def test_disconnect_clears_state() -> None:
    connector = Sap2000Connector()
    connector.connect_demo()
    assert connector.is_connected is True

    connector.disconnect()

    assert connector.model is None
    assert connector.sap_object is None
    assert connector.is_connected is False


def test_ensure_connected_reattaches_after_the_proxy_dies(monkeypatch) -> None:
    sap_object = FakeSapObject()
    _install_fake_comtypes(
        monkeypatch,
        create_object=lambda prog_id: _FakeHelper(sap_object=sap_object),
    )
    monkeypatch.setattr("bridge.viewer.sap2000.connector._find_sap_dll", lambda: "")

    connector = Sap2000Connector()
    assert connector.connect_to_open_instance().connected is True

    connector.model = _DeadProxy()
    assert connector.is_connected is False

    assert connector.ensure_connected() is True
    assert connector.model is sap_object.SapModel


def test_ensure_connected_does_not_try_com_in_demo_mode(monkeypatch) -> None:
    """En demo no hay nada a lo que reconectarse; no debe tocar comtypes."""
    calls: list[str] = []
    _install_fake_comtypes(
        monkeypatch, create_object=lambda prog_id: calls.append(prog_id) or _FakeHelper()
    )

    connector = Sap2000Connector()
    connector.connect_demo()
    connector.model = _DeadProxy()

    assert connector.ensure_connected() is False
    assert calls == []


# ── Editabilidad y bloqueo ───────────────────────────────────────────────


def test_is_model_editable_is_permissive_while_auto_unlock_is_on() -> None:
    """Con auto_unlock activo (default) un modelo bloqueado sigue siendo
    editable: execute_pending() lo desbloquea justo antes de escribir."""
    connector = Sap2000Connector()
    connector.connect_demo()
    connector.model.SetModelIsLocked(True)

    assert connector.auto_unlock is True
    assert connector.is_model_editable() is True


def test_is_model_editable_respects_the_lock_when_auto_unlock_is_off() -> None:
    """Reactiva validator.py::model_not_editable, que hasta ahora era código
    muerto porque is_model_editable() solo miraba `model is not None`."""
    connector = Sap2000Connector()
    connector.connect_demo()
    connector.auto_unlock = False

    connector.model.SetModelIsLocked(False)
    assert connector.is_model_editable() is True

    connector.model.SetModelIsLocked(True)
    assert connector.is_model_editable() is False


def test_is_model_editable_is_false_on_a_dead_proxy() -> None:
    connector = Sap2000Connector()
    connector.model = _DeadProxy()

    assert connector.is_model_editable() is False
