#!/usr/bin/env python3
"""Regenera backend/report_builder/generators/catalogo.json desde discover_generators().

Uso: python scripts/generar_catalogo_generadores.py

Hay que correrlo tras añadir o quitar un generador, o cambiar su schema o su nombre
visible. Importa los generadores para descubrirlos, así que necesita que carguen; si
alguno falla, no escribe nada. Luego corra backend/report_builder/tests/test_catalogo_generadores.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend.report_builder.engine.generator_registry import (  # noqa: E402
    CATALOGO_PATH,
    discover_generators_with_errors,
)


def main() -> int:
    infos, errores = discover_generators_with_errors()
    if errores:
        print("No se regenera el catálogo: hay generadores que no cargan:", file=sys.stderr)
        for error in errores:
            print(f"  - {error}", file=sys.stderr)
        return 1
    catalogo = [
        {
            "module_id": info.module_id,
            "import_path": info.import_path,
            "schema_path": info.schema_path,
            "display_name": info.display_name,
        }
        for info in infos
    ]
    CATALOGO_PATH.write_text(json.dumps(catalogo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catálogo escrito: {CATALOGO_PATH} ({len(catalogo)} generadores)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
