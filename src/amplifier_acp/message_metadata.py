"""Lazy selected-session message adjuncts; no execution or transcript mutation.

The caller authenticates scope/actor and resolves the native transcript directory.
No caller-supplied path, excerpt or actor is accepted as authority.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3

from .history import content_updates, message_id, source, stamp
from .maintenance_gate import checked
from .native.session_files import validate_id

REACTIONS = ('👍', '❤️', '😂', '🎉', '😮', '😢', '👎', '✅')
MAX_RECORD_BYTES = 262144
MAX_SELECTED = 50
MAX_RECEIPT_BYTES = 32768


class MessageError(ValueError):
    data = {'executed': False, 'replayed': False}


def token(value, maximum=200):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise MessageError('A bounded message identity is required')
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def resolve_reference(directory, session_id, reference, *, epoch=None):
    """Read one exact current-history row at its public byte locator, cold.

    Epoch is the owner-resolved context head, not caller authority. Appends are
    allowed; a changed row, stale epoch or wrong session fails before mutation.
    """
    token(session_id)
    allowed = {'sessionId', 'messageId', 'position', 'historyEpoch', 'sourceDigest'}
    if not isinstance(reference, dict) or set(reference) - allowed or not {'sessionId', 'messageId', 'position', 'historyEpoch'} <= set(reference):
        raise MessageError('Exact native message reference required')
    if reference['sessionId'] != session_id or reference['historyEpoch'] != epoch:
        raise MessageError('Message reference belongs to another session or history epoch')
    identity = token(reference['messageId'])
    position = reference['position']
    if identity.startswith('archive:') or type(position) is not int or position < 0:
        raise MessageError('Historical-only or invalid message reference')
    directory = checked(directory)
    path, diagnostics = source(directory)
    if diagnostics:
        raise MessageError('Current primary message history is unavailable')
    checked(path)
    before, size = stamp(path)
    if position >= size:
        raise MessageError('Message locator is outside current history')
    with path.open('rb') as stream:
        if position:
            stream.seek(position - 1)
            if stream.read(1) != b'\n':
                raise MessageError('Message locator is not a record boundary')
        stream.seek(position)
        raw = stream.readline(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES or not raw.endswith(b'\n'):
        raise MessageError('Selected message is incomplete or exceeds the read bound')
    if stamp(path)[0] != before:
        raise MessageError('Message history changed during the bounded read')
    try:
        row = json.loads(raw)
        updates, _ = content_updates(row, position)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MessageError('Selected native message is malformed') from exc
    if row.get('role') not in {'user', 'assistant'} or message_id(row, position, epoch) != identity:
        raise MessageError('Message identity changed or is not quotable')
    text = ''.join(update['content']['text'] for update in updates
                   if update.get('content', {}).get('type') == 'text')
    if not text.strip():
        raise MessageError('Selected message has no public text')
    source_digest = hashlib.sha256(text.encode()).hexdigest()
    if 'sourceDigest' in reference and reference['sourceDigest'] != source_digest:
        raise MessageError('Selected message digest changed')
    return {'sessionId': session_id, 'messageId': identity, 'role': row['role'],
            'text': text, 'sourceDigest': source_digest, 'historyEpoch': epoch}


def freeze_quote(directory, session_id, reference, *, input_id, epoch=None):
    """Only explicit Send calls this. The caller retains this with exact input."""
    token(input_id)
    row = resolve_reference(directory, session_id, reference, epoch=epoch)
    text = row.pop('text')
    quote = {**row, 'inputId': input_id, 'excerpt': text[:4000], 'truncated': len(text) > 4000}
    quote['id'] = digest(quote)
    return quote


class MessageMetadata:
    """One session sidecar, never a global registry or execution store.

    actor_id is a trusted constructor argument supplied by authenticated native
    composition. Protocol args cannot supply/override it. Connections are short
    lived; untouched passive reads do not create directories or databases.
    """
    def __init__(self, app_home, session_id, *, actor_id):
        self.session_id = validate_id(session_id)
        self.actor_id = token(actor_id)
        self.path = checked(Path(app_home) / 'sessions' / self.session_id / 'message-interactions.sqlite3')

    def _db(self, create=False):
        checked(self.path)
        if not self.path.exists() and not create:
            return None
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self.path.chmod(0o600)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''CREATE TABLE IF NOT EXISTS annotations(
              message TEXT NOT NULL, actor TEXT NOT NULL, emoji TEXT NOT NULL,
              PRIMARY KEY(message,actor,emoji));
              CREATE TABLE IF NOT EXISTS revisions(message TEXT PRIMARY KEY,value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS receipts(command TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,value TEXT NOT NULL);''')
        else:
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, isolation_level=None)
        return db

    def _projection(self, db, identity):
        counts = {} if db is None else dict(db.execute('SELECT emoji,count(*) FROM annotations WHERE message=? GROUP BY emoji', (identity,)))
        mine = set() if db is None else {row[0] for row in db.execute('SELECT emoji FROM annotations WHERE message=? AND actor=?', (identity, self.actor_id))}
        rev = None if db is None else db.execute('SELECT value FROM revisions WHERE message=?', (identity,)).fetchone()
        return {'sessionId': self.session_id, 'messageId': identity, 'revision': rev[0] if rev else 0,
                'reactions': [emoji for emoji in REACTIONS if counts.get(emoji)],
                'counts': {emoji: counts[emoji] for emoji in REACTIONS if counts.get(emoji)},
                'mine': [emoji for emoji in REACTIONS if emoji in mine]}

    def read(self, message_ids):
        ids = self._selected(message_ids)
        db = self._db()
        try:
            if db: db.execute('BEGIN')
            return {'items': [self._projection(db, identity) for identity in ids], 'coverage': 'selected', 'sent': False}
        finally:
            if db: db.close()

    def receipt(self, command_id):
        token(command_id)
        db = self._db()
        try:
            if db is None: return {'receipt': None, 'replayed': False}
            row = db.execute('SELECT value FROM receipts WHERE command=? AND json_extract(value,\'$.actorId\')=?', (command_id, self.actor_id)).fetchone()
            return {'receipt': json.loads(row[0])['result'] if row else None, 'replayed': False}
        finally:
            if db: db.close()

    def reaction(self, directory, reference, *, emoji, present, command_id, epoch=None):
        token(command_id)
        if emoji not in REACTIONS or type(present) is not bool:
            raise MessageError('Supported emoji and desired presence required')
        fingerprint = digest({'sessionId': self.session_id, 'actorId': self.actor_id,
                              'reference': reference, 'emoji': emoji, 'present': present})
        # Inspect exact committed retry before any history read. Old receipts
        # remain inspectable even after the original history becomes unavailable.
        db = self._db()
        try:
            prior = None if db is None else db.execute('SELECT fingerprint,value FROM receipts WHERE command=?', (command_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint: raise MessageError('Command identity already has different message arguments or actor')
                return json.loads(prior[1])['result']
        finally:
            if db: db.close()
        row = resolve_reference(directory, self.session_id, reference, epoch=epoch)
        db = self._db(create=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT fingerprint,value FROM receipts WHERE command=?', (command_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint: raise MessageError('Command identity already has different message arguments or actor')
                result = json.loads(prior[1])['result']
            else:
                # Recheck selected native bytes after obtaining the metadata
                # transaction. The integration owner must also hold its native
                # history/admin gate across this call and explicit send admission.
                row = resolve_reference(directory, self.session_id, reference, epoch=epoch)
                identity = row['messageId']
                if present:
                    actors = db.execute('SELECT count(DISTINCT actor) FROM annotations WHERE message=?', (identity,)).fetchone()[0]
                    existing = db.execute('SELECT 1 FROM annotations WHERE message=? AND actor=? LIMIT 1', (identity, self.actor_id)).fetchone()
                    if actors >= 100 and not existing:
                        raise MessageError('Selected message has 100 reaction actors; original metadata preserved')
                    changed = db.execute('INSERT OR IGNORE INTO annotations VALUES(?,?,?)', (identity, self.actor_id, emoji)).rowcount > 0
                else:
                    changed = db.execute('DELETE FROM annotations WHERE message=? AND actor=? AND emoji=?', (identity, self.actor_id, emoji)).rowcount > 0
                if changed:
                    db.execute('INSERT INTO revisions VALUES(?,1) ON CONFLICT(message) DO UPDATE SET value=value+1', (identity,))
                result = {**self._projection(db, identity), 'changed': changed, 'sent': False, 'commandId': command_id}
                receipt = encoded({'actorId': self.actor_id, 'result': result})
                if len(receipt.encode()) > MAX_RECEIPT_BYTES: raise MessageError('Message receipt exceeds its bound')
                db.execute('INSERT INTO receipts VALUES(?,?,?)', (command_id, fingerprint, receipt))
            db.execute('COMMIT')
            return result
        except BaseException:
            db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    @staticmethod
    def _selected(message_ids):
        if not isinstance(message_ids, list) or not 1 <= len(message_ids) <= MAX_SELECTED:
            raise MessageError('Select at most 50 message identities')
        ids = [token(identity) for identity in message_ids]
        if len(set(ids)) != len(ids): raise MessageError('Distinct selected message identities required')
        return ids

    def export_metadata(self, message_ids):
        """Trusted bounded copy/export hook. No drafts, receipts or authority.

        The copy owner remaps source IDs to its independently verified retained
        native message IDs; never reuse source command receipts in a new session.
        """
        ids = self._selected(message_ids)
        db = self._db()
        try:
            if db: db.execute('BEGIN')
            items = []
            for identity in ids:
                reactions = [] if db is None else [{'actorId': actor, 'emoji': emoji} for actor, emoji in db.execute('SELECT actor,emoji FROM annotations WHERE message=? ORDER BY actor,emoji LIMIT 101', (identity,))]
                if len(reactions) > 100: raise MessageError('Selected metadata export exceeds per-message actor bound; preserve original adjunct')
                items.append({'messageId': identity, 'reactions': reactions})
            return {'version': 1, 'sessionId': self.session_id, 'items': items,
                    'coverage': 'selected', 'executionAuthority': 'none'}
        finally:
            if db: db.close()


def handle_message_metadata(store, method, args, *, directory=None, epoch=None):
    """Minimal native handler proposal; caller negotiates/authenticates first.

    store.actor_id/session_id are supplied by trusted composition. No actor or
    history path may arrive through args. This function starts no worker.
    """
    fields = {
        '_amplifier/message/reaction': {'sessionId', 'reference', 'emoji', 'present', 'commandId'},
        '_amplifier/message/read': {'sessionId', 'messageIds'},
        '_amplifier/message/receipt': {'sessionId', 'commandId'},
    }
    required = fields.get(method)
    if required is None or not isinstance(args, dict) or set(args) != required or args['sessionId'] != store.session_id:
        raise MessageError('Exact negotiated message arguments and owning session required')
    if method.endswith('/reaction'):
        if directory is None: raise MessageError('Trusted native history directory required')
        return store.reaction(directory, args['reference'], emoji=args['emoji'],
                              present=args['present'], command_id=args['commandId'], epoch=epoch)
    if method.endswith('/read'): return store.read(args['messageIds'])
    return store.receipt(args['commandId'])
