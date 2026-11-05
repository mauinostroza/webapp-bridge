"""Factores K de diseño de acero (sap2000/steel_k.py).

IMPORTANTE sobre lo que estos tests prueban: la tabla de overwrites del
simulador (`FakeSapObject`) encarna una HIPÓTESIS sobre cómo SAP2000 la
expone. Estos tests demuestran que la LÓGICA es correcta (descubrir la
tabla, no inventar valores, verificar siempre lo escrito), no que SAP2000
real se comporte así: eso se valida en un modelo real con el diagnóstico.
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import pytest

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.steel_k import (
    K_FIELDS,
    KFactorError,
    KFactorLockedError,
    KFactorVerifyError,
    KValue,
    diagnose,
    find_k_columns,
    read_k_factors,
    write_k_factors,
)


@pytest.fixture
def model():
    # FakeSapObject envuelve el modelo real (cSapModel) en `.SapModel`.
    return FakeSapObject().SapModel


# ── Lectura ──────────────────────────────────────────────────────────────────


def test_lee_los_cuatro_k_de_las_barras_de_acero(model):
    r = read_k_factors(model)
    assert r.available and r.reason == ""
    assert r.table_key == "Steel Design Overwrites 1 - General - AISC 360-16"
    assert set(r.frames) == {"F1", "F3"}
    assert r.frames["F3"]["K1 Major"] == KValue(0.8, False)
    assert r.frames["F3"]["K2 Minor"] == KValue(1.2, False)


def test_program_determined_es_automatico_y_no_un_numero(model):
    """Un K no sobrescrito lo calcula SAP2000 en el diseño: no tiene valor y
    nunca debe leerse como número."""
    r = read_k_factors(model)
    k2 = r.frames["F1"]["K2 Major"]
    assert k2.auto is True and k2.value is None
    assert r.frames["F1"]["K1 Major"] == KValue(1.0, False)


def test_una_barra_de_hormigon_no_tiene_k(model):
    """F2 es de hormigón: sin fila, sin dato. No se rellena con 1,0."""
    assert "F2" not in read_k_factors(model).frames


def test_sin_tabla_de_overwrites_dice_por_que_y_no_devuelve_nada(model):
    model.DatabaseTables.GetAvailableTables = lambda *a: (1, ["Joint Coordinates"], 0)
    r = read_k_factors(model)
    assert not r.available and r.frames == {}
    assert "ninguna tabla de overwrites" in r.reason
    assert "Diagnóstico" in r.reason


def test_tabla_sin_columnas_k_se_rechaza_con_las_columnas_vistas(model):
    db = model.DatabaseTables
    db.STEEL_K_COLUMNS = ["Frame", "DesignSect", "LRatioMajor"]
    r = read_k_factors(model)
    assert not r.available
    assert "LRatioMajor" in r.reason


@pytest.mark.parametrize(
    "nombres",
    [
        ["Frame", "K1Major", "K1Minor", "K2Major", "K2Minor"],
        ["Frame", "K1 Major", "K1 Minor", "K2 Major", "K2 Minor"],
        ["Frame", "Effective Length Factor (K1 Major)", "Effective Length Factor (K1 Minor)",
         "Effective Length Factor (K2 Major)", "Effective Length Factor (K2 Minor)"],
    ],
)
def test_reconoce_las_columnas_por_nombre_normalizado(nombres):
    cols = find_k_columns(nombres)
    assert cols is not None and set(cols) == set(K_FIELDS)
    assert cols["K1 Major"] == nombres[1] and cols["K2 Minor"] == nombres[4]


def test_falta_un_k_y_no_se_reconoce_la_tabla():
    assert find_k_columns(["Frame", "K1Major", "K1Minor", "K2Major"]) is None


def test_valor_no_interpretable_no_se_inventa_y_deja_nota(model):
    model.DatabaseTables.steel_k_rows["F3"]["K1Major"] = "abc"
    r = read_k_factors(model)
    assert r.frames["F3"]["K1 Major"] == KValue(None, False)
    assert any("abc" in n for n in r.notes)


def test_coma_decimal(model):
    model.DatabaseTables.steel_k_rows["F3"]["K1Major"] = "0,85"
    assert read_k_factors(model).frames["F3"]["K1 Major"].value == 0.85


def test_prefiere_la_tabla_de_aisc_360_16(model):
    otra = "Steel Design Overwrites 1 - General - AISC 360-10"
    model.DatabaseTables.GetAvailableTables = lambda *a: (
        2, [otra, model.DatabaseTables.STEEL_K_TABLE], 0
    )
    assert read_k_factors(model).table_key.endswith("AISC 360-16")


# ── Escritura ────────────────────────────────────────────────────────────────


def test_escribe_y_verifica_releyendo(model):
    res = write_k_factors(model, "F1", {"K1 Major": 0.7, "K2 Minor": 1.5})
    assert res.verified
    por_campo = {c.field: c for c in res.changes}
    assert por_campo["K1 Major"].before == KValue(1.0, False)
    assert por_campo["K1 Major"].after == KValue(0.7, False)
    # Lo leído después viene de SAP2000 (simulado), no de lo que se pidió.
    assert read_k_factors(model).frames["F1"]["K2 Minor"].value == 1.5


def test_solo_toca_los_campos_indicados(model):
    antes = read_k_factors(model).frames["F3"]
    write_k_factors(model, "F3", {"K1 Major": 0.9})
    despues = read_k_factors(model).frames["F3"]
    assert despues["K1 Major"].value == 0.9
    for fld in ("K1 Minor", "K2 Major", "K2 Minor"):
        assert despues[fld] == antes[fld]


def test_poner_en_automatico(model):
    res = write_k_factors(model, "F3", {"K1 Major": "auto"})
    assert res.verified
    assert read_k_factors(model).frames["F3"]["K1 Major"].auto is True


@pytest.mark.parametrize("valor", [0, -1, 11, float("nan"), float("inf")])
def test_rechaza_valores_fuera_de_rango_sin_tocar_el_modelo(model, valor):
    with pytest.raises(ValueError, match="fuera de rango"):
        write_k_factors(model, "F1", {"K1 Major": valor})
    assert read_k_factors(model).frames["F1"]["K1 Major"].value == 1.0


def test_rechaza_campo_desconocido_vacio_y_no_numerico(model):
    with pytest.raises(ValueError, match="desconocido"):
        write_k_factors(model, "F1", {"K3 Major": 1.0})
    with pytest.raises(ValueError, match="ningún cambio"):
        write_k_factors(model, "F1", {})
    with pytest.raises(ValueError, match="número o 'auto'"):
        write_k_factors(model, "F1", {"K1 Major": "0.8"})
    with pytest.raises(ValueError, match="número o 'auto'"):
        write_k_factors(model, "F1", {"K1 Major": True})


def test_barra_sin_overwrites_no_crea_ninguno(model):
    with pytest.raises(KFactorError, match="no tiene overwrites"):
        write_k_factors(model, "F2", {"K1 Major": 1.0})
    assert "F2" not in model.DatabaseTables.steel_k_rows


def test_escritura_que_dice_ok_pero_no_escribe_no_se_da_por_buena(model):
    """El caso que obliga a releer: SAP2000 responde sin error y el valor no
    cambia. Debe informar del valor REAL, no fingir éxito."""
    model.DatabaseTables.apply_silently_ignores = True
    with pytest.raises(KFactorVerifyError) as exc:
        write_k_factors(model, "F1", {"K1 Major": 0.7})
    assert "pedido 0.7" in str(exc.value) and "en SAP2000 1" in str(exc.value)
    assert exc.value.result.verified is False
    assert exc.value.result.changes[0].after.value == 1.0


def test_modelo_bloqueado_lanza_error_especifico_para_pedir_confirmacion(model):
    model.DatabaseTables.locked_rejects_edits = True
    model.locked = True
    with pytest.raises(KFactorLockedError):
        write_k_factors(model, "F1", {"K1 Major": 0.7})
    assert read_k_factors(model).frames["F1"]["K1 Major"].value == 1.0


def test_rechazo_sin_bloqueo_es_un_error_normal(model):
    model.DatabaseTables.locked_rejects_edits = True
    model.locked = False
    with pytest.raises(KFactorError) as exc:
        write_k_factors(model, "F1", {"K1 Major": 0.7})
    assert not isinstance(exc.value, KFactorLockedError)


def test_el_simulador_rechaza_una_edicion_mal_formada(model):
    """Guarda del propio simulador: si aceptase cualquier cosa, el resto de
    tests daría por buena una escritura mal formada."""
    db = model.DatabaseTables
    key = db.STEEL_K_TABLE
    assert db.SetTableForEditingArray("Otra tabla", 0, ["Frame"], 1, ["F1"])[-1] == 1
    assert db.SetTableForEditingArray(key, 0, ["Frame", "NoExiste"], 1, ["F1", 1])[-1] == 1
    assert db.SetTableForEditingArray(key, 0, ["Frame", "K1Major"], 1, ["F1"])[-1] == 1
    assert db.SetTableForEditingArray(key, 0, ["Frame", "K1Major"], 1, ["F1", 1])[-1] == 0


# ── Diagnóstico ──────────────────────────────────────────────────────────────


def test_diagnostico_lista_tablas_columnas_y_resultado(model):
    texto = diagnose(model)
    assert "DIAGNÓSTICO DE FACTORES K" in texto
    assert "Steel Design Overwrites 1 - General - AISC 360-16" in texto
    assert "K1Major" in texto
    assert "Resultado de la lectura: OK" in texto


def test_diagnostico_sin_tabla_lo_dice(model):
    model.DatabaseTables.GetAvailableTables = lambda *a: (1, ["Joint Coordinates"], 0)
    texto = diagnose(model)
    assert "NO DISPONIBLE" in texto and "(ninguna)" in texto


def test_diagnostico_sin_database_tables():
    class Vacio:
        pass

    assert "no expone DatabaseTables" in diagnose(Vacio())


# ── Relaciones de longitud no arriostrada (L, solo lectura, opcionales) ─────
# Los nombres de columna son HIPÓTESIS sin verificar contra SAP2000 real.

from bridge.viewer.sap2000.steel_k import L_FIELDS, find_l_columns  # noqa: E402

NOTA_SIN_L = "Esta tabla no trae relaciones de longitud no arriostrada"


@pytest.mark.parametrize(
    "nombres",
    [
        ["Frame", "LRatioMajor", "LRatioMinor", "LRatioLTB"],
        ["Frame", "L Ratio Major", "L Ratio Minor", "L Ratio LTB"],
        ["Frame", "LRatio Major", "LRatio Minor", "LRatio LTB"],
        [
            "Frame",
            "Unbraced Length Ratio (Major)",
            "Unbraced Length Ratio (Minor)",
            "Unbraced Length Ratio (LTB)",
        ],
        ["Frame", "L Major", "L Minor", "L LTB"],
    ],
)
def test_reconoce_las_variantes_de_nombre_de_l(nombres):
    cols = find_l_columns(nombres)
    assert cols == {"L Major": nombres[1], "L Minor": nombres[2], "L LTB": nombres[3]}


def test_k1major_no_se_confunde_con_l_y_las_derivadas_pierden():
    assert find_l_columns(["Frame", "K1Major", "K1Minor", "K2Major", "K2Minor"]) == {}
    cols = find_l_columns(["Frame", "LRatioMajorFoo", "LRatioMajor"])
    assert cols == {"L Major": "LRatioMajor"}


def test_lee_l_con_auto_y_vacio_y_k_siguen_iguales(model):
    r = read_k_factors(model)
    assert r.available and set(r.length_columns) == set(L_FIELDS)
    assert r.lengths["F1"] == {
        "L Major": KValue(1.0, False),
        "L Minor": KValue(0.5, False),
        "L LTB": KValue(None, True),
    }
    assert r.lengths["F3"]["L Minor"] == KValue(None, False)
    assert r.lengths["F3"]["L LTB"] == KValue(1.2, False)
    assert set(r.frames["F1"]) == set(K_FIELDS)  # frames sigue solo con K
    assert NOTA_SIN_L not in " ".join(r.notes)


@pytest.mark.parametrize("malo", [0, -1.0, "0", "inf", float("inf"), float("nan"), "nan", "abc"])
def test_ratio_invalido_es_sin_dato_con_nota(model, malo):
    model.DatabaseTables.steel_k_rows["F3"]["LRatioMajor"] = malo
    r = read_k_factors(model)
    assert r.lengths["F3"]["L Major"] == KValue(None, False)
    assert any("barra F3" in n and "L mayor" in n for n in r.notes)


def test_ratio_mayor_que_uno_es_valido(model):
    model.DatabaseTables.steel_k_rows["F3"]["LRatioMajor"] = 2.5
    r = read_k_factors(model)
    assert r.lengths["F3"]["L Major"] == KValue(2.5, False)
    assert not any("L mayor" in n for n in r.notes)


def test_tabla_sin_l_solo_k_con_nota_y_available(model):
    model.DatabaseTables.include_length_columns = False
    r = read_k_factors(model)
    assert r.available and r.length_columns == {} and r.lengths == {}
    assert NOTA_SIN_L in r.notes
    assert set(r.frames["F1"]) == set(K_FIELDS)


def test_subconjunto_de_l_omite_las_que_faltan_y_lo_anota(model):
    model.DatabaseTables.STEEL_L_COLUMNS = ["LRatioMajor"]
    r = read_k_factors(model)
    assert set(r.length_columns) == {"L Major"}
    assert set(r.lengths["F1"]) == {"L Major"}
    assert any("L Minor" in n and "L LTB" in n for n in r.notes)


def test_tabla_sin_k_se_sigue_rechazando_aunque_tenga_l(model):
    model.DatabaseTables.STEEL_K_COLUMNS = ["Frame", "DesignSect"]
    r = read_k_factors(model)
    assert not r.available and r.lengths == {}


def test_write_k_factors_solo_k_no_toca_l(model):
    antes = {
        f: {k: v for k, v in row.items() if k.startswith("LRatio")}
        for f, row in model.DatabaseTables.steel_k_rows.items()
    }
    write_k_factors(model, "F1", {"K1 Major": 0.7, "K2 Minor": "auto"})
    despues = {
        f: {k: v for k, v in row.items() if k.startswith("LRatio")}
        for f, row in model.DatabaseTables.steel_k_rows.items()
    }
    assert antes == despues
    res = write_k_factors(model, "F1", {"K1 Major": 0.7})
    assert all(not c.field.startswith("L ") for c in res.changes)


def test_diagnostico_lista_columnas_l(model):
    txt = diagnose(model)
    assert "columnas L reconocidas" in txt and "LRatioMajor" in txt


# ── Escritura de L ───────────────────────────────────────────────────────────


def test_escribe_l_numero_y_auto_y_verifica(model):
    res = write_k_factors(model, "F1", {"L Major": 2.5, "L Minor": "auto"})
    assert res.verified
    por = {c.field: c for c in res.changes}
    assert por["L Major"].before == KValue(1.0, False)
    assert por["L Major"].after == KValue(2.5, False)  # >1 es válido
    assert por["L Minor"].before == KValue(0.5, False)
    assert por["L Minor"].after.auto is True
    r = read_k_factors(model)
    assert r.lengths["F1"]["L Major"].value == 2.5
    assert r.lengths["F1"]["L Minor"].auto is True


def test_escribe_l_sobre_celda_vacia(model):
    res = write_k_factors(model, "F3", {"L Minor": 0.6})
    assert res.verified and res.changes[0].before == KValue(None, False)
    assert read_k_factors(model).lengths["F3"]["L Minor"].value == 0.6


def test_mezcla_k_y_l_en_una_peticion(model):
    antes = read_k_factors(model)
    res = write_k_factors(model, "F3", {"K1 Major": 0.9, "L LTB": "auto", "L Major": 3.0})
    assert res.verified and [c.field for c in res.changes] == ["K1 Major", "L LTB", "L Major"]
    r = read_k_factors(model)
    assert r.frames["F3"]["K1 Major"].value == 0.9
    assert r.lengths["F3"]["L LTB"].auto and r.lengths["F3"]["L Major"].value == 3.0
    # Lo no pedido queda igual.
    assert r.frames["F3"]["K2 Minor"] == antes.frames["F3"]["K2 Minor"]
    assert r.lengths["F3"]["L Minor"] == antes.lengths["F3"]["L Minor"]


def test_escribir_l_no_toca_k_ni_otras_barras(model):
    filas = {f: dict(r) for f, r in model.DatabaseTables.steel_k_rows.items()}
    write_k_factors(model, "F1", {"L Major": 4.0})
    despues = model.DatabaseTables.steel_k_rows
    assert despues["F3"] == filas["F3"]
    assert {k: v for k, v in despues["F1"].items() if k != "LRatioMajor"} == {
        k: v for k, v in filas["F1"].items() if k != "LRatioMajor"
    }


def test_columna_l_ausente_es_error_claro_sin_escribir(model):
    db = model.DatabaseTables
    db.include_length_columns = False
    antes = {f: dict(r) for f, r in db.steel_k_rows.items()}
    with pytest.raises(KFactorError, match="no trae la columna de L mayor"):
        write_k_factors(model, "F1", {"K1 Major": 0.7, "L Major": 2.0})
    assert db.steel_k_rows == antes and db._pending_edit is None


def test_subconjunto_de_l_rechaza_la_que_falta(model):
    model.DatabaseTables.STEEL_L_COLUMNS = ["LRatioMajor"]
    write_k_factors(model, "F1", {"L Major": 2.0})
    with pytest.raises(KFactorError, match="L LTB"):
        write_k_factors(model, "F1", {"L LTB": 2.0})


@pytest.mark.parametrize("valor", [0, -1, -0.5, float("nan"), float("inf"), float("-inf")])
def test_l_rechaza_no_positivos_y_no_finitos(model, valor):
    with pytest.raises(ValueError, match="fuera de rango"):
        write_k_factors(model, "F1", {"L Major": valor})
    assert read_k_factors(model).lengths["F1"]["L Major"].value == 1.0


def test_l_no_tiene_tope_superior_ni_acepta_texto(model):
    assert write_k_factors(model, "F1", {"L Major": 50.0}).verified
    with pytest.raises(ValueError, match="número o 'auto'"):
        write_k_factors(model, "F1", {"L Major": "2.0"})


def test_l_escritura_ignorada_no_se_da_por_buena(model):
    model.DatabaseTables.apply_silently_ignores = True
    with pytest.raises(KFactorVerifyError) as exc:
        write_k_factors(model, "F1", {"L Major": 2.0})
    assert "L mayor" in str(exc.value) and "en SAP2000 1" in str(exc.value)
    assert exc.value.result.changes[0].after.value == 1.0


def test_l_barra_sin_overwrites_no_crea_ninguno(model):
    with pytest.raises(KFactorError, match="no tiene overwrites"):
        write_k_factors(model, "F2", {"L Major": 2.0})
    assert "F2" not in model.DatabaseTables.steel_k_rows


def test_l_modelo_bloqueado(model):
    model.DatabaseTables.locked_rejects_edits = True
    model.locked = True
    with pytest.raises(KFactorLockedError):
        write_k_factors(model, "F1", {"L Major": 2.0})


# ── Lectura robusta: tablas no listadas / nombres alternativos ───────────────


def test_tabla_no_listada_pero_legible_se_lee_directamente(model):
    db = model.DatabaseTables
    db.GetAvailableTables = lambda *a: (1, ["Joint Coordinates"], 0)
    assert not read_k_factors(model).available  # el simulador no la sirve sin listar
    db.read_unlisted_tables = True
    r = read_k_factors(model)
    assert r.available and r.table_key.endswith("AISC 360-16")
    assert "F1" in r.frames
    assert any("no figuraba en la lista" in n for n in r.notes)


def test_tabla_con_otro_nombre_que_contiene_overwrite_es_candidata():
    from bridge.viewer.sap2000.steel_k import _overwrite_table_candidates, table_probe_keys

    disp = ["Frame Overwrites X", "Preferences - Steel Design - AISC 360-16", "Joint Coordinates"]
    assert _overwrite_table_candidates(disp) == [
        "Frame Overwrites X",
        "Preferences - Steel Design - AISC 360-16",
    ]
    claves = table_probe_keys(disp)
    assert all(listada for _, listada in claves[:2])
    assert not any(listada for _, listada in claves[2:])  # hipótesis no listadas


# ── Forma REAL observada con la sonda (SAP2000 real, AISC 360-16) ────────────
from bridge.viewer.sap2000.steel_k import find_kltb_column  # noqa: E402


@pytest.fixture
def real_model():
    m = FakeSapObject().SapModel
    m.DatabaseTables.use_real_overwrites()
    return m


@pytest.mark.parametrize("nombres", [
    ["XLMajor", "XLMinor", "XLLTB"],
    ["XLMajor Unitless", "XLMinor Unitless", "XLLTB Unitless"],
    ["XL Major", "XL Minor", "XL LTB"],
])
def test_reconoce_xl_real_sin_confundir_con_k(nombres):
    cols = ["Frame", "K1Major", "K1Minor", "K2Major", "K2Minor", "KLTB", *nombres]
    assert find_l_columns(cols) == dict(zip(L_FIELDS, nombres))
    assert find_kltb_column(cols) == "KLTB"
    assert find_kltb_column(["KLTB Unitless"]) == "KLTB Unitless"
    assert find_l_columns(["K1Major", "KLTB"]) == {}


def test_tabla_real_cero_es_automatico_en_k_y_l(real_model):
    r = read_k_factors(real_model)
    assert r.table_key == "Overwrites - Steel Design - AISC 360-16"
    assert r.length_columns == {"L Major": "XLMajor", "L Minor": "XLMinor", "L LTB": "XLLTB"}
    assert r.auto_as_zero and r.kltb_column == "KLTB"
    assert r.frames["F1"]["K1 Minor"] == KValue(None, True)
    assert r.frames["F1"]["K1 Major"] == KValue(1.0, False)
    assert r.lengths["F1"]["L Major"] == KValue(None, True)
    assert r.lengths["F1"]["L Minor"] == KValue(0.5, False)
    assert r.kltb["F3"] == KValue(None, True) and r.kltb["F1"] == KValue(1.0, False)
    assert set(r.frames["F1"]) == set(K_FIELDS)  # KLTB no entra en las K de GET /k


def test_tabla_real_escribir_auto_escribe_cero_y_se_verifica(real_model):
    res = write_k_factors(real_model, "F1", {"K1 Major": "auto", "L Minor": "auto", "L Major": 2.0})
    assert res.verified
    fila = real_model.DatabaseTables.steel_k_rows["F1"]
    assert fila["K1Major"] == 0 and fila["XLMinor"] == 0 and fila["XLMajor"] == 2.0


def test_tabla_con_texto_program_determined_sigue_escribiendo_texto(model):
    write_k_factors(model, "F3", {"K1 Major": "auto"})
    assert model.DatabaseTables.steel_k_rows["F3"]["K1Major"] == "Program Determined"
