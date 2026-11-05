"""Snapshot del Viewer: cargas de todos los patrones por componente y combinaciones.

Modelo falso (sin SAP2000). Las lecturas de cargas se sustituyen por filas controladas
(``reader.frame_loads_list`` y compañía) para fijar signos, direcciones y unidades. Las
entradas sin ``component`` son las antiguas y no cambian; las que lo llevan son las nuevas.
"""

from __future__ import annotations

import json
import math

import pytest

from bridge.viewer.core import snapshot as snap
from bridge.viewer.core.combinations import MAX_COMBINATIONS, MAX_ITEMS, expand_combinations
from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.reader_metadata import load_combination_definitions

EJES = {
    "Fx": [1.0, 0.0, 0.0],
    "Fy": [0.0, 1.0, 0.0],
    "Fz": [0.0, 0.0, 1.0],
    "Mx": [1.0, 0.0, 0.0],
    "My": [0.0, 1.0, 0.0],
    "Mz": [0.0, 0.0, 1.0],
}
COMPONENTES = set(EJES)


def _reader(distributed=(), point=(), joints=(), local_axes=(), calls=None):
    """Lector con las filas de carga dadas; ``calls`` registra los argumentos de cada lectura."""
    reader = SapModelReader(FakeSapObject().SapModel)

    def rows(filas):
        def read(**kwargs):
            if calls is not None:
                calls.append(kwargs)
            return {"rows": [list(fila) for fila in filas]}

        return read

    reader.frame_loads_list = rows(distributed)
    reader.frame_point_loads_list = rows(point)
    reader.joint_loads_list = rows(joints)
    reader.frame_local_axes = rows(local_axes)
    return reader


def _snapshot(reader):
    return snap.build_snapshot(reader, "demo", "Demo", "tonf_m_C")


def _componentes(data):
    return [load for load in data["loads"] if "component" in load]


def _antiguas(data):
    return [load for load in data["loads"] if "component" not in load]


def _posicion_nodo(data, nombre):
    indice = data["nodes"]["ids"].index(nombre)
    return data["nodes"]["positions"][indice * 3 : indice * 3 + 3]


