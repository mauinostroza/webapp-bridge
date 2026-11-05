# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.viewer.sap2000.fake_model import FakeSapObject
from bridge.viewer.sap2000.reader_results import _load_case_names, _load_combination_names
from bridge.viewer.sap2000.reader_utils import _names
from bridge.viewer.sap2000.units import UNIT_CODES, unit_label


@dataclass
class ConnectionInfo:
    connected: bool = False
    version: str = ""
    model_path: str = ""
    model_name: str = ""
    units: str = ""
    locked: bool = False
    message: str = ""


class Sap2000Connector:
    def __init__(self) -> None:
        self.sap_object: Any | None = None
        self.model: Any | None = None
        # Modo demo (FakeSapObject): nunca se intenta reconectar por COM.
        self.is_demo: bool = False
        # Refleja settings.auto_unlock. Por defecto True para conservar el
        # comportamiento actual: un modelo bloqueado sigue siendo "editable"
        # porque execute_pending() lo desbloquea justo antes de escribir
        # (app/chat_controller.py). Solo cuando el auto-desbloqueo está
        # apagado un modelo bloqueado cuenta como no editable.
        self.auto_unlock: bool = True

    # ── Salud de la conexión ──────────────────────────────────────────

    def is_alive(self) -> bool:
        """Sonda barata que distingue un proxy COM vivo de uno muerto.

        `self.model is not None` no sirve: si el usuario cierra SAP2000, el
        proxy queda apuntando a un servidor inexistente y toda llamada
        posterior lanza COMError. Una llamada trivial es la única forma de
        saberlo.
        """
        if self.model is None:
            return False
        try:
            self.model.GetModelFilename()
            return True
        except Exception:
            return False

    def _mark_connection_lost(self) -> None:
        self.sap_object = None
        self.model = None
        self.is_demo = False

    def disconnect(self) -> None:
        """Suelta la conexión explícitamente (antes no existía)."""
        self._mark_connection_lost()

    def ensure_connected(self) -> bool:
        """Reintenta el attach una vez si el proxy murió.

        Devuelve True si al terminar hay una conexión usable. En modo demo
        no se reconecta (no hay nada a lo que reconectarse).
        """
        if self.is_alive():
            return True
        if self.is_demo:
            return False
        self._mark_connection_lost()
        return self.connect_to_open_instance().connected

    @property
    def is_connected(self) -> bool:
        return self.is_alive()

    def has_open_model(self) -> bool:
        if self.model is None:
            return False
        try:
            return bool(self.model.GetModelFilename())
        except Exception:
            # Que esta llamada falle es justamente la prueba de que la
            # conexión murió. Antes se devolvía True aquí, lo que dejaba
            # pasar escrituras contra un proxy muerto (safety/validator.py
            # usa este predicado como compuerta).
            self._mark_connection_lost()
            return False

    def has_api_access(self) -> bool:
        return self.is_alive()

    def is_model_editable(self) -> bool:
        if not self.is_alive():
            return False
        if self.auto_unlock:
            return True
        return not bool(_call_first(self.model, ["GetModelIsLocked"], default=False))

    def can_backup(self) -> bool:
        if not self.model:
            return False
        try:
            path = Path(str(self.model.GetModelFilename()))
            return bool(path.name) and path.parent.exists()
        except Exception:
            return False

    def group_exists(self, name: str) -> bool:
        return _exists_in_name_list(self.model.GroupDef.GetNameList, name) if self.model else False

    def material_exists(self, name: str) -> bool:
        return _exists_in_name_list(self.model.PropMaterial.GetNameList, name) if self.model else False

    def section_exists(self, name: str) -> bool:
        if not self.model:
            return False
        for prop_name in ("PropFrame", "PropArea", "PropCable", "PropTendon"):
            obj = getattr(self.model, prop_name, None)
            if obj and _exists_in_name_list(obj.GetNameList, name):
                return True
        return False

    def load_case_exists(self, name: str) -> bool:
        return _exists_in_names(_load_case_names(self.model), name) if self.model else False

    def combination_exists(self, name: str) -> bool:
        return _exists_in_names(_load_combination_names(self.model), name) if self.model else False

    def count_group_objects(self, name: str) -> int | None:
        if not self.model:
            return None
        try:
            result = self.model.GroupDef.GetAssignments(name)
            arrays = [x for x in result if isinstance(x, (list, tuple))]
            return len(arrays[-1]) if arrays else None
        except Exception:
            return None

    def connect_to_open_instance(self) -> ConnectionInfo:
        """Se adjunta a una instancia de SAP2000 ya abierta.

        Prueba varias estrategias en orden, la primera que funcione gana:

        1. `GetModule` + `cHelper` — el patrón oficial de CSI. Genera los
           wrappers de la typelib, lo que habilita *early binding*: las
           funciones ByRef devuelven tuplas de forma determinista en vez de
           la forma variable del enlace tardío.
        2. `cHelper` sin typelib, por si los wrappers no se pueden generar
           (SAP2000 instalado en una ruta no estándar, permisos, etc.).
        3. `GetActiveObject` sobre la lista de ProgIDs — la estrategia
           histórica de este repo, conservada como último recurso.
        """
        try:
            import comtypes.client  # type: ignore  # noqa: F401
        except Exception as exc:
            return ConnectionInfo(message=f"comtypes no está disponible: {exc}")

        errors: list[tuple[str, Exception]] = []
        for label, strategy in (
            ("typelib+cHelper", lambda: _attach_via_helper(use_typelib=True)),
            ("cHelper", lambda: _attach_via_helper(use_typelib=False)),
            ("GetActiveObject", _attach_via_get_active_object),
        ):
            try:
                sap_object = strategy()
            except Exception as exc:
                errors.append((label, exc))
                continue
            model = getattr(sap_object, "SapModel", None)
            if model is None:
                errors.append((label, RuntimeError("El objeto obtenido no expone SapModel.")))
                continue
            self.sap_object = sap_object
            self.model = model
            self.is_demo = False
            return self.info()

        self._mark_connection_lost()
        return ConnectionInfo(message=_diagnose_failure(errors))

    def launch_new_instance(self, exe_path: str | None = None) -> ConnectionInfo:
        """Lanza una instancia nueva de SAP2000 y se adjunta a ella.

        Deliberadamente NO se llama desde connect_to_open_instance(): abrir
        SAP2000 es lento y muy visible, así que solo debe ocurrir cuando el
        usuario lo pide explícitamente.
        """
        try:
            import comtypes.client  # type: ignore
        except Exception as exc:
            return ConnectionInfo(message=f"comtypes no está disponible: {exc}")

        exe = exe_path or _find_sap_exe()
        if not exe:
            return ConnectionInfo(
                message=(
                    "No se encontró el ejecutable de SAP2000 para lanzarlo. "
                    "Indica la ruta a SAP2000.exe manualmente."
                )
            )
        try:
            helper = _make_helper(use_typelib=True)
        except Exception:
            try:
                helper = _make_helper(use_typelib=False)
            except Exception as exc:
                return ConnectionInfo(message=f"No se pudo crear el helper de SAP2000: {exc}")
        try:
            sap_object = helper.CreateObject(exe)
            sap_object.ApplicationStart()
        except Exception as exc:
            return ConnectionInfo(message=f"No se pudo iniciar SAP2000 desde {exe}: {exc}")
        model = getattr(sap_object, "SapModel", None)
        if model is None:
            return ConnectionInfo(message="SAP2000 se inició pero no expuso SapModel.")
        self.sap_object = sap_object
        self.model = model
        self.is_demo = False
        return self.info()

    def connect_demo(self) -> ConnectionInfo:
        self.sap_object = FakeSapObject()
        self.model = self.sap_object.SapModel
        self.is_demo = True
        return self.info()

    def info(self) -> ConnectionInfo:
        if not self.model:
            return ConnectionInfo(message="Sin conexión.")
        if not self.is_alive():
            # Sin esto, un proxy muerto seguía renderizando "✓ Conectado"
            # con versión y ruta en blanco, porque _call_first se traga
            # todos los errores por separado.
            self._mark_connection_lost()
            return ConnectionInfo(
                message="Se perdió la conexión con SAP2000 (¿se cerró la aplicación?). Vuelve a conectar."
            )
        version = _call_first(self.sap_object, ["GetVersion"], default="")
        model_path = _call_first(self.model, ["GetModelFilename"], default="")
        units_code = _call_first(self.model, ["GetPresentUnits"], default=None)
        locked = bool(_call_first(self.model, ["GetModelIsLocked"], default=False))
        name = Path(str(model_path)).name if model_path else ""
        return ConnectionInfo(
            connected=True,
            version=str(version),
            model_path=str(model_path),
            model_name=name,
            units=unit_label(units_code),
            locked=locked,
            message="Conectado.",
        )

    def set_units(self, unit_name: str) -> None:
        if not self.model:
            raise RuntimeError("SAP2000 no está conectado.")
        code = UNIT_CODES.get(unit_name)
        if code is None:
            raise ValueError(f"Unidad no soportada: {unit_name}")
        ret = self.model.SetPresentUnits(code)
        _ensure_ok(ret, f"No se pudieron ajustar las unidades a {unit_name}.")

    def unlock_if_needed(self, enabled: bool = True) -> bool:
        if not self.model or not enabled:
            return False
        locked = bool(_call_first(self.model, ["GetModelIsLocked"], default=False))
        if locked:
            ret = self.model.SetModelIsLocked(False)
            _ensure_ok(ret, "No se pudo desbloquear el modelo.")
            return True
        return False


