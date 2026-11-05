"""Cargas nodales de Fundaciones: ida y vuelta con Excel de escritorio (sin COM).

Ida: ``POST /v1/sap/actions/excel/open-reactions`` escribe las filas en un ``.xlsx`` temporal
(hoja «Cargas», y una hoja «Leeme» con las unidades) y lo abre con ``os.startfile`` en Windows.
Vuelta: ``POST /v1/sap/actions/excel/read-reactions`` lee ese mismo fichero cuando el usuario lo
ha guardado y cerrado en Excel.

No habla con SAP2000: no usa el envoltorio ``call`` de ``bridge.actions`` (que convierte cualquier
excepción en 409 y perdería el 422/404 con ``code``). Los errores se lanzan aquí como
``HTTPException`` con ``detail = {"code", "message"}``, el mismo formato que ``DesignCodeError``.

Carpeta de trabajo: ``<tempdir>/ew_reacciones/``. La lectura solo acepta ficheros dentro de esa
carpeta (se resuelve la ruta con ``Path.resolve()`` para que enlaces simbólicos y ``..`` no escapen).
Al abrir se borran los ficheros de esa carpeta con más de un día.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CARPETA = "ew_reacciones"
HOJA_DATOS = "Cargas"
HOJA_NOTA = "Leeme"
MAX_FILAS = 5000
ANTIGUEDAD_LIMITE_S = 24 * 60 * 60  # un día

# Cabeceras canónicas (en orden) y columnas numéricas (fuerzas y momentos).
CAMPOS = ("joint", "load_pat", "coord_sys", "f1", "f2", "f3", "m1", "m2", "m3")
CABECERAS = ("Joint", "LoadPat", "CoordSys", "F1", "F2", "F3", "M1", "M2", "M3")
CAMPOS_NUMERICOS = frozenset({"f1", "f2", "f3", "m1", "m2", "m3"})
FILAS_CABECERA_BUSQUEDA = 10

NOTA_UNIDADES = (
    "Fuerzas F1, F2, F3 en Ton y momentos M1, M2, M3 en Ton·m. "
    "Edite los valores en la hoja «Cargas», guarde el libro y ciérrelo antes de traer los datos al puente."
)


class FilaReaccion(BaseModel):
    joint: str = ""
    load_pat: str = ""
    coord_sys: str = ""
    f1: str = ""
    f2: str = ""
    f3: str = ""
    m1: str = ""
    m2: str = ""
    m3: str = ""


class OpenReactionsBody(BaseModel):
    rows: list[FilaReaccion] = Field(max_length=MAX_FILAS)


class ReadReactionsBody(BaseModel):
    path: str


_MENSAJE_BLOQUEADO = "Guarde y cierre el libro en Excel y vuelva a intentarlo: no se pudo leer el fichero porque está abierto o bloqueado."


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _carpeta() -> Path:
    return Path(tempfile.gettempdir()) / CARPETA


def _importar_openpyxl():
    try:
        import openpyxl
    except ImportError as exc:
        raise _error(409, "openpyxl_missing", "Falta la librería openpyxl en el puente.") from exc
    return openpyxl


def _numero_o_texto(valor: str) -> float | str:
    """``float`` si el texto es un número finito; si no, el texto tal cual (sin «nan» ni «inf»)."""
    try:
        numero = float(valor.strip())
    except ValueError:
        return valor
    return numero if math.isfinite(numero) else valor


def _texto(valor: Any) -> str:
    """Valor de celda como texto. Los reales enteros se muestran sin «.0» para que el ida y vuelta sea estable."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, float) and valor.is_integer() and abs(valor) < 1e15:
        return str(int(valor))
    return str(valor).strip()


def limpiar_antiguos(carpeta: Path, ahora: float | None = None) -> int:
    """Borra los ficheros de ``carpeta`` con más de un día. Un fichero bloqueado se omite. Devuelve cuántos borró."""
    if not carpeta.is_dir():
        return 0
    limite = (time.time() if ahora is None else ahora) - ANTIGUEDAD_LIMITE_S
    borrados = 0
    for fichero in carpeta.iterdir():
        try:
            if fichero.is_file() and fichero.stat().st_mtime < limite:
                fichero.unlink()
                borrados += 1
        except OSError:
            logger.info("Excel reacciones: no se pudo borrar %s (¿abierto?)", fichero.name)
    return borrados


def _abrir(ruta: Path) -> tuple[bool, str | None]:
    """``os.startfile`` solo en Windows. Fuera de Windows no se abre y se explica en ``note``."""
    if not hasattr(os, "startfile"):
        return (
            False,
            "Sin Excel en este equipo: el fichero se creó pero no se abrió (la apertura es solo en Windows).",
        )
    try:
        os.startfile(str(ruta))  # type: ignore[attr-defined]
    except OSError as exc:
        return False, f"No se pudo abrir el fichero en Excel: {exc}"
    return True, None


