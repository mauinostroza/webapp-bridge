"""SAP2000 acciones para Equilibrio plástico: combos, Groups de diseño,
fuerza axial de diagonales y geometría (H, L), en vivo.

Puerto de la parte en vivo de ``sap2000_import.py`` (proyecto original,
Equilibrio plástico) y del flujo de ``sap2000_import_dialog.py`` («por nivel» y
«por Group de diseño») al patrón de este repositorio: sin conexión COM propia,
cada operación recibe el ``SapModel`` ya conectado a través del actor STA de la
sesión y las rutas se instalan desde ``bridge.actions.install_routes`` con el
mismo envoltorio ``call`` y la misma autenticación por token, igual que
``bridge/hormigon.py``.

Las lecturas COM (``bridge/equilibrio_sap.py``) son copia literal de las
funciones del original: mismas llamadas, indexado de resultados, tolerancia a
firma corta o larga y mensajes de error. Igual que el original, NO se cambian
las unidades presentes de SAP2000: los valores salen en las unidades con que
el modelo esté abierto (el original las documenta como tonf y m).

Lógica del diálogo original portada:

* Por nivel: ``Tdis`` = ``get_frame_axial_force`` (máxima |P| entre
  estaciones) del combo de sismo; ``qgrav`` = ``get_frame_axial_force_gravity``
  del combo de gravedad; ``H, L`` = ``get_frame_geometry``.
* Por Group: un miembro por frame (``ObjectType == 2``) con
  ``Tn_req = Pn_req = máxima |P|`` del combo elegido (el original iguala
  tracción y compresión); si falla la geometría usa ``H = L = 5.0``; si falla
  la fuerza, el miembro se omite y el error queda en ``avisos`` del grupo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, field_validator

from bridge import equilibrio_sap as sap
from bridge.actions import ActionValidationError, _valid_name

_MAX_NAME = 100
_MAX_MEMBERS = 200
_DEFAULT_HL = 5.0  # H, L de respaldo del diálogo original si falla la geometría


class SapEquilibrioError(RuntimeError):
    """Error de lectura de SAP2000 para Equilibrio plástico.

    ``detail`` sigue el mismo criterio estructurado que
    ``bridge.hormigon.SapHormigonError``: el envoltorio ``call`` de
    ``bridge.actions.install_routes`` lo usa como cuerpo del 409.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.detail = {"code": "sap_error", "message": message}


def list_combos(model: Any) -> list[str]:
    """Port de ``list_combo_names``."""
    return sap.list_combo_names(model)


def list_groups(model: Any) -> list[str]:
    """Port de ``list_group_names``."""
    return sap.list_group_names(model)


def list_group_members(model: Any, group: str) -> list[str]:
    """Port de ``list_group_frame_members`` (sólo ``ObjectType == 2``)."""
    members = sap.list_group_frame_members(model, group)
    if len(members) > _MAX_MEMBERS:
        raise SapEquilibrioError(f"El grupo '{group}' tiene más de {_MAX_MEMBERS} miembros.")
    return members


def get_frame_data(
    model: Any, frame: str, combo_sismo: str | None = None, combo_grav: str | None = None
) -> dict[str, Any]:
    """Flujo «por nivel» del diálogo original para una diagonal."""
    (h, length), ok, msg = sap.get_frame_geometry(model, frame)
    if not ok:
        raise SapEquilibrioError(msg)
    avisos: list[str] = []
    axial_sismo = axial_grav = None
    if combo_sismo:
        val, ok, msg = sap.get_frame_axial_force(model, frame, combo_sismo)
        if ok:
            axial_sismo = val
        else:
            avisos.append(msg)
    if combo_grav:
        val, ok, msg = sap.get_frame_axial_force_gravity(model, frame, combo_grav)
        if ok:
            axial_grav = val
        else:
            avisos.append(msg)
    return {
        "frame": frame,
        "H": h,
        "L": length,
        "axial_sismo": axial_sismo,
        "axial_grav": axial_grav,
        "avisos": avisos,
    }


