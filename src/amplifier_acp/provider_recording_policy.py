"""Native-owned recording policy, compatible with existing diagnostic settings.

Only explicit policy writes create receipts. Reads never prepare a runtime or
scan existing calls. A file marker proves a lost post-publication acknowledgement;
an uncertain command is inspected, never repeated.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from filelock import FileLock
from .history_import import encoded, strict_json, token
from .maintenance_gate import checked
from .native.shared_settings import atomic_write

OPERATIONS = {'provider.recording.get', 'provider.recording.configure', 'provider.recording.receipt'}
CAPABILITY = {'version': 1, 'cas': True, 'receipt': True, 'application': 'next-runtime-preparation'}
MARKER = '_amplifier_acp_recording'


def read_configuration(home):
    path = checked(Path(home) / 'diagnostics' / 'config.json')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError('Recording settings are unavailable; preserve the original file')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Recording settings exceed the bounded policy size')
    value = strict_json(raw)
    if not isinstance(value, dict) or ('providerRequests' in value and type(value['providerRequests']) is not bool):
        raise ValueError('Recording settings are malformed; preserve the original file')
    return value


def projection(value):
    # Include unrelated settings in CAS so a concurrent legacy edit is not lost.
    revision = hashlib.sha256(encoded({k: v for k, v in value.items() if k != MARKER})).hexdigest()
    return {'enabled': value.get('providerRequests') is True, 'revision': revision,
            'application': CAPABILITY['application']}


class ProviderRecording:
    def __init__(self, config):
        app = Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home() / '.amplifier-unified').expanduser().absolute()
        self.home = checked(app)
        self.root = checked(app / 'diagnostics')
        self.path = self.root / 'recording-receipts.sqlite3'

    def db(self, write=False):
        from .journal_schema import connect
        return connect(self.path, 'provider-recording', write=write, timeout=5 if write else 0, isolation_level=None)

    def resolved(self, row, configuration):
        signature, body = row
        result = json.loads(body)
        marker = configuration.get(MARKER)
        current = projection(configuration)
        if result['status'] == 'unknown' and marker == {'commandId': result['commandId'], 'signature': signature, 'revision': current['revision']}:
            result = {**result, 'status': 'succeeded', 'result': current}
        return result

    def receipt(self, identity):
        db = self.db()
        if db is None:
            return None
        try:
            row = db.execute('SELECT signature,body FROM commands WHERE id=?', (identity,)).fetchone()
            return self.resolved(row, read_configuration(self.home)) if row else None
        finally:
            db.close()

    def perform(self, operation, args):
        fields = {'provider.recording.get': set(), 'provider.recording.receipt': {'commandId'},
                  'provider.recording.configure': {'commandId', 'expectedRevision', 'enabled'}}
        if operation not in fields or set(args) != fields[operation]:
            raise ValueError('Exact recording policy fields required')
        if operation == 'provider.recording.get':
            return projection(read_configuration(self.home))
        identity = token(args['commandId'], 'commandId')
        if operation == 'provider.recording.receipt':
            return self.receipt(identity)
        expected = args['expectedRevision']
        if type(args['enabled']) is not bool or not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('Boolean recording choice and exact current revision required')
        signature = hashlib.sha256(encoded(args)).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = checked(self.root / 'recording-policy.lock')
        if lock.exists() and (not stat.S_ISREG(lock.stat().st_mode) or lock.stat().st_nlink != 1):
            raise ValueError('Recording policy lock is unavailable')
        with FileLock(lock, timeout=5, mode=0o600):
            db = self.db(True)
            try:
                configuration = read_configuration(self.home)
                old = db.execute('SELECT signature,body FROM commands WHERE id=?', (identity,)).fetchone()
                if old:
                    if old[0] != signature:
                        raise ValueError('Recording command identity already binds different arguments')
                    return self.resolved(old, configuration)
                # Retain original receipt proof before replacing its single file marker.
                marker = configuration.get(MARKER)
                if isinstance(marker, dict) and isinstance(marker.get('commandId'), str):
                    prior = db.execute('SELECT signature,body FROM commands WHERE id=?', (marker['commandId'],)).fetchone()
                    if prior:
                        db.execute('UPDATE commands SET body=? WHERE id=?', (encoded(self.resolved(prior, configuration)).decode(), marker['commandId']))
                if db.execute('SELECT count(*) FROM (SELECT 1 FROM commands LIMIT 4096)').fetchone()[0] >= 4096:
                    raise ValueError('Recording receipt capacity reached; original policy and receipts are preserved')
                current = projection(configuration)
                receipt = {'commandId': identity, 'status': 'unknown', 'replayed': False}
                if expected != current['revision']:
                    receipt.update(status='failed', executed=False, reason='recording_policy_changed', currentRevision=current['revision'])
                db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, signature, encoded(receipt).decode()))
                if receipt['status'] == 'failed':
                    return receipt
                updated = {**configuration, 'providerRequests': args['enabled']}
                result = projection(updated)
                updated[MARKER] = {'commandId': identity, 'signature': signature, 'revision': result['revision']}
                body = encoded(updated)
                if len(body) > 65536:
                    receipt.update(status='failed', executed=False, reason='recording_policy_too_large')
                else:
                    atomic_write(self.root / 'config.json', body.decode(), private=True)
                    receipt.update(status='succeeded', result=result)
                db.execute('UPDATE commands SET body=? WHERE id=?', (encoded(receipt).decode(), identity))
                return receipt
            finally:
                db.close()
