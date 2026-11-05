"""El puente no debe depender de los motores de cálculo del Workbench (backend/motor_calculo).

El puente se publica como repositorio independiente y los motores no viajan con él: su trabajo es leer
SAP2000 y devolver datos crudos; la conversión y el cálculo viven en el Workbench. Esta prueba recorre con
``ast`` los archivos del puente y falla ante cualquier import de ``backend.motor_calculo``.

``EXCEPCIONES`` lista lo que todavía los importa y se va vaciando paso a paso (ver el plan del puente sin
motores). Si un archivo de la lista ya no importa el motor, la prueba falla también: hay que quitarlo de la
lista, para que no queden permisos olvidados.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PREFIJO = "backend.motor_calculo"

# archivo (relativo a la raíz) -> motivo y paso del plan que lo elimina
EXCEPCIONES: dict[str, str] = {}


def _archivos_del_puente() -> list[Path]:
    archivos = sorted((RAIZ / "bridge").rglob("*.py"))
    archivos += [RAIZ / "run_sap_bridge.py", RAIZ / "runtime_paths.py"]
    return [a for a in archivos if a.exists()]


def _importa_motor(archivo: Path) -> list[str]:
    arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
    encontrados: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            encontrados += [a.name for a in nodo.names if a.name.startswith(PREFIJO)]
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            if nodo.module.startswith(PREFIJO):
                encontrados.append(nodo.module)
        # importaciones dinámicas con el nombre del módulo escrito en el código
        elif isinstance(nodo, ast.Call) and getattr(nodo.func, "attr", getattr(nodo.func, "id", "")) in (
            "import_module",
            "__import__",
        ):
            for arg in nodo.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith(PREFIJO)
                ):
                    encontrados.append(arg.value)
    return encontrados


def test_el_puente_no_importa_motores_salvo_las_excepciones_listadas():
    con_motor = {
        str(a.relative_to(RAIZ)).replace("\\", "/"): mods
        for a in _archivos_del_puente()
        if (mods := _importa_motor(a))
    }
    nuevos = {f: m for f, m in con_motor.items() if f not in EXCEPCIONES}
    assert not nuevos, f"El puente importa motores de cálculo (no deben viajar con él): {nuevos}"
    obsoletas = sorted(set(EXCEPCIONES) - set(con_motor))
    assert not obsoletas, f"Ya no importan el motor; quítalos de EXCEPCIONES: {obsoletas}"


def test_el_escaner_detecta_un_import_de_motor(tmp_path):
    ejemplo = tmp_path / "x.py"
    ejemplo.write_text("from backend.motor_calculo.cad_drwr.core import ir\n", encoding="utf-8")
    assert _importa_motor(ejemplo) == ["backend.motor_calculo.cad_drwr.core"]
    ejemplo.write_text(
        "import importlib\nimportlib.import_module('backend.motor_calculo.x')\n", encoding="utf-8"
    )
    assert _importa_motor(ejemplo) == ["backend.motor_calculo.x"]
    ejemplo.write_text('"""menciona backend.motor_calculo solo en texto"""\n', encoding="utf-8")
    assert _importa_motor(ejemplo) == []
