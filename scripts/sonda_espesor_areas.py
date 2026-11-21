#!/usr/bin/env python3
"""Sonda corta (solo lectura) del espesor de las áreas: ``AreaObj.GetProperty`` y ``PropArea.GetShell_1``/``GetShell``.

Confirma en SAP2000 real lo que la ruta ``shell-forces``/``shell-joint-forces`` supone al leer ``thickness``,
``thickness_bending`` y ``sections`` (bridge/viewer/shell_forces.py::espesores): la forma de las salidas de ``GetShell_1`` (se leen
por tipo: el tipo de shell es el primer entero y ``MatAngle``, ``Thickness`` y ``Bending`` los tres únicos reales), qué firma
funciona y que el espesor sale en las unidades presentes (la ruta fuerza Ton_m_C, así que sale en metros).

Uso (PowerShell, Windows, SAP2000 con un modelo con áreas abierto; no hace falta que esté analizado):

    py -3.12 scripts\\sonda_espesor_areas.py
    py -3.12 scripts\\sonda_espesor_areas.py --esperado 0.4        # espesor conocido de una losa (m)
    py -3.12 scripts\\sonda_espesor_areas.py --max-areas 30 --salida-dir C:\\sondas

Seguridad: solo lee. Cambia las unidades a Ton_m_C y a N_mm_C un momento (para medir el factor) y las restaura SIEMPRE.

Salida: sonda_espesor_areas_AAAAMMDD_HHMM.json y .md. Veredictos: PASS, FAIL, INCONCLUSO, ERROR. Código de salida: 0 sin FAIL, 1 con
algún FAIL, 2 sin conexión.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PASS, FAIL, INCONCLUSO, ERROR = "PASS", "FAIL", "INCONCLUSO", "ERROR"
TON_M_C, N_MM_C = 12, 9  # eUnits


class Informe:
    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.pruebas: list[dict[str, Any]] = []

    def agregar(self, id_: str, titulo: str, veredicto: str, nota: str = "", **datos: Any) -> None:
        self.pruebas.append(
            {"id": id_, "titulo": titulo, "veredicto": veredicto, "nota": nota, "datos": datos}
        )
        print(f"{id_:<4} {veredicto:<10} {titulo}\n       {nota}")

    def a_markdown(self) -> str:
        filas = ["| Id | Veredicto | Prueba | Nota |", "|---|---|---|---|"]
        filas += [f"| {p['id']} | {p['veredicto']} | {p['titulo']} | {p['nota']} |" for p in self.pruebas]
        return "# Sonda del espesor de las áreas\n\n" + "\n".join(filas) + "\n"


def _crudo(valor: Any) -> Any:
    if isinstance(valor, (list, tuple)):
        return [_crudo(v) for v in valor]
    return valor if isinstance(valor, (bool, int, float, str)) or valor is None else repr(valor)


def _unidades(model: Any, codigo: int) -> None:
    model.SetPresentUnits(codigo)


def sondear(model: Any, inf: Informe, esperado: float | None, max_areas: int) -> None:
    from bridge.actions import _get_name_list, split_return
    from bridge.viewer import shell_forces as sf

    areas = _get_name_list(model.AreaObj, "AreaObj.GetNameList")[:max_areas]
    if not areas:
        inf.agregar("E0", "El modelo tiene áreas", INCONCLUSO, "No hay áreas.")
        return
    previa = model.GetPresentUnits()
    try:
        _unidades(model, TON_M_C)
        # E1: AreaObj.GetProperty
        crudos: dict[str, Any] = {}
        secciones: dict[str, str] = {}
        for a in areas:
            try:
                crudos[a] = _crudo(model.AreaObj.GetProperty(a))
            except Exception as exc:  # noqa: BLE001
                crudos[a] = f"error: {exc}"
            try:
                secciones[a] = sf._nombre_seccion(model, a)
            except Exception:  # noqa: BLE001
                continue
        inf.agregar(
            "E1",
            "AreaObj.GetProperty devuelve el nombre de la sección",
            PASS if len(secciones) == len(areas) else FAIL if secciones else INCONCLUSO,
            f"{len(secciones)} de {len(areas)} áreas con sección; forma cruda: {crudos[areas[0]]!r}.",
            crudos=dict(list(crudos.items())[:5]),
        )
        if not secciones:
            return
        # E2: salidas crudas de GetShell_1 / GetShell por sección distinta
        nombres = list(dict.fromkeys(secciones.values()))
        formas: dict[str, Any] = {}
        for nombre in nombres:
            for metodo in ("GetShell_1", "GetShell"):
                f = getattr(model.PropArea, metodo, None)
                if f is None:
                    continue
                for args in ((nombre,), (nombre, 0, False, "", 0.0, 0.0, 0.0, -1, "", "")):
                    try:
                        formas.setdefault(nombre, {})[
                            f"{metodo}{'(corta)' if len(args) == 1 else '(larga)'}"
                        ] = _crudo(f(*args))
                    except Exception as exc:  # noqa: BLE001
                        formas.setdefault(nombre, {})[
                            f"{metodo}{'(corta)' if len(args) == 1 else '(larga)'}"
                        ] = f"error: {exc}"
        inf.agregar(
            "E2",
            "Salidas crudas de PropArea.GetShell_1 y GetShell",
            INCONCLUSO,
            "Informativo: qué firma responde y con qué forma (anote cuál funciona).",
            formas=formas,
        )
        # E3: lo que lee el puente (por tipo) frente a la posición documentada (ShellType, Drilling, MatProp, MatAng, Th, Bend, ...)
        diferencias, leidas = [], {}
        for nombre in nombres:
            try:
                esp, flex = sf._leer_espesor_seccion(model, nombre)
            except Exception as exc:  # noqa: BLE001
                leidas[nombre] = f"{type(exc).__name__}: {exc}"
                continue
            leidas[nombre] = {"thickness": esp, "bending": flex}
            f = getattr(model.PropArea, "GetShell_1", None) or model.PropArea.GetShell
            for args in ((nombre,), (nombre, 0, False, "", 0.0, 0.0, 0.0, -1, "", "")):
                try:
                    raw = f(*args)
                except Exception:  # noqa: BLE001
                    continue
                codigo, salidas = split_return(raw)
                if len(salidas) == 9 and isinstance(salidas[2], str):  # con IncludeDrillingDOF
                    if abs(float(salidas[4]) - esp) > 1e-12 or abs(float(salidas[5]) - flex) > 1e-12:
                        diferencias.append(nombre)
                break
        errores = [n for n, v in leidas.items() if isinstance(v, str)]
        inf.agregar(
            "E3",
            "El puente lee el espesor igual que la posición documentada",
            FAIL if diferencias else INCONCLUSO if errores and len(errores) == len(leidas) else PASS,
            f"{len(leidas) - len(errores)} secciones leídas; con error: {errores or 'ninguna'}; distintas: {diferencias or 'ninguna'}.",
            leidas=leidas,
        )
        # E4: unidades
        factor_txt = ""
        try:
            esp_m = sf._leer_espesor_seccion(model, nombres[0])[0]
            _unidades(model, N_MM_C)
            esp_mm = sf._leer_espesor_seccion(model, nombres[0])[0]
            ok = math.isclose(esp_mm / esp_m, 1000.0, rel_tol=1e-6)
            factor_txt = f"en Ton_m_C {esp_m!r} y en N_mm_C {esp_mm!r} (cociente {esp_mm / esp_m:.6g})."
            inf.agregar(
                "E4",
                "El espesor sigue las unidades presentes (Ton_m_C → metros)",
                PASS if ok else FAIL,
                factor_txt + ("" if ok else " El cociente no es 1000: revise las unidades."),
            )
            if esperado is not None:
                inf.agregar(
                    "E5",
                    f"Espesor esperado {esperado} m",
                    PASS if math.isclose(esp_m, esperado, rel_tol=1e-6) else FAIL,
                    f"El puente lee {esp_m!r} m para la sección '{nombres[0]}' (área {next(iter(secciones))}).",
                )
        except Exception as exc:  # noqa: BLE001
            inf.agregar("E4", "Unidades del espesor", ERROR, f"{type(exc).__name__}: {exc}")
        finally:
            _unidades(model, TON_M_C)
        # E6: la ruta completa
        from bridge.viewer.shell_forces import limpiar_cache_ejes, shell_forces

        limpiar_cache_ejes()
        try:
            r = shell_forces(
                model,
                {
                    "areas": areas[:3],
                    "components": ["M11"],
                    "include_axes": False,
                    "cases": [],
                    "max_elements": 50,
                },
            )
            inf.agregar(
                "E6",
                "shell-forces devuelve thickness y sections",
                PASS if r.get("thickness") and r.get("sections") else INCONCLUSO,
                f"thickness={dict(list(r.get('thickness', {}).items())[:3])}, sections={dict(list(r.get('sections', {}).items())[:3])}, "
                f"thickness_bending={r.get('thickness_bending', {})}; avisos de espesor: "
                f"{[w for w in r['warnings'] if 'espesor' in w] or 'ninguno'}.",
            )
        except Exception as exc:  # noqa: BLE001
            inf.agregar(
                "E6",
                "shell-forces con espesor",
                INCONCLUSO,
                f"{type(exc).__name__}: {exc} (la ruta necesita el modelo analizado).",
            )
    finally:
        model.SetPresentUnits(previa)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esperado", type=float, help="espesor conocido (m) de la primera sección")
    ap.add_argument("--max-areas", type=int, default=20)
    ap.add_argument("--salida-dir", default=".")
    args = ap.parse_args(argv)
    os.environ.setdefault("SAP2000_AGENT_TOKEN", secrets.token_hex(16))
    try:
        from bridge import sap2000_agent

        sesion = sap2000_agent.session
        sesion.attach(None)
    except ImportError as exc:
        print(
            f"Faltan dependencias del puente ({exc}): py -3.12 -m pip install -r backend\\requirements.txt pywin32 comtypes"
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo conectar con SAP2000: {exc}")
        return 2
    inf = Informe()

    def correr(model: Any) -> None:
        antes = model.GetPresentUnits()
        inf.meta["modelo"] = str(model.GetModelFilename() or "")
        try:
            sondear(model, inf, args.esperado, args.max_areas)
        except Exception as exc:  # noqa: BLE001
            inf.agregar("X", "Sonda", ERROR, f"{type(exc).__name__}: {exc}")
        despues = model.GetPresentUnits()
        inf.agregar(
            "Z",
            "Unidades restauradas",
            PASS if despues == antes else FAIL,
            f"antes={antes} después={despues}",
        )

    sesion.actor.call(lambda: correr(sesion._model()))
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    salida = Path(args.salida_dir)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"sonda_espesor_areas_{sello}.json").write_text(
        json.dumps({"meta": inf.meta, "pruebas": inf.pruebas}, indent=2, default=str), encoding="utf-8"
    )
    (salida / f"sonda_espesor_areas_{sello}.md").write_text(inf.a_markdown(), encoding="utf-8")
    resumen: dict[str, int] = {}
    for p in inf.pruebas:
        resumen[p["veredicto"]] = resumen.get(p["veredicto"], 0) + 1
    print(f"\nResumen: {resumen}\nInforme: {salida / f'sonda_espesor_areas_{sello}.json'}")
    return 1 if resumen.get(FAIL) else 0


if __name__ == "__main__":
    raise SystemExit(main())
