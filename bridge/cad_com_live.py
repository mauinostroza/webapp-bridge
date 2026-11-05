# -*- coding: utf-8 -*-
"""
cad.com_live — Conexión COM en vivo con AutoCAD / ZWCAD / BricsCAD.

Envía el dibujo (IR) directamente a la sesión CAD abierta a través de la
API COM/ActiveX (pywin32), sin pasar por archivo DXF. Las entidades se
crean en el espacio modelo del documento activo: líneas, polilíneas,
círculos, arcos, sólidos, textos, cotas asociativas nativas y capas.

Soportado:
- AutoCAD  (ProgID "AutoCAD.Application")
- ZWCAD    (ProgID "ZWCAD.Application", API COM idéntica a la de AutoCAD)
- BricsCAD (ProgID "BricscadApp.AcadApplication")

Requiere Windows + pywin32 (`pip install pywin32`) y el CAD abierto antes
de enviar. El resto del programa funciona sin esta dependencia: los
imports de win32 están dentro de las funciones.

--------------------------------------------------------------------------
COPIA DEL ESCRITORIO (puente web local, workbench_module/bridge)
--------------------------------------------------------------------------
Este archivo es una copia de `cad/com_live.py` del escritorio (que sigue
siendo la fuente original y no se modifica desde aquí). La lógica COM es
idéntica. Cambios respecto al original:

1. Las primitivas (`ir`) e `inferir_th` vienen de `bridge.cad_prims`
   (código propio del puente): no depende del motor de cálculo.
2. `_infer_ltscale` se define localmente con la misma regla que el
   escritorio. El puente no depende de ezdxf.
3. `_LW` (grosores de capa) se copia como constante local: en el
   escritorio se importa de `cad.dxf_out`, que a su vez importa ezdxf.
4. `_tabla_prims`, `_aplanar`, `enviar_dibujo` y `abrir_dxf_en_cad` se
   eliminan (sin uso en el puente): las tablas y llamadas llegan ya
   aplanadas desde la web y las rutas emiten con `_emit`/`_dim`.
5. No hay otros cambios de lógica COM.
"""

import math
import os
import random
import time

from bridge import cad_prims as ir
from bridge.cad_prims import (
    Arc,
    Circle,
    Dim,
    Filled,
    Line,
    Poly,
    Text,
)
from bridge.cad_prims import inferir_th as _infer_th

PROGIDS = [
    ("AutoCAD.Application", "AutoCAD"),
    ("ZWCAD.Application", "ZWCAD"),
    ("BricscadApp.AcadApplication", "BricsCAD"),
]

# grosores de capa en centésimas de mm (copia de cad/dxf_out.py::_LW)
_LW = {
    ir.L_EJE: 13,
    ir.L_ACOT: 18,
    ir.L_TXT: 18,
    ir.L_CONC: 35,
    ir.L_ACERO: 50,
    ir.L_PERF: 25,
    ir.L_SOLD: 18,
    ir.L_HACH: 9,
    ir.L_TABLA: 18,
    ir.L_OCULTO: 18,
}


def _infer_ltscale(dwg):
    """Misma regla que cad/dxf_out.py::_infer_ltscale."""
    th = _infer_th(dwg)
    return max(1.0, th / 3.0 * 2.0)


# ------------------------------------------------------------- conexión --


def _com():
    """Importa pywin32 (solo Windows). Lanza RuntimeError con mensaje claro."""
    if os.name != "nt":
        raise RuntimeError(
            "La conexión COM en vivo solo está disponible en Windows. "
            "Use la exportación DXF y abra el archivo en AutoCAD/ZWCAD."
        )
    try:
        import pythoncom
        from win32com.client import GetActiveObject, VARIANT

        return pythoncom, GetActiveObject, VARIANT
    except ImportError:
        raise RuntimeError(
            "pywin32 no está instalado. Ejecute:\n"
            "    pip install pywin32\n"
            "y vuelva a intentar el envío en vivo."
        )


