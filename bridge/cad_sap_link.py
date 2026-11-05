# -*- coding: utf-8 -*-
"""
bridge.cad_sap_link — Copia del módulo `cad/sap2000_link.py` del escritorio
para el puente local CAD-DRWR (lectura de fundaciones desde SAP2000).

Cambios respecto al original (la lógica COM NO se ha tocado):
  1. Se añade `threading`.
  2. Los dataclasses de geometría (Pedestal, AreaGeom, Zapata, FundacionGeom)
     y toda la geometría (componentes conexas, contorno, pedestales) ya no
     viven aquí: el puente solo LEE SAP2000 (`leer_fundacion_cruda`, formato
     `sap-crudo-v1`, ver docs/CAD_FUNDACION_CRUDO.md) y el servidor hace la
     geometría (`fundacion_desde_crudo`).
  3. Nuevo: TrabajoCancelado, ControlTrabajo y ModelLink (adaptador sobre un
     SapModel ya adjunto por el puente; no abre ni cierra conexión).
     Todo lo demás es idéntico al original.

Documentación original:

cad.sap2000_link — Conexión de solo lectura con SAP2000 (COM/OAPI).

Lee la geometría de fundaciones ya modeladas en SAP2000 (nodos y shells de
área agrupados en un grupo de SAP2000) para dibujarlas en StructGenCAD.

Un grupo de SAP2000 puede contener más de una fundación física (varios
shells sin relación entre sí): se detectan las componentes conexas por
adyacencia de arista (`core.geom.agrupar_por_adyacencia`) y cada una se
resuelve a una `Zapata` con su contorno exterior único (esquinas reales,
`core.geom.contorno_exterior` + `simplificar_colineales`), su espesor por
shell y los pedestales (columnas) que nacen sobre ella — detección
portada de la app de referencia "Foundations SAP2000"
(`app/sap2000/pedestales_automaticos.py`): frames verticales cuyo pie
coincide con un joint de la malla de un shell, con su dimensión real leída
de `PropFrame.GetRectangle`/`GetCircle`.

Estrategia de conexión y criterio de tolerancia a firmas COM adaptados de
esa misma app de referencia (win32com late-binding, sin `gencache`, para
no invocar DISPID de otra versión de SAP2000 instalada y cerrarla a mitad
de la conexión). Requiere Windows + pywin32 (ya listado en
requirements.txt) y SAP2000 abierto con el modelo cargado.
"""

import threading
from typing import Dict, List, Optional, Tuple

from bridge.actions import split_return

FORMATO_CRUDO = "sap-crudo-v1"

PROG_IDS = [
    "CSI.SAP2000.API.SapObject",
    "CSI.SAP2000.API.SapObject.1",
    "Sap2000.SapObject",
]

HELPER_PROG_IDS = [
    "CSI.SAP2000.API.Helper",
    "CSI.SAP2000.Helper",
    "SAP2000v1.Helper",
    "SAP2000.Helper",
]

UNIDADES_N_MM_C = 9  # eUnits de SAP2000: N, mm, °C


def _win32():
    """Importa win32com (solo Windows). Lanza RuntimeError con mensaje claro."""
    import os

    if os.name != "nt":
        raise RuntimeError("La conexión con SAP2000 solo está disponible en Windows.")
    try:
        import win32com.client
        import win32com.client.dynamic

        return win32com.client
    except ImportError:
        raise RuntimeError(
            "pywin32 no está instalado. Ejecute:\n    pip install pywin32\ny vuelva a intentar la conexión."
        )


def _dispatch_tardio(win32com_client, prog_id: str):
    """Activa un objeto COM SIEMPRE con late binding (dynamic.Dispatch).

    `Dispatch`/`gencache` usan el caché makepy, generado contra la type
    library de la versión de SAP2000 registrada en ese momento; con varias
    versiones instaladas eso invoca los DISPID de una versión distinta de
    la que está corriendo y puede cerrarla a mitad de la conexión.
    `dynamic.Dispatch` resuelve cada método por nombre contra el objeto
    real, sin ese riesgo."""
    return win32com_client.dynamic.Dispatch(prog_id)


def _clean_com_error(e: Exception) -> str:
    msg = str(e)
    if hasattr(e, "args") and e.args:
        args = e.args[0]
        if isinstance(args, tuple) and len(args) >= 3:
            return f"{args[1]} (hresult={args[0]})"
        if isinstance(args, str):
            return args
    return msg


