"""K y L calculados por el diseño de acero (solo lectura). Tabla simulada: HIPÓTESIS sin verificar."""
from __future__ import annotations

import pytest

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.steel_k import read_k_factors
from bridge.viewer.sap2000.steel_k_design import find_design_columns, read_design_k_factors

PREFIJO = "/v1/sap/actions/viewer"


@pytest.fixture
def model():
    return FakeSapObject().SapModel


def test_lectura_ok_ocho_valores_por_barra(model):
    r = read_design_k_factors(model)
    assert r.available and r.table_key.endswith("PMM Details - AISC 360-16")
    assert set(r.frames) == {"F1", "F3"}
    assert set(r.frames["F1"]) == {"K1 Major", "K1 Minor", "K2 Major", "K2 Minor", "K LTB",
                                   "L Major", "L Minor", "L LTB"}
    assert r.frames["F1"]["L Minor"] == 0.5


def test_fila_gobernante_por_mayor_ratio_y_nota_si_difieren(model):
    r = read_design_k_factors(model)
    assert r.governing["F1"] == {"combo": "COMB2", "station": 2.5, "ratio": 0.82,
                                 "designSect": "W21X44", "rows": 3}
    assert r.governing["F3"]["combo"] == "COMB2"
    assert r.frames["F3"]["K2 Major"] == 1.6  # la gobernante, no la primera
    assert any("F3" in n and "K2 Major" in n and "difiere" in n for n in r.notes)
    assert not any("F1" in n and "difiere" in n for n in r.notes)


def test_sin_columna_ratio_usa_la_primera_fila_con_nota(model):
    db = model.DatabaseTables
    db.DESIGN_PMM_COLUMNS = [c for c in db.DESIGN_PMM_COLUMNS if c != "Ratio"]
    r = read_design_k_factors(model)
    assert r.available and r.governing["F3"]["combo"] == "COMB1"
    assert any("Sin columna de ratio" in n for n in r.notes)


def test_diseno_no_corrido_no_es_error(model):
    model.DatabaseTables.design_run = False
    r = read_design_k_factors(model)
    assert not r.available and "ejecute el diseño de acero antes" in r.reason
    assert r.frames == {}


def test_tabla_vacia_es_diseno_no_corrido(model):
    model.DatabaseTables.design_rows = []
    r = read_design_k_factors(model)
    assert not r.available and "ejecute el diseño" in r.reason


@pytest.mark.parametrize("unitless", [False, True])
def test_tolera_nombres_con_y_sin_unitless(model, unitless):
    model.DatabaseTables.design_unitless_names = unitless
    r = read_design_k_factors(model)
    assert r.available and r.frames["F3"]["K LTB"] == 1.2
    assert r.columns["L LTB"].startswith("XLLTB") and r.frames["F1"]["L Major"] == 1.0


@pytest.mark.parametrize("cols", [
    ["K1Major Unitless", "K1Minor Unitless", "K2Major Unitless", "K2Minor Unitless", "KLTB Unitless",
     "XLMajor Unitless", "XLMinor Unitless", "XLLTB Unitless"],
    ["K1 Major", "K1 Minor", "K2 Major", "K2 Minor", "K LTB", "XL Major", "XL Minor", "XL LTB"],
])
def test_find_design_columns_normaliza(cols):
    assert len(find_design_columns(cols)) == 8


def test_no_modifica_el_modelo_y_no_se_confunde_con_overwrites(model):
    db = model.DatabaseTables
    antes = ({f: dict(r) for f, r in db.steel_k_rows.items()}, [dict(r) for r in db.design_rows])
    read_design_k_factors(model)
    assert db._pending_edit is None
    assert ({f: dict(r) for f, r in db.steel_k_rows.items()}, db.design_rows) == antes
    # La tabla de resultados no debe colarse como tabla de overwrites.
    assert read_k_factors(model).table_key == db.STEEL_K_TABLE
    db.steel_k_rows.clear()
    assert read_k_factors(model).frames == {}  # y no las filas PMM


def test_tabla_sin_columnas_reconocidas(model):
    db = model.DatabaseTables
    db.DESIGN_PMM_COLUMNS = ["Frame", "Combo", "Ratio"]
    r = read_design_k_factors(model)
    assert not r.available and "sin verificar" in r.reason


# ── HTTP ─────────────────────────────────────────────────────────────────────
from tests.viewer.test_viewer_web_k import Connector  # noqa: E402


@pytest.fixture
def client(rutas_viewer):
    holder = {}

    def factory():
        holder["c"] = Connector()
        return holder["c"]

    with rutas_viewer(factory) as c:
        c.holder = holder
        yield c


def test_http_requiere_conexion(client):
    assert client.get(PREFIJO + "/k/design").status_code == 409


