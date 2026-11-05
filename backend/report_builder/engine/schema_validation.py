"""
Validacion liviana de un JSON fuente contra el contrato declarado por su
modulo (generators/schemas/<modulo>.vN.schema.json).

No es una implementacion completa de JSON Schema (evita agregar una
dependencia nueva solo para esto) - soporta el subconjunto que los contratos
de esta suite realmente usan: "type" (object/array/string/number/integer/
boolean/null, como valor unico o como lista de tipos permitidos, p.ej.
["string", "null"]), "required" y "properties" para objetos, "items" para
arrays. Es suficiente para convertir un cambio de formato en el modulo
fuente en un error explicito en vez de un traceback críptico dentro del
generador.
"""

from __future__ import annotations

import json
from pathlib import Path

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaValidationError(Exception):
    """El JSON fuente no cumple el contrato declarado por el modulo."""

    def __init__(self, errors: list):
        self.errors = errors
        super().__init__("; ".join(errors))


def _validate_node(value, schema: dict, path: str, errors: list) -> None:
    expected_type = schema.get("type")
    expected_types = (
        expected_type
        if isinstance(expected_type, list)
        else ([expected_type] if expected_type is not None else [])
    )
    if expected_types:
        checks = [_TYPE_CHECKS[t] for t in expected_types if t in _TYPE_CHECKS]
        if checks and not any(check(value) for check in checks):
            errors.append(f"{path}: se esperaba tipo {expected_type!r}, se encontro {type(value).__name__!r}")
            return

    if "object" in expected_types and isinstance(value, dict):
        for required_key in schema.get("required", []):
            if required_key not in value:
                errors.append(f"{path}: falta la clave requerida {required_key!r}")

        properties = schema.get("properties", {})
        for key, sub_schema in properties.items():
            if key in value:
                _validate_node(value[key], sub_schema, f"{path}.{key}", errors)

    elif "array" in expected_types and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(value):
                _validate_node(item, item_schema, f"{path}[{i}]", errors)


def validate_against_schema(data: dict, schema: dict) -> list:
    """Devuelve una lista de mensajes de error (vacia si el JSON es valido)."""
    errors: list = []
    _validate_node(data, schema, "$", errors)
    return errors


def load_and_validate(json_path: str | Path, schema_path: str | Path) -> dict:
    """Carga un JSON fuente y lo valida contra su schema. Lanza SchemaValidationError si falla."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    errors = validate_against_schema(data, schema)
    if errors:
        raise SchemaValidationError(errors)

    return data
