"""Contract checks for the pure-data web viewer, with no SAP2000 or Qt required."""
# Adaptado de SAP2000_AI_ASSISTANT@572b684: imports; el bloque de servidor usa las rutas del
# Viewer en el puente compartido; los tests del lanzador run_web quedan omitidos.
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader


def snapshot():
    from bridge.viewer.core.snapshot import build_snapshot
    return build_snapshot(SapModelReader(FakeSapObject().SapModel), source='demo', model_name='Demo', unit_system='tonf_m_C')


def test_snapshot_contract_and_valid_indices():
    data = snapshot()
    assert data['schemaVersion'] == 1
    assert data['source'] == 'demo'
    assert data['units']['geometry'] == 'm'
    nodes = data['nodes']
    assert len(nodes['positions']) == len(nodes['ids']) * 3
    assert len(nodes['groups']) == len(nodes['ids'])
    assert len(set(nodes['ids'])) == len(nodes['ids'])
    assert data['frames'], 'The fake model contains frames; empty success hides a read failure'
    for frame in data['frames']:
        assert 0 <= frame['start'] < len(nodes['ids'])
        assert 0 <= frame['end'] < len(nodes['ids'])
    for area in data['areas']:
        assert all(0 <= index < len(nodes['ids']) for index in area['nodes'])
    json.dumps(data, allow_nan=False)


def test_snapshot_load_vectors_and_references():
    data = snapshot()
    identifiers = {'joint': set(data['nodes']['ids']), 'frame': {f['id'] for f in data['frames']}}
    assert data['loads'], 'Demo fixture assigns loads; they must survive serialization'
    assert len({load['id'] for load in data['loads']}) == len(data['loads'])
    for load in data['loads']:
        assert load['objectId'] in identifiers[load['objectType']]
        assert len(load['position']) == len(load['direction']) == 3
        assert all(math.isfinite(x) for x in load['position'] + load['direction'])
        assert math.isclose(sum(x*x for x in load['direction']), 1, rel_tol=1e-6)
        assert math.isfinite(load['value'])
        assert load['pattern'] in data['patterns']


@pytest.mark.skip(reason="Lanzador run_web.py del Viewer: no se porta; el arranque lo hace el puente compartido")
def test_bootstrap_is_loopback_and_uses_only_web_environment(monkeypatch, tmp_path):
    import run_web
    commands = []
    fake_python = tmp_path / ('env/Scripts/python.exe' if os.name == 'nt' else 'env/bin/python')
    fake_python.parent.mkdir(parents=True)
    fake_python.touch()
    (tmp_path / 'web/dist').mkdir(parents=True)
    (tmp_path / 'web/dist/index.html').touch()
    monkeypatch.setattr(run_web, 'ROOT', tmp_path)
    monkeypatch.setattr(run_web, 'ENV', tmp_path / 'env')
    monkeypatch.setattr(run_web, 'run', lambda command, cwd=None: commands.append(command))
    run_web.save_fingerprints({'frontend': run_web.source_digest(tmp_path / 'web')})
    assert run_web.main(['--no-install', '--no-browser']) == 0
    assert len(commands) == 1
    assert commands[0][-4:] == ['--host', '127.0.0.1', '--port', '8765']
    assert 'web_bridge.server:app' in commands[0]


@pytest.mark.skip(reason="Lanzador run_web.py del Viewer: no se porta; el arranque lo hace el puente compartido")
def test_bootstrap_fails_without_dependencies_in_offline_mode(monkeypatch, tmp_path):
    import run_web
    monkeypatch.setattr(run_web, 'ROOT', tmp_path)
    monkeypatch.setattr(run_web, 'ENV', tmp_path / 'missing')
    assert run_web.main(['--no-install']) == 1