def test_lee_todos_los_patrones_sin_filtrar_por_patron():
    calls: list[dict] = []
    reader = _reader(
        distributed=[["F1", "Dead", "Force", 4, 0.0, 1.0, 5.0, 5.0]],
        point=[["F2", "EQX", "Force", 5, 0.5, 2.0]],
        joints=[["J1", "Wind", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
        calls=calls,
    )
    data = _snapshot(reader)
    assert {load["pattern"] for load in data["loads"]} == {"Dead", "EQX", "Wind"}
    assert {"Dead", "EQX", "Wind"} <= set(data["patterns"])
    assert calls and all(kwargs == {"limit": None} for kwargs in calls)


def test_joint_emite_una_entrada_por_componente_no_nula_con_signo_y_unidad():
    data = _snapshot(_reader(joints=[["J3", "Dead", 0.0, -2.0, 3.0, 4.0, 0.0, -6.0, "Global"]]))
    componentes = _componentes(data)
    assert {(c["component"], c["value"], c["unit"]) for c in componentes} == {
        ("Fy", -2.0, "tonf"),
        ("Fz", 3.0, "tonf"),
        ("Mx", 4.0, "tonf·m"),
        ("Mz", -6.0, "tonf·m"),
    }
    assert len({c["id"] for c in data["loads"]}) == len(data["loads"])
    for c in componentes:
        assert c["objectType"] == "joint" and c["objectId"] == "J3" and c["kind"] == "point"
        assert c["position"] == _posicion_nodo(data, "J3")
        assert math.isclose(sum(x * x for x in c["direction"]), 1.0)
        assert c["direction"] == EJES[c["component"]]


def test_joint_antiguo_sigue_siendo_magnitud_de_fuerza_y_sin_momentos():
    data = _snapshot(
        _reader(
            joints=[
                ["J3", "Dead", 0.0, 0.0, -10.0, 0.0, 0.0, 0.0],
                ["J1", "Dead", 0.0, 0.0, 0.0, 2.0, 0.0, 0.0],
            ]
        )
    )
    antiguas = _antiguas(data)
    assert [(a["objectId"], a["value"], a["direction"], a["unit"]) for a in antiguas] == [
        ("J3", 10.0, [0.0, 0.0, -1.0], "tonf"),
    ]
    assert {c["objectId"] for c in _componentes(data)} == {"J3", "J1"}


def test_momento_de_barra_puntual_solo_genera_componente_mz_con_unidad_de_momento():
    data = _snapshot(_reader(point=[["F2", "Live", "Moment", 6, 0.25, -4.0]]))
    assert _antiguas(data) == []
    componentes = _componentes(data)
    assert len(componentes) == 1
    mz = componentes[0]
    assert (mz["component"], mz["value"], mz["unit"], mz["kind"]) == ("Mz", -4.0, "tonf·m", "point")
    assert mz["direction"] == [0.0, 0.0, 1.0]


def test_barra_con_direccion_local_se_descompone_en_ejes_globales():
    # F2 va de (6,0,0) a (6,4,3): eje (0,0.8,0.6). Carga local 1 de 10 => Fy=8 y Fz=6.
    data = _snapshot(_reader(point=[["F2", "Dead", "Force", 1, 0.5, 10.0]]))
    componentes = {c["component"]: c["value"] for c in _componentes(data)}
    assert componentes == pytest.approx({"Fy": 8.0, "Fz": 6.0})


def test_barra_local_2_con_angulo_usa_la_matriz_de_la_barra():
    # F1 sobre X con ángulo 90: el eje local 2 es (0,-1,0), así que Fy = -10.
    data = _snapshot(_reader(point=[["F1", "Dead", "Force", 2, 0.5, 10.0]], local_axes=[["F1", 90.0, False]]))
    assert [(c["component"], c["value"]) for c in _componentes(data)] == [("Fy", -10.0)]


def test_gravedad_es_hacia_abajo_y_da_fz_negativa_para_valor_positivo():
    data = _snapshot(_reader(point=[["F3", "Dead", "Force", 10, 0.5, 4.0]]))
    assert [(c["component"], c["value"], c["direction"]) for c in _componentes(data)] == [
        ("Fz", -4.0, [0.0, 0.0, 1.0])
    ]


def test_distribuida_por_muestras_componentes_coinciden_con_las_antiguas():
    data = _snapshot(_reader(distributed=[["F1", "Dead", "Force", 5, 0.0, 1.0, 2.0, 6.0]]))
    antiguas = _antiguas(data)
    componentes = _componentes(data)
    assert len(antiguas) == len(componentes) == 7
    assert [c["value"] for c in componentes] == pytest.approx([a["value"] for a in antiguas])
    assert [c["position"] for c in componentes] == [a["position"] for a in antiguas]
    assert {(c["component"], c["unit"], c["kind"]) for c in componentes} == {("Fy", "tonf/m", "distributed")}


def test_momento_distribuido_tiene_unidad_de_fuerza_por_longitud_igual_a_fuerza():
    data = _snapshot(_reader(distributed=[["F1", "Dead", "Moment", 4, 0.0, 1.0, 1.5, 1.5]]))
    assert _antiguas(data) == []
    assert {(c["component"], c["value"], c["unit"]) for c in _componentes(data)} == {("Mx", 1.5, "tonf")}


def test_entrada_antigua_de_barra_sigue_con_direccion_firmada_y_posicion_relativa():
    # F3 va de (6,4,3) a (0,4,3): a 0,25 de longitud, (4.5, 4, 3).
    data = _snapshot(_reader(point=[["F3", "Dead", "Force", 4, 0.25, -5.0, "Global"]]))
    antiguas = _antiguas(data)
    assert len(antiguas) == 1
    assert {k: v for k, v in antiguas[0].items() if k != "id"} == {
        "objectType": "frame",
        "objectId": "F3",
        "pattern": "Dead",
        "position": pytest.approx([4.5, 4.0, 3.0]),
        "direction": [-1.0, 0.0, 0.0],
        "value": -5.0,
        "kind": "point",
        "unit": "tonf",
    }


def test_ids_unicos_y_direcciones_unitarias_en_todas_las_entradas():
    data = _snapshot(
        _reader(
            distributed=[["F1", "Dead", "Force", 4, 0.0, 1.0, 5.0, 5.0]],
            point=[["F2", "Live", "Moment", 1, 0.5, 3.0]],
            joints=[["J3", "Dead", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]],
        )
    )
    ids = [load["id"] for load in data["loads"]]
    assert len(set(ids)) == len(ids)
    for load in data["loads"]:
        assert math.isclose(sum(x * x for x in load["direction"]), 1.0, rel_tol=1e-9)
        assert load.get("component", "antigua") in COMPONENTES | {"antigua"}
    json.dumps(data, allow_nan=False)


def test_limite_de_entradas_trunca_y_marca_loads_truncated(monkeypatch):
    monkeypatch.setattr(snap, "MAX_LOAD_ENTRIES", 5)
    data = _snapshot(
        _reader(
            joints=[
                ["J1", "Dead", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                ["J2", "Dead", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            ]
        )
    )
    assert len(data["loads"]) == 5
    assert data["loadsTruncated"] is True
    assert any("loadsTruncated" in w for w in data["warnings"])


def test_snapshot_sin_limite_no_marca_truncado():
    data = _snapshot(_reader(joints=[["J3", "Dead", 0.0, 0.0, -10.0, 0.0, 0.0, 0.0]]))
    assert data["loadsTruncated"] is False


def test_combinaciones_expanden_anidadas_multiplicando_factores_y_sumando_repetidos():
    data = snap.build_snapshot(SapModelReader(FakeSapObject().SapModel), "demo", "Demo", "tonf_m_C")
    combos = {c["name"]: {i["pattern"]: i["factor"] for i in c["items"]} for c in data["combinations"]}
    assert combos["ASD1.01"] == {"Dead": pytest.approx(1.0), "Live": pytest.approx(1.0)}
    # ASD1.02 = 1.2 * ASD1.01 + 1.0 * Live => Live = 1.2 + 1.0.
    assert combos["ASD1.02"] == {"Dead": pytest.approx(1.2), "Live": pytest.approx(2.2)}
    assert data["combinationsTruncated"] is False
    patrones_combo = {i["pattern"] for c in data["combinations"] for i in c["items"]}
    assert patrones_combo <= set(data["patterns"])


def test_definicion_marca_terminos_combo_y_casos():
    definiciones = load_combination_definitions(FakeSapObject().SapModel, [])
    asd = next(d for d in definiciones if d["name"] == "ASD1.02")
    assert asd["items"] == [
        {"case": "ASD1.01", "factor": 1.2, "combo": True},
        {"case": "Live", "factor": 1.0, "combo": False},
    ]


def test_error_al_leer_una_combinacion_se_avisa_y_no_tumba_el_snapshot():
    model = FakeSapObject().SapModel

    def falla(name, *args):
        if name == "ASD1.01":
            raise RuntimeError("COM caído")
        return type(model.RespCombo).GetCaseList(model.RespCombo, name, *args)

    model.RespCombo.GetCaseList = falla
    data = snap.build_snapshot(SapModelReader(model), "demo", "Demo", "tonf_m_C")
    assert {c["name"] for c in data["combinations"]} == {"ASD1.02", "ENVOLVENTE"}
    assert any("GetCaseList ASD1.01" in w and "COM caído" in w for w in data["warnings"])
    assert any("ASD1.01" in w and "no existe" in w for w in data["warnings"])


def test_ciclo_y_referencia_inexistente_se_cortan_y_se_anotan():
    definiciones = [
        {
            "name": "A",
            "items": [
                {"case": "B", "factor": 1.0, "combo": True},
                {"case": "Dead", "factor": 2.0, "combo": False},
            ],
        },
        {
            "name": "B",
            "items": [
                {"case": "A", "factor": 0.5, "combo": True},
                {"case": "Live", "factor": 1.0, "combo": False},
                {"case": "FANTASMA", "factor": 1.0, "combo": True},
            ],
        },
        {"name": "C", "items": [{"case": "C", "factor": 1.0, "combo": True}]},
    ]
    combinaciones, truncada, avisos = expand_combinations(definiciones)
    combos = {c["name"]: {i["pattern"]: i["factor"] for i in c["items"]} for c in combinaciones}
    assert combos["A"] == {"Live": pytest.approx(1.0), "Dead": pytest.approx(2.0)}
    # B = 0,5 * A + Live, con A = 2 Dead (la rama de vuelta a B se corta por ciclo).
    assert combos["B"] == {"Live": pytest.approx(1.0), "Dead": pytest.approx(1.0)}
    assert combos["C"] == {}
    assert truncada is False
    assert any("circular" in a for a in avisos)
    assert any("FANTASMA" in a and "no existe" in a for a in avisos)


def test_factores_nulos_tras_sumar_se_descartan():
    definiciones = [
        {
            "name": "X",
            "items": [
                {"case": "Dead", "factor": 1.0, "combo": False},
                {"case": "Dead", "factor": -1.0, "combo": False},
            ],
        }
    ]
    combinaciones, _, _ = expand_combinations(definiciones)
    assert combinaciones == [{"name": "X", "items": []}]


def test_limite_de_combinaciones_trunca_con_bandera():
    assert MAX_COMBINATIONS == 5000 and MAX_ITEMS == 500
    definiciones = [{"name": f"K{i}", "items": []} for i in range(MAX_COMBINATIONS + 1)]
    combinaciones, truncada, avisos = expand_combinations(definiciones)
    assert len(combinaciones) == MAX_COMBINATIONS
    assert truncada is True
    assert any("combinationsTruncated" in a for a in avisos)


def test_limite_de_patrones_por_combinacion_trunca_con_bandera():
    items = [{"case": f"P{i}", "factor": 1.0, "combo": False} for i in range(MAX_ITEMS + 1)]
    combinaciones, truncada, avisos = expand_combinations([{"name": "BIG", "items": items}])
    assert len(combinaciones[0]["items"]) == MAX_ITEMS
    assert truncada is True
    assert any("BIG" in a for a in avisos)


def test_snapshot_no_escribe_en_el_modelo():
    model = FakeSapObject().SapModel
    mutaciones: list[str] = []

    def prohibido(nombre):
        def bloqueado(*args, **kwargs):
            mutaciones.append(nombre)
            raise AssertionError(f"lectura de snapshot intentó escribir: {nombre}")

        return bloqueado

    for objeto, metodos in (
        (model.RespCombo, ("Add", "Delete", "SetCaseList")),
        (model.LoadPatterns, ("Add",)),
        (model.DatabaseTables, ("SetTableForEditingArray", "ApplyEditedTables", "CancelTableEditing")),
        (model.SelectObj, ("ClearSelection", "SetSelected")),
    ):
        for metodo in metodos:
            setattr(objeto, metodo, prohibido(f"{type(objeto).__name__}.{metodo}"))
    model.SetPresentUnits = prohibido("SetPresentUnits")
    model.SetModelIsLocked = prohibido("SetModelIsLocked")

    data = snap.build_snapshot(SapModelReader(model), "demo", "Demo", "tonf_m_C")
    assert mutaciones == []
    assert data["combinations"]


def test_demo_conserva_las_entradas_antiguas_una_por_carga_y_muestra():
    data = snap.build_snapshot(SapModelReader(FakeSapObject().SapModel), "demo", "Demo", "tonf_m_C")
    antiguas = _antiguas(data)
    # F1 y F2 distribuidas (7 muestras cada una), F2 y F3 puntuales, J2 y J3 con fuerza: 18.
    assert len(antiguas) == 18
    assert all(
        set(a)
        == {"id", "objectType", "objectId", "pattern", "position", "direction", "value", "kind", "unit"}
        for a in antiguas
    )
    assert [a["value"] for a in antiguas if a["objectId"] == "J3"] == [10.0]
    assert [a["direction"] for a in antiguas if a["objectId"] == "J3"] == [[0.0, 0.0, -1.0]]
    assert len(_componentes(data)) == len(antiguas)
    assert data["loadsTruncated"] is False
