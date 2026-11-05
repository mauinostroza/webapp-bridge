"""Windows-only SAP2000 OAPI loopback agent.

Run on the engineering workstation:
  SAP2000_AGENT_TOKEN=<long random token> python -m uvicorn bridge.sap2000_agent:app --host 127.0.0.1 --port 8765
The token is mandatory. Do not bind this process to a public interface.
"""

from __future__ import annotations

import os
import queue
import secrets
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bridge import pairing
from bridge.actions import _call_display_table, _call_get_coord_cartesian, _get_name_list, _ret
from bridge.actions import group_members as _group_assignments  # noqa: F401 (re-exported for tests)
from bridge.actions import groups_from_model as _groups_from_model
from bridge.actions import model_is_locked as _model_is_locked
from bridge.actions import select_all_cases_for_output as _select_all_cases_for_output
from bridge.alcances import ruta_permitida
from bridge.com_tiempo import limite as com_limite
from bridge.hosted import FUSION_ORIGIN, HOSTED_ORIGIN, SHARED_APP_ORIGINS


class SapComError(RuntimeError):
    pass


class StaActor:
    def __init__(self):
        self._queue: queue.Queue[tuple[Callable[..., Any], tuple, dict, Future] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="sap2000-com-sta", daemon=True)
        self._thread.start()

    def _run(self):
        if sys.platform != "win32":
            return
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            while (job := self._queue.get()) is not None:
                fn, args, kwargs, future = job
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    future.set_exception(exc)
        finally:
            pythoncom.CoUninitialize()

    def call(self, fn, *args, **kwargs):
        if sys.platform != "win32":
            raise SapComError("SAP2000 COM agent requires Windows.")
        f: Future = Future()
        self._queue.put((fn, args, kwargs, f))
        return f.result(timeout=com_limite.get())


def _name_list(response: Any) -> list[str]:
    """Normalize SAP OAPI ``GetNameList`` COM return tuples.

    pywin32 normally returns ``(ret, count, names)``; a few generated OAPI
    wrappers expose ``(ret, names)``.  Treat a non-zero SAP return code as an
    error instead of returning a plausible but incomplete model.
    """
    if not isinstance(response, tuple) or len(response) < 2:
        raise SapComError("Unexpected SAP2000 GetNameList response")
    ret = int(response[0])
    if ret != 0:
        raise SapComError(f"SAP2000 GetNameList returned {ret}")
    names = response[-1]
    if names is None:
        return []
    return [str(name) for name in names]


