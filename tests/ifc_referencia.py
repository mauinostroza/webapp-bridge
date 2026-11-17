"""Apoyo de las pruebas del IFC: ``ifcopenshell`` como referencia (solo desarrollo; el puente no la usa).

* ``requiere(modulo)``: importa un módulo de desarrollo; si falta, salta la prueba, salvo en CI (variable ``CI``), donde
  FALLA: una dependencia de desarrollo ausente no debe dejar el IFC sin comprobar en silencio.
* ``ArchivoIfcopenshell``: el mismo contrato que ``bridge.ifc_step.ArchivoStep`` sobre ``ifcopenshell.file``, para generar
  el mismo modelo con los dos escritores y compararlos (tests/test_bridge_cad_ifc_equivalencia.py).
"""

from __future__ import annotations

import importlib
import os
from typing import Any

import pytest


def requiere(modulo: str) -> Any:
    try:
        return importlib.import_module(modulo)
    except ImportError:
        if os.environ.get("CI"):
            pytest.fail(
                f"Falta {modulo}: instale backend/requirements-dev.txt (dependencias de desarrollo)",
                pytrace=False,
            )
        pytest.skip(f"{modulo} no está instalado (dependencia de desarrollo)")


class ArchivoIfcopenshell:
    """Adaptador de ``ifcopenshell.file`` a la interfaz de ``ArchivoStep`` (crear_entidad, cabecera, a_texto…)."""

    def __init__(self) -> None:
        self._ifc = requiere("ifcopenshell")
        self.f = self._ifc.file(schema="IFC4")

    def crear_entidad(self, tipo: str, *args: Any, **kw: Any) -> Any:
        return self.f.create_entity(tipo, *args, **kw)

    def cabecera(
        self,
        *,
        descripcion: tuple[str, ...] = ("ViewDefinition [ReferenceView_V1.2]",),
        nombre: str = "",
        marca_tiempo: str | None = None,
        sistema: str = "",
        preprocesador: str = "",
    ) -> None:
        h = self.f.header
        h.file_description.description = tuple(descripcion)
        h.file_name.name = nombre
        h.file_name.time_stamp = marca_tiempo or "2026-01-01T00:00:00"
        h.file_name.originating_system = sistema

    def admite_enum(self, entidad: str, atributo: str, valor: str) -> bool:
        iw = requiere("ifcopenshell.ifcopenshell_wrapper")
        ent = iw.schema_by_name("IFC4").declaration_by_name(entidad)
        tipo = next(a for a in ent.all_attributes() if a.name() == atributo).type_of_attribute()
        return valor in tipo.declared_type().enumeration_items()

    def a_texto(self) -> str:
        return self.f.to_string()
