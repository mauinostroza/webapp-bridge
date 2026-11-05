"""Bandeja del sistema para ``SAP2000Bridge.exe``.

Este módulo está deliberadamente partido en dos mitades, y esa frontera es lo
que hace que se pueda probar en este entorno Linux sin Windows:

1. **Lógica de estado, Python puro** — la cola de avisos de emparejamiento,
   el filtrado de ``list_pairings()`` para pintarlo, el texto y las filas de
   la ventana de estado, la geometría de las ventanas (anclaje a la esquina,
   banner centrado), la decisión de cierre del banner y la selección de
   conexiones a revocar. Ningún import de ``win32*``/``pythoncom`` a nivel de
   módulo ni dentro de estas funciones: son las que llevan cobertura real de
   pruebas.
2. **Capa Win32** (``TrayApp``) — todo lo que de verdad dibuja ventanas.
   Cada import de ``win32gui``/``win32api``/``win32con`` vive dentro de un
   método, nunca a nivel de módulo, para que ``import bridge.tray`` no
   reviente en Linux/macOS. Esta capa no se ejecuta en este entorno: sólo se
   puede comprobar en un equipo Windows real (Fase 7 del plan). Todo lo que
   pinta o crea ventanas es defensivo: un fallo visual se registra y no mata
   el puente.
"""

from __future__ import annotations

import logging
import queue
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bridge.pairing import PairingRequest

logger = logging.getLogger(__name__)

# Cuántas solicitudes de emparejamiento se mantienen en cola mientras el aviso
# de la actual sigue pendiente de que una persona lo responda. `pairing.py` ya
# limita a 5 pendientes en total (`MAX_PENDING_TOTAL`) y una por origen, así
# que 5 es un límite generoso que nunca descarta nada que `pairing.py` no
# hubiera descartado ya por su cuenta — sólo protege la cola del hilo de la
# ventana ante un error de programación (un llamador que no drena la cola).
# Superarlo indica que algo no está mostrando ni cerrando avisos: se descartan
# los más antiguos en silencio en vez de crecer sin límite.
PAIRING_QUEUE_MAXSIZE = 5

ICON_FILENAME = "sap2000_bridge.ico"

# Identificador de GetStockObject para la fuente estándar de los controles
# (pywin32 no lo exporta en win32con; es la constante DEFAULT_GUI_FONT de Win32).
_DEFAULT_GUI_FONT = 17

# Margen (a 96 DPI) entre una ventana anclada y el borde del área de trabajo.
WINDOW_MARGIN_PX = 12

# Banner de arranque: cuánto dura como máximo y cada cuánto se comprueba.
SPLASH_DURATION_MS = 2500
# Tiempo mínimo visible: si el servidor arranca casi al instante, el banner igual se alcanza a leer.
SPLASH_MIN_VISIBLE_MS = 1500
SPLASH_TIMER_INTERVAL_MS = 100

# Paleta sobria de las ventanas, como (R, G, B).
THEME_RGB = {
    "splash_bg": (30, 41, 59),
    "splash_text": (248, 250, 252),
    "splash_muted": (148, 163, 184),
    "status_bg": (248, 249, 251),
    "status_panel": (232, 237, 243),
    "status_divider": (203, 213, 225),
    "status_text": (30, 41, 59),
    "status_muted": (100, 116, 139),
}

# Indicador de la ventana de estado: nombre lógico -> color (verde/ámbar/rojo).
STATUS_INDICATOR_RGB = {
    "verde": (46, 160, 67),
    "ambar": (210, 153, 34),
    "rojo": (207, 34, 46),
}

# Etiquetas de la columna de datos de la ventana de estado, en orden.
STATUS_LABELS = (
    "Estado",
    "Puerto",
    "Modelo abierto",
    "Versión SAP2000",
    "Conexiones emparejadas",
    "Última actualización",
)


def tray_icon_path() -> Path:
    """Ruta del .ico del puente (el mismo del .exe), según se corra congelado o no.

    En el ejecutable de PyInstaller el archivo se empaqueta con ``--add-data``
    en ``assets`` dentro de ``sys._MEIPASS``; en desarrollo está en la carpeta
    ``assets`` de la raíz del repositorio. Pura: no toca el disco ni Win32.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / ICON_FILENAME  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "assets" / ICON_FILENAME


class PairingRequestQueue:
    """Cola thread-safe de solicitudes de emparejamiento pendientes de aviso.

    ``notify_pairing_request`` la alimenta desde el hilo de uvicorn/pairing
    (vía ``pairing.set_notifier``); el hilo de la ventana la vacía de a una,
    en el orden de llegada, mostrando un aviso por vez tal como pide la
    sección C del plan ("un aviso a la vez").
    """

    def __init__(self, maxsize: int = PAIRING_QUEUE_MAXSIZE):
        self._queue: queue.Queue[PairingRequest] = queue.Queue(maxsize=maxsize)

    def put(self, request: PairingRequest) -> None:
        """Encola ``request``; si está llena, descarta la más antigua.

        La cola llena es un caso ya degenerado (ver docstring del módulo):
        antes que bloquear al hilo de uvicorn o perder la solicitud nueva sin
        aviso, se libera un hueco quitando la más antigua de la cola.
        """
        while True:
            try:
                self._queue.put_nowait(request)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    continue

    def get_nowait(self) -> PairingRequest | None:
        """Saca la siguiente solicitud en orden, o ``None`` si no hay ninguna."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()


def notify_pairing_request(pairing_queue: PairingRequestQueue, request: PairingRequest) -> None:
    """Callback a conectar con ``pairing.set_notifier``.

    Se pasa ``pairing_queue`` explícita (en vez de un estado global de
    módulo) para que las pruebas puedan usar una cola propia sin compartir
    estado entre casos.
    """
    pairing_queue.put(request)