def detectar():
    """Busca una sesión CAD abierta. Devuelve (app, progid) o lanza
    RuntimeError con mensaje útil."""
    pythoncom, GetActiveObject, _ = _com()
    for pid, nombre in PROGIDS:
        try:
            app = GetActiveObject(pid)
            return app, pid
        except Exception:
            continue
    raise RuntimeError(
        "No se encontró AutoCAD, ZWCAD o BricsCAD abierto.\n"
        "Inicie el programa CAD (con al menos un documento activo) "
        "y reintente el envío en vivo."
    )


def _documento(app, abrir=None):
    """Devuelve el documento de trabajo: el indicado, el activo o uno nuevo."""
    if abrir:
        return app.Documents.Open(abrir)
    try:
        doc = app.ActiveDocument
        if doc is not None:
            return doc
    except Exception:
        pass
    return app.Documents.Add()


def documento_activo(app):
    """Alias público de `_documento` para reutilizar la misma conexión
    (`app`) entre `pedir_punto` y `enviar_dibujo` desde la UI."""
    return _documento(app)


_RPC_E_SERVERFAULT = -2147417851  # 0x80010105: "El servidor lanzó una excepción"


def _asentar_mensajes(pythoncom, segundos: float = 0.35):
    """Bombea mensajes COM/Windows pendientes brevemente. Necesario porque
    `Utility.GetPoint` (interactivo) puede fallar con RPC_E_SERVERFAULT si
    se llama inmediatamente después de forzar el foco/activación de la
    ventana del CAD, antes de que su propio message loop procese esos
    cambios (activación, repintado)."""
    import time

    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        try:
            pythoncom.PumpWaitingMessages()
        except Exception:
            pass
        time.sleep(0.02)


def _pedir_get_point(doc, mensaje, pythoncom):
    """Llama a `Utility.GetPoint`, reintentando UNA vez si el fallo es
    específicamente RPC_E_SERVERFAULT (fallo transitorio conocido al
    invocar un método interactivo justo después de cambiar el foco de
    la ventana — se le da tiempo a asentarse y se reintenta)."""
    for intento in range(2):
        try:
            return doc.Utility.GetPoint(None, mensaje)
        except Exception as exc:
            hresult = exc.args[0] if getattr(exc, "args", None) else None
            if intento == 0 and hresult == _RPC_E_SERVERFAULT:
                _asentar_mensajes(pythoncom)
                continue
            raise


def _pedir_punto_por_comando(doc, mensaje, pythoncom, tiempo_maximo: float = 600.0):
    """Pide un punto mediante el *command loop* propio del CAD.

    ``Utility.GetPoint`` es una llamada COM interactiva. En algunas
    versiones de ZWCAD ésta falla con ``RPC_E_SERVERFAULT`` cuando el
    proceso llamante acaba de minimizar su ventana, aunque ZWCAD esté
    correctamente conectado. En vez de cruzar ese límite COM, se envía una
    pequeña expresión AutoLISP: el propio CAD muestra el prompt y recibe el
    clic. El resultado se devuelve por variables de usuario y se consulta
    por COM sólo después de que el comando haya terminado.

    AutoCAD, ZWCAD y BricsCAD exponen ``SendCommand`` y AutoLISP, por lo que
    este camino no depende del foco que tenga la aplicación Python durante
    el clic. Las variables se restauran siempre al finalizar.
    """
    try:
        anteriores = tuple(float(doc.GetVariable(nombre)) for nombre in ("USERR1", "USERR2", "USERR3"))
    except Exception as exc:
        raise RuntimeError(f"El CAD no permite preparar la selección de punto por comando: {exc}") from exc

    # Marcador distinto de cero: positivo = esperando, negativo = éxito,
    # cero = el usuario canceló con Esc. Se evita 0 porque es el estado que
    # deja explícitamente la rama de cancelación del comando.
    marcador = random.randint(100_000, 900_000)
    texto = mensaje.replace('"', "'").replace("\\", "/")
    comando = (
        '(progn '
        f'(setvar "USERR3" {marcador}) '
        f'(setq CADDRWR_P (getpoint "\\n{texto}")) '
        '(if CADDRWR_P '
        f'  (progn (setvar "USERR1" (car CADDRWR_P)) '
        f'         (setvar "USERR2" (cadr CADDRWR_P)) '
        f'         (setvar "USERR3" {-marcador})) '
        '  (setvar "USERR3" 0)) '
        '(princ)) '
    )
    try:
        # El espacio final entrega la expresión al command loop. SendCommand
        # retorna de inmediato al requerir interacción del usuario.
        doc.SendCommand(comando)
        fin = time.monotonic() + tiempo_maximo
        while time.monotonic() < fin:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            estado = float(doc.GetVariable("USERR3"))
            if estado == -marcador:
                return (float(doc.GetVariable("USERR1")), float(doc.GetVariable("USERR2")))
            if estado == 0:
                raise RuntimeError("Selección de punto cancelada.")
            time.sleep(0.05)
        raise RuntimeError(
            "El CAD no terminó la selección de punto. Presione Esc en el CAD y vuelva a intentarlo."
        )
    finally:
        for nombre, valor in zip(("USERR1", "USERR2", "USERR3"), anteriores):
            try:
                doc.SetVariable(nombre, valor)
            except Exception:
                pass