# ── Descubrimiento de la instalación y estrategias de attach ──────────

# Versiones soportadas, de la más nueva a la más antigua. Antes solo se
# probaba v23, y el mensaje de error lo daba por hecho.
_SAP_VERSIONS: tuple[int, ...] = (25, 24, 23, 22, 21)
_PROG_IDS: tuple[str, ...] = ("CSI.SAP2000.API.SapObject",) + tuple(
    f"Sap2000v{version}.SapObject" for version in _SAP_VERSIONS
)


def _install_roots() -> list[Path]:
    """Carpetas de instalación de SAP2000, de la más nueva a la más antigua."""
    roots: list[Path] = []
    seen: set[str] = set()
    for base in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        r"C:\Program Files",
    ):
        if not base:
            continue
        csi = Path(base) / "Computers and Structures"
        key = str(csi).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if not csi.is_dir():
                continue
            for version in _SAP_VERSIONS:
                candidate = csi / f"SAP2000 {version}"
                if candidate.is_dir() and candidate not in roots:
                    roots.append(candidate)
            # Cualquier otra carpeta SAP2000 fuera del rango conocido.
            for child in sorted(csi.glob("SAP2000*"), reverse=True):
                if child.is_dir() and child not in roots:
                    roots.append(child)
        except Exception:
            continue
    return roots