class SapSession:
    # These mirror the shipped profile/combination integrations.  Attaching is
    # deliberately limited to existing SAP processes: the local agent must not
    # launch a new model behind a browser request.
    PROG_IDS = ("CSI.SAP2000.API.SapObject", "CSI.SAP2000.API.SapObject.1", "Sap2000.SapObject")
    HELPER_PROG_IDS = ("CSI.SAP2000.API.Helper", "CSI.SAP2000.Helper", "SAP2000v1.Helper", "SAP2000.Helper")

    def __init__(self):
        self.actor = StaActor()
        self.sap_object = None
        self.model = None

    def attach(self, model_path: str | None = None):
        def model_filename(model: Any) -> str:
            """Normalize GetModelFilename across pywin32/comtypes return shapes."""
            raw = model.GetModelFilename()
            if isinstance(raw, (tuple, list)):
                strings = [item for item in raw if isinstance(item, str)]
                # SAP's generated wrappers have returned both (path, ret) and
                # (ret, path). Prefer the non-empty filename, never stringify
                # the whole COM tuple (which breaks the source/target guard).
                path = next((item.strip() for item in strings if item.strip()), "")
                if len(raw) > 1:
                    numeric = [item for item in (raw[0], raw[-1]) if isinstance(item, int)]
                    if numeric and all(code != 0 for code in numeric):
                        raise SapComError(f"GetModelFilename returned {numeric[-1]}")
                return path
            return str(raw or "").strip()

        def registered_clsid(prog_id: str) -> str:
            """Resolve a ProgID without asking Dispatch to launch a new process.

            Some packaged pywin32 builds omit pythoncom.CLSIDFromProgID. The
            Windows COM registration is the safe fallback; GetActiveObject
            still attaches only to a running object.
            """
            try:
                import pythoncom

                resolver = getattr(pythoncom, "CLSIDFromProgID", None)
                if callable(resolver):
                    return str(resolver(prog_id))
            except Exception:
                pass
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\CLSID") as key:
                value, _kind = winreg.QueryValueEx(key, None)
            if not isinstance(value, str) or not value.strip():
                raise SapComError(f"Windows registry has no CLSID for {prog_id}")
            return value.strip()

        def accept(obj, method, prog_id):
            model = getattr(obj, "SapModel", None)
            if model is None:
                raise SapComError(f"{method} returned an object without SapModel")
            # Validate one harmless API call before keeping COM references.
            path = model_filename(model)
            if not path:
                raise SapComError(f"{method} returned an unsaved model without a stable filename")
            try:
                model.GetPresentUnits()
            except Exception as exc:
                raise SapComError(f"{method} returned an unusable SapModel: {exc}") from exc
            if model_path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(
                os.path.abspath(model_path)
            ):
                raise SapComError(f"Attached SAP2000 model does not match requested model_path: {path}")
            self.sap_object = obj
            self.model = model
            result = {"connected": True, "model_path": path, "prog_id": prog_id, "method": method}
            try:
                version = self._connected_version()
            except Exception:
                version = None
            if version is not None:
                result["version"] = version
            return result

        def action():
            import pythoncom
            import win32com.client.dynamic

            errors = []
            # CSI's documented Helper.GetObject path, always late-bound.
            for helper_id in self.HELPER_PROG_IDS:
                try:
                    helper = win32com.client.dynamic.Dispatch(helper_id)
                except Exception as exc:
                    errors.append(f"Dispatch({helper_id}): {exc}")
                    continue
                for prog_id in self.PROG_IDS:
                    try:
                        return accept(
                            helper.GetObject(prog_id), f"Helper.GetObject({helper_id}, {prog_id})", prog_id
                        )
                    except Exception as exc:
                        errors.append(f"Helper.GetObject({helper_id}, {prog_id}): {exc}")
            # Running Object Table fallback, also late-bound; no Dispatch of a
            # SapObject here because that would create a new SAP2000 instance.
            for prog_id in self.PROG_IDS:
                try:
                    clsid = registered_clsid(prog_id)
                    dispatch = pythoncom.GetActiveObject(clsid).QueryInterface(pythoncom.IID_IDispatch)
                    return accept(
                        win32com.client.dynamic.Dispatch(dispatch), f"GetActiveObject({prog_id})", prog_id
                    )
                except Exception as exc:
                    errors.append(f"GetActiveObject({prog_id}): {exc}")
            self.sap_object = self.model = None
            raise SapComError("Could not attach an existing SAP2000 instance: " + "; ".join(errors))

        return self.actor.call(action)

    def _model(self):
        if self.model is None:
            raise SapComError("No active SAP2000 connection. Call /v1/sap/attach first.")
        return self.model

    def _connected_version(self) -> str | None:
        """Best-effort SAP2000 version string; ``None`` if it cannot be read.

        Ported literally from
        ``subprograms/perfiles_assign/sap2000_connection.py::_connected_version``:
        tries ``SapModel.GetVersion()`` then ``SapObject.GetOAPIVersionNumber()``,
        each of which may return a bare value or a COM tuple. Never raises.
        """
        for getter in (
            lambda: self.model.GetVersion(),
            lambda: self.sap_object.GetOAPIVersionNumber(),
        ):
            try:
                ret = getter()
            except Exception:
                continue
            if isinstance(ret, (list, tuple)):
                for v in ret:
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                    if isinstance(v, float) and v > 0:
                        return str(v)
            elif ret:
                return str(ret)
        return None

    def _status_action(self):
        """The status computation itself, isolated from the STA actor so it
        can be exercised directly in tests without a real COM thread."""
        if self.model is None:
            return {"platform": sys.platform, "connected": False}
        try:
            result = {
                "platform": sys.platform,
                "connected": True,
                "model_path": str(self.model.GetModelFilename() or ""),
            }
            try:
                version = self._connected_version()
            except Exception:
                version = None
            if version is not None:
                result["version"] = version
            return result
        except Exception:
            self.model = self.sap_object = None
            return {"platform": sys.platform, "connected": False, "reason": "COM connection lost"}

    def status(self):
        if sys.platform != "win32":
            raise SapComError("SAP2000 COM agent requires Windows.")
        return self.actor.call(self._status_action)

    def model_data(self):
        def action():
            model = self._model()
            path = str(model.GetModelFilename() or "")
            units = model.GetPresentUnits()
            return {
                "path": path,
                "units": units,
                "load_cases": _name_list(model.LoadCases.GetNameList()),
                "nodes": _name_list(model.PointObj.GetNameList()),
            }

        return self.actor.call(action)

    def run_analysis(self):
        def action():
            m = self._model()
            ret = m.Analyze.RunAnalysis()
            if ret != 0:
                raise SapComError(f"SAP2000 Analyze.RunAnalysis returned {ret}")
            return {"ok": True}

        return self.actor.call(action)


