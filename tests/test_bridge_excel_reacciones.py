"""Tests de bridge/excel_reacciones.py: ida y vuelta con Excel (sin SAP2000, sin COM).

La carpeta temporal se redirige a ``tmp_path`` y ``os.startfile`` se monkeypatchea (o se quita)
para no abrir nada de verdad.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from bridge.actions import install_routes
from bridge.excel_reacciones import install_routes as install_excel_routes

CONTRATOS = Path(__file__).resolve().parent.parent / "web" / "src" / "bridge_contracts.json"
RUTA_ABRIR = "/v1/sap/actions/excel/open-reactions"
RUTA_LEER = "/v1/sap/actions/excel/read-reactions"
CAMPOS = ("joint", "load_pat", "coord_sys", "f1", "f2", "f3", "m1", "m2", "m3")


@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def abiertos(monkeypatch):
    """Registra las llamadas a os.startfile (Windows simulado en cualquier sistema)."""
    llamadas: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda ruta: llamadas.append(ruta), raising=False)
    return llamadas


@pytest.fixture
def sin_startfile(monkeypatch):
    monkeypatch.delattr(os, "startfile", raising=False)


@pytest.fixture
def cliente():
    app = FastAPI()
    install_excel_routes(app)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _fila(joint="1", **resto):
    base = {"joint": joint, "load_pat": "DEAD", "coord_sys": "Global"}
    base.update({c: "0" for c in ("f1", "f2", "f3", "m1", "m2", "m3")})
    base.update(resto)
    return base


def _crear(cliente, filas):
    r = cliente.post(RUTA_ABRIR, json={"rows": filas})
    assert r.status_code == 200, r.text
    return r.json()


def _leer(cliente, ruta):
    return cliente.post(RUTA_LEER, json={"path": ruta})


# --- Crear -------------------------------------------------------------------------------


def test_crear_escribe_hoja_cargas_y_leeme(temp_dir, sin_startfile, cliente):
    filas = [_fila("1", f1="1.5", m3="-20"), _fila("2", f1="texto", coord_sys="Local")]
    out = _crear(cliente, filas)

    assert set(out) == {"path", "rows_written", "opened", "note"}
    assert out["rows_written"] == 2
    ruta = Path(out["path"])
    assert ruta.parent == temp_dir / "ew_reacciones"
    assert ruta.name.startswith("reacciones_") and ruta.suffix == ".xlsx"
    assert ruta.is_file()

    libro = load_workbook(ruta)
    assert libro.sheetnames == ["Cargas", "Leeme"]
    hoja = libro["Cargas"]
    assert [c.value for c in hoja[1]] == ["Joint", "LoadPat", "CoordSys", "F1", "F2", "F3", "M1", "M2", "M3"]
    assert hoja["D2"].value == 1.5 and isinstance(hoja["D2"].value, float)
    assert hoja["I2"].value == -20.0
    assert hoja["D3"].value == "texto"
    assert hoja["A2"].value == "1" and hoja["C3"].value == "Local"
    assert "Ton·m" in libro["Leeme"]["A1"].value
    libro.close()


def test_numeros_no_finitos_quedan_como_texto(temp_dir, sin_startfile, cliente):
    out = _crear(cliente, [_fila("1", f1="nan", f2="inf", f3="1e3")])
    hoja = load_workbook(out["path"])["Cargas"]
    assert hoja["D2"].value == "nan" and hoja["E2"].value == "inf"
    assert hoja["F2"].value == 1000.0


def test_sin_startfile_no_abre_y_lo_explica(temp_dir, sin_startfile, cliente):
    out = _crear(cliente, [_fila()])
    assert out["opened"] is False
    assert out["note"] and "Windows" in out["note"]


def test_con_startfile_abre_el_fichero(temp_dir, abiertos, cliente):
    out = _crear(cliente, [_fila()])
    assert out["opened"] is True
    assert out["note"] is None
    assert abiertos == [out["path"]]


def test_startfile_con_error_no_tumba_la_respuesta(temp_dir, monkeypatch, cliente):
    def falla(ruta):
        raise OSError("sin asociación")

    monkeypatch.setattr(os, "startfile", falla, raising=False)
    out = _crear(cliente, [_fila()])
    assert out["opened"] is False
    assert "sin asociación" in out["note"]
    assert Path(out["path"]).is_file()


def test_mas_de_5000_filas_es_422(temp_dir, sin_startfile, cliente):
    r = cliente.post(RUTA_ABRIR, json={"rows": [_fila(str(i)) for i in range(5001)]})
    assert r.status_code == 422


def test_crear_borra_ficheros_de_mas_de_un_dia(temp_dir, sin_startfile, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    viejo = carpeta / "reacciones_viejo.xlsx"
    reciente = carpeta / "reacciones_reciente.xlsx"
    viejo.write_bytes(b"x")
    reciente.write_bytes(b"x")
    hace_dos_dias = time.time() - 2 * 24 * 3600
    os.utime(viejo, (hace_dos_dias, hace_dos_dias))

    _crear(cliente, [_fila()])

    assert not viejo.exists()
    assert reciente.exists()


# --- Leer y round-trip -------------------------------------------------------------------


def test_round_trip_crear_y_leer(temp_dir, sin_startfile, cliente):
    filas = [
        _fila("1", f1="1.5", f2="-2", f3="0", m1="0.25", m2="", m3="-20"),
        _fila("J-2", load_pat="LIVE", coord_sys="Local", f1="12.75"),
    ]
    creado = _crear(cliente, filas)
    r = _leer(cliente, creado["path"])

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["count"] == 2
    assert set(cuerpo) == {"rows", "count"}
    assert cuerpo["rows"][0] == {
        "joint": "1",
        "load_pat": "DEAD",
        "coord_sys": "Global",
        "f1": "1.5",
        "f2": "-2",
        "f3": "0",
        "m1": "0.25",
        "m2": "",
        "m3": "-20",
    }
    assert cuerpo["rows"][1]["joint"] == "J-2" and cuerpo["rows"][1]["f1"] == "12.75"
    assert all(set(fila) == set(CAMPOS) for fila in cuerpo["rows"])


def test_leer_cabeceras_en_otro_orden_mayusculas_y_espacios(temp_dir):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_orden.xlsx"
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Cargas"
    hoja.append(["m3", " LOAD PAT ", "f2", "JOINT", "f1", "coordsys", "m1", "M2", "F3"])
    hoja.append([9, "DEAD", 2, "A7", 1, "Global", 4, 5, 3])
    libro.save(ruta)

    from bridge.excel_reacciones import leer_reacciones

    out = leer_reacciones(str(ruta))
    assert out == {
        "rows": [
            {
                "joint": "A7",
                "load_pat": "DEAD",
                "coord_sys": "Global",
                "f1": "1",
                "f2": "2",
                "f3": "3",
                "m1": "4",
                "m2": "5",
                "m3": "9",
            }
        ],
        "count": 1,
    }


def test_filas_con_joint_vacio_se_ignoran_y_celdas_vacias_son_texto_vacio(temp_dir):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_vacias.xlsx"
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Cargas"
    hoja.append(["Joint", "LoadPat", "CoordSys", "F1", "F2", "F3", "M1", "M2", "M3"])
    hoja.append(["1", "DEAD", "Global", 1, None, None, None, None, None])
    hoja.append([None, "DEAD", "Global", 5, 5, 5, 5, 5, 5])  # sin Joint: se ignora
    hoja.append(["   ", None, None, 7, None, None, None, None, None])  # Joint solo espacios: se ignora
    hoja.append(["2", None, None, None, None, None, None, None, None])
    libro.save(ruta)

    from bridge.excel_reacciones import leer_reacciones

    out = leer_reacciones(str(ruta))
    assert out["count"] == 2
    assert [f["joint"] for f in out["rows"]] == ["1", "2"]
    assert out["rows"][0]["f2"] == "" and out["rows"][0]["m3"] == ""
    assert out["rows"][1]["load_pat"] == ""


def test_fichero_inexistente_es_404(temp_dir, cliente):
    ruta = temp_dir / "ew_reacciones" / "reacciones_nada.xlsx"
    r = _leer(cliente, str(ruta))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "file_not_found"


@pytest.mark.parametrize(
    "ruta",
    [
        "/etc/passwd",
        "C:/Windows/win.ini",
        "../../secreto.xlsx",
        "",
    ],
)
def test_path_fuera_del_temp_es_422(temp_dir, cliente, ruta):
    r = _leer(cliente, ruta)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "path_not_allowed"


def test_path_con_traversal_desde_la_carpeta_es_422(temp_dir, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    fuera = temp_dir / "fuera.xlsx"
    Workbook().save(fuera)
    r = _leer(cliente, str(carpeta / ".." / "fuera.xlsx"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "path_not_allowed"


def test_extension_distinta_de_xlsx_es_422(temp_dir, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    txt = carpeta / "reacciones.txt"
    txt.write_text("x", encoding="utf-8")
    r = _leer(cliente, str(txt))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "path_not_allowed"


def test_fichero_bloqueado_es_409_file_locked(temp_dir, sin_startfile, cliente, monkeypatch):
    creado = _crear(cliente, [_fila()])

    import openpyxl

    def bloqueado(*a, **k):
        raise PermissionError(13, "Permiso denegado")

    monkeypatch.setattr(openpyxl, "load_workbook", bloqueado)
    r = _leer(cliente, creado["path"])
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert detalle["code"] == "file_locked"
    assert "Guarde y cierre" in detalle["message"] and "no se pudo leer" in detalle["message"]


def test_sin_hoja_cargas_es_422_invalid_workbook(temp_dir, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_sin_hoja.xlsx"
    libro = Workbook()
    libro.active.title = "Otra"
    libro.save(ruta)
    r = _leer(cliente, str(ruta))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_workbook"


def test_sin_cabeceras_es_422_invalid_workbook(temp_dir, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_sin_cabeceras.xlsx"
    libro = Workbook()
    libro.active.title = "Cargas"
    libro.active.append(["Nudo", "Caso", "Sistema"])
    libro.active.append(["1", "DEAD", "Global"])
    libro.save(ruta)
    r = _leer(cliente, str(ruta))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_workbook"


def test_cabecera_incompleta_es_422_que_nombra_lo_que_falta(temp_dir):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_incompleta.xlsx"
    libro = Workbook()
    libro.active.title = "Cargas"
    libro.active.append(["Joint", "LoadPat", "CoordSys", "F1", "F2"])
    libro.save(ruta)

    from fastapi import HTTPException

    from bridge.excel_reacciones import leer_reacciones

    with pytest.raises(HTTPException) as info:
        leer_reacciones(str(ruta))
    assert info.value.status_code == 422
    assert "F3" in info.value.detail["message"] and "M3" in info.value.detail["message"]


def test_fichero_que_no_es_xlsx_valido_es_422(temp_dir, cliente):
    carpeta = temp_dir / "ew_reacciones"
    carpeta.mkdir()
    ruta = carpeta / "reacciones_roto.xlsx"
    ruta.write_bytes(b"esto no es un zip")
    r = _leer(cliente, str(ruta))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_workbook"


def test_falta_openpyxl_es_409_en_ambas_rutas(temp_dir, monkeypatch, cliente):
    monkeypatch.setitem(__import__("sys").modules, "openpyxl", None)
    r = cliente.post(RUTA_ABRIR, json={"rows": [_fila()]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "openpyxl_missing"
    r = _leer(cliente, str(temp_dir / "ew_reacciones" / "x.xlsx"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "openpyxl_missing"


# --- Instalación y contrato --------------------------------------------------------------


def test_actions_instala_las_dos_rutas_excel():
    app = FastAPI()
    install_routes(app, object(), RuntimeError)
    rutas = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    assert ("POST", RUTA_ABRIR) in rutas
    assert ("POST", RUTA_LEER) in rutas


def test_ruta_abrir_cumple_contrato_json(temp_dir, sin_startfile, cliente):
    contrato = json.loads(CONTRATOS.read_text("utf-8"))["routes"]["POST " + RUTA_ABRIR]
    cuerpo = _crear(cliente, [_fila()])
    assert set(cuerpo) == set(contrato["keys"])


def test_ruta_leer_cumple_contrato_json(temp_dir, sin_startfile, cliente):
    contrato = json.loads(CONTRATOS.read_text("utf-8"))["routes"]["POST " + RUTA_LEER]
    creado = _crear(cliente, [_fila()])
    cuerpo = _leer(cliente, creado["path"]).json()
    assert set(cuerpo) == set(contrato["keys"])
    assert set(cuerpo["rows"][0]) == set(contrato["items"]["rows"])
