"""Documentos Word vinculados en su sitio (sólo app local).

El Report Builder normalmente guarda una copia del .docx en el almacén del
proyecto. En la app local, en cambio, el documento se queda donde está la
persona: el manifiesto se escribe junto a él, la sincronización escribe en él
y «Abrir en Word» abre ese mismo archivo. Este módulo sólo valida la ruta y
pregunta al sistema por un archivo; el registro en la base lo hace el router.

Reglas que se aplican siempre: sólo .docx (no .doc), archivo regular, sin
enlaces simbólicos ni accesos directos en la última componente, nada de
archivos temporales de Word (``~$...``) y un tope de tamaño igual al de las
subidas. Un documento vinculado nunca se borra desde la aplicación.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException

MAX_LINKED_BYTES = 20_000_000
PICK_TIMEOUT_SECONDS = 900
PICK_TITLE = 'Seleccione el documento Word del informe'
UNAVAILABLE_MESSAGE = (
    'El diálogo de archivos no está disponible en este equipo. Pegue la ruta completa del documento.'
)

# El diálogo corre en un proceso hijo (en desarrollo): tkinter debe estar en el
# hilo principal de ese proceso, y en macOS sólo así es seguro. Códigos de salida
# que el padre traduce a «no disponible».
_EXIT_NO_TKINTER = 3
_EXIT_NO_DISPLAY = 4

_CHILD_CODE = f"""
import sys
try:
    import tkinter
    from tkinter import filedialog
except ImportError:
    sys.exit({_EXIT_NO_TKINTER})
try:
    root = tkinter.Tk()
except tkinter.TclError:
    sys.exit({_EXIT_NO_DISPLAY})
root.withdraw()
try:
    # Sin esto el diálogo puede quedar detrás de la ventana del navegador.
    root.attributes('-topmost', True)
except tkinter.TclError:
    pass
try:
    path = filedialog.askopenfilename(
        title={PICK_TITLE!r}, filetypes=[('Documento Word', '*.docx')], parent=root
    )
finally:
    root.destroy()