TOKEN = os.getenv("SAP2000_AGENT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("SAP2000_AGENT_TOKEN is required")
# El origen de la web alojada (HOSTED_ORIGIN) vive en bridge/hosted.py, que
# usan también los lanzadores; cualquier otro origen se añade con
# SAP2000_AGENT_ORIGINS o con bridge/origins.json.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def origins_file_path():
    from runtime_paths import user_data_dir

    return user_data_dir("EngineeringWorkbench") / "bridge" / "origins.json"


def _extra_origins() -> list[str]:
    """Orígenes adicionales del usuario: lista JSON de textos en origins.json."""
    import json

    try:
        data = json.loads(origins_file_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        x.strip().rstrip("/")
        for x in data
        if isinstance(x, str) and x.strip().startswith(("http://", "https://"))
    ]


def build_origins() -> list[str]:
    raw = os.getenv("SAP2000_AGENT_ORIGINS")
    if raw is None:
        base = [*DEV_ORIGINS, HOSTED_ORIGIN, FUSION_ORIGIN, *SHARED_APP_ORIGINS]
    else:
        base = [x.strip() for x in raw.split(",") if x.strip()]
    result: list[str] = []
    for origin in [*base, *_extra_origins()]:
        if origin not in result:
            result.append(origin)
    return result


def host_allowed(host_header: str) -> bool:
    """Sólo se atiende a quien llama al puente por su dirección de loopback.

    Un ataque de DNS rebinding hace que el navegador envíe el nombre del
    atacante en ``Host``; rechazarlo cierra esa vía.
    """
    name, sep, port = host_header.strip().lower().rpartition(":")
    if not sep or not port.isdigit():
        name = host_header.strip().lower()
    elif name.startswith("[") and not name.endswith("]"):
        name = host_header.strip().lower()
    return name in _LOOPBACK_HOSTS


origins = build_origins()
app = FastAPI(title="SAP2000 local agent", version="1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    # DELETE sólo lo usa /v1/documentos/anchors/{tag} (bridge/documentos_locales.py); sigue
    # exigiendo token y los mismos orígenes permitidos.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Snapshot-Stale"],
    # Chrome Local Network Access: una página pública que llama a loopback
    # envía Access-Control-Request-Private-Network en el preflight y exige
    # Access-Control-Allow-Private-Network: true en la respuesta.
    allow_private_network=True,
)
session = SapSession()


# --- Vida útil de los secretos emparejados (S-07) ---------------------------
# El secreto que guarda la web en localStorage caduca (por defecto 12 h,
# SAP2000_AGENT_TOKEN_TTL_HOURS; 0 = sin caducidad). bridge/pairing.py no guarda
# una caducidad: se deriva de ``created_at``. Los emparejamientos que ya
# existían cuando arrancó por primera vez este agente (puente anterior) se
# anotan en pairing_policy.json y siguen sin caducar, para no romper a quien ya
# está emparejado. El token de arranque (modo local) no caduca ni se revoca.
TOKEN_EXPIRED_MESSAGE = "El token del puente venció; vuelva a conectar"
_DEFAULT_TOKEN_TTL_HOURS = 12.0


def _token_ttl_seconds() -> float:
    raw = os.getenv("SAP2000_AGENT_TOKEN_TTL_HOURS", "")
    try:
        hours = float(raw) if raw.strip() else _DEFAULT_TOKEN_TTL_HOURS
    except ValueError:
        hours = _DEFAULT_TOKEN_TTL_HOURS
    return max(hours, 0.0) * 3600.0


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _policy_path():
    from runtime_paths import user_data_dir

    return user_data_dir("EngineeringWorkbench") / "bridge" / "pairing_policy.json"


def _legacy_pairing_ids() -> set[str]:
    """Ids de emparejamientos anteriores a la caducidad (no caducan).

    Si todavía no hay archivo, se crea con los emparejamientos actuales: es lo
    que ocurre en el primer arranque del agente nuevo (ver ``_init_policy``).
    """
    import json

    path = _policy_path()
    try:
        raw = json.loads(path.read_text("utf-8"))
        return {str(i) for i in raw.get("legacy_ids", [])}
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
        pass
    ids = sorted(str(p.get("id")) for p in pairing.list_pairings() if p.get("id"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"legacy_ids": ids}), "utf-8")
    except OSError:
        pass
    return set(ids)