def test_snapshot_path_does_not_import_desktop_toolkits():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    probe = """
import sys
from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.core.snapshot import build_snapshot
build_snapshot(SapModelReader(FakeSapObject().SapModel), source='demo', model_name='Demo', unit_system='tonf_m_C')
assert not any(name.split('.')[0] in {'PySide6', 'pyvista', 'vtk', 'vtkmodules', 'pyvistaqt'} for name in sys.modules)
"""
    result = subprocess.run([sys.executable, '-c', probe], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_snapshot_rejects_unknown_units_and_changed_units():
    import pytest
    from bridge.viewer.core.snapshot import build_snapshot
    model = FakeSapObject().SapModel
    reader = SapModelReader(model)
    with pytest.raises(ValueError, match='[Uu]nidades'):
        build_snapshot(reader, 'demo', 'Demo', 'unknown')
    calls = iter([12, 6])
    model.GetPresentUnits = lambda: next(calls, 6)
    with pytest.raises(ValueError, match='cambiaron'):
        build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')


def test_snapshot_preserves_partial_reader_errors_and_omits_unknown_load_direction():
    from bridge.viewer.core.snapshot import build_snapshot
    reader = SapModelReader(FakeSapObject().SapModel)
    reader.frame_loads_list = lambda **kwargs: {
        'rows': [['F1', 'Dead', 'Force', 999, 0., 1., 5., 5.]],
        'metadata': {'errors': ['COM returned 7 for F2']},
    }
    reader.frame_point_loads_list = lambda **kwargs: {'rows': []}
    reader.joint_loads_list = lambda **kwargs: {'rows': []}
    data = build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')
    assert not data['loads']
    assert any('COM returned 7' in warning for warning in data['warnings'])
    assert any('F1' in warning for warning in data['warnings'])


def test_snapshot_uses_signed_direction_and_relative_position():
    from bridge.viewer.core.snapshot import build_snapshot
    reader = SapModelReader(FakeSapObject().SapModel)
    reader.frame_loads_list = lambda **kwargs: {'rows': []}
    reader.frame_point_loads_list = lambda **kwargs: {'rows': [['F1', 'Dead', 'Force', 4, .25, -5., 'Global']]}
    reader.joint_loads_list = lambda **kwargs: {'rows': []}
    data = build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')
    load = data['loads'][0]
    frame = next(f for f in data['frames'] if f['id'] == 'F1')
    p = data['nodes']['positions'][frame['start']*3:frame['start']*3+3]
    q = data['nodes']['positions'][frame['end']*3:frame['end']*3+3]
    assert load['position'] == [a+.25*(b-a) for a,b in zip(p,q)]
    assert load['direction'] == [-1., 0., 0.]
    assert load['value'] == -5.


PREFIJO = '/v1/sap/actions/viewer'


def test_api_cache_failure_stale_and_com_thread_ownership(rutas_viewer):
    import threading
    from bridge.viewer.sap2000.connector import ConnectionInfo
    owner_threads = []
    instances = []
    class Connector:
        def __init__(self):
            owner_threads.append(threading.get_ident())
            self.model = FakeSapObject().SapModel
            self.is_demo = True
            self.fail = False
            units = self.model.GetPresentUnits
            def read_units():
                owner_threads.append(threading.get_ident())
                return units()
            self.model.GetPresentUnits = read_units
            instances.append(self)
        def connect_demo(self):
            owner_threads.append(threading.get_ident())
            return self.info()
        def connect_to_open_instance(self):
            owner_threads.append(threading.get_ident())
            return ConnectionInfo(connected=False, message='SAP not running')
        def info(self):
            owner_threads.append(threading.get_ident())
            return ConnectionInfo(connected=not self.fail, model_name='Demo', units='tonf_m_C', message='Disconnected')
        def is_alive(self):
            owner_threads.append(threading.get_ident())
            return not self.fail
        def disconnect(self):
            owner_threads.append(threading.get_ident())
    with rutas_viewer(Connector) as client:
        assert client.get(PREFIJO + '/snapshot').status_code == 204
        client.conectar_demo()
        response = client.get(PREFIJO + '/snapshot')
        assert response.status_code == 200
        snapshot_id = response.json()['id']
        count = len(owner_threads)
        assert client.get(PREFIJO + '/snapshot').json()['id'] == snapshot_id
        assert client.get(PREFIJO + '/status').json()['connected']
        assert len(owner_threads) == count, 'Cached GET endpoints must not access COM'
        instances[0].fail = True
        assert client.post(PREFIJO + '/refresh').status_code == 503
        cached = client.get(PREFIJO + '/snapshot')
        assert cached.json()['id'] == snapshot_id
        assert cached.headers['X-Snapshot-Stale'] == 'true'
        assert client.get(PREFIJO + '/status').json()['stale'] is True
        assert client.post(PREFIJO + '/connect', json={'mode':'sap2000'}).status_code == 503
        assert client.get(PREFIJO + '/snapshot').json()['source'] == 'demo'
        assert 'SAP not running' in client.get(PREFIJO + '/status').json()['error']
    assert len(set(owner_threads)) == 1
    assert owner_threads[0] != threading.get_ident()


def test_api_rejects_unknown_modes_and_writing_routes(rutas_viewer):
    with rutas_viewer(lambda: None) as client:
        assert client.post(PREFIJO + '/connect', json={'mode':'auto'}).status_code == 422
        assert client.post(PREFIJO + '/connect', json={'mode':'sap2000', 'unlock': True}).status_code == 422
        demo = client.post(PREFIJO + '/connect', json={'mode':'demo'})
        assert demo.status_code == 422 and demo.json()['detail'] == 'El modo demo corre en la web'
        assert client.post(PREFIJO + '/write').status_code == 404
        assert client.get(PREFIJO + '/status').headers['Cache-Control'] == 'no-store'


@pytest.mark.skip(reason='Host/Origin y CORS los comprueba el puente compartido (bridge/sap2000_agent.py), no las rutas del Viewer')
def test_api_rejects_foreign_origins():
    pass


def test_real_section_outputs_use_dimensions_not_color_or_return_code():
    from types import SimpleNamespace
    from bridge.viewer.core.sections import read_sections, area_property
    prop = SimpleNamespace(
        GetRectangle=lambda name, *args: ('file', 'Steel', 400., 200., 123456, 'notes', 'guid', 0) if name=='RECT' else ('file','Steel',400.,200.,123456,'notes','guid',7),
        GetISection=lambda name, *args: ('file', 'Steel', 600., 300., 20., 10., 280., 18., 123456, 'notes', 'guid', 0) if name=='I' else (),
    )
    model = SimpleNamespace(PropFrame=prop,
        AreaObj=SimpleNamespace(GetProperty=lambda name, *args: ('SLAB',0)),
        PropArea=SimpleNamespace(GetShell=lambda name, *args: (1,False,'Concrete',0.,150.,150.,123456,'notes','guid',0)))
    warnings = []
    sections = read_sections(model, {'RECT','I','FAILED'}, 'kN_mm_C', warnings)
    assert sections['RECT']['depth'] == .4
    assert sections['RECT']['width'] == .2
    assert sections['I']['bottom_flange_width'] == .28
    assert math.isclose(sections['I']['bottom_flange_thickness'], .018)
    assert sections['FAILED']['type'] == 'unknown'
    assert any('FAILED' in warning for warning in warnings)
    assert area_property(model, 'A1', 'kN_mm_C') == ('SLAB', .15)


def test_snapshot_rejects_nonfinite_geometry_instead_of_emitting_invalid_json():
    import pytest
    from bridge.viewer.core.snapshot import build_snapshot
    reader = SapModelReader(FakeSapObject().SapModel)
    reader.joint_coordinates_and_frame_geometry = lambda **kwargs: ({'rows': [['P1', float('nan'), 0., 0., '']]}, {'rows': []})
    with pytest.raises(ValueError, match='finitas'):
        build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')


@pytest.mark.skip(reason="Lanzador run_web.py del Viewer: no se porta; el arranque lo hace el puente compartido")
def test_bootstrap_opens_browser_only_after_server_ready(monkeypatch):
    import run_web
    from types import SimpleNamespace
    attempts = []
    opened = []
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def probe(url, timeout):
        attempts.append(url)
        if len(attempts) == 1:
            raise OSError('not ready')
        return Ready()
    monkeypatch.setattr(run_web, 'urlopen', probe)
    monkeypatch.setattr(run_web.time, 'sleep', lambda _: None)
    monkeypatch.setattr(run_web.webbrowser, 'open', opened.append)
    monkeypatch.setattr(run_web.threading, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target))
    run_web.open_browser_when_ready()
    assert len(attempts) == 2
    assert opened == ['http://127.0.0.1:8765']


