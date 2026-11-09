"""Orígenes de las webs alojadas del Workbench: se leen del JSON del secreto ORIGENES_ALOJADOS."""

from __future__ import annotations

import json

from bridge import hosted
from bridge.alcances import ruta_permitida

HOSTED = "https://structural-workbench.onrender.com"
FUSION = "https://workbench-fusion.onrender.com"


def test_el_json_del_secreto_define_hosted_y_fusion(tmp_path, monkeypatch):
    archivo = tmp_path / "origenes_alojados.json"
    archivo.write_text(json.dumps({"hosted": HOSTED + "/", "fusion": FUSION}), encoding="utf-8")
    monkeypatch.setattr(hosted, "ARCHIVO", str(archivo))  # ruta absoluta: prevalece sobre la carpeta
    assert hosted._cargar() == {"hosted": HOSTED, "fusion": FUSION}  # sin barra final


def test_hosted_y_fusion_tienen_acceso_completo_incluida_la_zapata():
    for origen in (HOSTED, FUSION):
        for ruta in (
            "/v1/sap/actions/zapata-biaxial/nodos",
            "/v1/sap/actions/zapata-biaxial/reacciones",
            "/v1/sap/actions/viewer/snapshot",
            "/v1/sap/actions/viewer/foundations/results",
            "/v1/sap/actions/acero-aisc360/fuerzas",
        ):
            assert ruta_permitida(origen, ruta), (origen, ruta)


def test_sin_el_secreto_los_origenes_son_marcadores_invalid():
    # El repositorio público no lleva direcciones reales: un .exe compilado sin ORIGENES_ALOJADOS no acepta la web.
    assert hosted.HOSTED_ORIGIN not in (HOSTED, FUSION) or hosted._ORIGENES
    assert hosted.FUSION_ORIGIN not in (HOSTED, FUSION) or hosted._ORIGENES
