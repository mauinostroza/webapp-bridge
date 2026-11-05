#!/usr/bin/env python3
"""Sonda de SOLO LECTURA para `GET /v1/sap/actions/viewer/sections/props` (bridge/viewer/core/section_props.py).

Comprueba en TU SAP2000 las llamadas de la API que usa la ruta y que hoy solo están probadas con COM
falso: `PropFrame.GetTypeOAPI`, los getters tipados (`GetISection`, `GetChannel`, `GetTee`, `GetAngle`,
`GetDblAngle`, `GetTube`, `GetPipe`, `GetRectangle`, `GetCircle`, `GetGeneral`), `GetSectProps` y, para los
materiales, `PropMaterial.GetTypeOAPI`, `GetOSteel_1` y `GetMPIsotropic`. Después ejecuta la lectura completa de la
ruta y verifica que lo devuelto es coherente (área contra dimensiones, ejes fuerte y débil, radios de giro).

Uso (PowerShell, Windows, SAP2000 abierto con un modelo que tenga secciones de barra):

    py -3.12 scripts\\sonda_viewer_secciones.py
    py -3.12 scripts\\sonda_viewer_secciones.py --salida-dir C:\\sondas
    py -3.12 scripts\\sonda_viewer_secciones.py --comparar-unidades

Seguridad:
* No escribe nada en el modelo, no lo bloquea ni lo desbloquea y no cambia la selección.
* Las firmas de los getters que el modelo no puede ejercitar (p. ej. un ángulo doble que no existe) se
  comprueban llamando con un nombre inexistente: si la firma es válida, SAP2000 responde con un código de
  error y no con una excepción de argumentos. Eso confirma la firma, no la disposición de las salidas.
* `--comparar-unidades` es OPCIONAL: cambia las unidades activas a N_mm_C y las restaura SIEMPRE en un
  `finally`. Sirve para contrastar los factores de conversión de la ruta contra los valores que entrega SAP2000
  ya en mm y MPa. Sin esa opción no se toca nada.

Salida: sonda_viewer_secciones_AAAAMMDD_HHMM.json (informe completo) y .md (tabla de veredictos).
Veredictos: PASS, FAIL (la hipótesis no se cumple; la nota dice qué mirar) e INCONCLUSO (el modelo no permite
decidirlo). Código de salida: 0 sin FAIL, 1 con algún FAIL, 2 si no se pudo conectar.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PASS, FAIL, INCONCLUSO = "PASS", "FAIL", "INCONCLUSO"
EU_N_MM = 9  # eUnits.N_mm_C
LIMITE_CRUDO = 400

# Tipo -> (getter, argumentos de relleno, número de salidas). Los mismos de section_props.py.
GETTERS = {
    1: ("GetISection", ("", "", 0, 0, 0, 0, 0, 0, 0, "", ""), 11),
    2: ("GetChannel", ("", "", 0, 0, 0, 0, 0, "", ""), 9),
    3: ("GetTee", ("", "", 0, 0, 0, 0, 0, "", ""), 9),
    4: ("GetAngle", ("", "", 0, 0, 0, 0, 0, "", ""), 9),
    5: ("GetDblAngle", ("", "", 0, 0, 0, 0, 0, 0, "", ""), 10),
    6: ("GetTube", ("", "", 0, 0, 0, 0, 0, "", ""), 9),
    7: ("GetPipe", ("", "", 0, 0, 0, "", ""), 7),
    8: ("GetRectangle", ("", "", 0, 0, 0, "", ""), 7),
    9: ("GetCircle", ("", "", 0, 0, "", ""), 6),
    10: ("GetGeneral", ("", "") + (0,) * 15 + ("", ""), 19),
}
TIPOS = {
    1: "I_H",
    2: "Channel",
    3: "Tee",
    4: "Angle",
    5: "Double_Angle",
    6: "Box_HSS",
    7: "Pipe_HSS_Round",
    8: "Rectangular",
    9: "Circle",
    10: "General",
    11: "Double_Channel",
}
NOMBRE_FALSO = "ZZS_NO_EXISTE"


def _recortar(valor: Any) -> str:
    texto = repr(valor)
    return texto if len(texto) <= LIMITE_CRUDO else texto[:LIMITE_CRUDO] + "…"


class Informe:
    def __init__(self) -> None:
        self.filas: list[dict[str, Any]] = []

    def agregar(self, id_: str, veredicto: str, nota: str, crudo: Any = None) -> None:
        self.filas.append(
            {
                "id": id_,
                "veredicto": veredicto,
                "nota": nota,
                "crudo": _recortar(crudo) if crudo is not None else None,
            }
        )

    def resumen(self) -> dict[str, int]:
        return {v: sum(1 for f in self.filas if f["veredicto"] == v) for v in (PASS, FAIL, INCONCLUSO)}


def llamar(fn: Callable[..., Any], *args: Any) -> tuple[Any, str | None]:
    """``(resultado, None)`` o ``(None, mensaje)`` si la llamada lanza una excepción."""
    try:
        return fn(*args), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def separar(raw: Any, n_salidas: int) -> tuple[int | None, list[Any]] | None:
    """``(código de retorno, salidas)`` aceptando el código al inicio o al final; ``None`` si la forma no encaja."""
    if not isinstance(raw, (tuple, list)) or len(raw) != n_salidas + 1:
        return None
    from bridge.actions import split_return

    try:
        codigo, salidas = split_return(raw)
    except Exception:  # noqa: BLE001
        return None
    return (int(codigo), list(salidas)) if len(salidas) == n_salidas else None


def _cerca(a: float, b: float, tol: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= tol


def area_desde_dimensiones(tipo: int, d: dict[str, float]) -> float | None:
    """Área aproximada (sin filetes) a partir de h/b/tf/tw, para contrastar con `GetSectProps`."""
    h, b, tf, tw = d.get("h"), d.get("b"), d.get("tf"), d.get("tw")
    if tipo == 1 and h and b and tf and tw:
        return 2 * b * tf + (h - 2 * tf) * tw
    if tipo == 2 and h and b and tf and tw:
        return 2 * b * tf + (h - 2 * tf) * tw
    if tipo == 6 and h and b and tf and tw:
        return 2 * (b - 2 * tw) * tf + 2 * h * tw if tf != tw else 2 * (h + b - 2 * tf) * tf
    if tipo == 7 and h and tw:
        return math.pi * (h - tw) * tw
    if tipo == 8 and h and b:
        return h * b
    if tipo == 9 and h:
        return math.pi * h**2 / 4
    return None


# --- Sondas --------------------------------------------------------------------------------------


def sonda_unidades(model: Any, inf: Informe) -> tuple[float, float] | None:
    from bridge.viewer.core.section_props import SeccionesError, _factores

    codigo, error = llamar(model.GetPresentUnits)
    if error:
        inf.agregar("U1 GetPresentUnits", FAIL, error)
        return None
    try:
        factores = _factores(model)
    except SeccionesError as exc:
        inf.agregar("U1 GetPresentUnits", FAIL, f"código {codigo!r}: {exc}", codigo)
        return None
    inf.agregar(
        "U1 GetPresentUnits",
        PASS,
        f"código {codigo!r}: {factores[0]} mm por unidad de longitud, {factores[1]} N por unidad de fuerza",
        codigo,
    )
    return factores


def sonda_firmas(model: Any, inf: Informe) -> None:
    """Cada getter se llama con un nombre inexistente: una firma válida responde con código de error, no con excepción."""
    for metodo, relleno, n in GETTERS.values():
        getter = getattr(model.PropFrame, metodo, None)
        if getter is None:
            inf.agregar(f"S1 {metodo}", FAIL, "PropFrame no expone este método")
            continue
        raw, error = llamar(getter, NOMBRE_FALSO, *relleno)
        if error and any(t in error.lower() for t in ("typeerror", "argument", "param")):
            inf.agregar(f"S1 {metodo}", FAIL, f"firma rechazada con {1 + len(relleno)} argumentos: {error}")
        elif error:
            inf.agregar(f"S1 {metodo}", PASS, f"firma aceptada (lanza al no existir la sección): {error}")
        else:
            partes = separar(raw, n)
            if partes is None:
                inf.agregar(f"S1 {metodo}", FAIL, f"se esperaban {n + 1} elementos y llegó otra forma", raw)
            else:
                inf.agregar(
                    f"S1 {metodo}",
                    PASS,
                    f"firma aceptada; devuelve código {partes[0]} para un nombre inexistente",
                    raw,
                )
    raw, error = llamar(model.PropMaterial.GetTypeOAPI, NOMBRE_FALSO, 0, 0)
    inf.agregar(
        "S2 PropMaterial.GetTypeOAPI",
        FAIL if error and "TypeError" in error else PASS,
        error or "firma aceptada",
        raw,
    )
    raw, error = llamar(model.PropMaterial.GetOSteel_1, NOMBRE_FALSO, *((0.0,) * 4 + (0, 0) + (0.0,) * 4))
    inf.agregar(
        "S2 PropMaterial.GetOSteel_1",
        FAIL if error and "TypeError" in error else PASS,
        error or "firma aceptada",
        raw,
    )
    raw, error = llamar(model.PropMaterial.GetMPIsotropic, NOMBRE_FALSO, 0.0, 0.0, 0.0, 0.0)
    inf.agregar(
        "S2 PropMaterial.GetMPIsotropic",
        FAIL if error and "TypeError" in error else PASS,
        error or "firma aceptada",
        raw,
    )


def sonda_seccion(model: Any, nombre: str, mm: float, inf: Informe) -> dict[str, Any]:
    """Lee una sección real con las llamadas de la ruta y verifica la disposición de las salidas."""
    visto: dict[str, Any] = {"nombre": nombre}
    raw, error = llamar(model.PropFrame.GetTypeOAPI, nombre, 0)
    partes = separar(raw, 1) if not error else None
    if partes is None or partes[0] != 0:
        inf.agregar(
            f"T1 GetTypeOAPI({nombre})", FAIL, error or "forma inesperada (se esperaba (tipo, código))", raw
        )
        return visto
    tipo = int(partes[1][0])
    visto["tipo"] = tipo
    inf.agregar(
        f"T1 GetTypeOAPI({nombre})",
        PASS if tipo in TIPOS else INCONCLUSO,
        f"tipo {tipo} = {TIPOS.get(tipo, 'desconocido')}",
        raw,
    )

    dims: dict[str, float] = {}
    if tipo in GETTERS:
        metodo, relleno, n = GETTERS[tipo]
        raw, error = llamar(getattr(model.PropFrame, metodo), nombre, *relleno)
        partes = separar(raw, n) if not error else None
        if partes is None or partes[0] != 0:
            inf.agregar(f"T2 {metodo}({nombre})", FAIL, error or f"se esperaban {n + 1} elementos", raw)
        else:
            salidas = partes[1]
            visto["material"] = str(salidas[1]).strip()
            campos = (
                ("h", "b", "tf", "tw")
                if tipo not in (7, 8, 9)
                else {7: ("h", "tw"), 8: ("h", "b"), 9: ("h",)}[tipo]
            )
            for campo, valor in zip(campos, salidas[2:]):
                try:
                    dims[campo] = float(valor) * mm
                except (TypeError, ValueError):
                    pass
            ok = (
                bool(visto["material"])
                and all(dims.get(c, 0) > 0 for c in campos[:2] if c in dims)
                and bool(dims)
            )
            inf.agregar(
                f"T2 {metodo}({nombre})",
                PASS if ok else FAIL,
                f"material {visto['material']!r}; dimensiones en mm: "
                + ", ".join(f"{k}={v:.3f}" for k, v in dims.items()),
                raw,
            )
    else:
        inf.agregar(
            f"T2 getter tipado({nombre})", INCONCLUSO, f"el tipo {tipo} no tiene getter tipado en la ruta"
        )

    raw, error = llamar(model.PropFrame.GetSectProps, nombre, *((0.0,) * 12))
    partes = separar(raw, 12) if not error else None
    if partes is None or partes[0] != 0:
        inf.agregar(
            f"T3 GetSectProps({nombre})",
            FAIL,
            error or "se esperaban 13 elementos (12 salidas y el código)",
            raw,
        )
        return visto
    s = [float(x) for x in partes[1]]
    area, i22, i33, r22, r33 = s[0] * mm**2, s[4] * mm**4, s[5] * mm**4, s[10] * mm, s[11] * mm
    visto.update(A=area, I33=i33, I22=i22, r33=r33, r22=r22)
    nota = f"A={area:.1f} mm², I33={i33:.4g}, I22={i22:.4g} mm⁴, r33={r33:.3f}, r22={r22:.3f} mm"
    # R = sqrt(I/A): confirma qué salida es cada eje (22 y 33 no pueden intercambiarse sin que esto falle).
    coherente = (
        area > 0 and _cerca(r33, math.sqrt(i33 / area), 0.02) and _cerca(r22, math.sqrt(i22 / area), 0.02)
    )
    inf.agregar(
        f"T3 GetSectProps({nombre})",
        PASS if coherente else FAIL,
        nota
        + ("" if coherente else " — R22/R33 no coinciden con raíz(I/A): revisar el orden de las salidas"),
        raw,
    )
    if tipo == 1:
        fuerte = i33 > i22 and r33 > r22
        inf.agregar(
            f"T4 eje fuerte({nombre})",
            PASS if fuerte else FAIL,
            "en una sección I el eje 3 (I33) es el fuerte"
            if fuerte
            else "I33 no es mayor que I22: revisar qué salida es cada eje",
        )
    esperada = area_desde_dimensiones(tipo, dims)
    if esperada:
        cerca = _cerca(area, esperada, 0.10)
        inf.agregar(
            f"T5 área vs dimensiones({nombre})",
            PASS if cerca else FAIL,
            f"A={area:.1f} mm² contra {esperada:.1f} mm² calculada con h/b/tf/tw (tolerancia 10 % por filetes)"
            + ("" if cerca else ": revisar la convención de las dimensiones"),
        )
    return visto


def sonda_material(model: Any, nombre: str, mm: float, fuerza_n: float, inf: Informe) -> None:
    mpa = fuerza_n / mm**2
    raw, error = llamar(model.PropMaterial.GetTypeOAPI, nombre, 0, 0)
    partes = separar(raw, 2) if not error else None
    if partes is None or partes[0] != 0:
        inf.agregar(
            f"M1 GetTypeOAPI({nombre})",
            FAIL,
            error or "se esperaban 3 elementos (tipo, subtipo, código)",
            raw,
        )
        return
    codigo = int(partes[1][0])
    inf.agregar(
        f"M1 GetTypeOAPI({nombre})",
        PASS,
        f"tipo de material {codigo} ({'Steel' if codigo == 1 else 'no acero'})",
        raw,
    )
    if codigo == 1:
        raw, error = llamar(model.PropMaterial.GetOSteel_1, nombre, *((0.0,) * 4 + (0, 0) + (0.0,) * 4))
        partes = separar(raw, 10) if not error else None
        if partes is None or partes[0] != 0:
            inf.agregar(f"M2 GetOSteel_1({nombre})", FAIL, error or "se esperaban 11 elementos", raw)
        else:
            fy, fu = float(partes[1][0]) * mpa, float(partes[1][1]) * mpa
            plausible = 100 < fy < 1500 and fy <= fu < 2000
            inf.agregar(
                f"M2 GetOSteel_1({nombre})",
                PASS if plausible else FAIL,
                f"fy={fy:.1f} MPa, fu={fu:.1f} MPa"
                + ("" if plausible else " — fuera de lo plausible para un acero: revisar la conversión"),
                raw,
            )
    raw, error = llamar(model.PropMaterial.GetMPIsotropic, nombre, 0.0, 0.0, 0.0, 0.0)
    partes = separar(raw, 4) if not error else None
    if partes is None or partes[0] != 0:
        inf.agregar(f"M3 GetMPIsotropic({nombre})", FAIL, error or "se esperaban 5 elementos", raw)
    else:
        e = float(partes[1][0]) * mpa
        plausible = 1000 < e < 400000
        inf.agregar(
            f"M3 GetMPIsotropic({nombre})",
            PASS if plausible else FAIL,
            f"E={e:.0f} MPa" + ("" if plausible else " — fuera de lo plausible: revisar la conversión"),
            raw,
        )


def sonda_ruta(model: Any, inf: Informe) -> dict[str, Any] | None:
    """Ejecuta la lectura completa de la ruta y la contrasta con lo que midieron las sondas anteriores."""
    from bridge.viewer.core.section_props import read_section_props

    try:
        resultado = read_section_props(model)
    except Exception as exc:  # noqa: BLE001
        inf.agregar("R1 read_section_props", FAIL, f"{type(exc).__name__}: {exc}")
        return None
    n_sec, n_mat, avisos = len(resultado["sections"]), len(resultado["materials"]), resultado["warnings"]
    inf.agregar(
        "R1 read_section_props",
        PASS if n_sec else FAIL,
        f"{n_sec} secciones, {n_mat} materiales, {len(avisos)} avisos"
        + (": " + " | ".join(avisos[:5]) if avisos else ""),
    )
    return resultado


def comparar_unidades(model: Any, resultado: dict[str, Any], inf: Informe) -> None:
    """Opcional: lee con N_mm_C (SAP2000 ya en mm y MPa) y compara con la conversión de la ruta. Restaura siempre."""
    from bridge.viewer.core.section_props import read_section_props

    original, error = llamar(model.GetPresentUnits)
    if error or original is None:
        inf.agregar("C1 comparar unidades", INCONCLUSO, f"no se pudo leer la unidad original: {error}")
        return
    try:
        model.SetPresentUnits(EU_N_MM)
        directo = read_section_props(model)
    except Exception as exc:  # noqa: BLE001
        inf.agregar("C1 comparar unidades", FAIL, f"{type(exc).__name__}: {exc}")
        return
    finally:
        try:
            model.SetPresentUnits(original)
        except Exception:  # noqa: BLE001
            pass
    diferencias = []
    for nombre, sec in resultado["sections"].items():
        otra = directo["sections"].get(nombre)
        if not otra:
            continue
        for campo in ("A", "I33", "I22", "h", "b", "tf", "tw"):
            a, b = sec.get(campo), otra.get(campo)
            if a is not None and b is not None and not _cerca(a, b, 1e-6):
                diferencias.append(f"{nombre}.{campo}: {a} contra {b}")
    for nombre, mat in resultado["materials"].items():
        otra = directo["materials"].get(nombre)
        for campo in ("fy", "fu", "E"):
            a, b = mat.get(campo), (otra or {}).get(campo)
            if a is not None and b is not None and not _cerca(a, b, 1e-6):
                diferencias.append(f"{nombre}.{campo}: {a} contra {b}")
    inf.agregar(
        "C1 comparar unidades",
        FAIL if diferencias else PASS,
        ("diferencias: " + "; ".join(diferencias[:10]))
        if diferencias
        else "la conversión de la ruta coincide con SAP2000 en N_mm_C",
    )


# --- Ejecución -----------------------------------------------------------------------------------


def ejecutar(model: Any, max_por_tipo: int = 2, con_comparacion: bool = False) -> dict[str, Any]:
    from bridge.actions import _get_name_list

    inf = Informe()
    factores = sonda_unidades(model, inf)
    sonda_firmas(model, inf)
    if factores is None:
        return {"filas": inf.filas, "resumen": inf.resumen()}
    mm, fuerza_n = factores

    nombres, error = llamar(_get_name_list, model.PropFrame, "PropFrame.GetNameList")
    if error or nombres is None:
        inf.agregar("N1 PropFrame.GetNameList", FAIL, error or "sin lista")
        return {"filas": inf.filas, "resumen": inf.resumen()}
    inf.agregar(
        "N1 PropFrame.GetNameList", PASS if nombres else INCONCLUSO, f"{len(nombres)} secciones de barra"
    )

    por_tipo: dict[int, int] = {}
    materiales: set[str] = set()
    tipos_vistos: set[int] = set()
    for nombre in sorted(nombres):
        raw, _ = llamar(model.PropFrame.GetTypeOAPI, nombre, 0)
        partes = separar(raw, 1) if raw is not None else None
        tipo = int(partes[1][0]) if partes else -1
        if por_tipo.get(tipo, 0) >= max_por_tipo:
            continue
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        visto = sonda_seccion(model, nombre, mm, inf)
        if visto.get("tipo") is not None:
            tipos_vistos.add(visto["tipo"])
        if visto.get("material"):
            materiales.add(visto["material"])
    for tipo, (metodo, _r, _n) in GETTERS.items():
        if tipo not in tipos_vistos:
            inf.agregar(
                f"T0 {metodo}",
                INCONCLUSO,
                f"el modelo no tiene secciones de tipo {TIPOS[tipo]}: solo se confirmó la firma",
            )
    for nombre in sorted(materiales):
        sonda_material(model, nombre, mm, fuerza_n, inf)
    resultado = sonda_ruta(model, inf)
    if con_comparacion and resultado:
        comparar_unidades(model, resultado, inf)
    return {"filas": inf.filas, "resumen": inf.resumen(), "ruta": resultado}


def markdown(informe: dict[str, Any], version: str) -> str:
    r = informe["resumen"]
    lineas = [
        "# Sonda de secciones del Viewer",
        "",
        f"SAP2000: {version} · PASS {r[PASS]} · FAIL {r[FAIL]} · INCONCLUSO {r[INCONCLUSO]}",
        "",
        "| Prueba | Veredicto | Nota |",
        "|---|---|---|",
    ]
    for f in informe["filas"]:
        lineas.append(f"| {f['id']} | {f['veredicto']} | {f['nota'].replace('|', '/')} |")
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--salida-dir", default=".", help="carpeta de los informes (por defecto, la actual)")
    p.add_argument(
        "--max-por-tipo", type=int, default=2, help="secciones que se leen por cada tipo (por defecto 2)"
    )
    p.add_argument(
        "--comparar-unidades",
        action="store_true",
        help="contrasta la conversión contra N_mm_C (cambia y restaura las unidades)",
    )
    args = p.parse_args(argv)

    from bridge.viewer.sap2000.connector import Sap2000Connector

    conector = Sap2000Connector()
    info = conector.connect_to_open_instance()
    if not info.connected:
        print(f"No se pudo conectar a SAP2000: {info.message}", file=sys.stderr)
        return 2
    print(f"Conectado: {info.model_name or '(sin nombre)'} · unidades {info.units}")
    try:
        informe = ejecutar(conector.model, args.max_por_tipo, args.comparar_unidades)
        version, _ = llamar(getattr(conector.sap_object, "GetVersion", lambda: "?"))
    finally:
        conector.disconnect()

    destino = Path(args.salida_dir)
    destino.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    (destino / f"sonda_viewer_secciones_{sello}.json").write_text(
        json.dumps(
            {"sap2000": str(version), "modelo": info.model_name, "unidades": str(info.units), **informe},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    (destino / f"sonda_viewer_secciones_{sello}.md").write_text(
        markdown(informe, str(version)), encoding="utf-8"
    )
    r = informe["resumen"]
    print(f"PASS {r[PASS]} · FAIL {r[FAIL]} · INCONCLUSO {r[INCONCLUSO]}")
    print(f"Informes en {destino.resolve()} (sonda_viewer_secciones_{sello}.json / .md)")
    return 1 if r[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
