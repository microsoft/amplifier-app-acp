"""Bounded, owner-local admission for native SQLite receipt journals.

No record reconstruction or file scan. Existing authority is inspected through a
read-only connection before any writable open. All cooperating initializers use
the same short OS lock; ordinary record transactions retain their owner policy.
"""
from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import os
from pathlib import Path
import sqlite3
import stat

from filelock import FileLock


class JournalUnavailable(RuntimeError):
    """Retain the original journal and inspect it; never infer a fresh command."""


# Fixed native-owned schemas, not a registry of user tables. SQL is never supplied
# by callers. The integer marker binds family + enrolled optional namespaces.
_SCHEMAS = {
    'bundle': (1, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,binding TEXT NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL)',), ()),
    'maintenance': (2, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,workspace TEXT,signature TEXT,body TEXT)',), ()),
    'lifecycle': (3, (
        'CREATE TABLE receipts(id TEXT PRIMARY KEY,signature TEXT,body TEXT)',), ()),
    'generations': (4, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL)',
        'CREATE TABLE state(id TEXT PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE inventory(id TEXT PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE diagnostics(sequence INTEGER PRIMARY KEY,body TEXT NOT NULL)'), ()),
    'history-import': (5, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,signature TEXT NOT NULL,body TEXT NOT NULL)',
        'CREATE TABLE uploads(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,spec TEXT NOT NULL,received INTEGER NOT NULL DEFAULT 0,body BLOB NOT NULL,prepared BLOB,preview TEXT UNIQUE,consumed TEXT,target TEXT)'), ()),
    'voice-preferences': (6, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL)',), ()),
    'permissions': (7, (
        'CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,workspace TEXT NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL)',), ()),
    'managed-disposal': (8, (
        'CREATE TABLE reviews(id TEXT PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL)'), ()),
    'steering': (9, (
        'CREATE TABLE receipts(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL)',), ()),
    'coordination': (10, (
        'CREATE TABLE workers(id TEXT PRIMARY KEY,body TEXT NOT NULL,sequence INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE reports(worker TEXT,sequence INTEGER,id TEXT,body TEXT,PRIMARY KEY(worker,sequence),UNIQUE(worker,id))',
        'CREATE TABLE commands(id TEXT PRIMARY KEY,signature TEXT,body TEXT)',
        'CREATE TABLE state(id INTEGER PRIMARY KEY,revision INTEGER)'), ()),
    'messages': (11, (
        'CREATE TABLE annotations(message TEXT NOT NULL,actor TEXT NOT NULL,emoji TEXT NOT NULL,PRIMARY KEY(message,actor,emoji))',
        'CREATE TABLE revisions(message TEXT PRIMARY KEY,value INTEGER NOT NULL)',
        'CREATE TABLE receipts(command TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,value TEXT NOT NULL)'), (
        'CREATE TABLE quotes(input TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,value TEXT NOT NULL)',)),
    'inputs': (12, (
        'CREATE TABLE provenance(id TEXT PRIMARY KEY,body TEXT NOT NULL)',), (
        'CREATE TABLE outcomes(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,body TEXT NOT NULL)',)),
}
_INDEXES = {
    'history-import': ('CREATE INDEX IF NOT EXISTS uploads_command ON uploads(consumed)',),
    'steering': ("CREATE INDEX IF NOT EXISTS receipts_disposition ON receipts(json_extract(body,'$.disposition'),json_extract(body,'$.generationId'))",),
    'coordination': (
        "CREATE INDEX IF NOT EXISTS workers_status ON workers(json_extract(body,'$.status'))",
        "CREATE INDEX IF NOT EXISTS workers_call ON workers(json_extract(body,'$.callId'),json_extract(body,'$.kind'))",
        "CREATE INDEX IF NOT EXISTS workers_visible ON workers(id) WHERE json_extract(body,'$.supersededBy') IS NULL",
        "CREATE INDEX IF NOT EXISTS command_disposition ON commands(json_extract(body,'$.disposition'))"),
}
_MAGIC = 0x414A0000


def _fail():
    raise JournalUnavailable('Native journal authority is unavailable; preserve the original files and inspect the original receipt without replay.')


def _paths(path):
    from .maintenance_gate import checked
    path = checked(Path(path).absolute())
    found = []
    for suffix in ('', '-wal', '-shm', '-journal'):
        item = checked(Path(str(path) + suffix))
        try:
            info = item.lstat()
        except FileNotFoundError:
            found.append(False)
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail()
        found.append(True)
    if not found[0] and any(found[1:]):
        _fail()
    return path, found[0]