class SapLink:
    """Conexión de solo lectura con una instancia de SAP2000 abierta."""

    def __init__(self):
        self._sap_object = None
        self._sap_model = None

    @property
    def is_connected(self) -> bool:
        return self._sap_model is not None

    def get_sap_model(self):
        if not self.is_connected:
            raise RuntimeError("No hay conexión activa con SAP2000.")
        return self._sap_model

    def connect(self) -> str:
        """Se adjunta a una instancia de SAP2000 ya abierta (no abre una
        nueva: no tiene sentido leer geometría de un SAP2000 vacío).
        Devuelve un texto de estado o lanza RuntimeError."""
        win32com_client = _win32()
        errores: List[str] = []

        # Intento 1: Helper.GetObject (forma documentada por CSI para
        # adjuntarse a una instancia ya corriendo).
        for helper_id in HELPER_PROG_IDS:
            try:
                helper = _dispatch_tardio(win32com_client, helper_id)
            except Exception as e:
                errores.append(f"Dispatch(Helper {helper_id}): {_clean_com_error(e)}")
                continue
            for sap_obj_id in PROG_IDS:
                try:
                    sap_object = helper.GetObject(sap_obj_id)
                except Exception as e:
                    errores.append(f"Helper({helper_id}).GetObject({sap_obj_id}): {_clean_com_error(e)}")
                    continue
                model = getattr(sap_object, "SapModel", None)
                if model is None:
                    errores.append(f"{sap_obj_id}: objeto sin SapModel")
                    continue
                self._sap_object = sap_object
                self._sap_model = model
                return self._info_modelo()

        # Intento 2: GetActiveObject (Running Object Table).
        for prog_id in PROG_IDS:
            try:
                sap_object = win32com_client.GetActiveObject(prog_id, dynamic=True)
            except Exception as e:
                errores.append(f"GetActiveObject({prog_id}): {_clean_com_error(e)}")
                continue
            model = getattr(sap_object, "SapModel", None)
            if model is None:
                errores.append(f"GetActiveObject({prog_id}): objeto sin SapModel")
                continue
            self._sap_object = sap_object
            self._sap_model = model
            return self._info_modelo()

        self._sap_object = None
        self._sap_model = None
        raise RuntimeError(
            "No se pudo conectar con SAP2000.\n"
            "Verifique que SAP2000 esté abierto con el modelo cargado y "
            "que 'Allow API Application Access' esté activo "
            "(File > Preferences > API).\n\n"
            "Detalle de los intentos:\n  - " + "\n  - ".join(errores)
        )

    def _info_modelo(self) -> str:
        nombre = "Modelo sin guardar"
        try:
            ruta = self._sap_model.GetModelFilename()
            if isinstance(ruta, (list, tuple)):
                ruta = ruta[0] if ruta else ""
            if ruta:
                import os

                nombre = os.path.basename(str(ruta))
        except Exception:
            pass
        return f"SAP2000 conectado — modelo: {nombre}"

    def set_units_mm(self) -> int:
        """Fija unidades N-mm-°C y devuelve las unidades originales."""
        model = self.get_sap_model()
        original = None
        try:
            original = model.GetPresentUnits()
        except Exception:
            pass
        try:
            model.SetPresentUnits(UNIDADES_N_MM_C)
        except Exception as e:
            raise RuntimeError(f"No se pudo fijar unidades mm en SAP2000: {_clean_com_error(e)}")
        return original if isinstance(original, int) else 0

    def restore_units(self, original: int) -> None:
        if not original or not self.is_connected:
            return
        try:
            self._sap_model.SetPresentUnits(original)
        except Exception:
            pass


class TrabajoCancelado(BaseException):
    """Lanzada en la siguiente operación COM tras una cancelación cooperativa.

    Hereda de BaseException (no de Exception) a propósito: los bloques
    `except Exception` del parseo tolerante de este módulo no deben
    tragarse la cancelación."""


