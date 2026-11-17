"""Equivalencia del escritor propio con ifcopenshell: el mismo modelo, escrito por los dos, da el mismo IFC.

Se reabren los dos archivos con ifcopenshell y se comparan por contenido (no por el orden ni la numeración ``#n``):
mismas entidades por clase, mismos GlobalId y, para cada entidad con GlobalId, la misma forma canónica recursiva.
La única diferencia permitida es la cabecera.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import cad_ifc  # noqa: E402
from tests.ifc_referencia import ArchivoIfcopenshell, requiere  # noqa: E402

FIXTURES = RAIZ / "tests" / "fixtures"
NOMBRES = ["pedestal", "placa_base", "perno_pg", "perno_l", "perfil_hss", "perfil_refuerzos", "fundacion"]


def _cargar(nombre: str) -> dict:
    return json.loads((FIXTURES / f"estructura_v1_{nombre}.json").read_text(encoding="utf-8"))


def _escribir(modelo: dict, archivo) -> str:
    w = cad_ifc._Escritor(cad_ifc.validar(modelo), archivo=archivo, marca_tiempo="2026-01-01T00:00:00")
    w.construir()
    return w.f.a_texto()


def _canonico(valor):
    """Forma comparable de lo que devuelve ``get_info(recursive=True)``: sin ``id``, reales redondeados."""
    if isinstance(valor, dict):
        return {k: _canonico(v) for k, v in valor.items() if k != "id"}
    if isinstance(valor, (list, tuple)):
        return [_canonico(v) for v in valor]
    if isinstance(valor, float):
        return 0.0 if valor == 0 else float(f"{valor:.12g}")
    return valor


def _resumen(texto: str):
    ifcopenshell = requiere("ifcopenshell")
    f = ifcopenshell.file.from_string(texto)
    clases = Counter(e.is_a() for e in f)
    raices = {}
    for e in f:
        if hasattr(e, "GlobalId") and e.GlobalId:
            raices[e.GlobalId] = _canonico(e.get_info(recursive=True, include_identifier=False))
    return clases, raices


def _variantes():
    for n in NOMBRES:
        yield pytest.param(n, False, id=n)
    for n in ("pedestal", "placa_base", "fundacion"):
        yield pytest.param(n, True, id=f"{n}-girado")


@pytest.mark.parametrize(("nombre", "girado"), list(_variantes()))
def test_el_escritor_propio_equivale_a_ifcopenshell(nombre, girado):
    requiere("ifcopenshell")
    m = _cargar(nombre)
    if girado:
        m = copy.deepcopy(m)
        m["origen"] = [1500.0, -250.5, 80.0]
        m["angulo"] = 37.0
    propio = _escribir(m, None)
    referencia = _escribir(m, ArchivoIfcopenshell())
    clases_p, raices_p = _resumen(propio)
    clases_r, raices_r = _resumen(referencia)
    assert clases_p == clases_r
    assert set(raices_p) == set(raices_r)
    assert len(raices_p) > 5
    for gid in raices_p:
        assert raices_p[gid] == raices_r[gid], gid


def test_la_salida_es_reproducible_byte_a_byte_con_marca_fija():
    m = _cargar("pedestal")
    a = cad_ifc.generar_ifc(m, marca_tiempo="2026-01-01T00:00:00")
    b = cad_ifc.generar_ifc(copy.deepcopy(m), marca_tiempo="2026-01-01T00:00:00")
    assert a == b


def test_modelo_grande_en_el_limite_de_instancias(tmp_path):
    """5000 instancias de barras (el máximo): valida con ifcopenshell y se genera en un tiempo razonable."""
    import time

    ifcopenshell = requiere("ifcopenshell")
    validate = requiere("ifcopenshell.validate")
    m = copy.deepcopy(_cargar("pedestal"))
    barras = [e for e in m["elementos"] if e["clase"] == "BarraRefuerzo"]
    base = barras[0]
    otros = sum(len(e["geom"].get("instancias") or [None]) for e in m["elementos"] if e is not base)
    n = cad_ifc.MAX_INSTANCIAS - otros  # el límite cuenta las instancias de TODOS los elementos
    base["geom"]["instancias"] = [[float(i % 50) * 30.0, float(i // 50) * 30.0, 0.0] for i in range(n)]
    t0 = time.perf_counter()
    nombre, datos = cad_ifc.generar_ifc(m)
    duracion = time.perf_counter() - t0
    assert duracion < 30, duracion  # holgado: el objetivo orientativo es < 3 s; se mide para ver la tendencia
    ruta = tmp_path / "grande.ifc"
    ruta.write_bytes(datos)
    f = ifcopenshell.open(str(ruta))
    assert len(f.by_type("IfcReinforcingBar")) >= n
    log = validate.json_logger()
    validate.validate(f, log, express_rules=True)
    assert log.statements == []
    assert math.isfinite(duracion)
