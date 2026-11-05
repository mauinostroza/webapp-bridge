# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_geometry import (
    _frame_distributed_loads,
    _frame_distributed_loads_from_database,
    _frame_point_loads,
    _frame_point_loads_from_database,
    _frame_releases_data,
    _frame_releases_from_database,
    _joint_force_loads,
    _joint_force_loads_from_database,
    _joint_restraint_data,
    _joint_restraints_from_database,
)


def _count_calls(obj, method_name):
    """Wraps obj.method_name to count calls, returning the counter dict."""
    counter = {"n": 0}
    original = getattr(obj, method_name)

    def wrapped(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    setattr(obj, method_name, wrapped)
    return counter


def test_frame_loads_list_uses_batch_read_not_per_frame_com_calls() -> None:
    """Fase 17: la lectura de cargas distribuidas ya no debe llamar
    GetLoadDistributed por cada frame si la tabla batch está disponible."""
    fake = FakeSapObject().SapModel
    counter = _count_calls(fake.FrameObj, "GetLoadDistributed")

    reader = SapModelReader(fake)
    table = reader.frame_loads_list()

    assert counter["n"] == 0, "no debe llamar GetLoadDistributed por frame si la tabla batch trae todo"
    assert {row[0] for row in table["rows"]} == {"F1", "F2"}


def test_frame_point_loads_list_uses_batch_read_not_per_frame_com_calls() -> None:
    fake = FakeSapObject().SapModel
    counter = _count_calls(fake.FrameObj, "GetLoadPoint")

    reader = SapModelReader(fake)
    table = reader.frame_point_loads_list()

    assert counter["n"] == 0
    assert {row[0] for row in table["rows"]} == {"F2", "F3"}


def test_joint_loads_list_uses_batch_read_not_per_joint_com_calls() -> None:
    fake = FakeSapObject().SapModel
    counter = _count_calls(fake.PointObj, "GetLoadForce")

    reader = SapModelReader(fake)
    table = reader.joint_loads_list()

    assert counter["n"] == 0
    assert {row[0] for row in table["rows"]} == {"J2", "J3"}


def test_joint_restraints_uses_batch_read_not_per_joint_com_calls() -> None:
    fake = FakeSapObject().SapModel
    counter = _count_calls(fake.PointObj, "GetRestraint")

    reader = SapModelReader(fake)
    table = reader.joint_restraints()

    assert counter["n"] == 0
    assert {row[0] for row in table["rows"]} == {"J1", "J2"}


def test_frame_releases_uses_batch_read_not_per_frame_com_calls() -> None:
    fake = FakeSapObject().SapModel
    counter = _count_calls(fake.FrameObj, "GetReleases")

    reader = SapModelReader(fake)
    table = reader.frame_releases()

    assert counter["n"] == 0
    f3_rows = [row for row in table["rows"] if row[0] == "F3"]
    j_row = next(r for r in f3_rows if r[1] == "J")
    assert j_row[2:] == [False, False, False, False, True, True]


# ── Equivalencia: para los objetos presentes en ambas fixtures (batch y
# per-object), el resultado debe coincidir exactamente. ─────────────────────


def test_frame_distributed_loads_batch_matches_per_object_for_shared_frame() -> None:
    fake = FakeSapObject().SapModel
    batch = _frame_distributed_loads_from_database(fake, [])
    loop = _frame_distributed_loads(fake.FrameObj, "F1")

    assert batch is not None
    assert batch["F1"] == loop


def test_frame_point_loads_batch_matches_per_object_for_shared_frame() -> None:
    fake = FakeSapObject().SapModel
    batch = _frame_point_loads_from_database(fake, [])
    loop = _frame_point_loads(fake.FrameObj, "F2")

    assert batch is not None
    assert batch["F2"] == loop


def test_joint_force_loads_batch_matches_per_object_for_shared_joint() -> None:
    fake = FakeSapObject().SapModel
    batch = _joint_force_loads_from_database(fake, [])
    loop = _joint_force_loads(fake.PointObj, "J3")

    assert batch is not None
    assert batch["J3"] == loop


def test_joint_restraints_batch_matches_per_object_loop() -> None:
    fake = FakeSapObject().SapModel
    batch = _joint_restraints_from_database(fake, [])

    assert batch is not None
    for name in ("J1", "J2", "J3", "J4"):
        loop = _joint_restraint_data(fake.PointObj, name)
        if loop is None or not any(loop):
            assert name not in batch or not any(batch[name])
        else:
            assert batch[name] == loop


def test_frame_releases_batch_matches_per_object_loop() -> None:
    fake = FakeSapObject().SapModel
    batch = _frame_releases_from_database(fake, [])

    assert batch is not None
    for name in ("F1", "F2", "F3"):
        loop = _frame_releases_data(fake.FrameObj, name)
        assert batch[name] == loop


# ── Fallback: si DatabaseTables no trae la tabla esperada, el loop por-objeto
# sigue funcionando sin romper nada. ─────────────────────────────────────────


def test_falls_back_to_per_object_loop_when_database_table_unavailable() -> None:
    fake = FakeSapObject().SapModel
    # Simulate the table genuinely not existing for this SAP2000
    # version/model — GetAvailableTables alone isn't enough to test this
    # since _database_geometry_tables also tries the literal candidate names
    # directly against GetTableForDisplayArray as a belt-and-suspenders
    # fallback (same as the existing geometry batching), so the COM method
    # itself must fail to genuinely disable the batch path.
    fake.DatabaseTables.GetAvailableTables = lambda *a: (0, [], 0)
    fake.DatabaseTables.GetTableForDisplayArray = lambda *a: (a[0] if a else "", [], 0, 0, [], 1)

    reader = SapModelReader(fake)
    table = reader.frame_loads_list()

    # Sin tabla batch, cae al loop por-objeto — sigue devolviendo F1 (el
    # único frame con carga distribuida según GetLoadDistributed).
    assert [row[0] for row in table["rows"]] == ["F1"]