def test_snapshot_rejects_empty_geometry_after_read_failure():
    import pytest
    from bridge.viewer.core.snapshot import build_snapshot
    reader = SapModelReader(FakeSapObject().SapModel)
    reader.joint_coordinates_and_frame_geometry = lambda **kwargs: (
        {'rows': [], 'metadata': {'errors': ['PointObj COM failed']}},
        {'rows': [], 'metadata': {'errors': ['FrameObj COM failed']}},
    )
    with pytest.raises(ValueError):
        build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')


def test_snapshot_rejects_model_change_even_when_units_match():
    import pytest
    from bridge.viewer.core.snapshot import build_snapshot
    model = FakeSapObject().SapModel
    filenames = iter(['A.sdb', 'B.sdb'])
    model.GetModelFilename = lambda: next(filenames, 'B.sdb')
    with pytest.raises(ValueError):
        build_snapshot(SapModelReader(model), 'demo', 'Demo', 'tonf_m_C')


def test_snapshot_never_changes_units_lock_or_selection():
    from bridge.viewer.core.snapshot import build_snapshot
    model = FakeSapObject().SapModel
    mutations = []
    def forbidden(*args, **kwargs):
        mutations.append((args, kwargs))
        raise AssertionError('Read-only snapshot attempted SAP mutation')
    model.SetPresentUnits = forbidden
    model.SetModelIsLocked = forbidden
    model.SelectObj.ClearSelection = forbidden
    model.SelectObj.All = forbidden
    build_snapshot(SapModelReader(model), 'demo', 'Demo', 'tonf_m_C')
    assert mutations == []


