"""Pruebas de bridge/agent_file.py: rastro en disco del puente local."""

import json
import os

import pytest

from bridge import agent_file


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Redirige agent_file_path() a una carpeta de la prueba."""
    path = tmp_path / 'EngineeringWorkbench' / 'bridge' / 'agent.json'
    monkeypatch.setattr(agent_file, 'agent_file_path', lambda: path)
    return path


def test_importable_without_sap2000_agent_token(monkeypatch):
    """agent_file no debe depender de bridge.sap2000_agent (exige un token al importarse)."""
    monkeypatch.delenv('SAP2000_AGENT_TOKEN', raising=False)
    import importlib

    importlib.reload(agent_file)  # no debe lanzar


def test_write_then_read_round_trips(isolated):
    agent_file.write_agent_file(port=54321, token='el-token')
    data = agent_file.read_agent_file()
    assert data == {'port': 54321, 'token': 'el-token', 'pid': os.getpid()}


def test_write_creates_missing_directories(isolated):
    assert not isolated.parent.exists()
    agent_file.write_agent_file(port=1, token='x')
    assert isolated.is_file()


def test_write_is_atomic_no_leftover_temp_files(isolated):
    agent_file.write_agent_file(port=1, token='x')
    leftovers = list(isolated.parent.glob('.agent-*'))
    assert leftovers == []


def test_read_missing_file_returns_none(isolated):
    assert agent_file.read_agent_file() is None


def test_read_corrupted_json_returns_none(isolated):
    isolated.parent.mkdir(parents=True)
    isolated.write_text('{no es json', encoding='utf-8')
    assert agent_file.read_agent_file() is None


def test_read_incomplete_json_missing_port_returns_none(isolated):
    isolated.parent.mkdir(parents=True)
    isolated.write_text(json.dumps({'token': 'x', 'pid': 1}), encoding='utf-8')
    assert agent_file.read_agent_file() is None


def test_read_incomplete_json_missing_token_returns_none(isolated):
    isolated.parent.mkdir(parents=True)
    isolated.write_text(json.dumps({'port': 1, 'pid': 1}), encoding='utf-8')
    assert agent_file.read_agent_file() is None


def test_remove_deletes_existing_file(isolated):
    agent_file.write_agent_file(port=1, token='x')
    agent_file.remove_agent_file()
    assert not isolated.exists()


def test_remove_does_not_fail_when_absent(isolated):
    agent_file.remove_agent_file()  # no debe lanzar