def _init_policy() -> None:
    _legacy_pairing_ids()


def _find_pairing(authorization: str) -> dict | None:
    import hashlib

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    found = None
    for item in pairing.list_pairings():
        if secrets.compare_digest(str(item.get("secret_hash", "")), digest):
            found = item
    return found


def _pairing_expired(item: dict) -> bool:
    from datetime import datetime

    ttl = _token_ttl_seconds()
    if ttl <= 0 or str(item.get("id")) in _legacy_pairing_ids():
        return False
    try:
        created = datetime.fromisoformat(str(item.get("created_at")))
    except ValueError:
        return True  # sin fecha legible no se puede probar vigencia: se rechaza
    return (_utcnow() - created).total_seconds() >= ttl


def _rechazo(request: Request, detail: str, status: int):
    """Respuesta de rechazo (401/403) con CORS para el origen permitido.

    ``require_token`` va por fuera de ``CORSMiddleware``: sin esto el navegador
    no puede leer el 401 de un secreto caducado y la web sólo vería «sin
    respuesta del puente» en lugar de poder pedir un nuevo emparejamiento.
    """
    respuesta = __import__("fastapi").responses.JSONResponse({"detail": detail}, status)
    origin = request.headers.get("origin")
    if origin and origin in origins:
        respuesta.headers["Access-Control-Allow-Origin"] = origin
        respuesta.headers["Vary"] = "Origin"
    return respuesta


def _unauthorized(request: Request, detail: str = "Unauthorized"):
    return _rechazo(request, detail, 401)


@app.middleware("http")
async def require_token(request: Request, call_next):
    # Browser preflight cannot carry a bearer token; CORSMiddleware answers it.
    if request.method == "OPTIONS":
        return await call_next(request)
    # Requiring a token here would be circular: these two routes are exactly
    # how a browser gets its first pairing secret (bridge/pairing.py).
    if request.method == "POST" and request.url.path == "/v1/sap/pairing/request":
        return await call_next(request)
    if request.method == "GET" and request.url.path.startswith("/v1/sap/pairing/poll/"):
        return await call_next(request)
    authorization = request.headers.get("authorization", "")
    request.state.pairing_id = None
    # El token de arranque se comprueba siempre en tiempo constante.
    if secrets.compare_digest(authorization, f"Bearer {TOKEN}"):
        return await call_next(request)
    item = _find_pairing(authorization)
    if item is None:
        return _unauthorized(request)
    if _pairing_expired(item):
        return _unauthorized(request, TOKEN_EXPIRED_MESSAGE)
    # Actualiza last_used_at (best-effort) y confirma el secreto.
    if not pairing.validate_secret(authorization):
        return _unauthorized(request)
    # El secreto vale sólo para el origen que lo pidió y para las rutas de su
    # alcance (bridge/alcances.py): una app de sólo lectura no puede escribir.
    paired_origin = str(item.get("origin", ""))
    request_origin = request.headers.get("origin")
    if request_origin is not None and request_origin != paired_origin:
        return _rechazo(request, "Origen no coincide con el emparejamiento", 403)
    if not ruta_permitida(paired_origin, request.url.path):
        return _rechazo(request, "Ruta fuera del alcance de este emparejamiento", 403)
    request.state.pairing_id = item.get("id")
    return await call_next(request)


