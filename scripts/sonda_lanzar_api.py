#!/usr/bin/env python3
"""Prueba mínima del OAPI de SAP2000 lanzando el programa DESDE la API (no enlaza con uno ya abierto).

Sirve para separar dos causas cuando la conexión con un SAP2000 abierto se queda sin respuesta:
  * si ESTA prueba funciona, el OAPI de esa versión está bien instalado y el fallo está en enlazar con una
    instancia abierta a mano (registro en la tabla de objetos en ejecución);
  * si también se queda sin respuesta o falla, el problema es la instalación o el registro COM de esa versión.

Abre una instancia NUEVA de SAP2000 con un modelo en blanco, lee versión y unidades y la cierra sin guardar.
No toca ningún archivo ni la instancia que ya tengas abierta. Cierre antes su SAP2000 para no mezclar licencias.

Uso (PowerShell):

    py -3.12 scripts\\sonda_lanzar_api.py
    py -3.12 scripts\\sonda_lanzar_api.py --exe "C:\\Program Files\\Computers and Structures\\SAP2000 26\\SAP2000.exe"

Deja `sonda_lanzar_api.json` con el resultado de cada paso.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from typing import Any

EXE_POR_DEFECTO = r"C:\Program Files\Computers and Structures\SAP2000 26\SAP2000.exe"
PASOS: list[dict[str, Any]] = []
ACTUAL = {"paso": ""}


def marcar(paso: str, **datos: Any) -> None:
    ACTUAL["paso"] = paso
    PASOS.append({"paso": paso, "t": round(time.monotonic() - T0, 1), **datos})
    print(f"  [{PASOS[-1]['t']:>6} s] {paso} {datos if datos else ''}")


def prueba(exe: str, via_ruta: bool) -> None:
    import pythoncom
    import win32com.client.dynamic

    pythoncom.CoInitialize()
    obj = None
    try:
        marcar("Dispatch SAP2000v1.Helper")
        helper = win32com.client.dynamic.Dispatch("SAP2000v1.Helper")
        if via_ruta:
            marcar("Helper.CreateObject(ruta del .exe)", exe=exe)
            obj = helper.CreateObject(exe)
        else:
            marcar("Helper.CreateObjectProgID(CSI.SAP2000.API.SapObject)")
            obj = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
        marcar("ApplicationStart()")
        ret = obj.ApplicationStart()
        marcar("ApplicationStart devolvió", ret=str(ret))
        model = obj.SapModel
        marcar("InitializeNewModel + File.NewBlank")
        model.InitializeNewModel()
        model.File.NewBlank()
        marcar("GetVersion", valor=str(model.GetVersion()))
        marcar("GetPresentUnits", valor=str(model.GetPresentUnits()))
        marcar("PointObj.GetNameList", valor=str(model.PointObj.GetNameList()))
    finally:
        if obj is not None:
            try:
                marcar("ApplicationExit(False)")
                obj.ApplicationExit(False)
            except Exception as exc:
                marcar("ApplicationExit falló", error=str(exc)[:150])
        pythoncom.CoUninitialize()


T0 = time.monotonic()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=EXE_POR_DEFECTO)
    ap.add_argument("--limite", type=float, default=150.0, help="segundos máximos por intento")
    args = ap.parse_args()
    resultados = []
    for via_ruta in (False, True):
        etiqueta = "por ruta del .exe" if via_ruta else "por ProgID"
        print(f"\n== Intento {etiqueta} ==")
        error: list[str] = []

        def correr(v=via_ruta, e=error):
            try:
                prueba(args.exe, v)
            except BaseException as exc:  # noqa: BLE001
                e.append(f"{type(exc).__name__}: {str(exc)[:200]}")

        hilo = threading.Thread(target=correr, daemon=True)
        hilo.start()
        hilo.join(args.limite)
        if hilo.is_alive():
            resultado = f"SIN RESPUESTA en {args.limite:.0f} s en el paso «{ACTUAL['paso']}»"
        elif error:
            resultado = f"FALLO en el paso «{ACTUAL['paso']}»: {error[0]}"
        else:
            resultado = "OK"
        print(f"  -> {resultado}")
        resultados.append({"intento": etiqueta, "resultado": resultado})
        if resultado == "OK":
            break
    with open("sonda_lanzar_api.json", "w", encoding="utf-8") as fh:
        json.dump({"intentos": resultados, "pasos": PASOS}, fh, ensure_ascii=False, indent=2)
    print("\nGuardado en sonda_lanzar_api.json")


if __name__ == "__main__":
    main()
