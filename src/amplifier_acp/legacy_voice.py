"""Offline conversion of legacy voice-only chats into native history.

This is a staging tool, never a startup migration. Read one retained application
snapshot and publish into an exclusive, new output directory. Existing native
history, mixed chats and uncertain work remain with their original owners.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from .history_import import encoded, strict_json
from .session_lifecycle import regular

MAX_STATE = 256 * 1024 * 1024
MAX_RECORD = 16 * 1024 * 1024
MAX_SESSIONS = 10000
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def bounded_row(db, query, args=(), maximum=MAX_RECORD):
    row = db.execute(query, args).fetchone()
    if row is None:
        raise ValueError("Required saved legacy record is unavailable")
    text = row[0]
    if not isinstance(text, str) or len(text.encode()) > maximum:
        raise ValueError("Saved legacy record exceeds the migration bound")
    return strict_json(text)


def legacy_sessions(db):
    """Apply current main's scoped committed records over its base checkpoint."""
    value = bounded_row(db, 'SELECT value FROM state WHERE id=1 AND length(CAST(value AS BLOB))<=?', (MAX_STATE,), MAX_STATE)
    if not isinstance(value, dict) or not isinstance(value.get('sessions'), list) or len(value['sessions']) > MAX_SESSIONS:
        raise ValueError("Unsupported legacy session checkpoint")
    sessions = {}
    for row in value['sessions']:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in sessions:
            raise ValueError("Invalid or duplicate legacy session identity")
        sessions[row['id']] = row
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_records'").fetchone():
        count, size = db.execute("SELECT count(*),coalesce(sum(length(CAST(value AS BLOB))),0) FROM state_records WHERE kind='session'").fetchone()
        if count > MAX_SESSIONS or size > MAX_STATE:
            raise ValueError("Scoped legacy session records exceed migration bounds")
        for identity, text in db.execute("SELECT id,value FROM state_records WHERE kind='session'"):
            row = strict_json(text)
            if row is None:
                sessions.pop(identity, None)
            elif isinstance(row, dict) and row.get('id') == identity:
                sessions[identity] = row
            else:
                raise ValueError("Invalid scoped legacy session record")
    if len(sessions) > MAX_SESSIONS:
        raise ValueError("Too many legacy sessions")
    return sessions


def messages(db, database, session):
    value = session.get('messages')
    if value is None:
        reference = session.get('_coldFields', {}).get('messages', {})
        identity = reference.get('$resource')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError("Saved message body is unavailable")
        value = bounded_row(db, 'SELECT value FROM state_resources WHERE id=? AND length(CAST(value AS BLOB))<=?', (identity, MAX_RECORD))
        if isinstance(value, dict) and set(value) == {'$blob'}:
            if value['$blob'] != identity:
                raise ValueError("Saved message resource identity differs")
            value = strict_json(regular(database.parent/'artifacts'/(identity+'.json'), MAX_RECORD))
        if sha(value) != identity:
            raise ValueError("Saved message resource digest differs")
    if not isinstance(value, list) or len(value) > 10000 or len(encoded(value)) > MAX_RECORD:
        raise ValueError("Unsupported saved message body")
    return value


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Saved voice timestamp is unavailable")
    return datetime.fromtimestamp(value, UTC).isoformat()


def voice_rows(rows):
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'} or row.get('via') != 'call':
            raise ValueError("Mixed or non-voice history needs separate reconciliation")
        if not isinstance(row.get('text'), str) or len(row['text'].encode()) > 1000000:
            raise ValueError("Unsupported saved voice text")
        call, item = row.get('voiceId'), row.get('voiceItemId')
        if any(not isinstance(v, str) or not 0 < len(v) <= 200 or any(ord(c) < 32 for c in v) for v in (call, item)):
            raise ValueError("Saved voice identities are unavailable")
        if (call, item) in seen:
            raise ValueError("Duplicate saved voice item identity")
        if any(row.get(key) for key in ('attachments', 'tool_calls', 'inputId', 'questionId', 'replyTo', 'reactions')):
            raise ValueError("Voice message has other owner data requiring reconciliation")
        seen.add((call, item))
        result.append({'role': row['role'], 'content': row['text'], 'metadata': {
            'timestamp': timestamp(row.get('createdAt')), 'inputOrigin': 'voice',
            'amplifier_voice': {'version': 1, 'callId': call, 'itemId': item, 'recordedOnly': True},
            'legacyVoice': {'messageId': row.get('id'), 'authorization': 'historical-only'},
        }})
    return result