def test_http_k_design_forma_exacta_y_k_no_cambia(client):
    client.conectar_demo()
    k_antes = client.get(PREFIJO + "/k").json()
    body = client.get(PREFIJO + "/k/design").json()
    assert set(body) == {"available", "reason", "tableKey", "notes", "frames", "governing"}
    assert body["available"] is True and body["reason"] == ""
    assert body["frames"]["F1"]["K1 Major"] == {"value": 1.0}
    assert body["frames"]["F3"]["L LTB"] == {"value": 1.2}
    assert body["governing"]["F1"]["combo"] == "COMB2"
    assert set(k_antes["frames"]["F1"]) == {"K1 Major", "K1 Minor", "K2 Major", "K2 Minor",
                                            "L Major", "L Minor", "L LTB"}
    assert client.get(PREFIJO + "/k").json() == k_antes


def test_http_diseno_no_corrido(client):
    client.conectar_demo()
    client.holder["c"].model.DatabaseTables.design_run = False
    r = client.get(PREFIJO + "/k/design")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and "ejecute el diseño de acero antes" in body["reason"]
    assert body["frames"] == {}


# ── Nombres reales (sonda): «Steel Details 2 - PMM Details - AISC 360-16» ────


def test_nombre_real_steel_details(model):
    r = read_design_k_factors(model)
    assert r.table_key == "Steel Details 2 - PMM Details - AISC 360-16"


def test_acepta_variante_steel_design_2(model):
    db = model.DatabaseTables
    db.design_table_name = "Steel Design 2 - PMM Details - AISC 360-16"
    r = read_design_k_factors(model)
    assert r.available and r.table_key == "Steel Design 2 - PMM Details - AISC 360-16"
    db.read_unlisted_tables = True  # la tabla no se lista pero se lee por nombre directo
    db.GetAvailableTables = lambda *a: (1, ["Joint Coordinates"], 0)
    assert read_design_k_factors(model).available


def test_coexiste_con_overwrites_reales(model):
    model.DatabaseTables.use_real_overwrites()
    assert read_design_k_factors(model).available
    assert read_k_factors(model).table_key == "Overwrites - Steel Design - AISC 360-16"


# ── Lectura basura (diseño sin ejecutar en la sesión) ────────────────────────


def test_lectura_basura_no_disponible(model):
    model.DatabaseTables.design_garbage = True
    r = read_design_k_factors(model)
    assert not r.available and r.frames == {} and r.governing == {}
    assert "probablemente el diseño de acero no está ejecutado" in r.reason


def test_http_lectura_basura_available_false(client):
    client.conectar_demo()
    client.holder["c"].model.DatabaseTables.design_garbage = True
    r = client.get(PREFIJO + "/k/design")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["frames"] == {} and body["governing"] == {}
    assert "no está ejecutado" in body["reason"]
    assert "SAP2000\"" not in r.text and "Tonf" not in r.text


# ── SAP2000 23.3.1 verificado: Steel Details 1/2 basura; «Steel Design 2» legible no listada ──

from bridge.viewer.sap2000.steel_k_design import (  # noqa: E402
    DESIGN_FIELDS,
    design_table_candidates,
    leer_tabla_valida,
)

NOMBRE_REAL = "Steel Design 2 - PMM Details - AISC 360-16"
NOMBRE_BASURA = "Steel Details 2 - PMM Details - AISC 360-16"


class _DbFirmas:
    """Stub de DatabaseTables: una firma (por su forma de llamada) devuelve basura, otra una tabla
    válida, otra nada. `llamadas` registra el orden de los intentos."""

    def __init__(self, basura_en=(), valida_en=(), lanza=False):
        self.basura_en, self.valida_en, self.lanza = set(basura_en), set(valida_en), lanza
        self.llamadas: list[str] = []

    @staticmethod
    def _firma(args):
        """Clave de la firma: «L» para lista vacía, valor literal para el resto."""
        return "|".join("L" if isinstance(a, list) else repr(a) for a in args)

    def GetTableForDisplayArray(self, key, *args):
        if self.lanza:
            raise RuntimeError("firma no soportada")
        firma = self._firma(args)
        self.llamadas.append(firma)
        if firma in self.valida_en:
            return (key, [c for c in NOMBRE_COLS], len(NOMBRE_COLS), 2,
                    [v for fila in NOMBRE_FILAS for v in fila], 0)
        if firma in self.basura_en:
            return (key, [""] * 17, 17, 1, ["SAP2000", "23.3.1"] + [""] * 15, 1)
        return (key, [], 0, 0, [], 0)