@pytest.mark.skip(reason="Lanzador run_web.py del Viewer: no se porta; el arranque lo hace el puente compartido")
def test_bootstrap_fingerprints_skip_network_until_inputs_change(monkeypatch, tmp_path):
    import run_web
    commands = []
    environment = tmp_path / 'env'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    requirements = tmp_path / 'requirements-web.txt'
    requirements.write_text('fastapi==1\n')
    frontend = tmp_path / 'web'
    (frontend / 'src').mkdir(parents=True)
    (frontend / 'package-lock.json').write_text('{}')
    source = frontend / 'src/App.tsx'
    source.write_text('first source')
    monkeypatch.setattr(run_web, 'ROOT', tmp_path)
    monkeypatch.setattr(run_web, 'ENV', environment)
    monkeypatch.setattr(run_web.shutil, 'which', lambda _: 'npm')
    def fake_run(command, cwd=None):
        commands.append(command)
        if command[1:3] == ['run', 'build']:
            (frontend / 'dist').mkdir(exist_ok=True)
            (frontend / 'dist/index.html').write_text('compiled')
    monkeypatch.setattr(run_web, 'run', fake_run)
    assert run_web.main(['--no-browser']) == 0
    assert sum('pip' in cmd for cmd in commands) == 1
    assert ['npm', 'ci'] in commands
    commands.clear()
    # Dependency trees and build output must not invalidate source fingerprints.
    (frontend / 'node_modules').mkdir()
    (frontend / 'node_modules/ignored.js').write_text('dependency')
    assert run_web.main(['--no-browser']) == 0
    assert len(commands) == 1 and 'uvicorn' in commands[0]
    commands.clear()
    requirements.write_text('fastapi==2\n')
    assert run_web.main(['--no-browser']) == 0
    assert sum('pip' in cmd for cmd in commands) == 1
    assert ['npm', 'ci'] not in commands
    commands.clear()
    source.write_text('updated source')
    assert run_web.main(['--no-browser']) == 0
    assert not any('pip' in cmd for cmd in commands)
    assert ['npm', 'ci'] in commands
    assert ['npm', 'run', 'build'] in commands
    commands.clear()
    assert run_web.main(['--no-browser', '--rebuild']) == 0
    assert ['npm', 'run', 'build'] in commands
    commands.clear()
    requirements.write_text('fastapi==3\n')
    assert run_web.main(['--no-browser', '--no-install']) == 0
    assert not any('pip' in cmd for cmd in commands)


