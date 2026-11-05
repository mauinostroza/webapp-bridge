"""Own every COM proxy on one dedicated STA thread; cache pure snapshots."""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
from threading import Lock
from typing import Callable

from bridge.viewer.sap2000.connector import Sap2000Connector
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.core.snapshot import build_snapshot


class BridgeError(RuntimeError):
    pass


class LocalBridge:
    def __init__(self, connector_factory: Callable = Sap2000Connector):
        self._factory = connector_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sap-com', initializer=self._initialize)
        self._lock = Lock()
        self._connector = None
        self._snapshot = None
        self._payload = None
        self._stale = False
        self._error = None
        self._busy = False
        self._closed = False

    @staticmethod
    def _initialize():
        if os.name == 'nt':
            already_imported = 'comtypes' in sys.modules
            import comtypes
            # comtypes initializes the importing thread itself on first import.
            # A previously imported module needs initialization on this worker.
            if already_imported:
                comtypes.CoInitializeEx(2)  # COINIT_APARTMENTTHREADED

    def status(self) -> dict:
        """Cached state only: never probe COM from a request thread."""
        with self._lock:
            snapshot = self._snapshot
            return {'connected': snapshot is not None and not self._stale,
                    'source': snapshot['source'] if snapshot else None,
                    'modelName': snapshot['modelName'] if snapshot else '',
                    'snapshotId': snapshot['id'] if snapshot else None,
                    'stale': self._stale, 'busy': self._busy, 'error': self._error}

    def cached(self) -> tuple[bytes | None, bool]:
        with self._lock:
            return self._payload, self._stale

    def _read(self, mode: str | None):
        with self._lock:
            self._busy = True
        try:
            if mode:
                candidate = self._factory()
                info = candidate.connect_demo() if mode == 'demo' else candidate.connect_to_open_instance()
                if not info.connected:
                    raise BridgeError(info.message or 'No se pudo conectar a SAP2000.')
            else:
                candidate = self._connector
                if candidate is None:
                    raise BridgeError('Conecta a SAP2000 o elige Demo antes de recargar.')
                info = candidate.info()
                if not info.connected:
                    raise BridgeError(info.message or 'Se perdió la conexión con SAP2000.')
            source = 'demo' if candidate.is_demo else 'sap2000'
            snapshot = build_snapshot(SapModelReader(candidate.model), source, info.model_name or 'Modelo sin nombre', info.units)
            if not candidate.is_alive():
                raise BridgeError('SAP2000 se desconectó durante la lectura; snapshot descartado.')
            # Pre-encoded once: cached GET doesn't rebuild JSON for a large model.
            payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
            self._connector = candidate
            with self._lock:
                self._snapshot = snapshot
                self._payload = payload
                self._stale = False
                self._error = None
            return snapshot
        except Exception as exc:
            with self._lock:
                self._stale = self._snapshot is not None
                self._error = str(exc)
            raise BridgeError(str(exc)) from exc
        finally:
            with self._lock:
                self._busy = False

    async def connect(self, mode: str):
        if mode not in ('demo', 'sap2000'):
            raise BridgeError('Modo no soportado.')
        if self._closed:
            raise BridgeError('El puente local está cerrado.')
        return await asyncio.wrap_future(self._executor.submit(self._read, mode))

    async def refresh(self):
        if self._closed:
            raise BridgeError('El puente local está cerrado.')
        return await asyncio.wrap_future(self._executor.submit(self._read, None))

    async def run_on_com(self, fn: Callable):
        """Ejecuta `fn(connector)` en el hilo COM (único dueño de los proxies)."""
        if self._closed:
            raise BridgeError('El puente local está cerrado.')

        def job():
            if self._connector is None:
                raise BridgeError('Conecta a SAP2000 o elige Demo primero.')
            return fn(self._connector)

        return await asyncio.wrap_future(self._executor.submit(job))

    def _release(self):
        if self._connector is not None:
            self._connector.disconnect()
            self._connector = None
        if os.name == 'nt':
            import comtypes
            comtypes.CoUninitialize()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._release).result()
        self._executor.shutdown(wait=True)
