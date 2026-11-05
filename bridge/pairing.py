"""Emparejamiento remoto de un clic para el puente SAP2000.

Sustituye la copia manual de un token de consola por un flujo de tres pasos:
la web pide emparejarse (``create_request``), una persona acepta o rechaza
desde la bandeja del sistema (``approve``/``deny``, fuera de este módulo:
quien construya la bandeja llama a estas funciones), y la web hace ``poll``
hasta recibir el secreto — una única vez.

Todo el estado vive en memoria de proceso, protegido por un ``threading.Lock``
porque uvicorn atiende peticiones concurrentes aunque el bridge sea un solo
proceso. Los emparejamientos aprobados (sólo su hash) se persisten en disco
para sobrevivir a un reinicio del puente.

Reloj de las solicitudes pendientes: se usa ``time.monotonic`` a través de la
función de módulo ``_now`` (inyectable por las pruebas, sin ``sleep`` real).
Es la elección correcta aquí porque el vencimiento sólo compara duraciones
relativas (120 s desde la creación) y nunca se muestra como fecha: un ajuste
del reloj del sistema no debe alargar ni acortar una ventana de 120 s. Los
campos que sí se muestran a una persona (``created_at``/``last_used_at`` de
un emparejamiento ya persistido, para "Conexiones emparejadas" en la bandeja)
usan en cambio hora real UTC en ISO 8601, calculada aparte con
``datetime.now(timezone.utc)``.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from runtime_paths import user_data_dir

TTL_SECONDS = 120.0
MAX_PENDING_TOTAL = 5
AGENT_URL = "http://127.0.0.1:8765"

# Reloj inyectable: las pruebas sustituyen ``_now`` por un contador falso para
# ejercer el vencimiento sin dormir de verdad. Ver docstring del módulo.
_now: Callable[[], float] = time.monotonic


class PairingQuotaExceeded(RuntimeError):
    """Ya hay 5 solicitudes pendientes; el router la traduce a 429."""


@dataclass
class PairingRequest:
    id: str
    origin: str
    created_at: float
    expires_at: float
    confirm_code: str
    status: str = "pending"  # pending | approved | denied | expired
    # Secreto en claro, sólo mientras no se ha entregado por poll una vez.
    _secret: str | None = field(default=None, repr=False)


_lock = threading.Lock()
_pending: dict[str, PairingRequest] = {}
_pairings: list[dict[str, Any]] = []
_pairings_loaded = False
_notifier: Callable[[PairingRequest], None] | None = None


def _pairings_path() -> Path:
    return user_data_dir("EngineeringWorkbench") / "bridge" / "pairings.json"


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_pairings_locked() -> None:
    """Carga ``pairings.json`` una vez por proceso. Llamar con ``_lock`` tomado."""
    global _pairings_loaded
    if _pairings_loaded:
        return
    _pairings_loaded = True
    _pairings.clear()
    try:
        raw = json.loads(_pairings_path().read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return
    if isinstance(raw, list):
        _pairings.extend(item for item in raw if isinstance(item, dict))


def _save_pairings_locked() -> None:
    """Escritura atómica: tempfile + ``os.replace`` en el mismo directorio.

    No hace falta nada más elaborado que en ``agent_file.py`` — a diferencia
    de ese archivo, aquí sólo escribe el propio proceso del puente (nunca el
    backend web), así que no hay lector concurrente a mitad de escritura más
    allá de "el archivo no existe todavía", que ya se tolera al cargar.
    """
    path = _pairings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".pairings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_pairings, handle, indent=2)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _expire_locked() -> None:
    """Vencimiento perezoso: se marca ``expired`` al consultar, sin hilo de fondo."""
    now = _now()
    for req in _pending.values():
        if req.status == "pending" and req.expires_at <= now:
            req.status = "expired"
            req._secret = None


def set_notifier(callback: Callable[[PairingRequest], None] | None) -> None:
    """Conecta el aviso visual (bandeja). Sin llamar, el módulo funciona igual."""
    global _notifier
    _notifier = callback


def create_request(origin: str) -> PairingRequest:
    """Crea una solicitud pendiente para ``origin``.

    Cupos: como mucho una solicitud pendiente por origen — una segunda del
    mismo origen **reemplaza** a la anterior (la anterior se retira de
    ``_pending`` por completo, no queda como "expirada" ni recuperable por su
    id viejo: para quien pidió emparejarse dos veces sólo existe la última
    solicitud). Como mucho 5 pendientes en total entre todos los orígenes;
    superarlo lanza ``PairingQuotaExceeded`` para que el router responda 429.
    """
    with _lock:
        _expire_locked()
        for existing_id, existing in list(_pending.items()):
            if existing.origin == origin and existing.status == "pending":
                del _pending[existing_id]
        pending_count = sum(1 for req in _pending.values() if req.status == "pending")
        if pending_count >= MAX_PENDING_TOTAL:
            raise PairingQuotaExceeded("Too many pending SAP2000 pairing requests")
        now = _now()
        request = PairingRequest(
            id=secrets.token_urlsafe(16),
            origin=origin,
            created_at=now,
            expires_at=now + TTL_SECONDS,
            confirm_code=f"{secrets.randbelow(10000):04d}",
        )
        _pending[request.id] = request
    notifier = _notifier
    if notifier is not None:
        notifier(request)
    return request


def get(request_id: str) -> PairingRequest | None:
    """Devuelve la solicitud, venciéndola primero si corresponde (perezoso)."""
    with _lock:
        _expire_locked()
        return _pending.get(request_id)


def poll(request_id: str) -> dict[str, Any] | None:
    """Respuesta lista para la ruta HTTP de poll, o ``None`` si no existe.

    Sólo la primera consulta de una solicitud ya aprobada trae ``secret`` y
    ``url``; a partir de ahí el secreto se ha borrado de memoria y siguientes
    polls siguen diciendo ``approved`` sin repetirlo.
    """
    with _lock:
        _expire_locked()
        request = _pending.get(request_id)
        if request is None:
            return None
        result: dict[str, Any] = {"status": request.status}
        if request.status == "approved" and request._secret is not None:
            result["secret"] = request._secret
            result["url"] = AGENT_URL
            request._secret = None
        return result


def approve(request_id: str, label: str | None = None) -> None:
    """Aprueba una solicitud pendiente: genera y persiste el secreto (su hash).

    Lanza ``KeyError`` si la solicitud no existe, y ``ValueError`` si ya no
    está ``pending`` (vencida, ya aprobada o rechazada) — quien construya la
    ventana de aviso debe tratar ambos casos como "ya no hay nada que hacer".
    """
    with _lock:
        _expire_locked()
        request = _pending.get(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != "pending":
            raise ValueError(f"pairing request {request_id!r} is not pending (status={request.status})")
        secret = secrets.token_urlsafe(32)
        _load_pairings_locked()
        _pairings.append(
            {
                "id": request.id,
                "origin": request.origin,
                "label": label or request.origin,
                "secret_hash": _hash_secret(secret),
                "created_at": _iso_now(),
                "last_used_at": None,
            }
        )
        _save_pairings_locked()
        request.status = "approved"
        request._secret = secret


def deny(request_id: str) -> None:
    """Rechaza una solicitud pendiente. Lanza ``KeyError`` si no existe."""
    with _lock:
        _expire_locked()
        request = _pending.get(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status == "pending":
            request.status = "denied"


def list_pairings() -> list[dict[str, Any]]:
    """Copia de los emparejamientos persistidos (nunca incluye el secreto)."""
    with _lock:
        _load_pairings_locked()
        return [dict(item) for item in _pairings]


def revoke(pairing_id: str) -> None:
    """Quita un emparejamiento de memoria y de disco. Silencioso si no existe."""
    with _lock:
        _load_pairings_locked()
        remaining = [item for item in _pairings if item.get("id") != pairing_id]
        if len(remaining) == len(_pairings):
            return
        _pairings[:] = remaining
        _save_pairings_locked()


def validate_secret(authorization_header: str) -> bool:
    """``True`` si ``authorization_header`` es ``Bearer <secreto emparejado>``.

    Compara en tiempo constante contra cada hash guardado (mismo estilo que
    ``backend/auth.py::_token_digest`` + ``secrets.compare_digest``): probar
    todos los hashes sin cortocircuito evita que el tiempo de respuesta revele
    cuál, si alguno, estuvo cerca de coincidir. Al encontrar coincidencia,
    actualiza ``last_used_at`` de forma best-effort: si la escritura a disco
    falla, la petición ya autenticada no debe romperse por eso.
    """
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    digest = _hash_secret(token)
    with _lock:
        _load_pairings_locked()
        matched: dict[str, Any] | None = None
        for item in _pairings:
            if secrets.compare_digest(str(item.get("secret_hash", "")), digest):
                matched = item
        if matched is None:
            return False
        matched["last_used_at"] = _iso_now()
        try:
            _save_pairings_locked()
        except OSError:
            pass
        return True


def reset_state_for_tests() -> None:
    """Sólo para pruebas: limpia solicitudes, emparejamientos y notificador."""
    global _pairings_loaded, _notifier
    with _lock:
        _pending.clear()
        _pairings.clear()
        _pairings_loaded = False
        _notifier = None


def install_routes(app: Any) -> None:
    """Instala las dos rutas de emparejamiento, exentas de ``require_token``.

    Mismo patrón que ``bridge.actions.install_routes``, más simple porque no
    necesita ``session`` ni el tipo de error COM: este módulo no toca SAP2000.
    """

    def _allowed_origins(request: Request) -> list[str]:
        # Import perezoso: evita un ciclo con sap2000_agent, que importa este
        # módulo para instalar sus rutas al final del archivo.
        from bridge.sap2000_agent import origins as configured_origins

        return configured_origins

    @app.post("/v1/sap/pairing/request")
    def pairing_request(request: Request):
        origin = request.headers.get("origin")
        if not origin:
            raise HTTPException(400, "Missing Origin header")
        if origin not in _allowed_origins(request):
            raise HTTPException(403, f"Origin not allowed to pair: {origin}")
        try:
            created = create_request(origin)
        except PairingQuotaExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        return {
            "request_id": created.id,
            "expires_in": int(TTL_SECONDS),
            "confirm_code": created.confirm_code,
        }

    @app.get("/v1/sap/pairing/poll/{request_id}")
    def pairing_poll(request_id: str):
        result = poll(request_id)
        if result is None:
            raise HTTPException(404, "Unknown pairing request")
        return result