def visible_pairings(pairings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filas listas para pintar en "Conexiones emparejadas", sin ``secret_hash``.

    Pura: recibe el resultado de ``pairing.list_pairings()`` y devuelve una
    lista nueva con sólo ``id``/``label``/``origin``/``created_at``/
    ``last_used_at`` — nunca el hash, que es un dato interno sin valor para
    quien mira la bandeja y no debería mostrarse por descuido.
    """
    keep = ("id", "label", "origin", "created_at", "last_used_at")
    return [{key: row.get(key) for key in keep} for row in pairings]


def status_text(status: dict[str, Any] | None, error: Exception | None = None) -> str:
    """Texto de una línea del estado, dado el resultado de ``session.status()``.

    Cubre las tres formas de retorno documentadas en el plan más el caso de
    excepción capturada (``session.status()`` puede lanzar ``SapComError``,
    p. ej. fuera de Windows) — pura, sin tocar ninguna ventana. La ventana de
    estado ya no usa este texto: pinta las filas de ``status_rows()``; se
    conserva como resumen de una línea.
    """
    if error is not None:
        return f"SAP2000: error al consultar el estado ({error})"
    if status is None:
        return "SAP2000: error al consultar el estado"
    if not status.get("connected"):
        reason = status.get("reason")
        if reason:
            return f"SAP2000: sin conexión ({reason})"
        return "SAP2000: sin conexión"
    model_path = status.get("model_path") or "(sin ruta)"
    return f"SAP2000: conectado — {model_path}"


def status_rows(
    status: dict[str, Any] | None,
    error: Exception | None = None,
    *,
    port: int | str,
    paired_count: int | None,
    updated_at: str | None,
) -> list[tuple[str, str]]:
    """Filas ``(etiqueta, valor)`` de la ventana de estado, en el orden de ``STATUS_LABELS``.

    Pura: recibe el dict de ``session.status()`` (o la excepción capturada) y
    los datos que la ventana ya tiene (puerto, nº de emparejadas, hora de la
    última consulta). Siempre devuelve exactamente seis filas, también si el
    estado es ``None`` o hay error, para que la columna no cambie de forma.
    ``paired_count=None`` significa que no se pudo leer la lista (se muestra "—").
    """
    failed = error is not None or status is None
    data: dict[str, Any] = {} if failed else status
    if failed:
        estado = "Error al consultar el estado"
        if error is not None:
            estado = f"{estado} ({error})"
    elif data.get("connected"):
        estado = "Conectado"
    elif data.get("reason"):
        estado = f"Sin conexión ({data['reason']})"
    else:
        estado = "Sin conexión"

    connected = not failed and bool(data.get("connected"))
    modelo = str(data.get("model_path") or "(sin ruta)") if connected else "—"
    version = str(data["version"]) if not failed and data.get("version") else "—"
    emparejadas = str(paired_count) if isinstance(paired_count, int) else "—"
    return [
        (STATUS_LABELS[0], estado),
        (STATUS_LABELS[1], str(port)),
        (STATUS_LABELS[2], modelo),
        (STATUS_LABELS[3], version),
        (STATUS_LABELS[4], emparejadas),
        (STATUS_LABELS[5], updated_at or "—"),
    ]


def status_indicator(status: dict[str, Any] | None, error: Exception | None = None) -> str:
    """Color del punto de estado: ``"verde"``, ``"ambar"`` o ``"rojo"``.

    - Verde: SAP2000 conectado.
    - Rojo: no se pudo consultar (excepción o ``None``) o hubo un fallo real
      de conexión (``reason``, p. ej. "COM connection lost").
    - Ámbar: sin conexión sin fallo, es decir, SAP2000 simplemente no está
      abierto o aún no se ha conectado.
    """
    if error is not None or status is None:
        return "rojo"
    if status.get("connected"):
        return "verde"
    if status.get("reason"):
        return "rojo"
    return "ambar"


def hora_actualizacion(timestamp: float) -> str:
    """Hora local ``HH:MM:SS`` de una marca de tiempo (segundos desde epoch)."""
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def pairing_dialog_text(request: PairingRequest) -> str:
    """Texto exacto del aviso de emparejamiento, dada la solicitud pendiente.

    Incluye el origen y el código corto (``confirm_code``) para que quien
    acepta pueda compararlo con el que muestra la web, en vez de aceptar por
    reflejo mirando sólo el origen — pura, sin tocar ninguna ventana.
    """
    return (
        "Un sitio web solicita emparejarse con este puente SAP2000.\n\n"
        f"Origen: {request.origin}\n"
        f"Código: {request.confirm_code}\n\n"
        "Acéptalo sólo si el código coincide con el que muestra la página web."
    )


PAIRING_DIALOG_TITLE = "Solicitud de emparejamiento"
PAIRING_CODE_LABEL = "Código de confirmación"
PAIRING_INSTRUCTION = "Acepta solo si este código coincide con el que muestra la página web."


def pairing_dialog_fields(request: PairingRequest) -> dict[str, str]:
    """Campos estructurados del aviso (origen y código) para la ventana rediseñada."""
    return {"origin": str(request.origin), "code": str(request.confirm_code)}


def dpi_scale(dpi: int) -> float:
    """Factor de escala respecto a 96 DPI (100 %); un DPI no válido cuenta como 96."""
    try:
        value = int(dpi)
    except (TypeError, ValueError):
        return 1.0
    if value <= 0:
        return 1.0
    return value / 96.0


def font_pixel_height(points: float, dpi: int) -> int:
    """Alto de fuente en píxeles (negativo = alto de carácter, como espera LOGFONT)."""
    return -int(round(points * max(int(dpi), 1) / 72.0))


def get_system_dpi() -> int:
    """DPI del sistema vía ctypes (GetDpiForSystem, luego GetDeviceCaps, luego 96)."""
    if sys.platform != "win32":
        return 96
    import ctypes

    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        logger.debug("GetDpiForSystem no disponible", exc_info=True)
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        hdc = user32.GetDC(None)
        if hdc:
            try:
                dpi = int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            finally:
                user32.ReleaseDC(None, hdc)
            if dpi > 0:
                return dpi
    except Exception:
        logger.debug("GetDeviceCaps(LOGPIXELSX) falló", exc_info=True)
    return 96


def enable_dpi_awareness() -> bool:
    """Marca el proceso como consciente de DPI; nunca lanza.

    Intenta per-monitor v2, luego ``SetProcessDpiAwareness(2)`` y por último
    ``SetProcessDPIAware``. Si ya estaba fijado (p. ej. por manifiesto) las
    llamadas fallan sin efecto y no pasa nada. Devuelve si alguna tuvo éxito.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        logger.debug("SetProcessDpiAwarenessContext no disponible", exc_info=True)
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:
        logger.debug("SetProcessDpiAwareness no disponible", exc_info=True)
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        logger.debug("SetProcessDPIAware no disponible", exc_info=True)
    return False


def _scaled_rect(rect: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    """Escala ``(x, y, w, h)`` redondeando los bordes, no el ancho/alto.

    Así dos rectángulos que no se tocaban en la base tampoco se solapan
    después de escalar (el redondeo de bordes es monótono).
    """
    x, y, w, h = rect
    x0, y0 = int(round(x * scale)), int(round(y * scale))
    x1, y1 = int(round((x + w) * scale)), int(round((y + h) * scale))
    return (x0, y0, x1 - x0, y1 - y0)


def anclar_esquina(area: tuple[int, int, int, int], ancho: int, alto: int, margen: int) -> tuple[int, int]:
    """Posición ``(x, y)`` de una ventana ``ancho`` x ``alto`` pegada a la esquina inferior derecha.

    ``area`` es ``(left, top, right, bottom)`` del área de trabajo (la pantalla
    menos la barra de tareas); puede empezar en cualquier punto, p. ej. si la
    barra está arriba o a la izquierda, o si el monitor tiene coordenadas
    negativas. ``margen`` separa la ventana de los bordes del área.

    Si la ventana es más grande que el área, se alinea con la esquina superior
    izquierda del área en vez de salirse por la izquierda o por arriba.
    """
    left, top, right, bottom = area
    x = max(left, right - ancho - margen)
    y = max(top, bottom - alto - margen)
    return x, y


def centrar_en_area(area: tuple[int, int, int, int], ancho: int, alto: int) -> tuple[int, int]:
    """Posición ``(x, y)`` que centra ``ancho`` x ``alto`` en ``area`` (``left, top, right, bottom``).

    Si no cabe, se alinea con la esquina superior izquierda del área.
    """
    left, top, right, bottom = area
    x = max(left, left + (right - left - ancho) // 2)
    y = max(top, top + (bottom - top - alto) // 2)
    return x, y


def splash_layout(scale: float) -> dict[str, Any]:
    """Geometría (px, área cliente) del banner de arranque a ``scale``.

    ``icon`` es el ícono grande; ``title``, ``subtitle`` y ``version`` son
    los tres bloques de texto centrados debajo.
    """
    base = {
        "icon": (176, 22, 128, 128),
        "title": (24, 156, 432, 40),
        "subtitle": (24, 200, 432, 24),
        "version": (24, 230, 432, 20),
    }
    layout: dict[str, Any] = {name: _scaled_rect(rect, scale) for name, rect in base.items()}
    layout["width"] = int(round(480 * scale))
    layout["height"] = int(round(270 * scale))
    return layout


def splash_text(version: str | None) -> dict[str, str]:
    """Textos del banner: nombre, "Abriendo el puente…" y la versión si se conoce.

    Sin versión el campo ``version`` queda vacío y no se pinta nada.
    """
    clean = str(version).strip() if version else ""
    return {
        "title": "SAP2000 Bridge",
        "subtitle": "Abriendo el puente…",
        "version": f"Versión {clean}" if clean else "",
    }


def splash_should_close(
    elapsed_ms: float,
    ready: bool,
    duration_ms: float = SPLASH_DURATION_MS,
    min_visible_ms: float = SPLASH_MIN_VISIBLE_MS,
) -> bool:
    """El banner se cierra al cumplirse ``duration_ms`` o cuando el puente escucha, pero nunca antes de
    ``min_visible_ms`` (para que alcance a leerse)."""
    if elapsed_ms >= duration_ms:
        return True
    return bool(ready) and elapsed_ms >= min(min_visible_ms, duration_ms)


def pairing_layout(scale: float) -> dict[str, Any]:
    """Geometría (px, área cliente) de la ventana de emparejamiento a ``scale``.

    ``width``/``height`` son el área cliente; el resto son rectángulos
    ``(x, y, w, h)`` de cada control. Pura: no toca Win32.
    """
    base = {
        "title": (24, 18, 412, 28),
        "origin": (24, 50, 412, 24),
        "code_label": (24, 90, 412, 22),
        "code": (24, 114, 412, 76),
        "instruction": (24, 204, 412, 44),
        "accept": (72, 266, 150, 40),
        "reject": (238, 266, 150, 40),
    }
    layout: dict[str, Any] = {name: _scaled_rect(rect, scale) for name, rect in base.items()}
    layout["width"] = int(round(460 * scale))
    layout["height"] = int(round(330 * scale))
    return layout


def status_layout(scale: float) -> dict[str, Any]:
    """Geometría (px, área cliente) de la ventana de estado a ``scale``.

    Cabecera (``icon_small`` y ``title``), línea divisoria, un panel a la
    izquierda con el ícono grande (``watermark``) y la columna de datos a la
    derecha (``label_column`` y ``value_column``, seis filas de ``row_height``).
    ``footer`` es la línea de ayuda inferior.
    """
    base = {
        "icon_small": (16, 14, 32, 32),
        "title": (58, 18, 446, 28),
        "divider": (16, 56, 488, 2),
        "panel": (0, 66, 164, 226),
        "watermark": (18, 104, 128, 128),
        "label_column": (184, 76, 150, 216),
        "value_column": (340, 76, 164, 216),
        "footer": (16, 304, 488, 22),
    }
    layout: dict[str, Any] = {name: _scaled_rect(rect, scale) for name, rect in base.items()}
    layout["width"] = int(round(520 * scale))
    layout["height"] = int(round(340 * scale))
    layout["row_height"] = int(round(36 * scale))
    return layout


def pairings_layout(scale: float, has_pairings: bool) -> dict[str, Any]:
    """Geometría de la ventana de conexiones emparejadas (área cliente, px).

    Los dos botones van en la misma fila: "select_all" (Seleccionar todas) y
    "revoke" (Revocar seleccionadas, de 190 px para que quepa el texto a 10 pt).
    """
    if has_pairings:
        base = {
            "list": (16, 16, 388, 170),
            "select_all": (16, 198, 130, 36),
            "revoke": (158, 198, 190, 36),
        }
        height = 252
    else:
        base = {
            "list": (16, 16, 388, 110),
            "hint": (16, 138, 388, 64),
            "select_all": (16, 214, 130, 36),
            "revoke": (158, 214, 190, 36),
        }
        height = 266
    layout: dict[str, Any] = {name: _scaled_rect(rect, scale) for name, rect in base.items()}
    layout["width"] = int(round(420 * scale))
    layout["height"] = int(round(height * scale))
    return layout


def pairing_indices_to_revoke(rows: list[dict[str, Any]], selected: list[int]) -> list[int]:
    """Índices de ``rows`` que se deben revocar, en orden ascendente.

    ``selected`` son los índices marcados en la lista. Se descartan los que
    están fuera de rango, los que no son enteros y las filas sin ``id`` (la
    fila de "Sin conexiones emparejadas" no es una conexión real). Pura.
    """
    limit = len(rows)
    valid = {index for index in selected if isinstance(index, int) and 0 <= index < limit}
    return [index for index in sorted(valid) if rows[index].get("id")]


def pairing_ids_to_revoke(rows: list[dict[str, Any]], selected: list[int]) -> list[str]:
    """Ids de las conexiones marcadas que se deben revocar (ver ``pairing_indices_to_revoke``)."""
    return [str(rows[index]["id"]) for index in pairing_indices_to_revoke(rows, selected)]


def revoke_confirm_message(count: int) -> str | None:
    """Pregunta de confirmación para revocar ``count`` conexiones, o ``None`` si no hace falta.

    Sólo se pide confirmación cuando son varias; revocar una sola sigue siendo
    un clic, como antes.
    """
    if count <= 1:
        return None
    return f"¿Revocar {count} conexiones?"


def resolve_pairing_request(
    pairing_module: Any, request_id: str, *, approve: bool, label: str | None = None
) -> None:
    """Aprueba o rechaza ``request_id``, tragando los casos "ya no hay nada que hacer".

    ``pairing_module`` se recibe como parámetro (en vez de importar
    ``bridge.pairing`` fijo) para poder pasar un doble en las pruebas.

    - Aprobar: ``KeyError`` (id inexistente) y ``ValueError`` (ya no
      ``pending``: vencida, ya aprobada o rechazada) se tratan igual —
      cerrar el aviso sin más, tal como exige el plan.
    - Rechazar: sólo ``KeyError``, porque ``pairing.deny`` no lanza
      ``ValueError`` (es un no-op silencioso si ya no está ``pending``).
    """
    if approve:
        try:
            pairing_module.approve(request_id, label=label)
        except (KeyError, ValueError):
            return
    else:
        try:
            pairing_module.deny(request_id)
        except KeyError:
            return


def reject_on_close(pairing_module: Any, request: PairingRequest | None) -> bool:
    """Cerrar la ventana con la X (o Alt+F4) equivale a Rechazar.

    Devuelve True si había una solicitud que rechazar. Si la solicitud ya no
    estaba pendiente, ``deny`` no hace nada, así que es seguro llamarla siempre.
    """
    if request is None:
        return False
    resolve_pairing_request(pairing_module, request.id, approve=False)
    return True


def menu_layout(extra_labels: list[str]) -> list[tuple[int, str] | None]:
    """Orden del menú contextual de la bandeja; ``None`` es un separador.

    Las entradas extra (p. ej. "Abrir Workbench" del lanzador todo en uno) van
    primero, con identificadores desde ``_MENU_EXTRA_BASE`` en el mismo orden.
    """
    entries: list[tuple[int, str] | None] = [
        (_MENU_EXTRA_BASE + index, label) for index, label in enumerate(extra_labels)
    ]
    if entries:
        entries.append(None)
    entries += [
        (_MENU_STATUS, "Estado…"),
        (_MENU_PAIRINGS, "Conexiones emparejadas…"),
        (_MENU_LOGS, "Abrir carpeta de registros"),
        None,
        (_MENU_EXIT, "Salir"),
    ]
    return entries


# ---------------------------------------------------------------------------
# Capa Win32. Nada de lo que sigue se importa a nivel de módulo: cada símbolo
# win32* se resuelve dentro de los métodos que lo usan, para que este archivo
# siga siendo importable en Linux/macOS (ver docstring del módulo).
# ---------------------------------------------------------------------------

WM_APP_PAIRING = 0x8000 + 1  # WM_APP + 1: cruce seguro de hilo -> ventana.
_TIMER_STATUS_ID = 1
_TIMER_SPLASH_ID = 2
_STATUS_INTERVAL_MS = 2000

_MENU_STATUS = 1001
_MENU_PAIRINGS = 1002
_MENU_EXIT = 1003
_MENU_LOGS = 1004
_MENU_EXTRA_BASE = 1100  # entradas extra del lanzador "todo en uno": 1100, 1101, ...

_ID_PAIRING_TEXT = 3001
_ID_PAIRING_ACCEPT = 3002
_ID_PAIRING_REJECT = 3003

_ID_PAIRINGS_LIST = 4001
_ID_PAIRINGS_REVOKE = 4002
_ID_PAIRINGS_SELECT_ALL = 4003

_NULL_PEN = 8  # GetStockObject(NULL_PEN): contorno vacío para dibujar el punto de estado


class TrayApp:
    """Icono de bandeja + ventanas del puente SAP2000. Sólo funciona en Windows.

    Parameters
    ----------
    port:
        Puerto donde escucha el puente (se muestra en "Estado…").
    get_status:
        Callable sin argumentos -> dict de ``session.status()`` (puede lanzar).
    get_pairings:
        Callable sin argumentos -> lista de ``pairing.list_pairings()``.
    on_revoke:
        Callable(pairing_id) para revocar cada conexión marcada en "Conexiones emparejadas".
    on_exit:
        Callable sin argumentos, invocado al elegir "Salir" antes de
        ``PostQuitMessage(0)``.
    """

    def __init__(
        self,
        port: int,
        get_status: Callable[[], dict[str, Any]],
        get_pairings: Callable[[], list[dict[str, Any]]],
        on_revoke: Callable[[str], None],
        on_exit: Callable[[], None],
        extra_menu_items: list[tuple[str, Callable[[], None]]] | None = None,
        on_open_logs: Callable[[], None] | None = None,
    ):
        if sys.platform != "win32":
            raise RuntimeError("bridge.tray.TrayApp requires Windows.")
        self.port = port
        self.get_status = get_status
        self.get_pairings = get_pairings
        self.on_revoke = on_revoke
        self.on_exit = on_exit
        self.extra_menu_items = list(extra_menu_items or [])
        self.on_open_logs = on_open_logs
        enable_dpi_awareness()  # no-op si ya estaba fijado; antes de crear ventanas
        self._pairing_queue = PairingRequestQueue()
        self._current_request: PairingRequest | None = None
        self._hwnd = None
        self._status_hwnd = None
        self._pairing_hwnd = None
        self._pairings_hwnd = None
        self._splash_hwnd = None
        self._pairings_rows: list[dict[str, Any]] = []
        self._pairing_fonts: list[int] = []
        self._status_fonts: list[int] = []
        self._pairings_fonts: list[int] = []
        self._splash_fonts: list[int] = []
        self._status_font_map: dict[str, int] = {}
        self._splash_font_map: dict[str, int] = {}
        self._status_layout: dict[str, Any] | None = None
        self._status_rows: list[tuple[str, str]] = []
        self._status_color = "ambar"
        self._splash_layout: dict[str, Any] | None = None
        self._splash_text: dict[str, str] = {}
        self._splash_is_ready: Callable[[], bool] | None = None
        self._splash_started = 0.0
        # Iconos cargados desde el .ico: el grande (banner y marca de agua) y el
        # pequeño de la cabecera. Se liberan al salir (ver `_release_owned_icons`).
        self._big_icon = 0
        self._header_icon = 0
        self._owned_icons: list[int] = []
        self._hicon = 0
        self._icon_is_owned = False
        self._create_window()
        self._add_tray_icon()

    # -- Ventana oculta principal (icono + menú) -------------------------

    def _create_window(self) -> None:
        import win32api
        import win32con
        import win32gui

        message_map = {
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_TIMER: self._on_timer,
            win32con.WM_COMMAND: self._on_command,
            WM_APP_PAIRING: self._on_tray_message,
        }
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "SAP2000BridgeTrayWindow"
        wc.lpfnWndProc = message_map
        class_atom = win32gui.RegisterClass(wc)
        self._hwnd = win32gui.CreateWindow(
            class_atom, "SAP2000 Bridge", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )
        # pywin32 no expone SetTimer en win32gui: se llama a user32 directo.
        # El WM_TIMER resultante lo atiende self._on_timer vía message_map.
        self._set_timer(self._hwnd, _TIMER_STATUS_ID, _STATUS_INTERVAL_MS)

    @staticmethod
    def _set_timer(hwnd, timer_id: int, interval_ms: int) -> None:
        """Arranca un temporizador de ventana vía user32 (pywin32 no lo expone)."""
        import ctypes
        from ctypes import wintypes

        set_timer = ctypes.windll.user32.SetTimer
        set_timer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        set_timer(hwnd, timer_id, interval_ms, None)

    def _load_tray_icon(self) -> tuple[int, bool]:
        """Devuelve ``(HICON, propio)``; nunca lanza (cae al icono genérico).

        ``propio`` es True si el icono hay que destruirlo al salir; el icono
        compartido de ``LoadIcon`` es del sistema y no se destruye.
        """
        import win32api
        import win32con
        import win32gui

        try:
            path = tray_icon_path()
            width = win32api.GetSystemMetrics(win32con.SM_CXSMICON)
            height = win32api.GetSystemMetrics(win32con.SM_CYSMICON)
            hicon = win32gui.LoadImage(
                0, str(path), win32con.IMAGE_ICON, width, height, win32con.LR_LOADFROMFILE
            )
            if hicon:
                return hicon, True
        except Exception:
            logger.warning("No se pudo cargar el icono de la bandeja desde archivo", exc_info=True)
        if getattr(sys, "frozen", False):
            try:
                large, small = win32gui.ExtractIconEx(sys.executable, 0, 1)
                if small:
                    for handle in large:
                        win32gui.DestroyIcon(handle)
                    return small[0], True
                if large:
                    return large[0], True
            except Exception:
                logger.warning("No se pudo extraer el icono del ejecutable", exc_info=True)
        try:
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION), False
        except Exception:
            return 0, False

    def _add_tray_icon(self) -> None:
        import win32gui

        hicon, self._icon_is_owned = self._load_tray_icon()
        self._hicon = hicon
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self._hwnd, 0, flags, WM_APP_PAIRING, hicon, "SAP2000 Bridge")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _release_tray_icon(self) -> None:
        """Libera el HICON propio (los iconos del sistema de LoadIcon no se destruyen)."""
        hicon = getattr(self, "_hicon", 0)
        self._hicon = 0
        if not hicon or not self._icon_is_owned:
            return
        try:
            import win32gui

            win32gui.DestroyIcon(hicon)
        except Exception:
            logger.debug("DestroyIcon falló", exc_info=True)

    def notify_pairing_request(self, request: PairingRequest) -> None:
        """Punto de entrada seguro entre hilos: encola y despierta la ventana.

        Nunca toca la UI directamente — sólo deposita en la cola pura
        (``PairingRequestQueue``, thread-safe) y avisa a la ventana con
        ``PostMessage`` para que sea el propio hilo de mensajes quien procese
        el aviso, tal como exige el plan.
        """
        import win32gui

        self._pairing_queue.put(request)
        if self._hwnd:
            win32gui.PostMessage(self._hwnd, WM_APP_PAIRING, 0, 0)

    def _on_tray_message(self, hwnd, msg, wparam, lparam):
        import win32con

        # `lparam` trae el mensaje real del icono (clic, etc.) cuando viene
        # de Shell_NotifyIcon; un `PostMessage` propio (ver
        # `notify_pairing_request`) llega con `lparam=0` y significa
        # "hay una solicitud nueva en la cola, revísala".
        if lparam == 0:
            self._maybe_show_next_pairing()
        elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
            self._show_context_menu()
        return 0

    def _maybe_show_next_pairing(self) -> None:
        if self._current_request is None:
            self._current_request = self._pairing_queue.get_nowait()
            if self._current_request is not None:
                self._show_pairing_dialog(self._current_request)

    # -- Utilidades de ventana: DPI, fuentes, controles, iconos y área ----

    def _create_font(self, face: str, points: float, weight: int, dpi: int) -> tuple[int, bool]:
        """Devuelve ``(HFONT, propia)``; cae a la fuente de GUI del sistema si falla."""
        import win32gui

        try:
            logfont = win32gui.LOGFONT()
            logfont.lfHeight = font_pixel_height(points, dpi)
            logfont.lfWeight = weight
            logfont.lfFaceName = face
            font = int(win32gui.CreateFontIndirect(logfont))
            if font:
                return font, True
        except Exception:
            logger.warning("No se pudo crear la fuente %s", face, exc_info=True)
        return int(win32gui.GetStockObject(_DEFAULT_GUI_FONT)), False

    def _make_fonts(self, specs: dict[str, tuple[str, float, int]], dpi: int):
        """Crea fuentes ``{nombre: (cara, puntos, peso)}``; devuelve (fuentes, propias)."""
        fonts: dict[str, int] = {}
        owned: list[int] = []
        for name, (face, points, weight) in specs.items():
            font, is_owned = self._create_font(face, points, weight, dpi)
            fonts[name] = font
            if is_owned:
                owned.append(font)
        return fonts, owned

    @staticmethod
    def _delete_fonts(owned: list[int]) -> None:
        import win32gui

        for font in owned:
            try:
                win32gui.DeleteObject(font)
            except Exception:
                logger.debug("DeleteObject falló", exc_info=True)
        owned.clear()

    @staticmethod
    def _outer_size(client_w: int, client_h: int, style: int, ex_style: int) -> tuple[int, int]:
        """Tamaño de ventana que deja un área cliente ``client_w`` x ``client_h``."""
        import ctypes
        from ctypes import wintypes

        try:
            rect = wintypes.RECT(0, 0, client_w, client_h)
            if ctypes.windll.user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style):
                return rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            logger.debug("AdjustWindowRectEx falló", exc_info=True)
        return client_w + 16, client_h + 39

    def _work_area(self) -> tuple[int, int, int, int]:
        """Área de trabajo ``(left, top, right, bottom)`` del monitor donde está el cursor.

        Es la pantalla menos la barra de tareas. Orden de intento: GetMonitorInfo
        del monitor del cursor, luego SPI_GETWORKAREA (monitor principal) y por
        último el tamaño completo de la pantalla. Nunca lanza.
        """
        import win32api
        import win32con

        try:
            point = win32api.GetCursorPos()
            hmonitor = win32api.MonitorFromPoint(point, win32con.MONITOR_DEFAULTTONEAREST)
            left, top, right, bottom = win32api.GetMonitorInfo(hmonitor)["Work"]
            return int(left), int(top), int(right), int(bottom)
        except Exception:
            logger.debug("GetMonitorInfo no disponible", exc_info=True)
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            spi_getworkarea = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(spi_getworkarea, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            logger.debug("SPI_GETWORKAREA no disponible", exc_info=True)
        try:
            width = int(win32api.GetSystemMetrics(win32con.SM_CXSCREEN))
            height = int(win32api.GetSystemMetrics(win32con.SM_CYSCREEN))
            return 0, 0, width, height
        except Exception:
            logger.debug("GetSystemMetrics no disponible", exc_info=True)
        return 0, 0, 1280, 720

    def _file_icon(self, size: int) -> int:
        """HICON del .ico del puente a ``size`` px, o 0 si no se puede cargar. Nunca lanza.

        Los handles se guardan en ``_owned_icons`` para liberarlos al salir.
        """
        import win32con
        import win32gui

        try:
            hicon = win32gui.LoadImage(
                0, str(tray_icon_path()), win32con.IMAGE_ICON, size, size, win32con.LR_LOADFROMFILE
            )
            if hicon:
                self._owned_icons.append(int(hicon))
                return int(hicon)
        except Exception:
            logger.debug("No se pudo cargar el icono grande del .ico", exc_info=True)
        return 0

    def _release_owned_icons(self) -> None:
        """Destruye los iconos cargados desde el .ico (al salir)."""
        import win32gui

        for hicon in self._owned_icons:
            try:
                win32gui.DestroyIcon(hicon)
            except Exception:
                logger.debug("DestroyIcon falló", exc_info=True)
        self._owned_icons.clear()
        self._big_icon = 0
        self._header_icon = 0

    def _create_fixed_window(
        self,
        class_name: str,
        title: str,
        message_map: dict,
        client_size: tuple[int, int],
        ex_style: int = 0,
        scale: float = 1.0,
    ):
        """Crea una ventana fija (no redimensionable) anclada a la esquina inferior derecha.

        Queda junto a la bandeja, fuera del centro de la pantalla, para no tapar
        el navegador. Devuelve ``(hwnd, hinstance)``.
        """
        import win32api
        import win32con
        import win32gui

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = class_name
        wc.lpfnWndProc = message_map
        wc.hbrBackground = win32con.COLOR_BTNFACE + 1
        try:
            class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            class_atom = class_name
        style = win32con.WS_CAPTION | win32con.WS_SYSMENU | win32con.WS_VISIBLE
        width, height = self._outer_size(client_size[0], client_size[1], style, ex_style)
        x, y = anclar_esquina(self._work_area(), width, height, round(WINDOW_MARGIN_PX * scale))
        hwnd = win32gui.CreateWindowEx(
            ex_style, class_atom, title, style, x, y, width, height, 0, 0, wc.hInstance, None
        )
        return hwnd, wc.hInstance

    def _create_popup_window(self, class_name: str, message_map: dict, size: tuple[int, int], ex_style: int):
        """Ventana sin barra de título, centrada en el área de trabajo. Devuelve el ``hwnd``."""
        import win32api
        import win32con
        import win32gui

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = class_name
        wc.lpfnWndProc = message_map
        wc.hbrBackground = 0  # el propio WM_PAINT pinta todo el fondo
        try:
            class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            class_atom = class_name
        width, height = size
        x, y = centrar_en_area(self._work_area(), width, height)
        return win32gui.CreateWindowEx(
            ex_style,
            class_atom,
            "SAP2000 Bridge",
            win32con.WS_POPUP | win32con.WS_VISIBLE,
            x,
            y,
            width,
            height,
            0,
            0,
            wc.hInstance,
            None,
        )

    @staticmethod
    def _add_control(parent, hinstance, class_name, text, style, rect, control_id, font):
        """Crea un control hijo en ``rect`` y le aplica ``font`` (WM_SETFONT)."""
        import win32con
        import win32gui

        x, y, w, h = rect
        child = win32gui.CreateWindow(
            class_name,
            text,
            win32con.WS_CHILD | win32con.WS_VISIBLE | style,
            x,
            y,
            w,
            h,
            parent,
            control_id,
            hinstance,
            None,
        )
        win32gui.SendMessage(child, win32con.WM_SETFONT, font, True)
        return child

    # -- Dibujo propio (GDI) ----------------------------------------------

    @staticmethod
    def _fill_rect(hdc, rect: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> None:
        import win32api
        import win32gui

        x, y, w, h = rect
        brush = win32gui.CreateSolidBrush(win32api.RGB(*rgb))
        try:
            win32gui.FillRect(hdc, (x, y, x + w, y + h), brush)
        finally:
            win32gui.DeleteObject(brush)

    @staticmethod
    def _draw_text(hdc, text: str, rect: tuple[int, int, int, int], font: int, rgb, flags: int) -> None:
        import win32api
        import win32con
        import win32gui

        if not text:
            return
        x, y, w, h = rect
        win32gui.SelectObject(hdc, font)
        win32gui.SetTextColor(hdc, win32api.RGB(*rgb))
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        win32gui.DrawText(hdc, text, -1, (x, y, x + w, y + h), flags)

    @staticmethod
    def _draw_icon(hdc, rect: tuple[int, int, int, int], hicon: int) -> None:
        import win32con
        import win32gui

        x, y, w, h = rect
        win32gui.DrawIconEx(hdc, x, y, hicon, w, h, 0, 0, win32con.DI_NORMAL)

    @staticmethod
    def _draw_indicator(hdc, x: int, center_y: int, rgb: tuple[int, int, int], size: int = 12) -> None:
        """Punto de color (círculo relleno sin contorno)."""
        import win32api
        import win32gui

        brush = win32gui.CreateSolidBrush(win32api.RGB(*rgb))
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, win32gui.GetStockObject(_NULL_PEN))
        try:
            win32gui.Ellipse(hdc, x, center_y - size // 2, x + size, center_y + size // 2)
        finally:
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(brush)

    def _paint_window(self, hwnd, draw: Callable[[Any], None]) -> int:
        """Envuelve un WM_PAINT: BeginPaint, ``draw(hdc)`` y EndPaint, sin lanzar nunca."""
        import win32gui

        try:
            hdc, paint = win32gui.BeginPaint(hwnd)
        except Exception:
            logger.debug("BeginPaint falló", exc_info=True)
            return 0
        try:
            draw(hdc)
        except Exception:
            logger.debug("Pintado de ventana falló", exc_info=True)
        finally:
            try:
                win32gui.EndPaint(hwnd, paint)
            except Exception:
                logger.debug("EndPaint falló", exc_info=True)
        return 0

    def _on_erase_background(self, hwnd, msg, wparam, lparam):
        # Todo el fondo lo pinta WM_PAINT; no borrar evita el parpadeo al refrescar.
        return 1

    # -- Banner de arranque (splash) --------------------------------------

    def show_splash(self, version: str | None = None, is_ready: Callable[[], bool] | None = None) -> None:
        """Muestra el banner de arranque. No bloquea y nunca lanza.

        Se cierra solo cuando ``is_ready()`` es True (el puente ya escucha) o a
        los ``SPLASH_DURATION_MS``, lo que ocurra primero; también al hacer clic.
        Si algo falla se registra y el puente arranca igual, sin banner.
        Debe llamarse antes de ``run()``: los mensajes se bombean allí.
        """
        try:
            import win32con

            self._close_splash()
            dpi = get_system_dpi()
            scale = dpi_scale(dpi)
            self._splash_layout = splash_layout(scale)
            self._splash_text = splash_text(version)
            self._splash_is_ready = is_ready
            self._splash_font_map, self._splash_fonts = self._make_fonts(
                {
                    "title": ("Segoe UI", 24, 700),
                    "subtitle": ("Segoe UI", 11, 400),
                    "version": ("Segoe UI", 9, 400),
                },
                dpi,
            )
            message_map = {
                win32con.WM_PAINT: lambda hwnd, msg, wparam, lparam: self._paint_window(
                    hwnd, self._draw_splash
                ),
                win32con.WM_ERASEBKGND: self._on_erase_background,
                win32con.WM_LBUTTONUP: self._on_splash_click,
                win32con.WM_TIMER: self._on_splash_timer,
                win32con.WM_DESTROY: self._on_splash_destroy,
            }
            layout = self._splash_layout
            hwnd = self._create_popup_window(
                "SAP2000BridgeSplashWindow",
                message_map,
                (layout["width"], layout["height"]),
                win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW,
            )
            self._splash_hwnd = hwnd
            self._splash_started = time.monotonic()
            self._set_timer(hwnd, _TIMER_SPLASH_ID, SPLASH_TIMER_INTERVAL_MS)
        except Exception:
            logger.warning("No se pudo mostrar el banner de arranque; el puente sigue", exc_info=True)
            self._close_splash()

    def _draw_splash(self, hdc) -> None:
        import win32con

        layout = self._splash_layout
        if layout is None:
            return
        fonts = self._splash_font_map
        text = self._splash_text
        centered = win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX
        self._fill_rect(hdc, (0, 0, layout["width"], layout["height"]), THEME_RGB["splash_bg"])
        if not self._big_icon:
            self._big_icon = self._file_icon(256)
        if self._big_icon:
            self._draw_icon(hdc, layout["icon"], self._big_icon)
        self._draw_text(
            hdc, text.get("title", ""), layout["title"], fonts["title"], THEME_RGB["splash_text"], centered
        )
        self._draw_text(
            hdc, text.get("subtitle", ""), layout["subtitle"], fonts["subtitle"],
            THEME_RGB["splash_muted"], centered,
        )  # fmt: skip
        self._draw_text(
            hdc, text.get("version", ""), layout["version"], fonts["version"],
            THEME_RGB["splash_muted"], centered,
        )  # fmt: skip

    def _on_splash_timer(self, hwnd, msg, wparam, lparam):
        try:
            if wparam == _TIMER_SPLASH_ID:
                elapsed_ms = (time.monotonic() - self._splash_started) * 1000.0
                ready = False
                if self._splash_is_ready is not None:
                    try:
                        ready = bool(self._splash_is_ready())
                    except Exception:
                        logger.debug("is_ready del banner falló", exc_info=True)
                if splash_should_close(elapsed_ms, ready):
                    self._close_splash()
        except Exception:
            logger.debug("Temporizador del banner falló", exc_info=True)
            self._close_splash()
        return 0

    def _on_splash_click(self, hwnd, msg, wparam, lparam):
        self._close_splash()
        return 0

    def _close_splash(self) -> None:
        """Cierra el banner si está abierto; nunca lanza."""
        hwnd = self._splash_hwnd
        self._splash_hwnd = None
        if hwnd:
            try:
                import win32gui

                win32gui.DestroyWindow(hwnd)
            except Exception:
                logger.debug("DestroyWindow del banner falló", exc_info=True)
        self._delete_fonts(self._splash_fonts)

    def _on_splash_destroy(self, hwnd, msg, wparam, lparam):
        self._splash_hwnd = None
        self._delete_fonts(self._splash_fonts)
        return 0

    # -- Ventana de aviso de emparejamiento -------------------------------

    def _show_pairing_dialog(self, request: PairingRequest) -> None:
        import win32con
        import win32gui

        if self._pairing_hwnd:
            win32gui.DestroyWindow(self._pairing_hwnd)
            self._pairing_hwnd = None
        self._delete_fonts(self._pairing_fonts)

        dpi = get_system_dpi()
        scale = dpi_scale(dpi)
        layout = pairing_layout(scale)
        fonts, self._pairing_fonts = self._make_fonts(
            {
                "ui": ("Segoe UI", 10, 400),
                "title": ("Segoe UI", 13, 700),
                "code": ("Consolas", 40, 700),
            },
            dpi,
        )
        fields = pairing_dialog_fields(request)
        message_map = {
            win32con.WM_COMMAND: self._on_pairing_command,
            win32con.WM_CLOSE: self._on_pairing_close,
            win32con.WM_DESTROY: self._on_pairing_destroy,
        }
        hwnd, hinst = self._create_fixed_window(
            "SAP2000BridgePairingWindow",
            f"SAP2000 Bridge — {PAIRING_DIALOG_TITLE}",
            message_map,
            (layout["width"], layout["height"]),
            ex_style=win32con.WS_EX_TOPMOST,
            scale=scale,
        )
        self._pairing_hwnd = hwnd

        def add(cls, text, style, key, font, control_id=0):
            return self._add_control(hwnd, hinst, cls, text, style, layout[key], control_id, fonts[font])

        add("STATIC", PAIRING_DIALOG_TITLE, win32con.SS_LEFT, "title", "title", _ID_PAIRING_TEXT)
        add(
            "STATIC",
            f"Origen: {fields['origin']}",
            win32con.SS_LEFT | win32con.SS_NOPREFIX,
            "origin",
            "ui",
        )
        add("STATIC", PAIRING_CODE_LABEL, win32con.SS_LEFT, "code_label", "ui")
        add(
            "STATIC",
            fields["code"],
            win32con.SS_CENTER | win32con.SS_CENTERIMAGE | win32con.SS_NOPREFIX,
            "code",
            "code",
        )
        add("STATIC", PAIRING_INSTRUCTION, win32con.SS_LEFT, "instruction", "ui")
        accept = add(
            "BUTTON",
            "Aceptar",
            win32con.BS_DEFPUSHBUTTON | win32con.WS_TABSTOP,
            "accept",
            "ui",
            _ID_PAIRING_ACCEPT,
        )
        add(
            "BUTTON",
            "Rechazar",
            win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP,
            "reject",
            "ui",
            _ID_PAIRING_REJECT,
        )
        # Al frente y por encima del resto de ventanas hasta que se responda.
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.SetForegroundWindow(hwnd)
        try:
            win32gui.SetFocus(accept)
        except Exception:
            logger.debug("SetFocus falló", exc_info=True)

    def _on_pairing_command(self, hwnd, msg, wparam, lparam):
        import win32api
        import win32gui

        from bridge import pairing as pairing_module

        command_id = win32api.LOWORD(wparam)
        if command_id in (_ID_PAIRING_ACCEPT, _ID_PAIRING_REJECT):
            request = self._current_request
            if request is not None:
                resolve_pairing_request(
                    pairing_module, request.id, approve=(command_id == _ID_PAIRING_ACCEPT)
                )
            win32gui.DestroyWindow(hwnd)
        return 0

    def _on_pairing_close(self, hwnd, msg, wparam, lparam):
        # La X de la ventana (o Alt+F4) rechaza la vinculación, igual que el
        # botón Rechazar; después se cierra y se pasa a la siguiente solicitud.
        import win32gui

        from bridge import pairing as pairing_module

        reject_on_close(pairing_module, self._current_request)
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_pairing_destroy(self, hwnd, msg, wparam, lparam):
        self._pairing_hwnd = None
        self._delete_fonts(self._pairing_fonts)
        self._current_request = None
        # Procesa la siguiente solicitud de la cola, si la hay.
        self._maybe_show_next_pairing()
        return 0

    # -- Ventana de estado -------------------------------------------------

    def _show_status_window(self) -> None:
        try:
            import win32con
            import win32gui

            if self._status_hwnd:
                win32gui.SetForegroundWindow(self._status_hwnd)
                self._refresh_status_window()
                return
            dpi = get_system_dpi()
            scale = dpi_scale(dpi)
            layout = status_layout(scale)
            self._status_layout = layout
            self._status_font_map, self._status_fonts = self._make_fonts(
                {
                    "ui": ("Segoe UI", 9, 400),
                    "value": ("Segoe UI", 10, 600),
                    "title": ("Segoe UI", 13, 700),
                    "small": ("Segoe UI", 8, 400),
                },
                dpi,
            )
            if not self._header_icon:
                self._header_icon = self._file_icon(max(layout["icon_small"][2], 16))
            hwnd, _hinst = self._create_fixed_window(
                "SAP2000BridgeStatusWindow",
                "SAP2000 Bridge — Estado",
                {
                    win32con.WM_DESTROY: self._on_status_destroy,
                    win32con.WM_PAINT: lambda h, m, w, l: self._paint_window(h, self._draw_status),
                    win32con.WM_ERASEBKGND: self._on_erase_background,
                },
                (layout["width"], layout["height"]),
                scale=scale,
            )
            self._status_hwnd = hwnd
            win32gui.SetForegroundWindow(hwnd)
            self._refresh_status_window()
        except Exception:
            logger.exception("Error al mostrar ventana de estado")

    def _on_status_destroy(self, hwnd, msg, wparam, lparam):
        self._status_hwnd = None
        self._status_font_map = {}
        self._delete_fonts(self._status_fonts)
        return 0

    def _on_timer(self, hwnd, msg, wparam, lparam):
        try:
            if wparam == _TIMER_STATUS_ID:
                self._refresh_status_window()
        except Exception:
            logger.exception("Error en temporizador de estado")
        return 0

    def _refresh_status_window(self) -> None:
        """Vuelve a leer el estado y repinta; los datos se calculan con funciones puras."""
        if not self._status_hwnd:
            return
        import win32gui

        status = None
        error: Exception | None = None
        try:
            status = self.get_status()
        except Exception as exc:  # noqa: BLE001 - cualquier excepción de session.status()
            error = exc
        try:
            paired: int | None = len(visible_pairings(self.get_pairings()))
        except Exception:  # noqa: BLE001 - la lista de emparejadas es opcional para pintar
            logger.debug("No se pudo leer las conexiones emparejadas", exc_info=True)
            paired = None
        self._status_rows = status_rows(
            status,
            error,
            port=self.port,
            paired_count=paired,
            updated_at=hora_actualizacion(time.time()),
        )
        self._status_color = status_indicator(status, error)
        win32gui.InvalidateRect(self._status_hwnd, None, True)

    def _draw_status(self, hdc) -> None:
        import win32con

        layout = self._status_layout
        if layout is None:
            return
        fonts = self._status_font_map
        left_align = win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX
        value_align = left_align | win32con.DT_END_ELLIPSIS | win32con.DT_PATH_ELLIPSIS
        self._fill_rect(hdc, (0, 0, layout["width"], layout["height"]), THEME_RGB["status_bg"])
        self._fill_rect(hdc, layout["panel"], THEME_RGB["status_panel"])
        if not self._big_icon:
            self._big_icon = self._file_icon(256)
        if self._big_icon:
            self._draw_icon(hdc, layout["watermark"], self._big_icon)
        if self._header_icon:
            self._draw_icon(hdc, layout["icon_small"], self._header_icon)
        self._draw_text(
            hdc, "SAP2000 Bridge — Estado", layout["title"], fonts["title"],
            THEME_RGB["status_text"], left_align,
        )  # fmt: skip
        self._fill_rect(hdc, layout["divider"], THEME_RGB["status_divider"])

        row_height = layout["row_height"]
        lx, _ly, lw, _lh = layout["label_column"]
        vx, vy, vw, _vh = layout["value_column"]
        for index, (label, value) in enumerate(self._status_rows):
            top = vy + index * row_height
            self._draw_text(
                hdc, label, (lx, top, lw, row_height), fonts["ui"], THEME_RGB["status_muted"], left_align
            )
            value_x = vx
            if index == 0:
                # Primera fila: el punto de color del estado va delante del texto.
                rgb = STATUS_INDICATOR_RGB.get(self._status_color, STATUS_INDICATOR_RGB["ambar"])
                self._draw_indicator(hdc, vx, top + row_height // 2, rgb)
                value_x = vx + 20
            self._draw_text(
                hdc, value, (value_x, top, vw - (value_x - vx), row_height), fonts["value"],
                THEME_RGB["status_text"], value_align,
            )  # fmt: skip

        self._draw_text(
            hdc,
            "Se actualiza cada 2 segundos. Cerrar esta ventana no afecta al puente.",
            layout["footer"],
            fonts["small"],
            THEME_RGB["status_muted"],
            left_align,
        )

    # -- Ventana de conexiones emparejadas ---------------------------------

    def _show_pairings_window(self) -> None:
        try:
            import win32con
            import win32gui

            if self._pairings_hwnd:
                win32gui.DestroyWindow(self._pairings_hwnd)
                self._pairings_hwnd = None
            self._delete_fonts(self._pairings_fonts)

            self._pairings_rows = visible_pairings(self.get_pairings())
            has_pairings = bool(self._pairings_rows)
            dpi = get_system_dpi()
            layout = pairings_layout(dpi_scale(dpi), has_pairings)
            fonts, self._pairings_fonts = self._make_fonts({"ui": ("Segoe UI", 10, 400)}, dpi)
            message_map = {
                win32con.WM_COMMAND: self._on_pairings_command,
                win32con.WM_DESTROY: self._on_pairings_destroy,
            }
            hwnd, hinst = self._create_fixed_window(
                "SAP2000BridgePairingsWindow",
                "SAP2000 Bridge — Conexiones emparejadas",
                message_map,
                (layout["width"], layout["height"]),
                scale=dpi_scale(dpi),
            )
            self._pairings_hwnd = hwnd
            listbox = self._add_control(
                hwnd, hinst, "LISTBOX", "",
                win32con.WS_BORDER | win32con.WS_VSCROLL | win32con.LBS_NOTIFY | win32con.LBS_EXTENDEDSEL,
                layout["list"], _ID_PAIRINGS_LIST, fonts["ui"],
            )  # fmt: skip

            if not has_pairings:
                win32gui.SendMessage(listbox, win32con.LB_ADDSTRING, 0, "Sin conexiones emparejadas.")
                self._pairings_rows = [{}]
                self._add_control(
                    hwnd, hinst, "STATIC",
                    "En este equipo la web se conecta sola (modo local); el emparejamiento "
                    "solo se usa con una web en otro servidor.",
                    win32con.SS_LEFT | win32con.SS_NOPREFIX, layout["hint"], 0, fonts["ui"],
                )  # fmt: skip
            else:
                for row in self._pairings_rows:
                    label = f"{row.get('label') or row.get('origin')} — {row.get('origin')}"
                    win32gui.SendMessage(listbox, win32con.LB_ADDSTRING, 0, label)

            self._add_control(
                hwnd, hinst, "BUTTON", "Seleccionar todas",
                win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP,
                layout["select_all"], _ID_PAIRINGS_SELECT_ALL, fonts["ui"],
            )  # fmt: skip
            self._add_control(
                hwnd, hinst, "BUTTON", "Revocar seleccionadas",
                win32con.BS_PUSHBUTTON | win32con.WS_TABSTOP,
                layout["revoke"], _ID_PAIRINGS_REVOKE, fonts["ui"],
            )  # fmt: skip
        except Exception:
            logger.exception("Error al mostrar ventana de conexiones emparejadas")

    def _on_pairings_command(self, hwnd, msg, wparam, lparam):
        try:
            import win32api
            import win32con
            import win32gui

            command_id = win32api.LOWORD(wparam)
            listbox = win32gui.GetDlgItem(hwnd, _ID_PAIRINGS_LIST)
            if command_id == _ID_PAIRINGS_SELECT_ALL:
                # LB_SETSEL con índice -1 marca (wparam=1) o desmarca (0) todas las filas.
                win32gui.SendMessage(listbox, win32con.LB_SETSEL, 1, -1)
            elif command_id == _ID_PAIRINGS_REVOKE:
                self._revoke_selected(hwnd, listbox)
        except Exception:
            logger.exception("Error en comando de conexiones emparejadas")
        return 0

    def _revoke_selected(self, hwnd, listbox) -> None:
        """Revoca cada conexión marcada, con confirmación si son varias."""
        import win32con
        import win32gui

        total = int(win32gui.SendMessage(listbox, win32con.LB_GETCOUNT, 0, 0))
        selected = [
            index for index in range(total) if win32gui.SendMessage(listbox, win32con.LB_GETSEL, index, 0) > 0
        ]
        indices = pairing_indices_to_revoke(self._pairings_rows, selected)
        if not indices:
            return
        question = revoke_confirm_message(len(indices))
        if question is not None:
            answer = win32gui.MessageBox(
                hwnd,
                question,
                "SAP2000 Bridge — Conexiones emparejadas",
                win32con.MB_YESNO | win32con.MB_ICONQUESTION | win32con.MB_DEFBUTTON2,
            )
            if answer != win32con.IDYES:
                return
        # De atrás adelante: quitar una fila no desplaza las anteriores.
        for index in reversed(indices):
            pairing_id = self._pairings_rows[index].get("id")
            try:
                self.on_revoke(pairing_id)
            except Exception:
                logger.exception("No se pudo revocar la conexión %s", pairing_id)
                continue
            win32gui.SendMessage(listbox, win32con.LB_DELETESTRING, index, 0)
            del self._pairings_rows[index]

    def _on_pairings_destroy(self, hwnd, msg, wparam, lparam):
        self._pairings_hwnd = None
        self._delete_fonts(self._pairings_fonts)
        return 0

    # -- Menú contextual y salida -------------------------------------------

    def _on_command(self, hwnd, msg, wparam, lparam):
        try:
            import win32api

            command_id = win32api.LOWORD(wparam)
            if command_id == _MENU_STATUS:
                self._show_status_window()
            elif command_id == _MENU_PAIRINGS:
                self._show_pairings_window()
            elif command_id == _MENU_LOGS:
                if self.on_open_logs is not None:
                    self.on_open_logs()
            elif command_id == _MENU_EXIT:
                self._exit()
            elif _MENU_EXTRA_BASE <= command_id < _MENU_EXTRA_BASE + len(self.extra_menu_items):
                self.extra_menu_items[command_id - _MENU_EXTRA_BASE][1]()
        except Exception:
            logger.exception("Error en comando del menú")
        return 0

    def _show_context_menu(self) -> None:
        import win32con
        import win32gui

        menu = win32gui.CreatePopupMenu()
        for entry in menu_layout([label for label, _ in self.extra_menu_items]):
            if entry is None:
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            else:
                win32gui.AppendMenu(menu, win32con.MF_STRING, entry[0], entry[1])
        position = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self._hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, position[0], position[1], 0, self._hwnd, None)

    def _exit(self) -> None:
        import ctypes

        import win32gui

        kill_timer = ctypes.windll.user32.KillTimer
        kill_timer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        kill_timer(self._hwnd, _TIMER_STATUS_ID)
        self._close_splash()
        win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self._hwnd, 0))
        self._release_tray_icon()
        self._release_owned_icons()
        self.on_exit()
        win32gui.PostQuitMessage(0)

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        import win32gui

        win32gui.PostQuitMessage(0)
        return 0

    def run(self) -> None:
        """Bloquea el hilo que la llama bombeando mensajes de Windows."""
        import win32gui

        win32gui.PumpMessages()