@app.middleware("http")
async def loopback_host_only(request: Request, call_next):
    # Registrado después de require_token, por lo que es la capa más externa.
    if not host_allowed(request.headers.get("host", "")):
        return __import__("fastapi").responses.JSONResponse({"detail": "Host no permitido"}, 400)
    return await call_next(request)


class AttachBody(BaseModel):
    model_path: str | None = None


@app.post("/v1/sap/revoke")
def revoke_current_token(request: Request):
    """Revoca el secreto emparejado con el que se hace la llamada (Desconectar).

    El token de arranque (modo local) no se puede revocar desde aquí: se
    cambia reiniciando el puente.
    """
    pairing_id = getattr(request.state, "pairing_id", None)
    if not pairing_id:
        return {"revoked": False}
    pairing.revoke(str(pairing_id))
    return {"revoked": True}


@app.get("/v1/sap/status")
def status():
    try:
        return session.status()
    except SapComError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/v1/sap/attach")
def attach(body: AttachBody):
    try:
        return session.attach(body.model_path)
    except SapComError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/v1/sap/model")
def model():
    try:
        return session.model_data()
    except SapComError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/sap/analysis/run")
def analysis_run():
    try:
        return session.run_analysis()
    except SapComError as exc:
        raise HTTPException(409, str(exc)) from exc


# Read-only result extraction. The documented SAP2000 OAPI JointReact tuple is
# (count,obj,elm,case,step_type,step_num,F1,F2,F3,M1,M2,M3,ret).
def _joint_reactions(load_case: str | None = None):
    def action():
        m = session._model()
        try:
            # Reading the current output selection avoids changing a user's SAP state.
            # eItemTypeElm.SelectionElm is 3; the object name is ignored for this type.
            if load_case:
                raise SapComError(
                    "Filtering reactions would change SAP output selection; use snapshot or configure the selection in SAP2000."
                )
            raw = m.Results.JointReact("", 3)  # eItemTypeElm.SelectionElm
            if not isinstance(raw, tuple) or len(raw) < 13:
                raise SapComError("Unexpected SAP2000 Results.JointReact response")
            count = int(raw[0])
            objects, cases, step_types, step_nums = raw[1], raw[3], raw[4], raw[5]
            f1, f2, f3, m1, m2, m3 = raw[6:12]
            # Obj identifies the joint even when Elm is a generated analysis element.
            return {
                "reactions": [
                    {
                        "joint": str(objects[index]),
                        "output_case": str(cases[index]),
                        "step_type": str(step_types[index]),
                        "step_num": float(step_nums[index]),
                        "f1": float(f1[index]),
                        "f2": float(f2[index]),
                        "f3": float(f3[index]),
                        "m1": float(m1[index]),
                        "m2": float(m2[index]),
                        "m3": float(m3[index]),
                    }
                    for index in range(count)
                ]
            }
        except SapComError:
            raise
        except Exception as exc:
            raise SapComError(f"SAP2000 reaction extraction failed: {exc}") from exc

    return session.actor.call(action)


@app.get("/v1/sap/results/reactions")
def reactions(load_case: str | None = None):
    try:
        return _joint_reactions(load_case)
    except SapComError as exc:
        raise HTTPException(409, str(exc)) from exc