def traer_al_frente(app):
    """Fuerza que la ventana del CAD pase al primer plano REAL de Windows
    (z-order + foco de input), no solo "visible". `Visible=True`,
    `WindowState` y `Activate()` (COM) pueden dejar la ventana visible
    pero sin foco real si otra ventana (p.ej. la nuestra, ya minimizada)
    dejó de tenerlo un instante antes.

    Un `SetForegroundWindow` simple NO alcanza en ese caso: Windows solo
    deja que un proceso le robe el foco a otra ventana si el proceso
    llamante actualmente TIENE el foco (o lo tuvo hace muy poco) — si ya
    nos minimizamos, la llamada puede ser denegada en silencio (no lanza
    excepción, simplemente no hace nada), y entonces `GetPoint` puede
    fallar con RPC_E_SERVERFAULT de forma repetible (no es un race
    transitorio, así que reintentar sin más no lo arregla).

    El truco estándar de automatización Win32 para esto es adjuntar
    temporalmente la cola de input de nuestro hilo a la del hilo dueño de
    la ventana en primer plano y a la del hilo dueño de la ventana del
    CAD (`AttachThreadInput`) — mientras están adjuntas, Windows sí deja
    que cualquiera de los hilos "unidos" fuerce el foco. `HWnd` es una
    propiedad estándar de `Application` en AutoCAD, replicada por ZWCAD
    (API COM compatible, ver docstring del módulo).

    Llamar esto ANTES de minimizar la ventana propia (además de, otra
    vez, justo antes de `GetPoint`) es lo que realmente corrige el
    problema — ver `pedir_punto` y `app/main_window.py::send_com`."""
    try:
        import win32api
        import win32con
        import win32gui
        import win32process

        hwnd = int(app.HWnd)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        actual = win32api.GetCurrentThreadId()
        frente = win32gui.GetForegroundWindow()
        hilo_frente = win32process.GetWindowThreadProcessId(frente)[0] if frente else 0
        hilo_destino = win32process.GetWindowThreadProcessId(hwnd)[0]
        adj_frente = adj_destino = False
        try:
            if hilo_frente and hilo_frente != actual:
                adj_frente = win32process.AttachThreadInput(actual, hilo_frente, True)
            if hilo_destino and hilo_destino != actual:
                adj_destino = win32process.AttachThreadInput(actual, hilo_destino, True)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if adj_frente:
                win32process.AttachThreadInput(actual, hilo_frente, False)
            if adj_destino:
                win32process.AttachThreadInput(actual, hilo_destino, False)
    except Exception:
        pass  # best-effort: ya se intentó Visible/WindowState/Activate antes


def _pt(VARIANT, pythoncom, p, z=0.0):
    """Punto IR (x, y) -> VARIANT array 3D para la API COM."""
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(p[0]), float(p[1]), float(z)))


def _flat(VARIANT, pythoncom, pts):
    vals = []
    for p in pts:
        vals.extend((float(p[0]), float(p[1])))
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, vals)


# ------------------------------------------------------------- capas --


