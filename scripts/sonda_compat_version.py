#!/usr/bin/env python3
"""Sonda de compatibilidad del puente con una versión de SAP2000 (p. ej. v26).

SOLO LECTURA: no crea, borra ni modifica nada, no cambia unidades ni selección y no
ejecuta el análisis. Se puede lanzar sobre el modelo real. Comprueba las llamadas
del OAPI que usa el puente y las compara con lo validado en SAP2000 23.3.1
(docs/skills/sap2000-programming/references/lecciones-puente-pywin32.md):

  * que cada método exista;
  * que el código de retorno vaya donde se espera (primero en 23.3.1);
  * que el modelo abierto devuelva datos con la forma esperada.

Uso (PowerShell, con SAP2000 abierto y un modelo con nudos y barras):

    py -3.12 scripts\\sonda_compat_version.py

Deja `sonda_compat_<versión>.json` junto a donde se ejecuta. Envíe ese archivo
(o el resumen impreso) para revisar las diferencias.

Requiere: Windows, pywin32.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sonda_oapi import HELPER_PROG_IDS, PROG_IDS, has, names, plain  # noqa: E402

# Métodos que usa el puente (bridge/*.py), por objeto del modelo.
METODOS = {
    "PointObj": [
        "GetNameList",
        "GetCoordCartesian",
        "GetRestraint",
        "GetLoadForce",
        "SetLoadForce",
        "GetConnectivity",
        "SetSelected",
        "AddCartesian",
    ],
    "FrameObj": [
        "GetNameList",
        "GetPoints",
        "GetSection",
        "SetSection",
        "GetLocalAxes",
        "GetModifiers",
        "SetSelected",
        "SetGroupAssign",
    ],
    "AreaObj": ["GetNameList", "GetPoints", "GetProperty", "GetElm"],
    "AreaElm": ["GetPoints"],
    "SelectObj": ["GetSelected", "ClearSelection"],
    "GroupDef": ["GetNameList", "GetAssignments", "SetGroup"],
    "LoadPatterns": ["GetNameList", "GetLoadType", "Add", "ChangeName"],
    "LoadCases": ["GetNameList", "GetTypeOAPI_1", "GetTypeOAPI", "ChangeName"],
    "RespCombo": ["GetNameList", "Add", "SetCaseList", "ComboType", "Delete"],
    "PropMaterial": ["GetNameList", "GetMaterial", "GetMPIsotropic", "GetWeightAndMass"],
    "PropFrame": [
        "GetNameList",
        "GetTypeOAPI",
        "GetRectangle",
        "GetCircle",
        "GetISection",
        "GetTube",
        "GetPipe",
        "GetTee",
        "GetChannel",
        "GetAngle",
        "GetGeneral",
        "GetSectProps",
        "GetMaterial",
    ],
    "PropArea": ["GetNameList"],
    "Func": ["GetNameList"],
    "DatabaseTables": ["GetTableForDisplayArray", "GetAllFieldsInTable", "ApplyEditedTables"],
    "Results": [
        "FrameForce",
        "JointReact",
        "BaseReact",
        "SectionCutAnalysis",
        "ModalParticipatingMassRatios",
    ],
    "View": ["RefreshView"],
}

RESULTADO: list[dict[str, Any]] = []


def registrar(estado: str, etiqueta: str, detalle: str = "", crudo: Any = None) -> None:
    RESULTADO.append({"estado": estado, "prueba": etiqueta, "detalle": detalle, "crudo": plain(crudo)})
    marca = {
        "OK": "  OK       ",
        "DIFERENTE": "  DIFERENTE",
        "FALLO": "  FALLO    ",
        "OMITIDO": "  omitido  ",
    }
    print(f"{marca[estado]} {etiqueta}" + (f"  -> {detalle}" if detalle else ""))


def ret_primero(raw: Any) -> bool | None:
    """True si el código de retorno (int) va primero, False si va al final, None si no se sabe."""
    if not isinstance(raw, (tuple, list)) or not raw:
        return None
    if isinstance(raw[0], int) and not isinstance(raw[0], bool):
        return True
    if isinstance(raw[-1], int) and not isinstance(raw[-1], bool):
        return False
    return None


def llamar(etiqueta: str, fn, esperar_ret_primero: bool = True, minimo: int = 0) -> Any:
    """Llama y valida la forma: código de retorno primero (como en 23.3.1) y largo mínimo."""
    try:
        raw = fn()
    except Exception as exc:
        registrar("FALLO", etiqueta, f"{type(exc).__name__}: {exc}"[:200])
        return None
    pos = ret_primero(raw)
    largo = len(raw) if isinstance(raw, (tuple, list)) else 1
    if esperar_ret_primero and pos is False:
        registrar("DIFERENTE", etiqueta, "el código de retorno va al final (en 23.3.1 va primero)", raw)
    elif largo < minimo:
        registrar("DIFERENTE", etiqueta, f"devuelve {largo} valores y se esperaban al menos {minimo}", raw)
    else:
        registrar("OK", etiqueta, f"{largo} valores", raw)
    return raw


CONEXION: list[dict[str, Any]] = []
PIDS: list[int] = []
# Referencias COM que deben seguir vivas mientras dure la sonda: si SapObject se libera, SapModel queda huérfano.
_VIVOS: list[Any] = []


def procesos_sap() -> list[str]:
    """PID de los SAP2000.exe en ejecución (puede haber varias instancias, incluso ocultas)."""
    import subprocess

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SAP2000.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except Exception as exc:
        return [f"error: {exc}"]
    return [ln.strip() for ln in out.splitlines() if "SAP2000" in ln]


def _clsid(prog_id: str) -> str:
    """CLSID de un ProgID; algunas builds de pywin32 no traen CLSIDFromProgID, entonces se lee el registro."""
    import pythoncom

    resolver = getattr(pythoncom, "CLSIDFromProgID", None)
    if callable(resolver):
        return str(resolver(prog_id))
    import winreg

    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\CLSID") as key:
        return str(winreg.QueryValueEx(key, None)[0])


PASO: dict[str, str] = {"actual": ""}


def con_limite(fn, segundos: float = 25.0):
    """Ejecuta `fn` en un hilo COM propio; si SAP2000 no responde, abandona el hilo y sigue (no cuelga la sonda)."""
    import threading

    caja: dict[str, Any] = {}

    def correr():
        import pythoncom

        pythoncom.CoInitialize()
        try:
            caja["valor"] = fn()
        except BaseException as exc:  # noqa: BLE001
            caja["error"] = exc
        finally:
            pythoncom.CoUninitialize()

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    hilo.join(segundos)
    if hilo.is_alive():
        raise TimeoutError(
            f"sin respuesta en {segundos:.0f} s en el paso «{PASO['actual']}» "
            "(SAP2000 no contestó; ¿hay un diálogo abierto en SAP2000 o un proceso duplicado?)"
        )
    if "error" in caja:
        raise caja["error"]
    return caja.get("valor")


def _candidatos():
    """Rutas para llegar a una instancia abierta: Helper.GetObject y la tabla de objetos en ejecución."""
    import pythoncom
    import win32com.client.dynamic

    for helper_id in HELPER_PROG_IDS:
        try:
            helper = win32com.client.dynamic.Dispatch(helper_id)
        except Exception as exc:
            CONEXION.append({"ruta": f"Dispatch({helper_id})", "error": str(exc)[:200]})
            continue
        for prog_id in PROG_IDS:
            yield f"Helper({helper_id}).GetObject({prog_id})", lambda h=helper, p=prog_id: h.GetObject(p)
            for pid in PIDS:
                yield (
                    f"Helper({helper_id}).GetObjectProcess({prog_id}, {pid})",
                    lambda h=helper, p=prog_id, i=pid: h.GetObjectProcess(p, i),
                )
    for prog_id in PROG_IDS:

        def activo(p=prog_id):
            clsid = _clsid(p)
            disp = pythoncom.GetActiveObject(clsid).QueryInterface(pythoncom.IID_IDispatch)
            return win32com.client.dynamic.Dispatch(disp)

        yield f"GetActiveObject({prog_id})", activo


def conectar() -> Any:
    """Prueba cada ruta y se queda con la primera cuyo modelo responde de verdad (no solo existe)."""
    try:
        import pythoncom  # noqa: F401
        import win32com.client.dynamic  # noqa: F401
    except ImportError as err:
        raise SystemExit("Falta pywin32: py -3.12 -m pip install pywin32") from err
    elegido = None
    filas = procesos_sap()
    print(f"  Procesos SAP2000.exe: {filas or 'ninguno'}")
    if len(filas) > 1:
        print(
            "  AVISO: hay más de un SAP2000.exe. Uno con muy poca memoria (decenas de KB) suele ser un proceso zombi."
        )
    CONEXION.append({"procesos": filas})
    for fila in filas:
        partes = [x.strip('"') for x in fila.split('","')]
        if len(partes) > 1 and partes[1].isdigit():
            PIDS.append(int(partes[1]))

    def sondear(abrir) -> dict[str, Any]:
        """Prueba una ruta completa (abrir, leer archivo, versión y nudos) y devuelve solo datos simples."""
        PASO["actual"] = "abrir el objeto (GetObject / GetActiveObject)"
        obj = abrir()
        PASO["actual"] = "leer obj.SapModel"
        model = obj.SapModel
        PASO["actual"] = "GetPresentUnits"
        res: dict[str, Any] = {"unidades": plain(model.GetPresentUnits())}
        PASO["actual"] = "GetModelFilename"
        res["archivo"] = plain(model.GetModelFilename())
        try:
            res["version_oapi"] = plain(obj.GetOAPIVersionNumber())
        except Exception as exc:
            res["version_oapi"] = f"error: {str(exc)[:100]}"
        try:
            res["version_programa"] = plain(model.GetVersion())
        except Exception as exc:
            res["version_programa"] = f"error: {str(exc)[:100]}"
        PASO["actual"] = "PointObj.GetNameList"
        res["nudos"] = plain(model.PointObj.GetNameList())
        PASO["actual"] = ""
        return res

    for ruta, abrir in _candidatos():
        info: dict[str, Any] = {"ruta": ruta}
        t0 = time.monotonic()
        try:
            # La prueba corre en un hilo con límite de tiempo: un SAP2000 zombi ya no cuelga la sonda.
            info.update(con_limite(lambda f=abrir: sondear(f)))
            info["responde"] = True
            if elegido is None:
                # Ruta confirmada: se reabre en el hilo principal para usar el modelo en las pruebas siguientes.
                obj = abrir()
                _VIVOS.append(obj)
                elegido = obj.SapModel
                _VIVOS.append(elegido)
        except Exception as exc:
            info["responde"] = False
            info["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        info["segundos"] = round(time.monotonic() - t0, 2)
        CONEXION.append(info)
        print(
            f"  {'OK    ' if info.get('responde') else 'falla '} {ruta}"
            + ("" if info.get("responde") else f" -> {info['error'][:90]}")
        )
    if elegido is None:
        print(
            "\nNinguna ruta devolvió un modelo que responda. Revise que SAP2000 tenga un modelo abierto y esté en primer plano."
        )
        guardar_conexion()
        raise SystemExit(1)
    return elegido


def guardar_conexion() -> None:
    with open("sonda_compat_conexion.json", "w", encoding="utf-8") as fh:
        json.dump(CONEXION, fh, ensure_ascii=False, indent=2)


def main() -> None:
    print("== Conexión ==")
    model = conectar()
    print("\nConectado a SAP2000. Solo lectura.\n")

    print("== Versión ==")
    version = ""
    try:
        v = model.GetVersion()
        version = str(v[0] if isinstance(v, (tuple, list)) else v)
        print(f"  GetVersion -> {plain(v)}")
    except Exception as exc:
        print(f"  GetVersion falló: {exc}")
    try:
        print(f"  Modelo: {plain(model.GetModelFilename())}")
        print(f"  Unidades actuales (código eUnits): {plain(model.GetPresentUnits())}")
        print(f"  ¿Bloqueado (con resultados)?: {plain(model.GetModelIsLocked())}")
    except Exception as exc:
        print(f"  Lectura básica falló: {exc}")

    print("\n== Existencia de métodos que usa el puente ==")
    faltan: list[str] = []
    for objeto, metodos in METODOS.items():
        try:
            obj = getattr(model, objeto)
        except Exception as exc:
            registrar("FALLO", f"{objeto}", f"el objeto no existe: {exc}"[:160])
            faltan.extend(f"{objeto}.{m}" for m in metodos)
            continue
        for m in metodos:
            if not has(obj, m):
                faltan.append(f"{objeto}.{m}")
                registrar("DIFERENTE", f"{objeto}.{m}", "no existe en esta versión")
    if not faltan:
        registrar("OK", "Todos los métodos existen", f"{sum(len(m) for m in METODOS.values())} comprobados")

    print("\n== Lecturas con el modelo abierto (forma de la respuesta) ==")
    puntos = names(llamar("PointObj.GetNameList", model.PointObj.GetNameList, minimo=3))
    frames = names(llamar("FrameObj.GetNameList", model.FrameObj.GetNameList, minimo=3))
    names(llamar("AreaObj.GetNameList", model.AreaObj.GetNameList, minimo=3))
    llamar("LoadPatterns.GetNameList", model.LoadPatterns.GetNameList, minimo=3)
    casos = names(llamar("LoadCases.GetNameList", model.LoadCases.GetNameList, minimo=3))
    llamar("RespCombo.GetNameList", model.RespCombo.GetNameList, minimo=3)
    llamar("GroupDef.GetNameList", model.GroupDef.GetNameList, minimo=3)
    llamar("PropFrame.GetNameList", model.PropFrame.GetNameList, minimo=3)
    llamar("PropArea.GetNameList", model.PropArea.GetNameList, minimo=3)
    mats = names(llamar("PropMaterial.GetNameList", lambda: model.PropMaterial.GetNameList(0, []), minimo=3))

    if puntos:
        p = puntos[0]
        llamar(f"PointObj.GetCoordCartesian({p})", lambda: model.PointObj.GetCoordCartesian(p), minimo=4)
        llamar(f"PointObj.GetRestraint({p})", lambda: model.PointObj.GetRestraint(p), minimo=2)
        llamar(f"PointObj.GetConnectivity({p})", lambda: model.PointObj.GetConnectivity(p), minimo=2)
    else:
        registrar("OMITIDO", "Lecturas de nudos", "el modelo no tiene nudos")

    if frames:
        f = frames[0]
        llamar(f"FrameObj.GetPoints({f})", lambda: model.FrameObj.GetPoints(f), minimo=3)
        sec = llamar(f"FrameObj.GetSection({f})", lambda: model.FrameObj.GetSection(f), minimo=3)
        llamar(f"FrameObj.GetLocalAxes({f})", lambda: model.FrameObj.GetLocalAxes(f), minimo=3)
        llamar(f"FrameObj.GetModifiers({f})", lambda: model.FrameObj.GetModifiers(f), minimo=2)
        nombre_sec = next((x for x in (sec or []) if isinstance(x, str) and x and x != f), "")
        if nombre_sec:
            for familia, _minimo in (
                ("GetRectangle", 7),
                ("GetCircle", 6),
                ("GetISection", 8),
                ("GetTube", 7),
                ("GetPipe", 6),
            ):
                # Una familia que no corresponde a la sección devuelve ret=1: eso es normal.
                fn = getattr(model.PropFrame, familia)
                try:
                    raw = fn(nombre_sec)
                except Exception as exc:
                    registrar("FALLO", f"PropFrame.{familia}({nombre_sec})", str(exc)[:160])
                    continue
                if ret_primero(raw) is False:
                    registrar("DIFERENTE", f"PropFrame.{familia}", "el retorno va al final", raw)
                else:
                    registrar("OK", f"PropFrame.{familia}({nombre_sec})", f"{len(raw)} valores", raw)
            llamar(
                f"PropFrame.GetTypeOAPI({nombre_sec})",
                lambda: model.PropFrame.GetTypeOAPI(nombre_sec),
                minimo=2,
            )
    else:
        registrar("OMITIDO", "Lecturas de barras", "el modelo no tiene barras")

    if casos:
        llamar(
            f"LoadCases.GetTypeOAPI_1({casos[0]})",
            lambda: model.LoadCases.GetTypeOAPI_1(casos[0]),
            minimo=5,
        )
    if mats:
        llamar(
            f"PropMaterial.GetMaterial({mats[0]})", lambda: model.PropMaterial.GetMaterial(mats[0]), minimo=2
        )
        llamar(
            f"PropMaterial.GetMPIsotropic({mats[0]})",
            lambda: model.PropMaterial.GetMPIsotropic(mats[0]),
            minimo=5,
        )

    llamar("SelectObj.GetSelected", model.SelectObj.GetSelected, minimo=2)
    llamar(
        "DatabaseTables.GetAllFieldsInTable",
        lambda: model.DatabaseTables.GetAllFieldsInTable("Joint Coordinates"),
        minimo=4,
    )

    print("\n== Resumen ==")
    cuenta = {
        k: sum(1 for r in RESULTADO if r["estado"] == k) for k in ("OK", "DIFERENTE", "FALLO", "OMITIDO")
    }
    print("  " + "  ".join(f"{k}: {n}" for k, n in cuenta.items()))
    if faltan:
        print("  Métodos que faltan: " + ", ".join(faltan))
    for r in RESULTADO:
        if r["estado"] in ("DIFERENTE", "FALLO"):
            print(f"  - {r['estado']}: {r['prueba']}  {r['detalle']}")
    print(
        "\nNo se probó lo que exige análisis o escribe en el modelo (Results.*, creación de secciones, cargas, "
        "espectros): requiere pruebas específicas sobre un modelo analizado o en blanco."
    )

    destino = f"sonda_compat_{(version or 'sin_version').replace(' ', '_')}.json"
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "version": version,
                "conexion": CONEXION,
                "resultados": RESULTADO,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nGuardado en {os.path.abspath(destino)}")


if __name__ == "__main__":
    main()
