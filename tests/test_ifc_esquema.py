"""Cobertura del esquema propio (bridge/ifc4_esquema.py) y archivos de referencia (golden) del IFC. Sin ifcopenshell."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import cad_ifc, ifc4_esquema  # noqa: E402

FIXTURES = RAIZ / "tests" / "fixtures"
NOMBRES = ["pedestal", "placa_base", "perno_pg", "perno_l", "perfil_hss", "perfil_refuerzos", "fundacion"]
MARCA = "2026-01-01T00:00:00"
HASHES = FIXTURES / "ifc_sha256.json"


def _literales_ifc() -> set[str]:
    """Todo literal de texto ``"Ifc…"`` de bridge/cad_ifc.py (entidades, tipos definidos y la tabla CLASES)."""
    arbol = ast.parse((RAIZ / "bridge" / "cad_ifc.py").read_text(encoding="utf-8"))
    return {
        n.value
        for n in ast.walk(arbol)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and re.fullmatch(r"Ifc[A-Za-z0-9]+", n.value)
    }


def test_todo_literal_ifc_de_cad_ifc_esta_en_el_esquema():
    conocidos = set(ifc4_esquema.ENTIDADES) | set(ifc4_esquema.TIPOS_DEFINIDOS)
    faltan = sorted(_literales_ifc() - conocidos)
    assert not faltan, (
        f"añádalos a ENTIDADES_USADAS/TIPOS_USADOS de scripts/generar_esquema_ifc4.py y regenere: {faltan}"
    )
    assert len(_literales_ifc()) >= 50


def test_clases_del_modelo_usan_tipos_predefinidos_del_esquema():
    """Lo que se escribe como tipo predefinido existe en IFC4; si no, cae en USERDEFINED (decisión de ``_admite``)."""
    from bridge.ifc_step import ArchivoStep

    f = ArchivoStep()
    esperado = {
        ("IfcFooting", "PAD_FOOTING"): True,
        ("IfcColumn", "COLUMN"): True,
        ("IfcColumn", "PEDESTAL"): False,  # es de IFC4X3 → USERDEFINED
        ("IfcPlate", "BASE_PLATE"): False,
        ("IfcPlate", "STIFFENER_PLATE"): False,
        ("IfcPlate", "SHEET"): True,
        ("IfcMechanicalFastener", "ANCHORBOLT"): True,
        ("IfcMechanicalFastener", "NUT"): False,
        ("IfcMechanicalFastener", "WASHER"): False,
        ("IfcBuildingElementProxy", "GROUT"): False,
        ("IfcReinforcingBar", "MAIN"): True,
    }
    for (entidad, valor), ok in esperado.items():
        assert f.admite_enum(entidad, "PredefinedType", valor) is ok, (entidad, valor)
    for clase in cad_ifc.CLASES.values():
        assert clase.ifc in ifc4_esquema.ENTIDADES


def test_el_esquema_tiene_las_entidades_y_enumeraciones_basicas():
    e = ifc4_esquema.ENTIDADES
    assert e["IfcReinforcingBar"][2][-2][0] == "PredefinedType"
    assert [a[0] for a in e["IfcSIUnit"][2]] == ["Dimensions", "UnitType", "Prefix", "Name"]
    assert e["IfcSIUnit"][2][0][3] is True  # Dimensions es derivado
    assert "PAD_FOOTING" in ifc4_esquema.ENUMERACIONES["IfcFootingTypeEnum"]
    assert ifc4_esquema.ESQUEMA == "IFC4"


@pytest.mark.parametrize("nombre", NOMBRES)
def test_el_ifc_de_cada_fixture_coincide_con_su_huella(nombre):
    """Archivo de referencia (golden) por huella SHA-256: cualquier cambio de la salida aparece como un cambio revisable.

    Para regenerar tras un cambio INTENCIONADO: ``python tests/test_ifc_esquema.py --regenerar``.
    """
    m = json.loads((FIXTURES / f"estructura_v1_{nombre}.json").read_text(encoding="utf-8"))
    _, datos = cad_ifc.generar_ifc(m, marca_tiempo=MARCA)
    esperado = json.loads(HASHES.read_text(encoding="utf-8"))[nombre]
    assert hashlib.sha256(datos).hexdigest() == esperado["sha256"], (
        "la salida del IFC cambió (¿intencionado? regenere)"
    )
    assert len(datos) == esperado["bytes"]


def _regenerar() -> None:
    salida = {}
    for nombre in NOMBRES:
        m = json.loads((FIXTURES / f"estructura_v1_{nombre}.json").read_text(encoding="utf-8"))
        _, datos = cad_ifc.generar_ifc(m, marca_tiempo=MARCA)
        salida[nombre] = {"sha256": hashlib.sha256(datos).hexdigest(), "bytes": len(datos)}
    HASHES.write_text(json.dumps(salida, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Escrito {HASHES.relative_to(RAIZ)}")


if __name__ == "__main__":
    if "--regenerar" in sys.argv:
        _regenerar()