sys.stdout.buffer.write((path or '').encode('utf-8'))
"""


class PickerUnavailable(Exception):
    """No hay diálogo de archivos en este equipo (sin tkinter o sin pantalla)."""


def normalize_input(raw: str) -> str:
    """Quita las comillas que trae «Copiar como ruta» de Windows y los espacios sobrantes."""
    return raw.strip().strip('"').strip("'").strip()


def check_linkable(raw: str) -> Path:
    """Valida la ruta de un .docx a vincular y devuelve la ruta absoluta. Lanza 422 si no sirve."""
    text = normalize_input(raw)
    if not text:
        raise HTTPException(422, 'Indique la ruta del documento')
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise HTTPException(422, 'Indique la ruta completa del archivo (con la unidad o desde la raíz).')
    if path.is_symlink():
        raise HTTPException(
            422, 'No se admiten enlaces simbólicos ni accesos directos: elija el archivo real.'
        )
    if not path.exists():
        raise HTTPException(422, 'No se encontró el archivo en esa ruta.')
    if not path.is_file():
        raise HTTPException(422, 'La ruta no es un archivo.')
    if path.suffix.lower() != '.docx':
        raise HTTPException(
            422,
            'Solo se pueden vincular documentos .docx. Si es un .doc, ábralo en Word y guárdelo como .docx.',
        )
    if path.name.startswith('~$'):
        raise HTTPException(
            422,
            'Ese archivo es temporal de Word (~$…), no el documento. Elija el .docx original.',
        )
    if path.stat().st_size > MAX_LINKED_BYTES:
        raise HTTPException(422, 'El documento supera 20 MB.')
    return path


def pick_docx() -> str | None:
    """Abre el diálogo nativo para elegir un .docx. None si se cancela.

    Lanza PickerUnavailable si no hay tkinter o pantalla. No debe llamarse con una
    transacción de base de datos abierta: el diálogo puede tardar minutos.
    """
    if getattr(sys, 'frozen', False):
        # Ejecutable empaquetado: no hay intérprete al que lanzar un hijo; el
        # diálogo corre en el hilo del servidor (la ruta sincrónica de FastAPI).
        return _ask_in_process()
    try:
        result = subprocess.run(
            [sys.executable, '-c', _CHILD_CODE],
            capture_output=True,
            timeout=PICK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode in (_EXIT_NO_TKINTER, _EXIT_NO_DISPLAY):
        raise PickerUnavailable(UNAVAILABLE_MESSAGE)
    if result.returncode != 0:
        raise HTTPException(500, 'No se pudo abrir el diálogo de archivos')
    chosen = result.stdout.decode('utf-8').strip()
    return chosen or None


def _ask_windows_native() -> str | None:
    """Diálogo «Abrir» de Windows (comdlg32) sin tkinter.

    En el .exe del puente el diálogo corre en un hilo del servidor y el proceso ya tiene su
    propio bucle de mensajes (bandeja): tkinter ahí cierra el programa. GetOpenFileNameW
    funciona en cualquier hilo. None si se cancela.
    """
    import ctypes
    from ctypes import wintypes

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ('lStructSize', wintypes.DWORD),
            ('hwndOwner', wintypes.HWND),
            ('hInstance', wintypes.HINSTANCE),
            ('lpstrFilter', wintypes.LPCWSTR),
            ('lpstrCustomFilter', wintypes.LPWSTR),
            ('nMaxCustFilter', wintypes.DWORD),
            ('nFilterIndex', wintypes.DWORD),
            ('lpstrFile', wintypes.LPWSTR),
            ('nMaxFile', wintypes.DWORD),
            ('lpstrFileTitle', wintypes.LPWSTR),
            ('nMaxFileTitle', wintypes.DWORD),
            ('lpstrInitialDir', wintypes.LPCWSTR),
            ('lpstrTitle', wintypes.LPCWSTR),
            ('Flags', wintypes.DWORD),
            ('nFileOffset', wintypes.WORD),
            ('nFileExtension', wintypes.WORD),
            ('lpstrDefExt', wintypes.LPCWSTR),
            ('lCustData', wintypes.LPARAM),
            ('lpfnHook', ctypes.c_void_p),
            ('lpTemplateName', wintypes.LPCWSTR),
            ('pvReserved', ctypes.c_void_p),
            ('dwReserved', wintypes.DWORD),
            ('FlagsEx', wintypes.DWORD),
        ]

    buffer = ctypes.create_unicode_buffer(32768)
    dialog = OpenFileName()
    dialog.lStructSize = ctypes.sizeof(OpenFileName)
    dialog.lpstrFilter = 'Documento Word (*.docx)\0*.docx\0\0'
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrTitle = PICK_TITLE
    # OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008
    try:
        accepted = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog))
    except (AttributeError, OSError) as exc:
        raise PickerUnavailable(UNAVAILABLE_MESSAGE) from exc
    if accepted:
        return buffer.value
    # 0 también es «cancelado»: CommDlgExtendedError distingue la cancelación (0) de un fallo real,
    # que antes se confundía con ella y la web no mostraba nada.
    try:
        codigo = int(ctypes.windll.comdlg32.CommDlgExtendedError())
    except (AttributeError, OSError):
        codigo = 0
    if codigo:
        raise HTTPException(500, f'El diálogo de archivos de Windows falló (código 0x{codigo:04X})')
    return None


def _ask_in_process() -> str | None:
    if sys.platform == 'win32':
        return _ask_windows_native()
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as exc:
        raise PickerUnavailable(UNAVAILABLE_MESSAGE) from exc
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        raise PickerUnavailable(UNAVAILABLE_MESSAGE) from exc
    root.withdraw()
    try:
        # Sin esto el diálogo puede quedar detrás de la ventana del navegador.
        root.attributes('-topmost', True)
    except tkinter.TclError:
        pass
    try:
        chosen = filedialog.askopenfilename(
            title=PICK_TITLE, filetypes=[('Documento Word', '*.docx')], parent=root
        )
    finally:
        root.destroy()
    return chosen or None