# Snapshot extraction deliberately changes SAP present units only inside this
# context and restores them even when a COM table/result call fails.
#
# Forced to Ton_m_C (eUnits code 12), matching every original foundations
# COM read literally: coordenadas.py/reacciones.py/cargas_nodales.py all call
# ``conn.set_units("Ton_m_C")`` unconditionally before reading joints/
# reactions/displacements/loads, with no user-facing way to pick a different
# unit system. This used to be configurable via ``SAP2000_OUTPUT_UNITS``
# (defaulting to kgf_m_C, 1000x off from Ton), which silently returned wrong
# magnitudes to a web UI whose foundation labels (see
# ``web/src/foundations/cargasNodales.ts``, ``calculos.ts``) assume Ton/Ton·m
# unconditionally — this is exclusively the Foundations snapshot
# (``/v1/sap/snapshot``, "Importar datos del modelo a Fundaciones"), not
# shared with the unrelated ``/v1/sap/actions/combinaciones/snapshot`` route.
_TON_M_C_UNITS = 12


def _configured_units() -> tuple[str, int]:
    return "Ton_m_C", _TON_M_C_UNITS


def _return_code(value: Any) -> int:
    if isinstance(value, tuple):
        value = value[-1] if len(value) == 1 else value[0]
    return int(value)


def _coordinates(model: Any, names: list[str]) -> list[dict[str, Any]]:
    rows = []
    for name in names:
        try:
            x, y, z = _call_get_coord_cartesian(model, name)
        except RuntimeError as exc:
            raise SapComError(str(exc)) from exc
        rows.append({"joint": str(name), "x": x, "y": y, "z": z})
    return rows


def _call_joint_result(model: Any, method: str, name: str) -> Any:
    """Call Results.JointReact/JointDispl tolerating short and long COM signatures.

    Mirrors ``reacciones.py::_llamar_joint_react`` and the equivalent
    ``nodos_levantados.py::_llamar_joint_displ`` from the verified source: try
    the short signature first (``(name, item_type)``); a SAP2000 build that
    only exposes the long signature raises there, and only then do we retry
    with the explicit output-parameter arrays.  Both methods return six value
    columns (F1..M3 for JointReact, U1..R3 for JointDispl), so both take six
    empty ByRef placeholders.
    """
    try:
        return getattr(model.Results, method)(str(name), 0)
    except Exception:
        obj: list = []
        elm: list = []
        load_case: list = []
        step_type: list = []
        step_num: list = []
        values = [[] for _ in range(6)]
        return getattr(model.Results, method)(
            str(name), 0, 0, obj, elm, load_case, step_type, step_num, *values
        )


def _joint_result_rows(model: Any, method: str, names: list[str]) -> list[dict[str, Any]]:
    rows = []
    for name in names:
        raw = _call_joint_result(model, method, name)
        if not isinstance(raw, tuple):
            raise SapComError(f"Unexpected SAP2000 Results.{method} response")
        try:
            _ret(raw, f"Results.{method}({name!r})")
        except RuntimeError as exc:
            raise SapComError(str(exc)) from exc
        arrays = [list(x) for x in raw if isinstance(x, (list, tuple))]
        if len(arrays) < 11:
            raise SapComError(f"Unexpected SAP2000 Results.{method} response")
        obj, _elm, cases, step_types, step_nums, *values = arrays[-11:]
        operation = f"Results.{method}({name!r})"
        # Both supported bindings declare NumberResults: directly first in
        # the short form, or immediately after the leading return code in the
        # long form.  Do not truncate to the shortest vector: that turns a
        # partial COM response into a plausible, incomplete calculation.
        if isinstance(raw[0], int) and raw[0] == 0 and len(raw) > 1 and isinstance(raw[1], int):
            count = int(raw[1])
        elif isinstance(raw[0], int):
            count = int(raw[0])
        else:
            raise SapComError(f"Unexpected SAP2000 {operation} result count")
        vectors = [obj, cases, step_types, step_nums, *values]
        lengths = [len(vector) for vector in vectors]
        if len(values) != 6 or any(length != count for length in lengths):
            raise SapComError(
                f"SAP2000 {operation} returned inconsistent result arrays "
                f"(declared {count}; lengths {lengths})"
            )
        for i in range(count):
            row = {
                "joint": str(obj[i]),
                "output_case": str(cases[i]),
                "step_type": str(step_types[i]),
                "step_num": float(step_nums[i]),
            }
            if method == "JointReact":
                row.update(dict(zip(("f1", "f2", "f3", "m1", "m2", "m3"), (float(v[i]) for v in values[:6]))))
            else:
                row.update(dict(zip(("u1", "u2", "u3"), (float(v[i]) for v in values[:3]))))
            rows.append(row)
    return rows