def test_snapshot_skips_broken_objects_with_visible_count():
    from bridge.viewer.core.snapshot import build_snapshot
    reader = SapModelReader(FakeSapObject().SapModel)
    reader.joint_coordinates_and_frame_geometry = lambda **kwargs: (
        {'rows': [['P1', 0., 0., 0., ''], ['P2', 1., 0., 0., ''], ['P3', float('nan'), 0., 0., '']]},
        {'rows': [['', 'B1', 'P1', 'P2'], ['', 'B2', 'P2', 'P3'], ['', 'B3', 'P1', 'PX']]},
    )
    snap = build_snapshot(reader, 'demo', 'Demo', 'tonf_m_C')
    json.dumps(snap, allow_nan=False)
    assert snap['nodes']['ids'] == ['P1', 'P2']
    assert [f['id'] for f in snap['frames']] == ['B1']
    assert snap['stats']['skippedNodes'] == 1 and snap['stats']['skippedFrames'] == 2
    assert any('1 de 3 nodos omitidas' in w for w in snap['warnings'])
    assert any('2 de 3 barras omitidas' in w for w in snap['warnings'])


def test_section_table_is_all_or_nothing_per_section_with_getter_fallback():
    from bridge.viewer.core.sections import read_sections_from_table
    import bridge.viewer.core.sections as sec

    cols = ['Name', 'Material', 'Shape', 't3', 't2', 'tf', 'tw', 't2b', 'tfb']
    rows = [
        ['R', 'S355', 'Rectangular', '400', '200', '', '', '', ''],
        ['IOK', 'S355', 'I/Wide Flange', '500', '200', '16', '10', '200', '16'],
        ['IBAD', 'S355', 'I/Wide Flange', '500', '200', '16', '10', '', ''],
        ['ZERO', 'S355', 'Rectangular', '0', '200', '', '', '', ''],
    ]
    orig = (sec._available_database_tables, sec._match_database_table_keys, sec._get_database_table_for_display)
    sec._available_database_tables = lambda db: ['T']
    sec._match_database_table_keys = lambda aliases, avail: ['T']
    sec._get_database_table_for_display = lambda db, key: (cols, rows)
    try:
        class M: DatabaseTables = object()
        got = read_sections_from_table(M(), {'R', 'IOK', 'IBAD', 'ZERO', 'NOPE'}, 'kN_mm_C')
    finally:
        sec._available_database_tables, sec._match_database_table_keys, sec._get_database_table_for_display = orig
    assert set(got) == {'R', 'IOK'}          # I sin ala inferior y dimensión cero: NO se adivinan
    assert math.isclose(got['R']['depth'], 0.4)
    assert math.isclose(got['IOK']['bottom_flange_thickness'], 0.016)


def test_area_property_caches_getshell_per_property():
    from bridge.viewer.core.sections import area_property
    calls = []
    class Prop:
        def GetShell(self, name, *a):
            calls.append(name); return [0, 0, '', 0, 0.15, 0, 0, 0, 0, 0]
    class Area:
        def GetProperty(self, name, *a): return ['SLAB', 0]
    class M: PropArea = Prop(); AreaObj = Area()
    cache = {}
    first = area_property(M(), 'A1', 'kN_mm_C', cache)
    second = area_property(M(), 'A2', 'kN_mm_C', cache)
    assert first[0] == 'SLAB' and first[1] is not None
    assert second == first
    assert len(calls) == 1