def _capas(doc):
    for name, aci in ir.LAYER_COLORS.items():
        try:
            ly = doc.Layers.Item(name)
        except Exception:
            ly = doc.Layers.Add(name)
        try:
            ly.Color = int(aci)
            ly.Lineweight = _LW.get(name, 25)
        except Exception:
            pass
    try:
        try:
            style = doc.TextStyles.Item("CAD_DRWR_ING")
        except Exception:
            style = doc.TextStyles.Add("CAD_DRWR_ING")
        style.FontFile = "txt.shx"
        style.Height = 0.0
    except Exception:
        pass
    for lt in ("CENTER", "HIDDEN"):
        try:
            doc.Linetypes.Load(lt)
        except Exception:
            pass  # ya cargado
    try:
        doc.Layers.Item(ir.L_EJE).Linetype = "CENTER"
        doc.Layers.Item(ir.L_OCULTO).Linetype = "HIDDEN"
    except Exception:
        pass


def _vars_cota(doc, dwg):
    """Variables de estilo de cota equivalentes al dimstyle del DXF."""
    th = _infer_th(dwg)
    pares = [
        ("DIMTXT", th),
        ("DIMASZ", 0.85 * th),
        ("DIMEXE", 0.45 * th),
        ("DIMEXO", 0.30 * th),
        ("DIMGAP", 0.70 * th),
        ("DIMTAD", 1),
        ("DIMTIH", 0),
        ("DIMTOH", 0),
        ("DIMSCALE", 1.0),
        ("DIMTOFL", 1),
        ("DIMSOXD", 0),
        ("DIMJUST", 0),
        ("DIMDEC", 0),
        ("DIMZIN", 8),
        ("DIMLUNIT", 2),
        ("LTSCALE", _infer_ltscale(dwg)),
    ]
    for k, v in pares:
        try:
            doc.SetVariable(k, v)
        except Exception:
            pass


# -------------------------------------------------------- alineación texto --

# enum acAlignment de AutoCAD/ZWCAD
_ALTXT = {
    ("l", "b"): 12,
    ("c", "b"): 13,
    ("r", "b"): 14,
    ("l", "m"): 9,
    ("c", "m"): 10,
    ("r", "m"): 11,
    ("l", "t"): 6,
    ("c", "t"): 7,
    ("r", "t"): 8,
}


def _texto(msp, e, VPT):
    t = msp.AddText(e.s, VPT(e.pos, 0.0), float(e.h))
    try:
        t.StyleName = "CAD_DRWR_ING"
    except Exception:
        pass
    try:
        t.Rotation = math.radians(e.rot)
    except Exception:
        pass
    al = _ALTXT.get((e.ha, e.va), 0)
    try:
        if al in (0, 1, 2):  # usa punto de inserción
            t.InsertionPoint = VPT(e.pos, 0.0)
        else:  # alineaciones de bloque: punto de alineación
            t.Alignment = al
            t.TextAlignmentPoint = VPT(e.pos, 0.0)
    except Exception:
        try:
            t.InsertionPoint = VPT(e.pos, 0.0)
        except Exception:
            pass
    try:
        t.Layer = e.layer
    except Exception:
        pass
    return t


# ----------------------------------------------------------- entidades --


def _emit(msp, e, VPT, VF, pythoncom, VARIANT):
    """Crea una entidad IR primitiva en el espacio modelo COM."""
    if isinstance(e, Line):
        if e.width > 0:
            pl = msp.AddLightWeightPolyline(VF([e.p1, e.p2]))
            pl.ConstantWidth = float(e.width)
        else:
            msp.AddLine(VPT(e.p1), VPT(e.p2))

    elif isinstance(e, Poly):
        if len(e.pts) < 2:
            return
        pl = msp.AddLightWeightPolyline(VF(e.pts))
        pl.Closed = bool(e.closed)
        if e.width > 0:
            pl.ConstantWidth = float(e.width)

    elif isinstance(e, Circle):
        c = msp.AddCircle(VPT(e.c), float(e.r))
        c.Layer = e.layer
        if e.filled:
            try:
                h = msp.AddHatch(0, "SOLID", True)
                from win32com.client import VARIANT as V

                loop = V(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [c])
                h.AppendOuterLoop(loop)
                h.Layer = e.layer
                h.Evaluate()
            except Exception:
                pass  # queda el círculo sin relleno
        return

    elif isinstance(e, Arc):
        a1, a2 = (e.a1, e.a2) if e.ccw else (e.a2, e.a1)
        if a2 < a1:
            a2 += 360.0
        msp.AddArc(VPT(e.c), float(e.r), math.radians(a1), math.radians(a2))

    elif isinstance(e, Filled):
        p = e.pts
        if len(p) == 3:
            msp.AddSolid(VPT(p[0]), VPT(p[1]), VPT(p[2]), VPT(p[2]))
        elif len(p) >= 4:
            msp.AddSolid(VPT(p[0]), VPT(p[1]), VPT(p[3]), VPT(p[2]))

    elif isinstance(e, Text):
        _texto(msp, e, VPT)
        return

    else:
        return  # no primitiva: se maneja aparte
    try:
        msp.Item(msp.Count - 1).Layer = e.layer
    except Exception:
        pass


