"""El catálogo estático de generadores debe coincidir con discover_generators().

Si este test falla tras añadir, quitar o renombrar un generador, regenere el catálogo con
``python scripts/generar_catalogo_generadores.py``.
"""

from __future__ import annotations

import json

import pytest

from backend.report_builder.engine import generator_registry as registro


def _filas(infos):
    return [(i.module_id, i.import_path, i.schema_path, i.display_name) for i in infos]


def test_catalogo_coincide_con_discover_generators():
    descubiertos = registro.discover_generators()
    assert descubiertos, "discover_generators() no encontró generadores: el test no valdría nada"
    assert _filas(registro.catalogo_generadores()) == _filas(descubiertos)


def test_display_name_del_json_coincide_con_modulo_display_names():
    datos = json.loads(registro.CATALOGO_PATH.read_text(encoding="utf-8"))
    descubiertos = registro.discover_generators()
    assert [(d["module_id"], d["display_name"]) for d in datos] == [
        (i.module_id, i.display_name) for i in descubiertos
    ]


def test_catalogo_roto_lanza_error_claro(tmp_path, monkeypatch):
    ruta = tmp_path / "catalogo.json"
    ruta.write_text('{"no": "una lista"}', encoding="utf-8")
    monkeypatch.setattr(registro, "CATALOGO_PATH", ruta)
    with pytest.raises(RuntimeError, match="Catálogo de generadores no válido"):
        registro.catalogo_generadores()
