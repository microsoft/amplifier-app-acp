"""Lazy selected-session message adjuncts; no execution or transcript mutation.

The caller authenticates scope/actor and resolves the native transcript directory.
No caller-supplied path, excerpt or actor is accepted as authority.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import UTC, datetime

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
    if (row.get('metadata') or {}).get('amplifier_voice') and 'sourceDigest' not in reference:
        raise MessageError('Mutable voice message reference requires a verified source digest')
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

    def _db(self, create=False, *, quotes=False):
        from .journal_schema import connect
        return connect(self.path,'messages',write=create,enroll=quotes,isolation_level=None)

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
            events = [] if db is None else [json.loads(row[0]).get('result', {}).get('feedbackEvent') for row in db.execute('SELECT value FROM receipts ORDER BY rowid DESC LIMIT 100')]
            return {'items': [self._projection(db, identity) for identity in ids], 'coverage': 'selected', 'sent': False,
                    'feedbackEvents': [event for event in reversed(events) if event and event['target']['messageId'] in ids]}
        finally:
            if db: db.close()

    def receipt(self, command_id):
        token(command_id)
        db = self._db()
        try:
            if db is None: return {'receipt': None, 'replayed': False}
            row = db.execute('SELECT value FROM receipts WHERE command=? AND json_extract(value,\'$.actorId\')=?', (command_id, self.actor_id)).fetchone()
            if row:
                return {'receipt': json.loads(row[0])['result'], 'replayed': False}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'").fetchone():
                quote = db.execute("SELECT value FROM quotes WHERE input=? AND json_extract(value,'$.actorId')=?", (command_id,self.actor_id)).fetchone()
                if quote:
                    value=json.loads(quote[0]);value.pop('actorId',None)
                    return {'receipt':value,'replayed':False}
            return {'receipt':None,'replayed':False}
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
            if db: refuse_quote_collision(db,command_id)
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
            refuse_quote_collision(db,command_id)
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
                feedback = ({'version': 1, 'id': 'reaction:' + command_id, 'kind': 'reaction',
                    'createdAt': datetime.now(UTC).isoformat(), 'emoji': emoji, 'present': present,
                    'target': {key: row[key] for key in ('sessionId', 'messageId', 'role', 'sourceDigest', 'historyEpoch')},
                    'excerpt': row['text'][:4000]} if changed else None)
                if feedback: result['feedbackEvent'] = feedback
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


def input_quote(path, input_id):
    """Passive exact-input quote used by accepted native context projection."""
    path = checked(path)
    from .journal_schema import connect
    db=connect(path,'messages')
    if db is None:return None
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'").fetchone(): return None
        row = db.execute('SELECT value FROM quotes WHERE input=?', (token(input_id),)).fetchone()
        return json.loads(row[0])['quote'] if row else None
    finally: db.close()


def reserve_quote(store, directory, reference, *, input_id, input_payload, epoch=None):
    """Freeze once before effects. A reservation is never permission to replay."""
    token(input_id)
    fingerprint = digest({'reference':reference,'input':input_payload,'actor':store.actor_id})
    db = store._db()
    try:
        if db and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'").fetchone():
            row = db.execute('SELECT fingerprint,value FROM quotes WHERE input=?', (input_id,)).fetchone()
            if row:
                if row[0] != fingerprint: raise MessageError('Quoted command identity already has different payload or principal')
                raise QuotedInputUnknown(input_id, accepted=json.loads(row[1]).get('status')=='accepted')
    finally:
        if db:db.close()
    guard_input_identity(store.path,input_id,reference=reference,input_payload=input_payload,actor_id=store.actor_id)
    quote = freeze_quote(directory,store.session_id,reference,input_id=input_id,epoch=epoch)
    db=store._db(create=True,quotes=True)
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM receipts WHERE command=?',(input_id,)).fetchone():
            raise MessageError('Command identity is already reserved for a reaction')
        prior=db.execute('SELECT fingerprint,value FROM quotes WHERE input=?',(input_id,)).fetchone()
        if prior:
            if prior[0] != fingerprint: raise MessageError('Quoted command identity already has different payload or principal')
            raise QuotedInputUnknown(input_id,accepted=json.loads(prior[1]).get('status')=='accepted')
        quote=freeze_quote(directory,store.session_id,reference,input_id=input_id,epoch=epoch)
        value=encoded({'actorId':store.actor_id,'quote':quote,'status':'reserved','outcome':'unknown','replayed':False})
        if len(value.encode())>MAX_RECEIPT_BYTES:raise MessageError('Frozen quoted input exceeds its receipt bound')
        db.execute('INSERT INTO quotes VALUES(?,?,?)',(input_id,fingerprint,value))
        db.execute('COMMIT');return quote
    except BaseException:
        db.execute('ROLLBACK');raise
    finally:db.close()


class QuotedInputUnknown(MessageError):
    def __init__(self,input_id,accepted=False):
        super().__init__('Original quoted input was already admitted; no replay' if accepted else 'Original quoted input was already reserved; inspect its delivery without replay')
        self.data={'outcome':'already-admitted' if accepted else 'unknown','commandId':input_id,'replayed':False}


def quote_admitted(store, input_id):
    """Only an actual worker admission acknowledgement may mark delivery known."""
    db=store._db()
    if db is None:raise MessageError('Frozen quoted input receipt is unavailable')
    db.close()
    db=store._db(create=True)
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM quotes WHERE input=?',(token(input_id),)).fetchone()
        if not row:raise MessageError('Frozen quoted input receipt is unavailable')
        value=json.loads(row[0])
        if value['actorId']!=store.actor_id:raise MessageError('Quoted input principal changed')
        value.update(status='accepted',delivery='accepted',outcome='unobserved')
        db.execute('UPDATE quotes SET value=? WHERE input=?',(encoded(value),input_id))
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK');raise
    finally:db.close()


def refuse_quote_collision(db, command_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'").fetchone() and db.execute('SELECT 1 FROM quotes WHERE input=?',(command_id,)).fetchone():
        raise MessageError('Command identity is already reserved for quoted input')


def guard_input_identity(path, input_id, *, reference=None, input_payload=None, actor_id=None):
    """Check every subsequent input, including one omitting reply metadata.

    Exact committed or unknown quoted input cannot be admitted again, nor can a
    reaction command become an input. This passive guard creates no storage.
    """
    path=checked(path);token(input_id)
    from .journal_schema import connect
    db=connect(path,'messages')
    if db is None:return
    try:
        if db.execute('SELECT 1 FROM receipts WHERE command=?',(input_id,)).fetchone():
            raise MessageError('Command identity is already reserved for a reaction')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'").fetchone():return
        row=db.execute('SELECT fingerprint,value FROM quotes WHERE input=?',(input_id,)).fetchone()
        if not row:return
        expected=digest({'reference':reference,'input':input_payload,'actor':actor_id})
        if row[0]!=expected:raise MessageError('Quoted input identity already has a different payload, reference or principal')
        raise QuotedInputUnknown(input_id,accepted=json.loads(row[1]).get('status')=='accepted')
    finally:db.close()
