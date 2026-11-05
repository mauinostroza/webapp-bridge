"""SAP2000 acciones para Esbelteces: extracción de frames, grupos, secciones y K.

Puerto fiel de ``esb_app/sap/sap_client.py`` (proyecto de escritorio original) al
patrón de este repositorio: sin estado COM propio, todo pasa por el actor STA de
la sesión (ver ``bridge/sap2000_agent.py``) y las rutas se instalan desde
``bridge.actions.install_routes`` con la misma autenticación por token.

Unidades de trabajo: mm y MPa (``eUnits.N_mm_C = 9``), fijadas temporalmente
para cada extracción y restauradas siempre, igual que ``SapUnitsContext`` en el
original.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bridge.actions import _get_name_list, _ret, _valid_name, group_members

N_MM_C = 9  # eUnits.N_mm_C: longitudes en mm, esfuerzos en MPa.

_PROP_TYPE_NAMES = {
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


def _prop_type_to_name(prop_type: int) -> str:
    return _PROP_TYPE_NAMES.get(prop_type, f"Type_{prop_type}")


def _ret_is_first(value: Any, first_output_is_text: bool = True) -> bool:
    """Whether the binding put the return code before the outputs.

    comtypes (and the original desktop app) return ``(out1, out2, ..., ret)``; pywin32
    late binding can return ``(ret, out1, out2, ...)``. Only meaningful for calls whose
    first output is text (``GetSection``): the code is then the only leading ``int``.
    """
    return (
        first_output_is_text
        and isinstance(value, (list, tuple))
        and len(value) > 0
        and isinstance(value[0], int)
        and not isinstance(value[0], bool)
    )


def _outputs(value: Any, ret_first: bool) -> tuple[int, list[Any]] | None:
    """Split a tuple return into ``(code, outputs)`` honoring where the code sits."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        if ret_first:
            return int(value[0]), list(value[1:])
        return int(value[-1]), list(value[:-1])
    except (TypeError, ValueError):
        return None


def _ok_outputs(value: Any, ret_first: bool) -> list[Any] | None:
    """Outputs of a call when its return code is 0, else ``None``."""
    split = _outputs(value, ret_first)
    if split is None or split[0] != 0:
        return None
    return split[1]


class DesignCodeError(RuntimeError):
    """The active SAP2000 steel design code is not AISC 360-16."""

    def __init__(self, code: str):
        message = (
            f"El código de diseño de acero activo en SAP2000 es '{code}', no AISC 360-16. "
            "No se escribió ningún valor."
        )
        super().__init__(message)
        self.detail = {"code": "design_code", "message": message}


@contextmanager
def _units_context(model: Any):
    """Force ``eUnits.N_mm_C`` for the block and restore the original units.

    Literal port of ``SapUnitsContext``: always sets the target unit (even if
    already active) and restores in a ``finally``; a restore failure is
    swallowed, exactly like the original's best-effort restore.
    """
    original = None
    try:
        original = model.GetPresentUnits()
        _ret(model.SetPresentUnits(N_MM_C), "SetPresentUnits(N_mm_C)")
        yield original
    finally:
        if original is not None:
            try:
                _ret(model.SetPresentUnits(original), "SetPresentUnits(restore)")
            except Exception:
                pass


@dataclass
class FrameSectionData:
    """Datos extraídos de un frame + su sección asignada. Ver ``FrameSectionData``
    en ``esb_app/sap/sap_client.py`` (original): mismos campos, mismo orden."""

    frame_name: str
    section_name: str
    prop_type: int
    prop_type_name: str

    h_mm: float | None = None
    b_mm: float | None = None
    tf_mm: float | None = None
    tw_mm: float | None = None

    material_name: str = ""
    fy_mpa: float = 0.0

    rx_mm: float | None = None
    ry_mm: float | None = None
    area_mm2: float | None = None
    i33_mm4: float | None = None
    i22_mm4: float | None = None

    length_mm: float | None = None

    rz_mm: float | None = None
    is_rz_calculated: bool = False

    is_user_defined: bool = False
    notes: str = ""

    group_category: str | None = None


class _RetCodeError(RuntimeError):
    """A SAP OAPI call in the length lookup returned a nonzero code."""