def get_group_demands(model: Any, group: str, combo_sismo: str | None = None) -> dict[str, Any]:
    """Flujo «por Group de diseño» del diálogo original."""
    frames = list_group_members(model, group)
    members: list[dict[str, Any]] = []
    avisos: list[str] = []
    if not combo_sismo:
        # El original omite las filas sin combo elegido.
        return {"group": group, "members": members, "avisos": avisos}
    for frame in frames:
        force, ok, msg = sap.get_frame_axial_force(model, frame, combo_sismo)
        if not ok:
            avisos.append(f"Grupo — {frame}: {msg}")
            continue
        member_avisos: list[str] = []
        (h, length), ok_h, msg_h = sap.get_frame_geometry(model, frame)
        if not ok_h:
            member_avisos.append(f"Grupo — {frame}: {msg_h}")
            h, length = _DEFAULT_HL, _DEFAULT_HL
        members.append(
            {"label": frame, "Tn_req": force, "Pn_req": force, "H": h, "L": length, "avisos": member_avisos}
        )
    return {"group": group, "members": members, "avisos": avisos}


# ---------------------------------------------------------------------------
# Cuerpos Pydantic
# ---------------------------------------------------------------------------


def _short_name(value: str, label: str) -> str:
    value = _valid_name(value, label)
    if len(value) > _MAX_NAME:
        raise ValueError(f"{label} must be at most {_MAX_NAME} characters")
    return value


def _optional_name(value: str | None, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _short_name(value, label)


class FrameDataBody(BaseModel):
    frame: str
    combo_sismo: str | None = None
    combo_grav: str | None = None

    @field_validator("frame")
    @classmethod
    def frame_name(cls, value: str) -> str:
        return _short_name(value, "frame")

    @field_validator("combo_sismo")
    @classmethod
    def sismo_name(cls, value: str | None) -> str | None:
        return _optional_name(value, "combinación de sismo")

    @field_validator("combo_grav")
    @classmethod
    def grav_name(cls, value: str | None) -> str | None:
        return _optional_name(value, "combinación de gravedad")


class GroupDemandsBody(BaseModel):
    group: str
    combo_sismo: str | None = None

    @field_validator("group")
    @classmethod
    def group_name(cls, value: str) -> str:
        return _short_name(value, "grupo")

    @field_validator("combo_sismo")
    @classmethod
    def sismo_name(cls, value: str | None) -> str | None:
        return _optional_name(value, "combinación de sismo")


def _query_group(group: str) -> str:
    """Valida el parámetro de consulta ``group`` (422 vía ``call``)."""
    try:
        return _short_name(group, "grupo")
    except ActionValidationError:
        raise
    except ValueError as exc:
        raise ActionValidationError(str(exc)) from exc


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/equilibrio/*`` con el mismo
    envoltorio ``call`` (actor STA, token y mapeo de errores a HTTP) que
    ``bridge.hormigon``."""
    prefix = "/v1/sap/actions/equilibrio"

    @app.get(f"{prefix}/combos")
    def equilibrio_combos():
        return call(lambda model: {"combos": list_combos(model)})

    @app.get(f"{prefix}/groups")
    def equilibrio_groups():
        return call(lambda model: {"groups": list_groups(model)})

    @app.get(f"{prefix}/group-members")
    def equilibrio_group_members(group: str):
        def read(model):
            name = _query_group(group)
            return {"group": name, "members": list_group_members(model, name)}

        return call(read)

    @app.post(f"{prefix}/frame-data")
    def equilibrio_frame_data(body: FrameDataBody):
        return call(lambda model: get_frame_data(model, body.frame, body.combo_sismo, body.combo_grav))

    @app.post(f"{prefix}/group-demands")
    def equilibrio_group_demands(body: GroupDemandsBody):
        return call(lambda model: get_group_demands(model, body.group, body.combo_sismo))