def _shape(db):
    # Tables/columns only, fixed upper bounds. No command rows, SQL text, history,
    # full integrity_check or full-file reads. Index contents remain rebuildable.
    rows = db.execute("SELECT type,substr(name,1,128) FROM sqlite_schema WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%' LIMIT 9").fetchall()
    if len(rows) > 8 or any(kind != 'table' for kind, _ in rows):
        _fail()
    result = {}
    for _, name in rows:
        if name not in {'commands','receipts','state','inventory','diagnostics','uploads','reviews','workers','reports','annotations','revisions','quotes','provenance','outcomes'}:
            _fail()
        columns = db.execute('PRAGMA table_info("' + name + '")').fetchmany(16)
        if len(columns) >= 16:
            _fail()
        unique = []
        indexes = db.execute('SELECT name,origin FROM pragma_index_list(?) WHERE "unique"=1 LIMIT 9', (name,)).fetchall()
        if len(indexes) > 8:
            _fail()
        for index, origin in indexes:
            keys = db.execute('SELECT name FROM pragma_index_info(?) ORDER BY seqno LIMIT 16', (index,)).fetchall()
            if len(keys) >= 16 or any(key[0] is None for key in keys):
                _fail()
            unique.append((origin, tuple(key[0] for key in keys)))
        result[name] = (tuple(tuple(row[1:]) for row in columns), tuple(sorted(unique)))
    return result


@lru_cache(maxsize=len(_SCHEMAS) * 2)
def _expected(profile, optional):
    _, base, extras = _SCHEMAS[profile]
    with closing(sqlite3.connect(':memory:')) as db:
        for sql in base + (extras if optional else ()):
            db.execute(sql)
        return _shape(db)


def _validate(db, profile):
    number, _, extras = _SCHEMAS[profile]
    marker = db.execute('PRAGMA user_version').fetchone()[0]
    shape = _shape(db)
    optional_name = 'quotes' if profile == 'messages' else 'outcomes' if profile == 'inputs' else None
    optional = bool(optional_name and optional_name in shape)
    expected = _expected(profile, optional)
    current = _MAGIC | (number << 8) | int(optional)
    if marker not in (0, current):
        _fail()
    migration = False
    if profile == 'history-import' and marker == 0 and shape.get('uploads') == (expected['uploads'][0][:-1], expected['uploads'][1]):
        # The pre-existing explicit target-column migration, no arbitrary subset.
        expected = {**expected, 'uploads': (expected['uploads'][0][:-1], expected['uploads'][1])}
        migration = True
    if shape != expected:
        _fail()
    if profile == 'coordination':
        # This singleton has existed from the first schema. Never reset revision.
        row = db.execute('SELECT revision FROM state WHERE id=1').fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            _fail()
    return optional, migration


def _read(path, profile, timeout):
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=timeout, isolation_level=None)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        _validate(db, profile)
        db.execute('COMMIT')
        return db
    except BaseException:
        db.close()
        raise


def connect(path, profile, *, write=False, enroll=False, create=True, timeout=5, isolation_level=''):
    """Return a validated connection, or None for a genuinely untouched read.

    Optional schema enrollment is explicit, atomic and shared by every writer.
    Existing unversioned absence remains ambiguous legacy; it is not recreated
    as a previously known namespace. Unknown markers/layouts fail closed.
    """
    if profile not in _SCHEMAS or enroll and not _SCHEMAS[profile][2]:
        raise ValueError('Unknown native journal schema')
    path, exists = _paths(path)
    if not write:
        if not exists:
            return None
        db = _read(path, profile, timeout)
        db.isolation_level = isolation_level
        return db
    if not exists and not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .maintenance_gate import checked
    lock = checked(Path(str(path) + '.schema.lock'))
    try:
        info = lock.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail()
    # A short initialization lock, not a persistent owner/canonical authority.
    with FileLock(lock, timeout=timeout, mode=0o600):
        path, exists = _paths(path)
        if not exists and not create:
            return None
        if exists:
            probe = _read(path, profile, timeout)
            probe.close()  # Must precede ANY writable SQLite open or WAL recovery.
        db = sqlite3.connect(path, timeout=timeout, isolation_level=None)
        try:
            os.chmod(path, 0o600)
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            number, base, extras = _SCHEMAS[profile]
            if exists:
                optional, migration = _validate(db, profile)
                if migration:
                    db.execute('ALTER TABLE uploads ADD COLUMN target TEXT')
            else:
                optional = False
                for sql in base:
                    db.execute(sql)
                if profile == 'coordination':
                    db.execute('INSERT INTO state VALUES(1,0)')
            if enroll and not optional:
                for sql in extras:
                    db.execute(sql)
                optional = True
            for sql in _INDEXES.get(profile, ()):
                db.execute(sql)
            db.execute('PRAGMA user_version=' + str(_MAGIC | (number << 8) | int(optional)))
            db.execute('COMMIT')
            db.isolation_level = isolation_level
            return db
        except BaseException:
            db.close()
            raise