def crear_reacciones(rows: list[FilaReaccion]) -> dict[str, Any]:
    openpyxl = _importar_openpyxl()
    carpeta = _carpeta()
    carpeta.mkdir(parents=True, exist_ok=True)
    limpiar_antiguos(carpeta)

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = HOJA_DATOS
    hoja.append(list(CABECERAS))
    for fila in rows:
        valores = fila.model_dump()
        hoja.append(
            [
                _numero_o_texto(valores[campo]) if campo in CAMPOS_NUMERICOS else valores[campo]
                for campo in CAMPOS
            ]
        )
    nota = libro.create_sheet(HOJA_NOTA)
    nota["A1"] = NOTA_UNIDADES

    ruta = carpeta / f"reacciones_{uuid.uuid4().hex}.xlsx"
    libro.save(ruta)
    abierto, aviso = _abrir(ruta)
    return {"path": str(ruta), "rows_written": len(rows), "opened": abierto, "note": aviso}


def _ruta_permitida(ruta_pedida: str) -> Path:
    """Ruta resuelta dentro de la carpeta de trabajo, con extensión .xlsx; si no, 422 ``path_not_allowed``."""
    base = _carpeta().resolve()
    try:
        candidata = Path(ruta_pedida).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error(422, "path_not_allowed", "La ruta no es válida.") from exc
    if base not in candidata.parents:
        raise _error(
            422, "path_not_allowed", "Solo se pueden leer ficheros de la carpeta de trabajo del puente."
        )
    if candidata.suffix.lower() != ".xlsx":
        raise _error(422, "path_not_allowed", "Solo se pueden leer ficheros .xlsx.")
    return candidata


def _mapear_cabeceras(filas: list[tuple[Any, ...]]) -> tuple[int, dict[str, int]]:
    """(índice de la fila de cabecera, columna de cada campo). Sin distinguir mayúsculas ni espacios."""
    for indice, fila in enumerate(filas[:FILAS_CABECERA_BUSQUEDA]):
        normalizadas = {"".join(_texto(c).lower().split()): col for col, c in enumerate(fila) if _texto(c)}
        if "joint" not in normalizadas:
            continue
        por_campo: dict[str, int] = {}
        for campo, cabecera in zip(CAMPOS, CABECERAS):
            clave = cabecera.lower()
            if clave in normalizadas:
                por_campo[campo] = normalizadas[clave]
        faltan = [cabecera for campo, cabecera in zip(CAMPOS, CABECERAS) if campo not in por_campo]
        if faltan:
            raise _error(
                422, "invalid_workbook", f"Faltan cabeceras en la hoja «{HOJA_DATOS}»: {', '.join(faltan)}."
            )
        return indice, por_campo
    raise _error(
        422,
        "invalid_workbook",
        f"La hoja «{HOJA_DATOS}» no tiene la fila de cabeceras Joint, LoadPat, CoordSys, F1…M3.",
    )


def leer_reacciones(ruta_pedida: str) -> dict[str, Any]:
    openpyxl = _importar_openpyxl()
    ruta = _ruta_permitida(ruta_pedida)
    if not ruta.is_file():
        raise _error(404, "file_not_found", "No existe el fichero de reacciones.")
    try:
        libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    except PermissionError as exc:
        raise _error(409, "file_locked", _MENSAJE_BLOQUEADO) from exc
    except FileNotFoundError as exc:
        raise _error(404, "file_not_found", "No existe el fichero de reacciones.") from exc
    except Exception as exc:
        raise _error(422, "invalid_workbook", "El fichero no es un libro .xlsx válido.") from exc
    try:
        if HOJA_DATOS not in libro.sheetnames:
            raise _error(422, "invalid_workbook", f"Falta la hoja «{HOJA_DATOS}».")
        filas = list(libro[HOJA_DATOS].iter_rows(values_only=True))
        indice, columnas = _mapear_cabeceras(filas)
        resultado: list[dict[str, str]] = []
        for fila in filas[indice + 1 :]:
            registro = {
                campo: (_texto(fila[columnas[campo]]) if columnas[campo] < len(fila) else "")
                for campo in CAMPOS
            }
            if not registro["joint"]:
                continue
            resultado.append(registro)
    except PermissionError as exc:
        raise _error(409, "file_locked", _MENSAJE_BLOQUEADO) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(422, "invalid_workbook", "No se pudo leer la hoja de reacciones.") from exc
    finally:
        libro.close()
    return {"rows": resultado, "count": len(resultado)}


def install_routes(app: Any) -> None:
    """Instala ``/v1/sap/actions/excel/*``. Sin COM: no recibe el envoltorio ``call``."""

    @app.post("/v1/sap/actions/excel/open-reactions")
    def excel_open_reactions(body: OpenReactionsBody):
        return crear_reacciones(body.rows)

    @app.post("/v1/sap/actions/excel/read-reactions")
    def excel_read_reactions(body: ReadReactionsBody):
        return leer_reacciones(body.path)