class ControlTrabajo:
    """Contador de operaciones COM y bandera de cancelación de una lectura.
    Lo comparten el hilo de fondo que lee y la ruta que cancela."""

    def __init__(self):
        self._lock = threading.Lock()
        self.hechas = 0
        self.cancelado = False

    def paso(self) -> None:
        """Cuenta una operación COM; lanza TrabajoCancelado si se pidió cancelar."""
        with self._lock:
            if self.cancelado:
                raise TrabajoCancelado("Lectura de SAP2000 cancelada por el usuario.")
            self.hechas += 1

    def cancelar(self) -> None:
        with self._lock:
            self.cancelado = True


class ModelLink:
    """Adaptador con la misma interfaz que `SapLink` que usa `leer_fundacion_cruda`,
    sobre un SapModel ya adjunto por el puente. No conecta ni desconecta."""

    def __init__(self, sap_model, control: ControlTrabajo):
        self._sap_model = sap_model
        self._control = control

    @property
    def is_connected(self) -> bool:
        return self._sap_model is not None

    def get_sap_model(self):
        if not self.is_connected:
            raise RuntimeError("No hay conexión activa con SAP2000.")
        self._control.paso()
        return self._sap_model

    def set_units_mm(self) -> int:
        """Fija unidades N-mm-°C y devuelve las unidades originales."""
        model = self.get_sap_model()
        original = None
        try:
            original = model.GetPresentUnits()
        except Exception:
            pass
        try:
            model.SetPresentUnits(UNIDADES_N_MM_C)
        except Exception as e:
            raise RuntimeError(f"No se pudo fijar unidades mm en SAP2000: {_clean_com_error(e)}")
        return original if isinstance(original, int) else 0

    def restore_units(self, original: int) -> None:
        # Sin comprobar cancelación: debe ejecutarse también en el `finally`
        # de leer_fundacion_cruda aunque la lectura se haya cancelado.
        if not original or not self.is_connected:
            return
        try:
            self._sap_model.SetPresentUnits(original)
        except Exception:
            pass


# ---------------------------------------------------- parseo tolerante --


def _parse_name_list(ret) -> List[str]:
    """Normaliza GetNameList/variantes COM a lista de strings, tolerando
    firmas (ret, count, names) / (count, names) / lista de strings."""
    if isinstance(ret, (list, tuple)) and ret and all(isinstance(x, str) for x in ret):
        return [str(x) for x in ret]
    if not isinstance(ret, (list, tuple)):
        return []
    for i in range(len(ret) - 1):
        names = ret[i + 1]
        if isinstance(names, (list, tuple)):
            try:
                count = int(ret[i])
                return [str(n) for n in names[:count]]
            except Exception:
                if all(isinstance(n, str) for n in names):
                    return [str(n) for n in names]
    return []


def _arrays_de_respuesta(raw) -> List[list]:
    """Filtra los elementos list/tuple de una respuesta COM, en orden —
    descarta el código de retorno y contadores escalares sin asumir una
    posición fija (las firmas ByRef varían según la versión de SAP2000)."""
    if not isinstance(raw, (list, tuple)):
        return []
    return [list(x) for x in raw if isinstance(x, (list, tuple))]


def listar_grupos(link: SapLink) -> List[str]:
    model = link.get_sap_model()
    try:
        ret = model.GroupDef.GetNameList()
    except Exception as e:
        raise RuntimeError(f"No se pudo listar los grupos de SAP2000: {_clean_com_error(e)}")
    return _parse_name_list(ret)


def _llamar_get_assignments(model, nombre_grupo: str):
    try:
        return model.GroupDef.GetAssignments(nombre_grupo, 0, [], [])
    except Exception:
        return model.GroupDef.GetAssignments(nombre_grupo)


def _parsear_get_assignments(ret) -> Tuple[list, list]:
    if not isinstance(ret, (list, tuple)):
        return [], []
    for i in range(len(ret) - 1):
        tipos, nombres = ret[i], ret[i + 1]
        if (
            isinstance(tipos, (list, tuple))
            and isinstance(nombres, (list, tuple))
            and len(tipos) == len(nombres)
            and len(tipos) > 0
        ):
            try:
                return [int(t) for t in tipos], [str(n) for n in nombres]
            except Exception:
                continue
    return [], []