def _soil_pressures(model: Any) -> list[dict[str, Any]]:
    """Read the verified SAP table ``Element Soil Pressure - Area Shells``.

    Uses ``_call_display_table`` (port of
    ``presion_contacto._llamar_tabla_presion_suelo``) so the same six
    ``GetTableForDisplayArray`` signature variants the desktop tries are
    tried here too, instead of giving up after the first one.
    """
    try:
        raw = _call_display_table(model, "Element Soil Pressure - Area Shells")
        _ret(raw, "DatabaseTables.GetTableForDisplayArray('Element Soil Pressure - Area Shells')")
    except Exception as exc:
        raise SapComError(f"SAP2000 soil-pressure table failed: {exc}") from exc
    arrays = [
        list(x) for x in raw if isinstance(x, (list, tuple)) and x and all(isinstance(v, str) for v in x)
    ]
    if len(arrays) < 2:
        return []
    fields = min(arrays, key=len)
    candidates = [array for array in arrays if array is not fields]
    if not candidates:
        return []
    data = max(candidates, key=len)
    if len(data) % len(fields) != 0:
        raise SapComError(
            "SAP2000 soil-pressure table has an incomplete row "
            f"({len(data)} values for {len(fields)} columns)"
        )
    lower = {name.casefold().replace(" ", "").replace("_", ""): i for i, name in enumerate(fields)}
    area = next((i for n, i in lower.items() if n in ("area", "areaid")), None)
    case = next((i for n, i in lower.items() if n == "outputcase"), None)
    pressure = next((i for n, i in lower.items() if n in ("pressure", "soilpressure", "p")), None)
    if None in (area, case, pressure):
        raise SapComError("SAP2000 soil-pressure table lacks Area, OutputCase, or Pressure")
    step_type = lower.get("steptype")
    step_num = lower.get("stepnum")
    # Desktop (presion_contacto.py) never validates the step columns: the native
    # soil-pressure table may expose neither or only some of them.
    rows = []
    for i in range(0, len(data), len(fields)):
        try:
            row = {
                "shell": data[i + area],
                "output_case": data[i + case],
                "pressure": float(data[i + pressure]),
            }
            if step_type is not None:
                row["step_type"] = data[i + step_type]
            if step_num is not None:
                row["step_num"] = float(data[i + step_num])
        except (TypeError, ValueError, IndexError) as exc:
            raise SapComError(
                f"SAP2000 soil-pressure table has invalid data at row {i // len(fields) + 1}"
            ) from exc
        rows.append(row)
    return rows


def _set_output_selection(model: Any, cases: list[str] | None, combos: list[str] | None) -> None:
    """Restrict Results output to ``cases``/``combos``, port of reacciones.py:112-157.

    ``DeselectAllCasesAndCombosForOutput`` always runs first so a previous
    SAP selection never leaks into a scoped snapshot; each name is then
    selected with the matching Set*SelectedForOutput call.
    """
    if _return_code(model.Results.Setup.DeselectAllCasesAndCombosForOutput()) != 0:
        raise SapComError("Results.Setup.DeselectAllCasesAndCombosForOutput failed")
    for case in cases or []:
        if _return_code(model.Results.Setup.SetCaseSelectedForOutput(str(case), True)) != 0:
            raise SapComError(f"Results.Setup.SetCaseSelectedForOutput({case!r}) failed")
    for combo in combos or []:
        if _return_code(model.Results.Setup.SetComboSelectedForOutput(str(combo), True)) != 0:
            raise SapComError(f"Results.Setup.SetComboSelectedForOutput({combo!r}) failed")


