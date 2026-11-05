"""La sonda de secciones del Viewer corre sobre un COM falso y distingue lo correcto de lo erróneo."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from tests.viewer.test_viewer_section_props import SECCIONES, Modelo

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "sonda_viewer_secciones.py"


@pytest.fixture(scope="module")
def sonda():
    spec = importlib.util.spec_from_file_location("sonda_viewer_secciones", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _veredicto(informe, prefijo):
    return {f["id"]: f["veredicto"] for f in informe["filas"] if f["id"].startswith(prefijo)}


def test_modelo_coherente_sin_fallos_en_lectura(sonda):
    informe = sonda.ejecutar(Modelo(SECCIONES), max_por_tipo=2)
    for prefijo in ("U1", "N1", "T1", "T2", "T3", "T4", "T5", "M1", "M2", "M3", "R1"):
        v = _veredicto(informe, prefijo)
        assert v, prefijo
        assert set(v.values()) <= {"PASS"}, (prefijo, v)
    # Tipos que el modelo no tiene: solo se confirma la firma.
    assert _veredicto(informe, "T0 GetTube") == {"T0 GetTube": "INCONCLUSO"}


def test_detecta_radios_de_giro_intercambiados(sonda):
    class RadiosCruzados(Modelo):
        def __init__(self):
            super().__init__(SECCIONES)
            original = self.PropFrame.GetSectProps

            def cruzado(nombre, *a):
                s = list(original(nombre, *a))
                s[10], s[11] = s[11], s[10]
                return tuple(s)

            self.PropFrame.GetSectProps = cruzado

    informe = sonda.ejecutar(RadiosCruzados())
    assert "FAIL" in _veredicto(informe, "T3").values()


def test_detecta_dimensiones_que_no_cuadran_con_el_area(sonda):
    modelo = Modelo(SECCIONES)
    modelo.PropFrame.secciones["W300"]["A"] = 20000.0  # área incompatible con h/b/tf/tw
    informe = sonda.ejecutar(modelo)
    assert _veredicto(informe, "T5 área vs dimensiones(W300)")["T5 área vs dimensiones(W300)"] == "FAIL"


def test_comparar_unidades_restaura_las_unidades(sonda):
    modelo = Modelo(SECCIONES, 14)
    llamadas: list[int] = []
    modelo.SetPresentUnits = lambda codigo: llamadas.append(codigo) or 0  # type: ignore[method-assign]
    informe = sonda.ejecutar(modelo, con_comparacion=True)
    assert llamadas[0] == 9 and llamadas[-1] == 14  # N_mm_C y luego la unidad original
    assert _veredicto(informe, "C1")["C1 comparar unidades"] in {"PASS", "FAIL"}


def test_sin_comparar_no_toca_las_unidades(sonda):
    modelo = Modelo(SECCIONES)
    sonda.ejecutar(modelo)
    assert modelo.escrituras == []


def test_markdown_resume_los_veredictos(sonda):
    informe = sonda.ejecutar(Modelo(SECCIONES))
    texto = sonda.markdown(informe, "demo")
    assert "| Prueba | Veredicto | Nota |" in texto and "PASS" in texto
    assert math.isfinite(informe["resumen"]["PASS"])