def miembros_de_grupo(link: SapLink, grupo: str) -> Dict[str, List[str]]:
    """Nodos (tipo 1) y shells (tipo 5) asignados a un grupo de SAP2000."""
    model = link.get_sap_model()
    try:
        ret = _llamar_get_assignments(model, grupo)
    except Exception as e:
        raise RuntimeError(f"No se pudieron leer los miembros del grupo '{grupo}': {_clean_com_error(e)}")
    tipos, nombres = _parsear_get_assignments(ret)
    return {
        "nodos": [nombres[i] for i in range(len(nombres)) if tipos[i] == 1],
        "shells": [nombres[i] for i in range(len(nombres)) if tipos[i] == 5],
    }


def coordenada(link: SapLink, nodo: str) -> Tuple[float, float, float]:
    """Coordenada cartesiana de un nodo (Point object), en las unidades
    activas del modelo (mm si se llamó `set_units_mm` antes)."""
    model = link.get_sap_model()
    try:
        # El código de retorno va al inicio (pywin32) o al final (comtypes): split_return lo ubica.
        codigo, salidas = split_return(model.PointObj.GetCoordCartesian(str(nodo)))
        numeros = [float(v) for v in salidas if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if codigo == 0 and len(numeros) >= 3:
            return (numeros[0], numeros[1], numeros[2])
    except Exception:
        pass
    try:
        import pythoncom
        import win32com.client

        x = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        y = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        z = win32com.client.VARIANT(pythoncom.VT_R8, 0.0)
        ret_code = model.PointObj.GetCoordCartesian(str(nodo), x, y, z)
        if ret_code == 0 or ret_code == (0,):
            return (float(x.value), float(y.value), float(z.value))
    except Exception:
        pass
    try:
        ret = model.PointObj.GetCoordCartesian(str(nodo))
        if isinstance(ret, (list, tuple)) and len(ret) >= 3:
            return (float(ret[0]), float(ret[1]), float(ret[2]))
    except Exception:
        pass
    raise RuntimeError(f"No se pudo leer la coordenada del nodo '{nodo}'.")


def puntos_de_area(link: SapLink, area: str) -> List[str]:
    """Nombres de los joints que forman el objeto de área tal como fue
    dibujado en SAP2000 (contorno del objeto, no de su malla de análisis)."""
    model = link.get_sap_model()
    try:
        raw = model.AreaObj.GetPoints(area)
    except Exception as e:
        raise RuntimeError(f"No se pudieron leer los puntos del área '{area}': {_clean_com_error(e)}")
    arrays = _arrays_de_respuesta(raw)
    for arr in arrays:
        if arr and all(isinstance(x, str) for x in arr):
            return [str(x) for x in arr]
    if isinstance(raw, (list, tuple)):
        nombres = [str(x) for x in raw if isinstance(x, str)]
        if nombres:
            return nombres
    raise RuntimeError(f"No se pudo interpretar AreaObj.GetPoints({area!r}).")


def seccion_de_area(link: SapLink, area: str) -> str:
    """Nombre de la propiedad de área (sección shell) asignada al objeto."""
    model = link.get_sap_model()
    try:
        ret = model.AreaObj.GetProperty(area)
    except Exception as e:
        raise RuntimeError(f"No se pudo leer la sección del área '{area}': {_clean_com_error(e)}")
    if isinstance(ret, (list, tuple)):
        nombres = [x for x in ret if isinstance(x, str) and x]
        if nombres:
            return nombres[0]
    if isinstance(ret, str) and ret:
        return ret
    raise RuntimeError(f"No se pudo interpretar AreaObj.GetProperty({area!r}).")


def elementos_de_area(link: SapLink, area: str) -> List[str]:
    """Nombres de los elementos de la malla de análisis (AreaElm) en que se
    subdivide un objeto de área — puede ser uno solo si no se subdividió."""
    model = link.get_sap_model()
    try:
        raw = model.AreaObj.GetElm(area)
    except Exception:
        return []
    arrays = _arrays_de_respuesta(raw)
    for arr in arrays:
        if arr and all(isinstance(x, str) for x in arr):
            return [str(x) for x in arr]
    if isinstance(raw, (list, tuple)):
        nombres = [str(x) for x in raw if isinstance(x, str)]
        if nombres:
            return nombres
    return []


def puntos_de_elemento(link: SapLink, elm: str) -> List[str]:
    """Nombres de los joints de un elemento de la malla de análisis
    (AreaElm) — incluye nodos intermedios que la malla automática agrega
    y que no son esquina del objeto de área (p. ej. el punto donde se
    apoya una columna al centro de una zapata de un solo shell)."""
    model = link.get_sap_model()
    try:
        raw = model.AreaElm.GetPoints(elm)
    except Exception:
        return []
    arrays = _arrays_de_respuesta(raw)
    for arr in arrays:
        if arr and all(isinstance(x, str) for x in arr):
            return [str(x) for x in arr]
    if isinstance(raw, (list, tuple)):
        nombres = [str(x) for x in raw if isinstance(x, str)]
        if nombres:
            return nombres
    return []


def joints_malla_de_area(link: SapLink, area: str) -> List[str]:
    """Todos los joints de la malla de análisis de un objeto de área
    (unión de los joints de cada uno de sus elementos), usada para detectar
    pedestales que se apoyan en un nodo interior generado por el mallado
    automático — mismo criterio que `diagramas_shell.py` en la app de
    referencia (`AreaObj.GetElm` + `AreaElm.GetPoints`)."""
    vistos = []
    for elm in elementos_de_area(link, area):
        for nombre in puntos_de_elemento(link, elm):
            if nombre not in vistos:
                vistos.append(nombre)
    return vistos


def espesor_de_seccion(link: SapLink, nombre_seccion: str) -> Optional[float]:
    """Espesor (mm, con unidades ya fijadas a N-mm-°C) de una propiedad de
    área tipo shell. La firma exacta de PropArea.GetShell no está
    verificada contra todas las versiones de SAP2000 (mismo problema ya
    documentado para otras llamadas OAPI de área): se buscan los valores
    float del resultado y se toma el más grande y razonable como espesor.
    Devuelve None si no se puede determinar — la UI lo deja editable."""
    model = link.get_sap_model()
    candidatos = []
    for metodo in ("GetShell", "GetShell_1"):
        try:
            fn = getattr(model.PropArea, metodo)
            ret = fn(nombre_seccion)
        except Exception:
            continue
        if not isinstance(ret, (list, tuple)):
            continue
        for v in ret:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if 0.5 <= fv <= 5000.0:  # rango plausible de espesor en mm
                candidatos.append(fv)
        if candidatos:
            break
    if not candidatos:
        return None
    return max(candidatos)


# --------------------------------------------------------- pedestales --
# Puerto de app/sap2000/pedestales_automaticos.py (Foundations SAP2000):
# frames verticales cuyo pie coincide con un joint que es esquina de algún
# shell del grupo, con su dimensión real leída de PropFrame.


def nombres_de_frames(link: SapLink) -> List[str]:
    model = link.get_sap_model()
    try:
        return _parse_name_list(model.FrameObj.GetNameList())
    except Exception:
        return []


def puntos_de_frame(link: SapLink, frame: str) -> Tuple[str, str]:
    model = link.get_sap_model()
    raw = model.FrameObj.GetPoints(frame)
    arrays = _arrays_de_respuesta(raw)
    if arrays:
        punto_i = str(arrays[0][0]) if arrays[0] else None
        punto_j = str(arrays[1][0]) if len(arrays) > 1 and arrays[1] else None
        if punto_i and punto_j:
            return punto_i, punto_j
    if isinstance(raw, (list, tuple)):
        candidatos = [str(x) for x in raw if isinstance(x, str)]
        if len(candidatos) >= 2:
            return candidatos[0], candidatos[1]
    raise RuntimeError(f"No se pudo interpretar FrameObj.GetPoints({frame!r}).")


def seccion_de_frame(link: SapLink, frame: str) -> str:
    model = link.get_sap_model()
    ret = model.FrameObj.GetSection(frame)
    if isinstance(ret, (list, tuple)):
        nombres = [x for x in ret if isinstance(x, str)]
        if nombres:
            return nombres[0]
    raise RuntimeError(f"No se pudo interpretar FrameObj.GetSection({frame!r}).")


def dimensiones_rectangulo_frame(link: SapLink, seccion: str) -> Tuple[float, float]:
    model = link.get_sap_model()
    ret = model.PropFrame.GetRectangle(seccion)
    # T3/T2 llegan como `float`; el código de retorno y `Color` son `int`
    # — filtrar por float aísla las dimensiones reales.
    dims = [v for v in ret if isinstance(v, float)] if isinstance(ret, (list, tuple)) else []
    if len(dims) >= 2 and dims[0] > 0 and dims[1] > 0:
        return float(dims[0]), float(dims[1])
    raise RuntimeError(f"PropFrame.GetRectangle({seccion!r}) no devolvió dimensiones válidas.")


def dimensiones_circulo_frame(link: SapLink, seccion: str) -> Tuple[float, float]:
    model = link.get_sap_model()
    ret = model.PropFrame.GetCircle(seccion)
    dims = [v for v in ret if isinstance(v, float)] if isinstance(ret, (list, tuple)) else []
    if dims and dims[0] > 0:
        return float(dims[0]), float(dims[0])
    raise RuntimeError(f"PropFrame.GetCircle({seccion!r}) no devolvió un diámetro válido.")


def angulo_local_frame(link: SapLink, frame: str) -> Optional[float]:
    """Ángulo crudo (grados) de los ejes locales del frame
    (`FrameObj.GetLocalAxes`), o None si no se pudo leer. Interpretarlo
    (orientación del lado largo) es cosa del servidor."""
    try:
        model = link.get_sap_model()
        # Salidas: (ang, advanced) con el código de retorno al inicio o al final.
        codigo, salidas = split_return(model.FrameObj.GetLocalAxes(frame))
        angulos = [v for v in salidas if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if codigo == 0 and angulos:
            return float(angulos[0])
    except Exception:
        pass
    return None


def leer_fundacion_cruda(link: SapLink, grupo: str) -> dict:
    """Lee de SAP2000 el dato crudo de un grupo (formato `sap-crudo-v1`,
    docs/CAD_FUNDACION_CRUDO.md): áreas, coordenadas de nudos y frames
    candidatos a pedestal. Sin geometría derivada."""
    miembros = miembros_de_grupo(link, grupo)
    nombres_shells = miembros["shells"]

    original_units = link.set_units_mm()
    try:
        areas: List[dict] = []
        nudos: Dict[str, Tuple[float, float, float]] = {}
        for area in nombres_shells:
            nombres_pts = puntos_de_area(link, area)
            for nombre_pt in nombres_pts:
                nudos[nombre_pt] = coordenada(link, nombre_pt)
            try:
                seccion = seccion_de_area(link, area)
            except RuntimeError:
                seccion = ""
            espesor = espesor_de_seccion(link, seccion) if seccion else None

            pts_malla = joints_malla_de_area(link, area)
            for nombre_pt in pts_malla:
                if nombre_pt not in nudos:
                    try:
                        nudos[nombre_pt] = coordenada(link, nombre_pt)
                    except RuntimeError:
                        continue
            if not pts_malla:
                pts_malla = list(nombres_pts)

            areas.append(
                {
                    "nombre": area,
                    "seccion": seccion,
                    "espesor": espesor,
                    "pts_nombres": list(nombres_pts),
                    "pts_malla": list(pts_malla),
                }
            )

        if not areas:
            raise RuntimeError(f"El grupo '{grupo}' no tiene shells asignados.")

        # Solo cuentan los frames con algún extremo en un joint de las áreas.
        en_areas = {n for a in areas for n in a["pts_nombres"] + a["pts_malla"]}
        frames: List[dict] = []
        for frame in nombres_de_frames(link):
            try:
                punto_i, punto_j = puntos_de_frame(link, frame)
                if punto_i not in en_areas and punto_j not in en_areas:
                    continue
                for p in (punto_i, punto_j):
                    if p not in nudos:
                        nudos[p] = coordenada(link, p)
                seccion = seccion_de_frame(link, frame)
            except Exception:
                continue
            rect = circ = None
            try:
                rect = list(dimensiones_rectangulo_frame(link, seccion))
            except Exception:
                try:
                    circ = list(dimensiones_circulo_frame(link, seccion))
                except Exception:
                    pass
            frames.append(
                {
                    "frame": frame,
                    "punto_i": punto_i,
                    "punto_j": punto_j,
                    "seccion": seccion,
                    "rect": rect,
                    "circ": circ,
                    "angulo_local": angulo_local_frame(link, frame),
                }
            )
    finally:
        link.restore_units(original_units)

    return {
        "formato": FORMATO_CRUDO,
        "grupo": grupo,
        "unidades": "mm",
        "areas": areas,
        "nudos": {k: list(v) for k, v in nudos.items()},
        "frames": frames,
    }