def _modeled_length_mm(model: Any, frame_name: str, ret_first: bool = False) -> float | None:
    """Length of a frame from its two end points. Port of ``get_modeled_length_mm``."""
    try:
        points = model.FrameObj.GetPoints(frame_name, "", "")
        if not isinstance(points, (list, tuple)) or len(points) < 3:
            raise _RetCodeError("Unexpected SAP2000 response from FrameObj.GetPoints")
        _ret(points, "FrameObj.GetPoints")
        names = _outputs(points, ret_first)
        if names is None or len(names[1]) < 2:
            raise _RetCodeError("Unexpected SAP2000 response from FrameObj.GetPoints")
        p1, p2 = str(names[1][0]), str(names[1][1])

        c1 = model.PointObj.GetCoordCartesian(p1, 0.0, 0.0, 0.0)
        c2 = model.PointObj.GetCoordCartesian(p2, 0.0, 0.0, 0.0)
        if not isinstance(c1, (list, tuple)) or not isinstance(c2, (list, tuple)):
            raise _RetCodeError("Unexpected SAP2000 response from PointObj.GetCoordCartesian")
        _ret(c1, "PointObj.GetCoordCartesian(p1)")
        _ret(c2, "PointObj.GetCoordCartesian(p2)")

        o1, o2 = _outputs(c1, ret_first), _outputs(c2, ret_first)
        if o1 is None or o2 is None or len(o1[1]) < 3 or len(o2[1]) < 3:
            raise _RetCodeError("Unexpected SAP2000 response from PointObj.GetCoordCartesian")
        x1, y1, z1 = float(o1[1][0]), float(o1[1][1]), float(o1[1][2])
        x2, y2, z2 = float(o2[1][0]), float(o2[1][1]), float(o2[1][2])
        return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)
    except _RetCodeError:
        raise
    except Exception:
        return None


def _get_fy_mpa(model: Any, mat_prop: str, ret_first: bool = False) -> float:
    """Fy en MPa. Port de ``_get_fy_mpa``: contexto de unidades propio (anidado)."""
    if not mat_prop:
        return 0.0
    try:
        with _units_context(model):
            ret = model.PropMaterial.GetOSteel_1(mat_prop, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0)
            outs = _ok_outputs(ret, ret_first)
            return float(outs[0]) if outs else 0.0
    except Exception:
        return 0.0


