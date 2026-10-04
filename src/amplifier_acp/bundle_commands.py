"""Exact bundle command outcomes; no reconstruction from current settings/history."""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

MAX_RESULT = 512 * 1024
MAX_RECEIPT = MAX_RESULT + 2048
ADMIN_OPERATIONS = {'bundle.save', 'bundle.export', 'bundle.default'}
CAPABILITY = {'version': 1, 'commandId': 'optional-legacy-required-for-receipt',
              'adminOperations': sorted(ADMIN_OPERATIONS), 'activation': 'bundle.switch',
              'adminReceipt': 'bundle.receipt', 'activationReceipt': '_amplifier/bundle/receipt',
              'passiveReceipt': True, 'maxResultBytes': MAX_RESULT, 'replayUnknown': False}

def diagnostic_reference(value):
    reference = value.get('diagnosticReceipt') if isinstance(value, dict) else None
    return {'diagnosticReceipt': reference} if isinstance(reference, str) and re.fullmatch(r'startup-[a-f0-9]{32}\.log', reference) else {}


class BundleCommandError(ValueError):
    def __init__(self, reason, *, refused=False, command_id=None, diagnostic=None):
        super().__init__('Bundle command was refused without an effect.' if refused else 'Bundle command outcome is unknown; inspect its exact receipt without replay.')
        self.data = {'reason': reason, 'replayed': False, **({'executed': False} if refused else {'outcome': 'unknown'}),
                     **({'commandId': command_id} if command_id else {}), **diagnostic_reference(diagnostic)}

class BundlePreflightRefusal(ValueError):
    data = {'executed': False, 'reason': 'bundle-preflight-refused', 'replayed': False}

    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.data = {**self.data, **diagnostic_reference(diagnostic)}


class BundleActivationFailure(RuntimeError):
    def __init__(self, *, diagnostic=None):
        super().__init__('Bundle activation did not complete conclusively; inspect its exact receipt without replay.')
        self.data = {'outcome': 'unknown', 'replayed': False, **diagnostic_reference(diagnostic)}

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def command_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise BundleCommandError('invalid-bundle-command-id', refused=True)
    return value

class BundleCommands:
    def __init__(self, config, workspace, session_id=None):
        from .maintenance_gate import checked
        home = config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified'
        self.path = checked(Path(home).expanduser().absolute()/'bundles'/'command-receipts.sqlite3')
        self.scope = {'workspace': str(Path(workspace).expanduser().resolve()), 'sessionId': session_id}

    def connect(self, write=False):
        from .maintenance_gate import checked
        checked(self.path)
        for suffix in ('-wal', '-shm', '-journal'): checked(Path(str(self.path)+suffix))
        if not write and not self.path.exists(): return None
        if write: self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path if write else self.path.as_uri()+'?mode=ro', uri=not write, timeout=0, isolation_level=None)
        db.execute('PRAGMA trusted_schema=OFF')
        if write:
            os.chmod(self.path, 0o600)
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, binding TEXT NOT NULL, scope TEXT NOT NULL, body TEXT NOT NULL)')
        else: db.execute('PRAGMA query_only=ON')
        return db

    @staticmethod
    def decode(body):
        if body is None:raise BundleCommandError('bundle-command-receipt-oversized')
        return json.loads(body)

    def receipt(self, identity):
        identity = command_id(identity)
        db = self.connect()
        if db is None: return None
        try:
            row = db.execute('SELECT CASE WHEN length(CAST(body AS BLOB))<=? THEN body END FROM commands WHERE id=? AND scope=?', (MAX_RECEIPT, identity, encoded(self.scope).decode())).fetchone()
            return self.decode(row[0]) if row else None
        finally: db.close()

    def claim(self, operation, args):
        identity = command_id(args.get('commandId'))
        binding = hashlib.sha256(encoded({'scope': self.scope, 'operation': operation, 'args': args})).hexdigest()
        db = self.connect(True)
        try:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT binding,CASE WHEN length(CAST(body AS BLOB))<=? THEN body END FROM commands WHERE id=?', (MAX_RECEIPT, identity)).fetchone()
            if old:
                if old[0] != binding: raise BundleCommandError('bundle-command-identity-conflict', refused=True, command_id=identity)
                db.execute('COMMIT'); return False, self.decode(old[1])
            row = {'version': 1, 'commandId': identity, 'operation': operation, 'status': 'unknown', 'createdAt': time.time(), 'replayed': False}
            db.execute('INSERT INTO commands VALUES (?,?,?,?)', (identity, binding, encoded(self.scope).decode(), encoded(row).decode()))
            db.execute('COMMIT'); return True, row
        finally: db.close()

    def finish(self, row, *, result=None, refused=False):
        row = {**row, 'status': 'refused' if refused else 'succeeded', 'completedAt': time.time()}
        if refused: row.update(executed=False, reason='bundle-preflight-refused')
        else: row['result'] = result
        db = self.connect(True)
        try: db.execute('UPDATE commands SET body=? WHERE id=? AND scope=?', (encoded(row).decode(), row['commandId'], encoded(self.scope).decode()))
        finally: db.close()
        return row

    @staticmethod
    def outcome(row):
        if row['status'] == 'succeeded': return row['result']
        raise BundleCommandError(row.get('reason', 'bundle-command-outcome-unknown'), refused=row['status']=='refused', command_id=row['commandId'], diagnostic=row)

    async def perform(self, operation, args, invoke):
        fresh, row = self.claim(operation, args)
        if not fresh: return self.outcome(row)
        effected = False
        def effect():
            nonlocal effected
            effected = True
        try:
            result = await invoke(effect)
            if len(encoded(result)) > MAX_RESULT: raise ValueError('Bundle result exceeds receipt byte limit')
            self.finish(row, result=result)
            return result
        except BaseException as exc:
            # Only owner preflight or an explicit worker no-effect proof permits
            # refusal. A cancelled/lost transport or arbitrary post-write error
            # retains the original unknown reservation, never reruns the call.
            proof = getattr(exc, 'data', {})
            refused = isinstance(exc, ValueError) and not effected or isinstance(proof, dict) and proof.get('executed') is False
            diagnostic = diagnostic_reference(proof)
            if diagnostic:
                # Add only an owned private-log basename. This supplies evidence,
                # never a terminal result or permission to replay an unknown.
                row = {**row, **diagnostic}
                try:
                    db = self.connect(True)
                    try: db.execute('UPDATE commands SET body=? WHERE id=? AND scope=?', (encoded(row).decode(), row['commandId'], encoded(self.scope).decode()))
                    finally: db.close()
                except Exception:
                    pass  # Keep the existing reservation if diagnostics cannot persist.
            if refused: self.finish(row, refused=True)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)): raise
            raise BundleCommandError('bundle-preflight-refused' if refused else 'bundle-command-outcome-unknown', refused=refused, command_id=row['commandId'], diagnostic=diagnostic) from None


def passive(server, params):
    from .native.session_files import validate_id
    if set(params) != {'sessionId', 'cwd', 'commandId'}: raise ValueError('Exact bundle receipt selectors required')
    identity = validate_id(params['sessionId']); cwd = params['cwd']
    if not isinstance(cwd, str) or not Path(cwd).is_absolute(): raise ValueError('Native bundle receipt cwd must be absolute')
    cwd = str(Path(cwd).resolve())
    server.directory(cwd, identity)  # validate native identity/path without opening history
    return {'receipt': BundleCommands(server.config, cwd, identity).receipt(params['commandId'])}