def prepare(database, native_home):
    database, native_home = Path(database).absolute(), Path(native_home).absolute()
    # Refuse redirected sources; immutable is only valid for an offline snapshot.
    # Query-only normal SQLite also reads its WAL if this is an online backup.
    for path in (database, native_home):
        if path.resolve() != path:
            raise ValueError("Legacy source paths must be canonical without symlinks")
    if not database.is_file() or not native_home.is_dir():
        raise ValueError("Existing legacy database and native home are required")
    items, payloads = [], {}
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        sessions = legacy_sessions(db)
        for identity, session in sorted(sessions.items()):
            item = {'sessionId': identity, 'status': 'review-required'}
            items.append(item)
            if not IDENTITY.fullmatch(identity):
                item['reason'] = 'Unsupported session identity'; continue
            native = list((native_home/'projects').glob('*/sessions/'+identity))
            legacy = database.parent/'sessions'/identity
            if any(any((directory/name).exists() or (directory/name).is_symlink() for name in ('transcript.jsonl', 'transcript.jsonl.backup', 'checkpoint.json')) for directory in [*native, legacy]):
                item.update(status='native-history-owned', reason='Existing history is preserved by its native migration path'); continue
            try:
                if session.get('status') not in {'idle', 'ready'}:
                    raise ValueError('Session was active or its outcome is uncertain')
                if any(session.get(key) for key in ('workers', 'approvals', 'queue', 'queuedMessages', 'pendingInputs', 'activeInputId', 'turns', 'execution', 'selection', 'configuration', 'location', 'parentId')):
                    raise ValueError('Session has other configuration or execution state requiring reconciliation')
                if set(session.get('_coldFields', {})) - {'messages'}:
                    raise ValueError('Other saved cold fields require reconciliation')
                rows = messages(db, database, session)
                if not rows:
                    item.update(status='empty', reason='Empty chat retained in the original snapshot'); continue
                converted = voice_rows(rows)
                cwd, bundle = session.get('workspace'), session.get('bundle')
                if not isinstance(cwd, str) or not Path(cwd).is_absolute() or '\x00' in cwd:
                    raise ValueError('Recorded workspace is unavailable')
                if not isinstance(bundle, str) or not 0 < len(bundle) <= 4096 or any(ord(c) < 32 for c in bundle):
                    raise ValueError('Recorded bundle is unavailable')
                title = session.get('title')
                if not isinstance(title, str) or not title.strip() or len(title) > 500:
                    raise ValueError('Recorded title is unavailable')
                created = timestamp(session.get('createdAt'))
                metadata = {'session_id': identity, 'parent_id': None, 'name': title,
                    'name_source': 'legacy', 'name_auto': session.get('autoName', session.get('titleSource') != 'manual'),
                    'working_dir': cwd, 'bundle': bundle, 'bundle_name': bundle,
                    'created_at': created, 'updated_at': timestamp(session.get('recentActivityAt', session['createdAt'])),
                    'status': 'idle', 'legacy_voice_migration': {'sourceSessionId': identity, 'sourceDigest': sha(session), 'workReplayed': False}}
                payloads[identity] = {'messages': converted, 'metadata': metadata, 'original': session}
                item.update(status='ready', messageCount=len(rows), sourceDigest=sha(session), payloadDigest=sha(payloads[identity]))
            except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
                item['reason'] = str(exc)[:300]
        db.rollback()
    report = {'version': 1, 'kind': 'legacy-voice-only-migration', 'database': str(database), 'nativeHome': str(native_home),
        'items': items, 'counts': dict(Counter(item['status'] for item in items)), 'sourceMutated': False,
        'executionStarted': False, 'scope': 'voice-only chats without existing native history; other owners remain unconverted'}
    report['planHash'] = sha(report)
    return report, payloads


def materialize(database, native_home, output, expected):
    report, payloads = prepare(database, native_home)
    if report['planHash'] != expected:
        raise ValueError('Legacy snapshot changed; review a new migration plan')
    output = Path(output).absolute()
    if output.resolve() != output or any(output.is_relative_to(p) for p in (Path(database).parent, Path(native_home))):
        raise ValueError('Output must be separate from both original source roots')
    output.mkdir(mode=0o700)  # Exclusive reservation; a retry never overwrites work.
    from .relocation import write
    from .native.session_files import project_slug
    from amplifier_foundation.session.history import SessionHistoryStore
    receipt = {**report, 'status': 'reserved', 'output': str(output), 'nativeHome': str(output/'native'), 'sourceNativeHome': str(native_home)}
    write(output/'receipt.json', receipt)
    (output/'native').mkdir(mode=0o700)
    (output/'originals').mkdir(mode=0o700)
    for identity, payload in payloads.items():
        cwd = payload['metadata']['working_dir']
        directory = output/'native'/'projects'/project_slug(cwd)/'sessions'/identity
        directory.mkdir(parents=True, mode=0o700)
        write(output/'originals'/(identity+'.json'), payload['original'])
        SessionHistoryStore(directory, session_id=identity).save(payload['messages'], payload['metadata'], preserve_system=True)
    # A partial/unknown output is never activated. All source originals remain.
    receipt['status'] = 'completed'
    write(output/'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--native-home', required=True)
    parser.add_argument('--output')
    parser.add_argument('--expected-plan')
    args = parser.parse_args()
    if bool(args.output) != bool(args.expected_plan):
        parser.error('--output and --expected-plan are required together')
    result = materialize(args.database, args.native_home, args.output, args.expected_plan) if args.output else prepare(args.database, args.native_home)[0]
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