def _dim(msp, e, VPT):
    ang = math.pi / 2.0 if e.vertical else 0.0
    dim = msp.AddDimRotated(VPT(e.p1), VPT(e.p2), VPT(e.base), ang)
    try:
        dim.Layer = e.layer
        dim.TextStyle = "CAD_DRWR_ING"
    except Exception:
        pass
    if e.txt:
        try:
            dim.TextOverride = e.txt
        except Exception:
            pass
    if e.text_height > 0:
        th = e.text_height
        for name, value in (
            ("TextHeight", th),
            ("ArrowheadSize", 0.85 * th),
            ("ExtensionLineExtend", 0.45 * th),
            ("ExtensionLineOffset", 0.30 * th),
            ("TextGap", 0.70 * th),
            ("ScaleFactor", 1.0),
        ):
            try:
                setattr(dim, name, value)
            except Exception:
                pass
    # Fuerza la línea entre puntos cuando AutoCAD desplaza flechas/texto.
    try:
        dim.Fit = 1
    except Exception:
        pass
    return dim


# ------------------------------------------------------------- API pública --


def pedir_punto(mensaje: str = "Especifique el punto de inserción del dibujo: ", app=None, doc=None):
    """Activa el documento del CAD y pide al usuario un clic en pantalla
    (comando nativo GetPoint). Devuelve (x, y) en coordenadas de modelo
    del CAD. Lanza RuntimeError si no hay CAD/documento, si se cancela
    (Esc / botón derecho) o si la llamada COM falla por cualquier otro
    motivo (se incluye el detalle real del error, no un mensaje genérico).

    `app`/`doc` permiten reutilizar una conexión ya establecida (evita
    reconectar/re-detectar innecesariamente cuando se llama junto con
    `enviar_dibujo`, p.ej. desde `send_com` en la UI)."""
    pythoncom, GetActiveObject, VARIANT = _com()
    if app is None:
        app, _ = detectar()
    pythoncom.CoInitialize()
    try:
        if doc is None:
            doc = _documento(app)
        try:
            app.Visible = True
            app.WindowState = 3  # acMax: intento "suave" adicional
        except Exception:
            pass
        try:
            doc.Activate()
        except Exception:
            pass
        traer_al_frente(app)  # fuerza foco real de Windows (ver docstring)
        _asentar_mensajes(pythoncom)  # deja procesar la activación de la ventana
        try:
            # No usar Utility.GetPoint aquí. En ZWCAD puede lanzar
            # RPC_E_SERVERFAULT al entrar en modo interactivo desde COM; el
            # command loop nativo sí recibe el clic de forma estable.
            pt = _pedir_punto_por_comando(doc, mensaje, pythoncom)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"No se pudo iniciar la selección de punto en el CAD: {exc}") from exc
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise RuntimeError(f"El CAD devolvió un punto con formato inesperado: {pt!r}")
        return (float(pt[0]), float(pt[1]))
    finally:
        pythoncom.CoUninitialize()


def estado() -> str:
    """Prueba de conexión para el botón 'Detectar CAD'."""
    app, pid = detectar()
    ver = ""
    try:
        ver = app.Version
    except Exception:
        pass
    doc_name = ""
    try:
        doc_name = app.ActiveDocument.Name
    except Exception:
        pass
    return f"{pid}  |  versión {ver}  |  documento: {doc_name or '(ninguno)'}"
