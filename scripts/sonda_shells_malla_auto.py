#!/usr/bin/env python3
"""Sonda de shells (solo lectura): ejes locales en malla automática, signo de V13/V23 y equivalencia API/tabla.

Completa la sonda fase 0 de shells (docs/SONDA_FASE0_SHELL.md del Workbench, SAP2000 v23.3.1) con las pruebas que quedaron
pendientes y con la nueva ruta ``shell-joint-forces``:

* **A. Ejes locales en malla automática.** Con la losa L2 convertida con Assign > Area > Automatic Area Mesh: ¿los
  elementos de la malla automática heredan los ejes del objeto (ángulo de 30°)? ¿Los nudos internos ``~n`` no son
  ``PointObj``? ¿La interpretación por columnas de ``GetTransformationMatrix`` sigue validando? ¿Qué ejes entrega el puente?
* **B. Signo de V13/V23 entre L1 y L3** (L3 = L1 desplazada 10 m en Y con la normal invertida): hipótesis «V13 igual, V23
  opuesto, M11 opuesto». Mide los cocientes elemento a elemento y compara los totales de ``AreaJointForceShell`` en ejes
  globales de las tres losas (si la conversión a globales es correcta, ΣF3 es igual en L1, L2 y L3).
* **C. API frente a tabla** con el modelo real: ``Results.AreaForceShell("ALL")`` y la tabla «Element Forces - Area Shells»
  deben dar los mismos valores.

Uso (PowerShell, Windows, SAP2000 con el modelo ANALIZADO —bloqueado— abierto):

    py -3.12 scripts\\sonda_shells_malla_auto.py --l2 L2
    py -3.12 scripts\\sonda_shells_malla_auto.py --l2 L2 --caso Q --caso-p P --salida-dir C:\\sondas

``--l1`` y ``--l3`` (por defecto las áreas 1–96 y 2001–2096 del modelo de la sonda) y ``--l2`` (nombre del área que se
convirtió a malla automática; si se dejó el rango 1001–1096, no pasa nada: se analiza igual pero la prueba A queda como
INCONCLUSA). Instrucciones y qué anotar: docs/SONDA_SHELLS_MALLA_AUTO.md.

Seguridad: solo lee. Las lecturas de resultados marcan casos para salida y cambian las unidades a Ton_m_C, pero el puente
restaura ambas siempre; el script comprueba al final que unidades, bloqueo y selección de salida son los de antes.

Salida: sonda_shells_malla_auto_AAAAMMDD_HHMM.json (informe completo) y .md (veredictos). Devuélvame el .json.
Veredictos: PASS, FAIL (la hipótesis no se cumple; la nota dice qué mirar), INCONCLUSO y ERROR. Código de salida: 0 sin FAIL,
1 con algún FAIL, 2 sin conexión.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PASS, FAIL, INCONCLUSO, ERROR = "PASS", "FAIL", "INCONCLUSO", "ERROR"
TOL_ANGULO = 1e-3  # rad
TOL_VALOR = 1e-9
COMPONENTES_V = ["M11", "M22", "V13", "V23"]


def _rango(texto: str) -> list[str]:
    """``'1-96'`` → ['1', …, '96']; ``'A,B'`` → ['A', 'B']."""
    nombres: list[str] = []
    for parte in (p.strip() for p in texto.split(",") if p.strip()):
        if "-" in parte and all(x.strip().isdigit() for x in parte.split("-", 1)):
            a, b = (int(x) for x in parte.split("-", 1))
            nombres.extend(str(i) for i in range(a, b + 1))
        else:
            nombres.append(parte)
    return nombres


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
        lineas = [
            "# Sonda de shells: malla automática, signo de V y API/tabla",
            "",
            f"Modelo: {self.meta.get('modelo')}",
            "",
        ]
        lineas += ["| Id | Veredicto | Prueba | Nota |", "|---|---|---|---|"]
        for p in self.pruebas:
            lineas.append(f"| {p['id']} | {p['veredicto']} | {p['titulo']} | {p['nota']} |")
        return "\n".join(lineas) + "\n"


def _angulo_entre(a: list[float], b: list[float]) -> float:
    p = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True))))
    return math.acos(p)


# ------------------------------------------------------------------------------------------------------------------------
# Pruebas (se ejecutan dentro del hilo COM del agente: ``model`` es el SapModel)
# ------------------------------------------------------------------------------------------------------------------------


def prueba_a(model: Any, inf: Informe, l2: list[str]) -> None:
    from bridge.viewer import shell_forces as sf

    areas_existentes = set(sf._get_name_list(model.AreaObj, "AreaObj.GetNameList"))
    l2 = [a for a in l2 if a in areas_existentes]
    if not l2:
        inf.agregar("A0", "Áreas de L2", INCONCLUSO, "Ninguna de las áreas de --l2 existe en el modelo.")
        return
    elementos_por_area: dict[str, list[str]] = {}
    for a in l2:
        elementos_por_area[a] = sf._nombres_de(model.AreaObj.GetElm(a))
    n_elm = sum(len(v) for v in elementos_por_area.values())
    malla_auto = n_elm > len(l2)
    inf.agregar(
        "A0",
        "L2 está mallada automáticamente",
        PASS if malla_auto else INCONCLUSO,
        f"{len(l2)} área(s) con {n_elm} elemento(s) de análisis"
        + (
            ""
            if malla_auto
            else ": un elemento por área, es malla explícita; convierta L2 con Automatic Area Mesh."
        ),
        elementos_por_area={a: len(v) for a, v in elementos_por_area.items()},
    )
    # Ejes del objeto y de cada elemento
    estados: dict[str, Any] = {}
    desviaciones, validas, total = [], 0, 0
    desv_regla: list[float] = []
    nudos_internos: set[str] = set()
    for a, elms in elementos_por_area.items():
        angulo, avanzado = sf._angulo_y_avanzado(model, a)
        m_obj = sf._matriz_objeto(model, a)
        estados[a] = {"angulo_objeto": angulo, "avanzado": avanzado, "matriz_objeto": m_obj}
        for elm in elms:
            total += 1
            nodos = sf._nombres_de(model.AreaElm.GetPoints(elm))
            nudos_internos.update(n for n in nodos if n.startswith("~"))
            coords = {n: sf._coord_punto_elemento(model, n) for n in nodos}
            if any(c is None for c in coords.values()) or len(nodos) < 3:
                continue
            n_geo = sf._normal_geometrica(nodos, {n: list(c) for n, c in coords.items()})  # type: ignore[arg-type]
            m_elm = sf._matriz_elemento(model, elm)
            res = sf._ejes_desde_matriz(m_elm, n_geo) if m_elm is not None and n_geo is not None else None
            if res is None:
                continue
            validas += 1
            v1 = list(sf._unitario(res[0]) or res[0])
            if m_obj is not None and n_geo is not None:
                obj = sf._ejes_desde_matriz(m_obj, n_geo)
                if obj is not None:
                    desviaciones.append(_angulo_entre(v1, list(sf._unitario(obj[0]) or obj[0])))
            if n_geo is not None:
                regla = sf._ejes_por_regla(n_geo, angulo)[0]
                desv_regla.append(_angulo_entre(v1, list(sf._unitario(regla) or regla)))
    inf.agregar(
        "A1",
        "Interpretación por columnas válida en los elementos",
        PASS if total and validas == total else FAIL if total else INCONCLUSO,
        f"{validas} de {total} elementos con matriz válida por columnas (det +1, v3·n = ±1).",
    )
    inf.agregar(
        "A2",
        "Nudos internos de la malla automática no son PointObj",
        PASS if nudos_internos else INCONCLUSO,
        f"{len(nudos_internos)} nudos internos '~…'."
        if nudos_internos
        else "No hay nudos '~…': ¿la malla es explícita?",
        ejemplo=sorted(nudos_internos)[:5],
    )
    if desviaciones:
        mx = max(desviaciones)
        inf.agregar(
            "A3",
            "Los elementos heredan los ejes del objeto (v1 de AreaElm = v1 de AreaObj)",
            PASS if mx < TOL_ANGULO else FAIL,
            f"Desviación máxima entre el v1 del elemento y el del objeto: {math.degrees(mx):.4f}°."
            + (
                ""
                if mx < TOL_ANGULO
                else " Los elementos NO heredan los ejes del objeto: el puente debe leerlos por elemento."
            ),
            desviacion_max_grados=math.degrees(mx),
            objetos=estados,
        )
    else:
        inf.agregar(
            "A3",
            "Herencia de ejes",
            INCONCLUSO,
            "AreaObj.GetTransformationMatrix no respondió o no validó.",
            objetos=estados,
        )
    if desv_regla:
        mx = max(desv_regla)
        inf.agregar(
            "A4",
            "La regla por defecto (+ ángulo del objeto) reproduce los ejes de los elementos",
            PASS if mx < TOL_ANGULO else FAIL,
            f"Desviación máxima: {math.degrees(mx):.4f}° (es lo que daría el respaldo del puente si no hubiera matriz).",
        )
    # Lo que entrega el puente
    from bridge.viewer.shell_forces import limpiar_cache_ejes, shell_forces

    limpiar_cache_ejes()
    t0 = time.perf_counter()
    r = shell_forces(
        model, {"areas": l2, "include_axes": True, "components": ["M11"], "cases": [], "max_elements": 200000}
    )
    dt = time.perf_counter() - t0
    avisos_ejes = [w for w in r["warnings"] if "Ejes" in w or "ejes" in w]
    inf.agregar(
        "A5",
        "El puente devuelve ejes para todos los elementos de L2",
        PASS if set(r["axes"]) == set(r["elements"]) else FAIL,
        f"{len(r['axes'])} de {len(r['elements'])} elementos con ejes en {dt:.2f} s. Avisos de ejes: {avisos_ejes or 'ninguno'}.",
        warnings=r["warnings"],
    )


def prueba_c(model: Any, inf: Informe, areas: list[str], caso: str) -> None:
    from bridge.viewer import shell_forces as sf

    cuerpo = {
        "areas": areas,
        "cases": [caso],
        "components": list(sf.COMPONENTES),
        "include_axes": False,
        "max_elements": 200000,
    }
    t0 = time.perf_counter()
    con_api = sf.shell_forces(model, cuerpo)
    t_api = time.perf_counter() - t0
    original = sf._llamar_area_force
    sf._llamar_area_force = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("API desactivada por la sonda")
    )  # type: ignore[assignment]
    try:
        t0 = time.perf_counter()
        con_tabla = sf.shell_forces(model, cuerpo)
        t_tabla = time.perf_counter() - t0
    finally:
        sf._llamar_area_force = original
    ra = {tuple(f[:4]): f[4:] for f in con_api["rows"]}
    rt = {tuple(f[:4]): f[4:] for f in con_tabla["rows"]}
    solo_a, solo_t = set(ra) - set(rt), set(rt) - set(ra)
    diff = 0.0
    for k in set(ra) & set(rt):
        for x, y in zip(ra[k], rt[k], strict=True):
            if x is None or y is None:
                continue
            diff = max(diff, abs(x - y))
    ok = not solo_a and not solo_t and diff < TOL_VALOR + 1e-9 * max(1.0, diff)
    inf.agregar(
        "C1",
        f"API y tabla dan los mismos valores (caso {caso})",
        PASS if ok and ra else FAIL if ra else INCONCLUSO,
        f"{len(ra)} filas por API ({t_api:.2f} s) y {len(rt)} por tabla ({t_tabla:.2f} s); diferencia máxima {diff:.3e}; "
        f"solo API: {len(solo_a)}, solo tabla: {len(solo_t)}.",
        diferencia_max=diff,
        warnings_tabla=con_tabla["warnings"],
    )


def prueba_b(
    model: Any, inf: Informe, l1: list[str], l2: list[str], l3: list[str], caso: str, desplaza_l3: int
) -> None:
    from bridge.viewer.shell_forces import limpiar_cache_ejes, shell_forces
    from bridge.viewer.shell_joint_forces import shell_joint_forces

    # B1: signo de V13/V23/M11 entre L1 y L3, elemento a elemento (L3 = L1 desplazada, con otra numeración).
    cuerpo = {
        "areas": [*l1, *l3],
        "cases": [caso],
        "components": COMPONENTES_V,
        "include_axes": True,
        "max_elements": 200000,
    }
    r = shell_forces(model, cuerpo)
    columnas = r["columns"]
    idx = {c: columnas.index(c) for c in COMPONENTES_V}
    por_clave = {(f[0], f[1], f[3]): f for f in r["rows"]}
    elementos_l3 = {e for e, d in r["elements"].items() if d["area"] in l3}
    conteo = {c: {"igual": 0, "opuesto": 0, "nulo": 0} for c in COMPONENTES_V}
    pares = 0
    for (elm, nodo, paso), fila in por_clave.items():
        if elm not in elementos_l3 or not (elm.isdigit() and nodo.isdigit()):
            continue
        # L3 es L1 desplazada: el elemento y el nudo equivalentes llevan la numeración desplazada.
        f1 = por_clave.get((str(int(elm) - desplaza_l3), str(int(nodo) - desplaza_l3), paso))
        if f1 is None:
            continue
        pares += 1
        for c in COMPONENTES_V:
            a, b = f1[idx[c]], fila[idx[c]]
            if a is None or b is None or abs(a) < 1e-6 or abs(b) < 1e-6:
                conteo[c]["nulo"] += 1
            elif (a > 0) == (b > 0):
                conteo[c]["igual"] += 1
            else:
                conteo[c]["opuesto"] += 1

    def dominante(c: str) -> str:
        d = conteo[c]
        if d["igual"] + d["opuesto"] == 0:
            return "sin datos"
        return "igual" if d["igual"] >= d["opuesto"] else "opuesto"

    def pureza(c: str) -> float:
        d = conteo[c]
        t = d["igual"] + d["opuesto"]
        return max(d["igual"], d["opuesto"]) / t if t else 0.0

    esperado = {"V13": "igual", "V23": "opuesto", "M11": "opuesto"}
    for c, esp in esperado.items():
        veredicto = (
            INCONCLUSO
            if dominante(c) == "sin datos"
            else PASS
            if dominante(c) == esp and pureza(c) > 0.99
            else FAIL
        )
        inf.agregar(
            f"B1{c[0]}{c[1:]}",
            f"Signo de {c} en L3 respecto a L1 bajo el caso {caso} (esperado: {esp})",
            veredicto,
            f"{pares} pares elemento-esquina: {conteo[c]['igual']} igual, {conteo[c]['opuesto']} opuesto, "
            f"{conteo[c]['nulo']} nulos; dominante: {dominante(c)}.",
            conteo=conteo[c],
        )
    # B2: totales de AreaJointForceShell en globales; la conversión es correcta si ΣF3 coincide en las tres losas.
    totales: dict[str, Any] = {}
    for nombre, areas in (("L1", l1), ("L2", l2), ("L3", l3)):
        limpiar_cache_ejes()
        try:
            g = shell_joint_forces(
                model, {"areas": areas, "cases": [caso], "frame": "global", "max_elements": 200000}
            )
            e = shell_joint_forces(
                model, {"areas": areas, "cases": [caso], "frame": "element", "max_elements": 200000}
            )
        except Exception as exc:  # noqa: BLE001
            totales[nombre] = {"error": str(exc)}
            continue
        sumas = lambda rr: [sum(f[4 + i] or 0.0 for f in rr["rows"]) for i in range(6)]  # noqa: E731
        totales[nombre] = {"global": sumas(g), "elemento": sumas(e), "warnings": g["warnings"]}
    ok_losas = [n for n in ("L1", "L2", "L3") if "global" in totales.get(n, {})]
    if len(ok_losas) == 3:
        f3 = [totales[n]["global"][2] for n in ok_losas]
        iguales = max(f3) - min(f3) < 1e-3 * max(1.0, max(abs(x) for x in f3))
        inf.agregar(
            "B2",
            f"ΣF3 de AreaJointForceShell en ejes globales coincide en L1, L2 y L3 (caso {caso})",
            PASS if iguales else FAIL,
            f"ΣF3 global: {dict(zip(ok_losas, f3, strict=True))}. "
            + (
                ""
                if iguales
                else "No coinciden: la conversión con los ejes del elemento no es la correcta (compare con 'elemento')."
            ),
            totales=totales,
        )
    else:
        inf.agregar(
            "B2",
            "Totales de AreaJointForceShell",
            INCONCLUSO,
            "No se pudieron leer las tres losas.",
            totales=totales,
        )


# ------------------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--l1", default="1-96")
    ap.add_argument("--l2", default="1001-1096")
    ap.add_argument("--l3", default="2001-2096")
    ap.add_argument(
        "--desplaza-l3", type=int, default=2000, help="diferencia de numeración entre L3 y L1 (2000)"
    )
    ap.add_argument("--caso", default="Q")
    ap.add_argument("--caso-p", default="P")
    ap.add_argument("--salida-dir", default=".")
    args = ap.parse_args(argv)
    l1, l2, l3 = _rango(args.l1), _rango(args.l2), _rango(args.l3)

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

    def estado(model: Any) -> dict[str, Any]:
        from bridge.seleccion_salida import _capture_selection

        return {
            "unidades": model.GetPresentUnits(),
            "bloqueado": model.GetModelIsLocked(),
            "seleccion": _capture_selection(model),
            "modelo": str(model.GetModelFilename() or ""),
        }

    def correr(model: Any) -> None:
        antes = estado(model)
        inf.meta["modelo"] = antes["modelo"]
        inf.meta["antes"] = antes
        if not antes["bloqueado"]:
            inf.agregar(
                "0",
                "Modelo analizado",
                FAIL,
                "El modelo no está bloqueado: corra el análisis y vuelva a ejecutar.",
            )
            return
        for id_, f in (
            ("A", lambda: prueba_a(model, inf, l2)),
            ("C", lambda: prueba_c(model, inf, [*l1, *l3], args.caso)),
            ("B", lambda: prueba_b(model, inf, l1, l2, l3, args.caso, args.desplaza_l3)),
            ("B(P)", lambda: prueba_b(model, inf, l1, l2, l3, args.caso_p, args.desplaza_l3)),
        ):
            try:
                f()
            except Exception as exc:  # noqa: BLE001
                inf.agregar(id_, f"Prueba {id_}", ERROR, f"{type(exc).__name__}: {exc}")
        despues = estado(model)
        inf.meta["despues"] = despues
        igual = despues == antes
        inf.agregar(
            "Z",
            "Unidades, bloqueo y selección de salida restaurados",
            PASS if igual else FAIL,
            "Iguales a los de antes." if igual else f"Cambiaron: antes={antes} después={despues}",
        )

    sesion.actor.call(lambda: correr(sesion._model()))
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    salida = Path(args.salida_dir)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"sonda_shells_malla_auto_{sello}.json").write_text(
        json.dumps({"meta": inf.meta, "pruebas": inf.pruebas}, indent=2, default=str), encoding="utf-8"
    )
    (salida / f"sonda_shells_malla_auto_{sello}.md").write_text(inf.a_markdown(), encoding="utf-8")
    resumen: dict[str, int] = {}
    for p in inf.pruebas:
        resumen[p["veredicto"]] = resumen.get(p["veredicto"], 0) + 1
    print(f"\nResumen: {resumen}\nInforme: {salida / f'sonda_shells_malla_auto_{sello}.json'}")
    return 1 if resumen.get(FAIL) else 0


if __name__ == "__main__":
    raise SystemExit(main())
