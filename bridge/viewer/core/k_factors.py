"""Factores K de diseño de acero para la web: lectura, vista previa y aplicación.

La escritura es SIEMPRE en dos pasos: `preview` no toca el modelo y devuelve
las diferencias más un *nonce* de un solo uso; `apply` lo consume. Así lo que
el usuario confirma en pantalla es exactamente lo que se escribe, y una
petición suelta no puede cambiar el modelo.

Todo esto se ejecuta en el hilo COM del puente (ver `LocalBridge.run_on_com`).
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any, Mapping

from bridge.viewer.safety.audit_log import AuditLog
from bridge.viewer.sap2000.backup import BackupManager
from bridge.viewer.sap2000.steel_k_design import DESIGN_FIELDS, read_design_k_factors
from bridge.viewer.sap2000.steel_k import (
    KFactorError,
    KFactorLockedError,
    KFactorVerifyError,
    KValue,
    _validate_changes,
    field_label,
    diagnose,
    read_k_factors,
    write_k_factors,
)

NONCE_TTL_SECONDS = 300
STALE_WARNING = (
    "Cambiar un overwrite INVALIDA los resultados de diseño de acero (los D/C actuales "
    "dejarán de valer): tendrás que volver a ejecutar el diseño en SAP2000."
)
LOCKED_WARNING = (
    "SAP2000 rechazó la escritura porque el modelo está BLOQUEADO (tiene resultados de análisis). "
    "Desbloquearlo DESCARTA esos resultados de análisis: tendrás que volver a analizar el modelo."
)


class KServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def kvalue_json(v: KValue) -> dict:
    return {'value': v.value, 'auto': v.auto}


def _fmt(v: Any) -> str:
    if isinstance(v, KValue):
        return 'automático' if v.auto else ('sin dato' if v.value is None else f'{v.value:.3f}')
    return 'automático' if v == 'auto' else f'{float(v):.3f}'


class KFactorService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.audit = AuditLog(self.data_dir / 'logs' / 'audit.jsonl')
        self.backups = BackupManager(self.data_dir / 'backups')
        self._pending: dict[str, dict] = {}

    # ── lectura ────────────────────────────────────────────────────────────
    def read(self, model: Any) -> dict:
        res = read_k_factors(model)
        if not res.available:
            return {'available': False, 'reason': res.reason, 'frames': {}, 'notes': []}
        return {
            'available': True, 'reason': '', 'tableKey': res.table_key, 'notes': list(res.notes),
            'frames': {f: {k: kvalue_json(v) for k, v in {**vals, **res.lengths.get(f, {})}.items()}
                       for f, vals in res.frames.items()},
        }

    def read_design(self, model: Any) -> dict:
        """K y L CALCULADOS por el diseño (solo lectura). Sin diseño: available False, no error."""
        res = read_design_k_factors(model)
        if not res.available:
            return {'available': False, 'reason': res.reason, 'tableKey': res.table_key,
                    'frames': {}, 'governing': {}, 'notes': list(res.notes)}
        return {
            'available': True, 'reason': '', 'tableKey': res.table_key, 'notes': list(res.notes),
            'frames': {f: {k: {'value': vals[k]} for k in DESIGN_FIELDS if k in vals}
                       for f, vals in res.frames.items()},
            'governing': {f: dict(g) for f, g in res.governing.items()},
        }

    def read_effective(self, model: Any) -> dict:
        """K y L efectivos por barra: overwrite numérico, o el valor calculado por el diseño
        cuando el overwrite es automático. Solo lectura; reutiliza `read()` y `read_design()`."""
        ow = self.read(model)
        dg = self.read_design(model)
        if not ow['available'] and not dg['available']:
            return {'available': False,
                    'reason': f"Overwrites: {ow['reason']} Diseño: {dg['reason']}",
                    'tableKey': None, 'designTableKey': None, 'notes': [], 'frames': {}, 'governing': {}}
        notes = list(ow['notes']) + list(dg['notes']) + [
            'El K de diseño es el de la fila gobernante de cada barra (ver «governing»), '
            'no necesariamente el de cada combinación.',
            'Tomar el 0 como «automático» es una hipótesis por tabla, sin verificar en todos los modelos; '
            '«overwriteAuto» indica cuándo se aplicó.',
        ]
        if not ow['available']:
            notes.append(f"Overwrites no disponibles ({ow['reason']}); solo se muestran los valores de diseño.")
        if not dg['available']:
            notes.append(f"Diseño no disponible ({dg['reason']}); ejecute el diseño de acero "
                         'para resolver los factores automáticos.')
        fields = list(DESIGN_FIELDS)
        frames: dict[str, dict] = {}
        for f in list(ow['frames']) + [f for f in dg['frames'] if f not in ow['frames']]:
            ow_f, dg_f = ow['frames'].get(f, {}), dg['frames'].get(f, {})
            out: dict[str, dict] = {}
            for fld in fields + [k for k in ow_f if k not in fields]:
                if fld not in ow_f and fld not in dg_f:
                    continue
                dv = dg_f.get(fld, {}).get('value')
                cell = ow_f.get(fld)
                if cell is None:  # sin dato de overwrite (K LTB, o /k no disponible)
                    src, val, auto = ('design', dv, None) if dv is not None else ('none', None, None)
                elif cell['auto']:
                    src, val, auto = ('design', dv, True) if dv is not None else ('none', None, True)
                    if dv is None:
                        notes.append(f'{f} · {fld}: automático sin valor de diseño; ejecute el diseño de acero.')
                elif cell['value'] is not None:
                    src, val, auto = 'overwrite', cell['value'], False
                else:
                    src, val, auto = 'none', None, False
                out[fld] = {'value': val, 'source': src, 'overwriteAuto': auto, 'designValue': dv}
            frames[f] = out
        return {'available': True, 'reason': '',
                'tableKey': ow.get('tableKey') if ow['available'] else None,
                'designTableKey': dg.get('tableKey') if dg['available'] else None,
                'notes': notes, 'frames': frames, 'governing': dg['governing']}

    def diagnose(self, model: Any) -> dict:
        return {'text': diagnose(model)}

    # ── paso 1: vista previa ───────────────────────────────────────────────
    def preview(self, model: Any, frame: str, changes: Mapping[str, Any]) -> dict:
        try:
            clean = _validate_changes(changes)
        except ValueError as exc:
            raise KServiceError(422, 'invalid_changes', str(exc)) from exc
        current = read_k_factors(model)
        if not current.available:
            raise KServiceError(409, 'k_unavailable', current.reason)
        if frame not in current.frames:
            raise KServiceError(404, 'frame_without_k',
                                f'La barra {frame} no tiene overwrites de diseño de acero; no se crea ninguno.')
        before = {**current.frames[frame], **current.lengths.get(frame, {})}
        missing = [f for f in clean if f not in before]
        if missing:
            raise KServiceError(409, 'column_missing',
                                f'La tabla {current.table_key} no trae la columna de ' +
                                ', '.join(field_label(f) for f in missing) + '; no se escribe nada.')
        diff = [{'field': f, 'label': field_label(f), 'before': kvalue_json(before[f]),
                 'after': {'value': None if v == 'auto' else v, 'auto': v == 'auto'},
                 'text': f'{field_label(f)}: {_fmt(before[f])} → {_fmt(v)}'}
                for f, v in clean.items()]
        self._expire()
        nonce = secrets.token_urlsafe(24)
        self._pending[nonce] = {'frame': frame, 'changes': clean, 'model': str(model.GetModelFilename()),
                                'at': time.monotonic(), 'diff': diff}
        try:
            locked = bool(model.GetModelIsLocked())
        except Exception:  # noqa: BLE001
            locked = False
        return {'nonce': nonce, 'frame': frame, 'diff': diff, 'modelLocked': locked,
                'warnings': [STALE_WARNING]}

    def _expire(self) -> None:
        now = time.monotonic()
        for n in [n for n, p in self._pending.items() if now - p['at'] > NONCE_TTL_SECONDS]:
            del self._pending[n]

    def _audit(self, event: str, frame: str, changes: Mapping[str, Any], **extra: Any) -> str:
        try:
            self.audit.write({'event': event, 'action': 'set_k_factors', 'frame': frame,
                              'changes': dict(changes), 'source': 'web', **extra})
            return ''
        except Exception as exc:  # noqa: BLE001
            return f'No se pudo escribir en el registro de auditoría: {exc}'

    # ── paso 2: aplicar ────────────────────────────────────────────────────
    def apply(self, connector: Any, nonce: str, unlock: bool) -> dict:
        self._expire()
        pending = self._pending.get(nonce)
        if pending is None:
            raise KServiceError(410, 'nonce_invalid',
                                'La vista previa caducó o ya se usó. Vuelve a pedirla antes de aplicar.')
        model = connector.model
        frame, changes = pending['frame'], pending['changes']
        if str(model.GetModelFilename()) != pending['model']:
            del self._pending[nonce]
            raise KServiceError(409, 'model_changed', 'El modelo abierto cambió desde la vista previa; no se aplicó nada.')
        is_demo = bool(getattr(connector, 'is_demo', False))
        backup_path = None
        if not is_demo:
            try:
                backup_path = self.backups.create_backup(model, f'k_factors_{frame}')
            except Exception as exc:  # noqa: BLE001
                note = self._audit('write_failed', frame, changes, error=f'copia de seguridad: {exc}')
                raise KServiceError(500, 'backup_failed',
                                    f'No se aplicó nada: no se pudo crear la copia de seguridad ({exc}).', note=note) from exc
        unlocked = False
        try:
            try:
                result = write_k_factors(model, frame, changes)
            except KFactorLockedError:
                if not unlock:
                    # El nonce sigue vigente: la web pide la 2.ª confirmación y reenvía con unlock=true.
                    self._audit('write_blocked_locked', frame, changes, backup=str(backup_path or ''))
                    raise KServiceError(409, 'model_locked', LOCKED_WARNING, backup=str(backup_path or ''))
                unlocked = bool(connector.unlock_if_needed(True))
                result = write_k_factors(model, frame, changes)
        except KFactorVerifyError as exc:
            del self._pending[nonce]
            note = self._audit('write_failed', frame, changes, error=str(exc), unlocked=unlocked, verified=False,
                               backup=str(backup_path or ''))
            raise KServiceError(502, 'verify_failed', f'{exc} Se muestran los valores reales de SAP2000.',
                                actual={c.field: kvalue_json(c.after) for c in (exc.result.changes if exc.result else ())},
                                unlocked=unlocked, note=note) from exc
        except KFactorError as exc:
            del self._pending[nonce]
            note = self._audit('write_failed', frame, changes, error=str(exc), unlocked=unlocked, backup=str(backup_path or ''))
            raise KServiceError(502, 'write_failed', f'No se pudo aplicar: {exc}', unlocked=unlocked, note=note) from exc
        del self._pending[nonce]
        note = self._audit('write_executed', frame, changes, backup=str(backup_path or ''), unlocked=unlocked, verified=True,
                           result={c.field: {'before': str(c.before), 'after': str(c.after)} for c in result.changes})
        return {'ok': True, 'frame': frame, 'unlocked': unlocked, 'backup': str(backup_path or ''),
                'values': {c.field: kvalue_json(c.after) for c in result.changes},
                'warnings': [STALE_WARNING] + (['Se desbloqueó el modelo: se descartaron los resultados de análisis.'] if unlocked else []),
                'designResultsStale': True, 'auditNote': note}