def _get_section_props(
    model: Any, section_name: str, ret_first: bool = False
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """rx, ry, area, I33, I22 vía ``GetSectProps``. Port de ``_get_section_props``."""
    try:
        ret = model.PropFrame.GetSectProps(
            section_name, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        )
        outs = _ok_outputs(ret, ret_first)
        if outs:
            area = float(outs[0])
            i22 = float(outs[4])
            i33 = float(outs[5])
            if area > 0:
                r22 = math.sqrt(i22 / area)
                r33 = math.sqrt(i33 / area)
            else:
                r22 = r33 = None
            return r33, r22, area, i33, i22
        return None, None, None, None, None
    except Exception:
        return None, None, None, None, None


def extract_frame_data(model: Any, frame_name: str) -> FrameSectionData | None:
    """Extrae toda la información relevante de un frame. Port literal de
    ``SapClient.extract_frame_data``. Devuelve ``None`` en cualquier falla
    (mismo criterio del original: se registra y se omite el frame)."""
    try:
        with _units_context(model):
            ret_sec = model.FrameObj.GetSection(frame_name, "", "")
            _ret(ret_sec, "FrameObj.GetSection")
            # El código de retorno va al final con comtypes (app de escritorio) pero puede ir
            # al inicio con pywin32; GetSection devuelve texto, así que delata el orden.
            rf = _ret_is_first(ret_sec)
            sec_outs = _outputs(ret_sec, rf)
            prop_name = str(sec_outs[1][0]) if sec_outs else ""

            type_outs = _ok_outputs(model.PropFrame.GetTypeOAPI(prop_name, 0), rf)
            prop_type = int(type_outs[0]) if type_outs else 10

            length_mm = _modeled_length_mm(model, frame_name, rf)

            data = FrameSectionData(
                frame_name=frame_name,
                section_name=prop_name,
                prop_type=prop_type,
                prop_type_name=_prop_type_to_name(prop_type),
                length_mm=length_mm,
            )

            mat_prop = ""
            h = b = tf = tw = None

            if prop_type == 1:  # I / H
                data.prop_type_name = "I_H"
                ret = model.PropFrame.GetISection(prop_name, "", "", 0, 0, 0, 0, 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b, tf, tw = float(outs[2]), float(outs[3]), float(outs[4]), float(outs[5])

            elif prop_type == 2:  # Channel
                data.prop_type_name = "Channel"
                ret = model.PropFrame.GetChannel(prop_name, "", "", 0, 0, 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b, tf, tw = float(outs[2]), float(outs[3]), float(outs[4]), float(outs[5])

            elif prop_type == 4:  # Angle
                data.prop_type_name = "Angle"
                ret = model.PropFrame.GetAngle(prop_name, "", "", 0, 0, 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b, tf, tw = float(outs[2]), float(outs[3]), float(outs[4]), float(outs[5])

            elif prop_type == 6:  # Box / Tube (rectangular HSS)
                data.prop_type_name = "Box_HSS"
                ret = model.PropFrame.GetTube(prop_name, "", "", 0, 0, 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b, tf, tw = float(outs[2]), float(outs[3]), float(outs[4]), float(outs[5])

            elif prop_type == 7:  # Pipe (redondo)
                data.prop_type_name = "Pipe_HSS_Round"
                ret = model.PropFrame.GetPipe(prop_name, "", "", 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h = float(outs[2])
                    tw = float(outs[3])

            elif prop_type == 8:  # Rectangular sólido
                data.prop_type_name = "Rectangular"
                ret = model.PropFrame.GetRectangle(prop_name, "", "", 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b = float(outs[2]), float(outs[3])

            elif prop_type == 3:  # Tee
                data.prop_type_name = "Tee"
                ret = model.PropFrame.GetTee(prop_name, "", "", 0, 0, 0, 0, 0, "", "")
                outs = _ok_outputs(ret, rf)
                if outs:
                    mat_prop = str(outs[1])
                    h, b, tf, tw = float(outs[2]), float(outs[3]), float(outs[4]), float(outs[5])

            else:  # General / usuario
                data.prop_type_name = "General"
                data.is_user_defined = True
                try:
                    ret_gen = model.PropFrame.GetGeneral(
                        prop_name, "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "", ""
                    )
                    outs = _ok_outputs(ret_gen, rf)
                    if outs:
                        mat_prop = str(outs[1])
                        h, b = float(outs[2]), float(outs[3])
                except Exception:
                    pass

            if mat_prop:
                data.material_name = mat_prop
                data.fy_mpa = _get_fy_mpa(model, mat_prop, rf)

            rx, ry, area, i33, i22 = _get_section_props(model, prop_name, rf)
            data.rx_mm = rx
            data.ry_mm = ry
            data.area_mm2 = area
            data.i33_mm4 = i33
            data.i22_mm4 = i22

            # Ángulos: dato CRUDO. rz, el reemplazo de ry y las notas/avisos los calcula el servidor
            # (rz_mm = None, is_rz_calculated = False; ry_mm es el de SAP2000 sin tocar).

            data.h_mm = h
            data.b_mm = b
            data.tf_mm = tf
            data.tw_mm = tw

            return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Selección, grupos y secciones
# ---------------------------------------------------------------------------


def _selected_frame_names(model: Any) -> list[str]:
    """Nombres de los frames seleccionados. Port de ``get_selected_frame_names``."""
    try:
        raw = model.SelectObj.GetSelected(0, [], [])
    except TypeError:
        raw = model.SelectObj.GetSelected()
    if not isinstance(raw, (list, tuple)):
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    _ret(raw, "SelectObj.GetSelected")
    # Con el código de retorno al final los arreglos son (types, names) antes de él; con el
    # retorno al inicio (pywin32: ret, n, types, names) ``names`` es el último elemento.
    # Por eso se miran todos los elementos, no sólo ``raw[:-1]``.
    arrays = [item for item in raw if isinstance(item, (list, tuple))]
    if len(arrays) < 2:
        raise RuntimeError("Unexpected SAP2000 response from SelectObj.GetSelected")
    types, names = arrays[-2], arrays[-1]
    if len(types) != len(names):
        raise RuntimeError("SAP2000 SelectObj.GetSelected returned mismatched arrays")
    return [str(name) for object_type, name in zip(types, names) if int(object_type) == 2]


def selected_members(model: Any) -> list[dict[str, Any]]:
    """Extrae todos los frames seleccionados; los que fallan se omiten
    (mismo criterio de ``ImportWorker``: no aborta la importación completa)."""
    members: list[dict[str, Any]] = []
    for name in _selected_frame_names(model):
        data = extract_frame_data(model, name)
        if data is not None:
            members.append(asdict(data))
    return members


def _group_name_list(model: Any) -> list[str]:
    """Todos los nombres de grupo definidos. Port de ``get_group_names``."""
    return _get_name_list(model.GroupDef, "GroupDef.GetNameList", 0, [])


def group_names(model: Any) -> list[str]:
    return _group_name_list(model)


def group_frame_names(model: Any, group_name: str) -> list[str]:
    """Frames que pertenecen a ``group_name``. Port de ``get_group_frame_names``."""
    return group_members(model, group_name, 2)  # eObjectType.Frame


def import_by_groups(model: Any, category_map: dict[str, str]) -> list[dict[str, Any]]:
    """Port de ``ImportByGroupWorker``: dedup por frame, primera categoría gana,
    en el orden en que ``category_map`` fue entregado."""
    results: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for category, group_name in category_map.items():
        if not group_name:
            continue
        for name in group_frame_names(model, group_name):
            if name in seen:
                continue
            data = extract_frame_data(model, name)
            if data is not None:
                data.group_category = category
                seen[name] = category
                results.append(asdict(data))
    return results


def section_names(model: Any) -> list[str]:
    return _get_name_list(model.PropFrame, "PropFrame.GetNameList")


def apply_section(model: Any, frame_name: str, section_name: str) -> dict[str, Any]:
    """Asigna una sección y re-extrae. Port de ``SetSectionWorker``/``set_frame_section``."""
    try:
        model.SetModelIsLocked(False)
    except Exception:
        pass  # el modelo puede no estar bloqueado; no es un error fatal

    ret = model.FrameObj.SetSection(frame_name, section_name)
    _ret(ret, f"FrameObj.SetSection({frame_name!r}, {section_name!r})")

    data = extract_frame_data(model, frame_name)
    if data is None:
        raise RuntimeError(f"No se pudo re-extraer datos de {frame_name} tras el cambio de sección.")
    return {"member": asdict(data)}


def highlight_frames(model: Any, frame_names: list[str]) -> int:
    """Port de ``highlight_frames``/``HighlightWorker``."""
    _ret(model.SelectObj.ClearSelection(), "SelectObj.ClearSelection")
    for name in frame_names:
        _ret(model.FrameObj.SetSelected(name, True), f"FrameObj.SetSelected({name!r})")
    return len(frame_names)


# ---------------------------------------------------------------------------
# Factores de longitud efectiva K (Design Overwrite, AISC 360-16)
# ---------------------------------------------------------------------------

AISC360_16_ITEM_K2_MAJOR = 23
AISC360_16_ITEM_K2_MINOR = 24


def _steel_design_code(model: Any) -> str:
    ret = model.DesignSteel.GetCode("")
    _ret(ret, "DesignSteel.GetCode")
    outs = _outputs(ret, _ret_is_first(ret))
    return str(outs[1][0]) if outs else ""


def apply_k_factors(model: Any, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Port de ``ApplyKFactorsWorker``/``set_effective_length_factors``:
    valida el código de diseño una sola vez (no escribe nada si no es AISC
    360-16) y luego aplica cada Kx/Ky, acumulando fallas por frame sin
    abortar el resto."""
    code = _steel_design_code(model)
    normalized = code.upper().replace(" ", "").replace("-", "").replace("/", "")
    if not normalized.startswith("AISC36016"):
        raise DesignCodeError(code)

    applied = 0
    failures: list[dict[str, str]] = []
    for item in items:
        frame_name, kx, ky = item["frame"], item["kx"], item["ky"]
        try:
            if kx is not None and kx > 0:
                _ret(
                    model.DesignSteel.AISC360_16.SetOverwrite(
                        frame_name, AISC360_16_ITEM_K2_MAJOR, float(kx), 0
                    ),
                    f"DesignSteel.AISC360_16.SetOverwrite K2 Major ({frame_name!r})",
                )
            if ky is not None and ky > 0:
                _ret(
                    model.DesignSteel.AISC360_16.SetOverwrite(
                        frame_name, AISC360_16_ITEM_K2_MINOR, float(ky), 0
                    ),
                    f"DesignSteel.AISC360_16.SetOverwrite K2 Minor ({frame_name!r})",
                )
            applied += 1
        except Exception as exc:
            failures.append({"frame": frame_name, "error": str(exc)})

    return {"applied": applied, "failures": failures}


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------

_MAX_ITEMS = 500


class ImportGroupsBody(BaseModel):
    map: dict[str, str]

    @field_validator("map")
    @classmethod
    def bounded_map(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > _MAX_ITEMS:
            raise ValueError("map must contain at most 500 entries")
        return value


class SectionApplyBody(BaseModel):
    frame: str
    section: str
    confirmed: bool = False

    @field_validator("frame")
    @classmethod
    def frame_name(cls, value: str) -> str:
        return _valid_name(value, "frame")

    @field_validator("section")
    @classmethod
    def section_name_field(cls, value: str) -> str:
        return _valid_name(value, "section")


class HighlightBody(BaseModel):
    frames: list[str]

    @field_validator("frames")
    @classmethod
    def frame_names(cls, value: list[str]) -> list[str]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("frames must contain 1-500 entries")
        return [_valid_name(item, "frame") for item in value]


class KFactorItem(BaseModel):
    frame: str
    kx: float = Field(default=0.0, ge=0)
    ky: float = Field(default=0.0, ge=0)

    @field_validator("frame")
    @classmethod
    def frame_name(cls, value: str) -> str:
        return _valid_name(value, "frame")

    @field_validator("kx", "ky")
    @classmethod
    def finite_k(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("kx/ky must be finite")
        return value


class KFactorsBody(BaseModel):
    items: list[KFactorItem]
    confirmed: bool = False

    @field_validator("items")
    @classmethod
    def bounded_items(cls, value: list[KFactorItem]) -> list[KFactorItem]:
        if not value or len(value) > _MAX_ITEMS:
            raise ValueError("items must contain 1-500 entries")
        return value


def install_routes(app: Any, call: Callable[[Callable[[Any], Any]], Any]) -> None:
    """Instala las rutas ``/v1/sap/actions/esbelteces/*``.

    ``call`` es el mismo envoltorio de ``bridge.actions.install_routes`` que
    ejecuta la función dada en el actor STA y traduce las excepciones a
    respuestas HTTP (mismo criterio de errores y autenticación que el resto
    del agente).
    """
    from fastapi import HTTPException

    prefix = "/v1/sap/actions/esbelteces"

    @app.get(f"{prefix}/selected-raw")
    def esbelteces_selected_raw():
        return call(lambda model: {"members": selected_members(model)})

    @app.get(f"{prefix}/groups")
    def esbelteces_groups():
        return call(lambda model: {"groups": group_names(model)})

    @app.post(f"{prefix}/import-groups-raw")
    def esbelteces_import_groups_raw(body: ImportGroupsBody):
        return call(lambda model: {"members": import_by_groups(model, body.map)})

    @app.get(f"{prefix}/sections")
    def esbelteces_sections():
        return call(lambda model: {"sections": section_names(model)})

    @app.post(f"{prefix}/section/apply")
    def esbelteces_section_apply(body: SectionApplyBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to apply a section assignment")
        return call(lambda model: apply_section(model, body.frame, body.section))

    @app.post(f"{prefix}/highlight")
    def esbelteces_highlight(body: HighlightBody):
        return call(lambda model: {"count": highlight_frames(model, body.frames)})

    @app.post(f"{prefix}/k-factors/apply")
    def esbelteces_k_factors_apply(body: KFactorsBody):
        if not body.confirmed:
            raise HTTPException(422, "confirmed must be true to write K factors to SAP2000")
        items = [item.model_dump() for item in body.items]
        return call(lambda model: apply_k_factors(model, items))