def _find_install_file(filename: str) -> str:
    for root in _install_roots():
        candidate = root / filename
        try:
            if candidate.is_file():
                return str(candidate)
        except Exception:
            continue
    return ""


def _find_sap_dll() -> str:
    return _find_install_file("SAP2000v1.dll")


def _find_sap_exe() -> str:
    return _find_install_file("SAP2000.exe")


def _make_helper(use_typelib: bool) -> Any:
    """Crea el `cHelper` de CSI, con o sin wrappers de typelib.

    Con typelib se obtiene *early binding*, que vuelve deterministas las
    tuplas devueltas por las funciones ByRef de la API.
    """
    import comtypes.client  # type: ignore

    helper = comtypes.client.CreateObject("SAP2000v1.Helper")
    if not use_typelib:
        return helper
    dll = _find_sap_dll()
    if not dll:
        raise RuntimeError("No se encontró SAP2000v1.dll para generar los wrappers de la typelib.")
    comtypes.client.GetModule(dll)
    import comtypes.gen.SAP2000v1 as sap  # type: ignore

    return helper.QueryInterface(sap.cHelper)


def _attach_via_helper(use_typelib: bool) -> Any:
    return _make_helper(use_typelib).GetObject("CSI.SAP2000.API.SapObject")


def _attach_via_get_active_object() -> Any:
    import comtypes.client  # type: ignore

    errors: list[str] = []
    for prog_id in _PROG_IDS:
        try:
            return comtypes.client.GetActiveObject(prog_id)
        except Exception as exc:
            errors.append(f"{prog_id}: {exc}")
    raise RuntimeError(" | ".join(errors))


def _diagnose_failure(errors: list[tuple[str, Exception]]) -> str:
    """Traduce los fallos COM a causas accionables.

    Antes se volcaban dos `repr` de excepción de Python, que no le dicen
    nada al usuario sobre lo que tiene que hacer.
    """
    detail = " | ".join(f"{label}: {exc}" for label, exc in errors)
    blob = detail.lower()
    causes: list[str] = []
    if any(token in blob for token in ("-2147221021", "mk_e_unavailable", "operation unavailable")):
        causes.append(
            "SAP2000 no parece estar abierto con un modelo cargado, o se ejecuta con un "
            "nivel de privilegios distinto al de esta aplicación (si abriste SAP2000 como "
            "administrador, abre también esta aplicación como administrador)."
        )
    if any(
        token in blob
        for token in ("-2147221164", "regdb_e_classnotreg", "-2147221005", "invalid class string")
    ):
        causes.append(
            "La API COM de SAP2000 no está registrada en este equipo: reinstala SAP2000 o "
            "ábrelo una vez como administrador para que se registre."
        )
    if any(token in blob for token in ("-2147024891", "access is denied", "acceso denegado")):
        causes.append(
            "Windows denegó el acceso al objeto COM, típicamente por un desajuste de "
            "privilegios entre SAP2000 y esta aplicación."
        )
    if not causes:
        causes.append("No se pudo adjuntar a ninguna instancia abierta de SAP2000.")
    causes.append(
        'Comprueba además que "Allow API Access" esté activado en SAP2000 '
        "(Options > Allow API Access) y que haya un modelo abierto."
    )
    return "No se pudo conectar con SAP2000.\n- " + "\n- ".join(causes) + f"\n\nDetalle técnico: {detail}"


def _call_first(obj: Any, names: list[str], default: Any = None) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)()
            if isinstance(value, tuple):
                if len(value) == 2 and isinstance(value[0], int):
                    return value[1]
                if len(value) > 1:
                    return value[-1]
            return value
        except Exception:
            continue
    return default


def _ensure_ok(ret: Any, message: str) -> None:
    code = ret[0] if isinstance(ret, tuple) and ret else ret
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"{message} Código SAP2000: {code}")


def _exists_in_name_list(func: Any, name: str) -> bool:
    try:
        result = func()
    except Exception:
        return False
    return _exists_in_names(_names(result), name)


def _exists_in_names(names: list[str], name: str) -> bool:
    return any(item.lower() == name.lower() for item in names)
