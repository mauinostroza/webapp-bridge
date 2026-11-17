"""El escritor propio frente a ifcopenshell (referencia, solo desarrollo): ida y vuelta, GlobalId y deriva del esquema."""

from __future__ import annotations

import random
import subprocess
import sys
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from bridge import ifc_step  # noqa: E402
from bridge.ifc_step import ArchivoStep  # noqa: E402
from tests.ifc_referencia import requiere  # noqa: E402


def _reabrir(f: ArchivoStep):
    ifcopenshell = requiere("ifcopenshell")
    return ifcopenshell.file.from_string(f.a_texto())


def test_guid_igual_al_de_ifcopenshell():
    guid = requiere("ifcopenshell.guid")
    rng = random.Random(1234)
    muestras = [uuid.UUID(int=rng.getrandbits(128)).hex for _ in range(10_000)]
    muestras += ["0" * 32, "f" * 32, "1" * 32, "8" * 32]
    for h in muestras:
        assert ifc_step.comprimir_guid(h) == guid.compress(h), h


def test_cadenas_aleatorias_se_leen_igual_con_ifcopenshell():
    rng = random.Random(1234)
    textos = ["", "L'angulo", "A\\B", "Ø25", "ó·é", "linea\nnueva", "😀"]
    for _ in range(200):
        n = rng.randint(0, 25)
        textos.append(
            "".join(
                chr(
                    rng.choice(
                        [rng.randint(0x20, 0x7E), rng.randint(0xA0, 0x2FF), rng.randint(0x3000, 0xD7FF)]
                    )
                )
                for _ in range(n)
            )
        )
    f = ArchivoStep()
    for t in textos:
        f.crear_entidad("IfcPropertySingleValue", Name="n", NominalValue=f.crear_entidad("IfcLabel", t))
    ifc = _reabrir(f)
    leidos = [p.NominalValue.wrappedValue for p in ifc.by_type("IfcPropertySingleValue")]
    assert leidos == textos


def test_reales_aleatorios_se_leen_igual_con_ifcopenshell():
    import math
    import struct

    rng = random.Random(1234)
    reales = [0.0, 1.0, 1e-5, 1e16, 123456.789, 5e-324, 1.7976931348623157e308, -2.5]
    while len(reales) < 500:
        x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if math.isfinite(x):
            reales.append(x)
    f = ArchivoStep()
    for i in range(0, len(reales) - 2, 3):
        f.crear_entidad("IfcCartesianPoint", Coordinates=reales[i : i + 3])
    ifc = _reabrir(f)
    leidos = [c for p in ifc.by_type("IfcCartesianPoint") for c in p.Coordinates]
    assert leidos == reales[: len(leidos)]


def test_el_esquema_versionado_esta_al_dia():
    """``bridge/ifc4_esquema.py`` coincide con lo que generaría hoy scripts/generar_esquema_ifc4.py (deriva del esquema)."""
    requiere("ifcopenshell")
    r = subprocess.run(
        [sys.executable, str(RAIZ / "scripts" / "generar_esquema_ifc4.py"), "--comprobar"],
        capture_output=True,
        text=True,
        cwd=RAIZ,
    )
    assert r.returncode == 0, r.stderr + r.stdout