def _snapshot_from_model(
    model: Any,
    groups: list[dict[str, Any]] | None = None,
    joints: list[str] | None = None,
    shells: list[str] | None = None,
    cases: list[str] | None = None,
    combos: list[str] | None = None,
) -> dict[str, Any]:
    """Builds the FoundationDataset payload with only documented OAPI calls.

    With none of ``joints``/``shells``/``cases``/``combos`` this matches the
    original, unscoped snapshot: every model joint and every soil-pressure
    row, but WITH the same output-case selection every original caller of
    ``reacciones._seleccionar_casos_output`` forces first — deselect
    everything, then select every load case (not combos) — since the
    original never reads reactions/displacements without first replacing
    SAP2000's output-case selection with its own.
    """
    unit_name, target = _configured_units()
    previous = model.GetPresentUnits()
    if _return_code(model.SetPresentUnits(target)) != 0:
        raise SapComError(f"SetPresentUnits({unit_name}) failed")
    try:
        if cases is None and combos is None:
            _select_all_cases_for_output(model)
        else:
            _set_output_selection(model, cases, combos)
        names = (
            [str(joint) for joint in joints]
            if joints is not None
            else _get_name_list(model.PointObj, "PointObj.GetNameList")
        )
        resolved_groups = groups if groups is not None else _groups_from_model(model)
        if shells is None and joints is not None:
            # Joints-only request (e.g. nodal diagrams): no shell was asked
            # for, so the soil-pressure table is not read at all.
            pressures = []
        else:
            pressures = _soil_pressures(model)
            if shells is not None:
                wanted = {str(shell) for shell in shells}
                pressures = [row for row in pressures if row["shell"] in wanted]
        return {
            "model": {"path": str(model.GetModelFilename() or ""), "units": unit_name},
            "coordinates": _coordinates(model, names),
            "reactions": _joint_result_rows(model, "JointReact", names),
            "displacements": _joint_result_rows(model, "JointDispl", names),
            "soil_pressures": pressures,
            "groups": resolved_groups,
            # Port of estado_modelo.modelo_no_corrido == not model_is_locked:
            # lets the web show the desktop's literal "modelo no corrido"
            # message instead of a generic empty-results one.
            "model_locked": _model_is_locked(model),
        }
    finally:
        if _return_code(model.SetPresentUnits(previous)) != 0:
            raise SapComError("SAP2000 present units could not be restored")


def _snapshot(
    self,
    groups: list[dict[str, Any]] | None = None,
    joints: list[str] | None = None,
    shells: list[str] | None = None,
    cases: list[str] | None = None,
    combos: list[str] | None = None,
):
    return self.actor.call(lambda: _snapshot_from_model(self._model(), groups, joints, shells, cases, combos))


SapSession.snapshot = _snapshot


class SnapshotBody(BaseModel):
    # Group membership is explicit: this avoids silently treating all model
    # nodes/shells as a foundation when SAP selection/group APIs are absent.
    groups: list[dict[str, Any]] | None = None
    # Scoping a snapshot to a chosen foundation/combination selection, as the
    # Foundations panels do before "Calcular" (see reacciones.py:112-157).
    joints: list[str] | None = None
    shells: list[str] | None = None
    cases: list[str] | None = None
    combos: list[str] | None = None


@app.post("/v1/sap/snapshot")
def snapshot(body: SnapshotBody):
    try:
        return session.snapshot(body.groups, body.joints, body.shells, body.cases, body.combos)
    except SapComError as exc:
        raise HTTPException(409, str(exc)) from exc


# Mutating actions are exposed only by this authenticated loopback agent.
from bridge.actions import install_routes

install_routes(app, session, SapComError)
from bridge.str2fund import install_routes as install_str2fund_routes

install_str2fund_routes(app, session, SapComError)
app.router.add_event_handler("startup", _init_policy)
pairing.install_routes(app)