# Firmas de GetTableForDisplayArray tal como las ve _DbFirmas (en el mismo orden que el lector).
FIRMA_1 = "L|'ALL'|0|L|0|L"   # (key, [], 'ALL', 0, [], 0, [])  verificada
FIRMA_2 = "''|'ALL'"          # (key, '', 'ALL')
FIRMA_3 = "L|''|0|L|0|L"      # (key, [], '', 0, [], 0, [])
FIRMA_4 = "''|''|0|L|0|L"     # (key, '', '', 0, [], 0, [])
NOMBRE_COLS = ["Frame", "DesignSect", "Combo", "Location", "TotalRatio", "K1Major", "XLMajor"]
NOMBRE_FILAS = [["1", "W21X44", "DSTL8", 0, 0.845412, 1, 1], ["2", "W18X35", "DSTL3", 2.5, 0.61, 1, 0.5]]


@pytest.fixture
def model_real(model):
    model.DatabaseTables.design_details_garbage_but_design_name_valid = True
    return model


def test_real_lee_steel_design_2_no_listada_y_descarta_details(model_real):
    r = read_design_k_factors(model_real)
    assert r.available and r.table_key == NOMBRE_REAL
    assert any("no figuraba en la lista" in n for n in r.notes)
    assert not any("difiere" in n or "Sin columna de ratio" in n for n in r.notes)


def test_real_orden_de_candidatos_salta_details_basura(model_real):
    disponibles = model_real.DatabaseTables.GetAvailableTables()[1]
    cands = design_table_candidates(disponibles)
    assert cands[0] == (NOMBRE_BASURA, True)
    assert (NOMBRE_REAL, False) in cands


def test_real_tabla_de_55_columnas_una_fila_por_barra(model_real):
    db = model_real.DatabaseTables
    assert len(db.REAL_DESIGN_COLUMNS) == 55 and len(set(db.REAL_DESIGN_COLUMNS)) == 55
    r = read_design_k_factors(model_real)
    assert set(r.frames) == {"1", "2"}
    assert all(g["rows"] == 1 for g in r.governing.values())
    assert set(r.frames["1"]) == set(DESIGN_FIELDS)
    assert r.frames["1"]["K2 Major"] == pytest.approx(1.352689)
    assert r.frames["1"]["K2 Minor"] == pytest.approx(1.142895)
    assert r.frames["1"]["K LTB"] == pytest.approx(1.142895)
    assert r.frames["2"]["L Minor"] == 0.5
    assert r.columns == {"K1 Major": "K1Major", "K1 Minor": "K1Minor", "K2 Major": "K2Major",
                         "K2 Minor": "K2Minor", "K LTB": "KLTB", "L Major": "XLMajor",
                         "L Minor": "XLMinor", "L LTB": "XLLTB"}


def test_real_fila_gobernante_combo_station_ratio_designsect(model_real):
    """Columnas reales: Combo, Location (estación), TotalRatio (ratio), DesignSect."""
    r = read_design_k_factors(model_real)
    assert r.governing["1"] == {"combo": "DSTL8", "station": 0.0, "ratio": 0.845412,
                                "designSect": "HN20X33.8", "rows": 1}
    assert r.governing["2"] == {"combo": "DSTL3", "station": 2.5, "ratio": 0.61,
                                "designSect": "W21X44", "rows": 1}
    assert all("difiere" not in n for n in r.notes)


def test_real_diseno_no_corrido_no_da_frames(model_real):
    model_real.DatabaseTables.design_run = False
    r = read_design_k_factors(model_real)
    assert not r.available and r.frames == {} and r.governing == {}


def test_leer_tabla_valida_prueba_la_firma_siguiente_tras_basura():
    db = _DbFirmas(basura_en={FIRMA_1}, valida_en={FIRMA_2})
    tabla, basura = leer_tabla_valida(db, NOMBRE_REAL)
    assert basura is None and tabla is not None
    assert tabla[0] == NOMBRE_COLS and len(tabla[1]) == 2
    assert db.llamadas[:2] == [FIRMA_1, FIRMA_2]  # la basura no impide probar la siguiente


def test_leer_tabla_valida_solo_basura_no_da_tabla():
    db = _DbFirmas(basura_en={FIRMA_1, FIRMA_2, FIRMA_3, FIRMA_4})
    tabla, basura = leer_tabla_valida(db, NOMBRE_BASURA)
    assert tabla is None and basura is not None and len(basura[0]) == 17


def test_leer_tabla_valida_todas_con_excepcion_relanza():
    db = _DbFirmas(lanza=True)
    with pytest.raises(RuntimeError):
        leer_tabla_valida(db, NOMBRE_REAL)


def test_real_http_k_design_valido_con_tabla_no_listada(client):
    client.conectar_demo()
    client.holder["c"].model.DatabaseTables.design_details_garbage_but_design_name_valid = True
    r = client.get(PREFIJO + "/k/design")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["reason"] == ""
    assert body["tableKey"] == NOMBRE_REAL
    assert body["frames"]["1"]["K2 Major"] == {"value": pytest.approx(1.352689)}
    assert body["governing"]["1"]["combo"] == "DSTL8"
    assert body["governing"]["2"]["station"] == 2.5
    assert "Tonf" not in r.text
