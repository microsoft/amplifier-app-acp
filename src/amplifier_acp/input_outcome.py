"""Exact settled input failures in the existing session input provenance store.

These receipts prove terminal execution, never absence of provider/tool effects.
Missing receipts are unknown, not permission to replay an original input.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

CAPABILITY = {'version': 1, 'method': '_amplifier/input/receipt', 'passive': True, 'durable': True}
KEY = 'amplifier.dev/inputOutcome'
MAX_BYTES = 4096


def input_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('A bounded original native inputId is required')
    return value


def validate(value, session_id, identity):
    keys = {'version', 'sessionId', 'inputId', 'status', 'terminal', 'noReplay', 'inputPersisted', 'effectBoundary', 'error'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int
            or value['version'] != 1 or value['sessionId'] != session_id or value['inputId'] != identity
            or value['status'] != 'failed' or any(value[k] is not True for k in ('terminal', 'noReplay', 'inputPersisted'))
            or not isinstance(value['effectBoundary'], str) or value['effectBoundary'] not in {'unknown', 'input-persisted', 'provider-request', 'model-output', 'tool-effects'}):
        raise ValueError('Invalid exact native input outcome')
    error = value['error']
    if (not isinstance(error, dict) or set(error) != {'type', 'message'}
            or not isinstance(error['type'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,99}', error['type'])
            or not isinstance(error['message'], str) or not 1 <= len(error['message']) <= 1000
            or any(ord(c) < 32 or ord(c) == 127 for c in error['message'])):
        raise ValueError('Invalid public native input error')
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(body.encode()) > MAX_BYTES: raise ValueError('Native input outcome exceeds its bound')
    return body


class Outcomes:
    def __init__(self, app_home, session_id, cwd):
        from .native.session_files import validate_id
        from .maintenance_gate import checked
        self.session_id = validate_id(session_id)
        if not isinstance(cwd, str) or not 1 <= len(cwd) <= 8192 or not Path(cwd).is_absolute():
            raise ValueError('Original absolute native history cwd is required')
        self.cwd = str(Path(cwd).resolve())
        self.path = checked(Path(app_home).absolute()/'sessions'/self.session_id/'input-provenance.sqlite3')

    def connect(self, write=False):
        from .maintenance_gate import checked
        checked(self.path)
        for suffix in ('-wal', '-shm', '-journal'): checked(Path(str(self.path)+suffix))
        if not write and not self.path.exists(): return None
        if write: self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.path if write else self.path.as_uri()+'?mode=ro', uri=not write, timeout=0)
        db.execute('PRAGMA trusted_schema=OFF')
        if write:
            os.chmod(self.path, 0o600)
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
            # Keep the existing context decorator and provenance reader valid
            # when this is the first record in an ordinary ACP conversation.
            db.execute('CREATE TABLE IF NOT EXISTS provenance(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS outcomes(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,body TEXT NOT NULL)')
        else: db.execute('PRAGMA query_only=ON')
        return db

    def read(self, identity):
        identity = input_id(identity)
        db = self.connect()
        if db is None: return None
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outcomes'").fetchone(): return None
            row = db.execute('SELECT CASE WHEN length(CAST(body AS BLOB))<=? THEN body END FROM outcomes WHERE id=? AND cwd=?', (MAX_BYTES, identity, self.cwd)).fetchone()
            if row is None: return None
            if row[0] is None: raise ValueError('Native input outcome exceeds its bound')
            value = json.loads(row[0]); validate(value, self.session_id, identity)
            return value
        finally: db.close()

    def failed(self, identity, event):
        """Caller holds the writer and has checkpointed this exact input."""
        identity = input_id(identity)
        from .lifecycle_failure import generation_failure
        detail = generation_failure(event) or generation_failure({**event, 'error_category': 'unknown'})
        value = {'version': 1, 'sessionId': self.session_id, 'inputId': identity, 'status': 'failed',
                 'terminal': True, 'noReplay': True, 'inputPersisted': True,
                 # Failure stage alone cannot exclude earlier provider/tool effects.
                 'effectBoundary': 'unknown', 'error': {'type': detail['errorType'], 'message': detail['summary']}}
        body = validate(value, self.session_id, identity)
        db = self.connect(True)
        try:
            with db:
                old = db.execute('SELECT cwd,body FROM outcomes WHERE id=?', (identity,)).fetchone()
                if old and old != (self.cwd, body): raise ValueError('An original input outcome cannot be replaced')
                db.execute('INSERT OR IGNORE INTO outcomes VALUES(?,?,?)', (identity, self.cwd, body))
        finally: db.close()
        return value


def passive(server, params):
    if set(params) != {'sessionId', 'cwd', 'inputId'}: raise ValueError('Exact native input receipt selectors required')
    from .relocation import home
    owner = Outcomes(home(server), params['sessionId'], params['cwd'])
    server.directory(owner.cwd, owner.session_id)
    loaded = server.sessions.get(owner.session_id)
    if loaded and loaded.cwd != owner.cwd: raise ValueError('Input receipt belongs to another native history workspace')
    return {'receipt': owner.read(params['inputId'])}
