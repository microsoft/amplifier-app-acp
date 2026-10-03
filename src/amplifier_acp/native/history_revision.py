"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import copy


import hashlib


import json
import os
import time


from pathlib import Path


from .host.config import app_home, write_private


from .host.storage import SessionStore


from .session_store import fork_session


from .session_files import validate_id


# Context clearing is a history revision, never a second resume store. Only the
# selected session is indexed: immutable segments form a backwards linked list.
CONTEXT_CLEAR = {'version': 1, 'review': 'context.clear.review', 'apply': 'context.clear',
                 'receipt': '_amplifier/context/receipt', 'preservesHistory': True,
                 'clearsExplicitGoal': True, 'activeTask': 'pause-required', 'replayUnknown': False}
MAX_DESCRIPTOR = 16384


class ContextClearError(ValueError):
    def __init__(self, reason, *, refused=False):
        super().__init__(reason)
        self.data = {'reason': reason, 'replayed': False,
                     **({'executed': False} if refused else {'outcome': 'unknown'})}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _directory(home, identity):
    from amplifier_acp.maintenance_gate import checked
    return checked(Path(home) / 'sessions' / validate_id(identity) / 'history-revisions')


def _read(path):
    from amplifier_acp.session_lifecycle import regular
    return json.loads(regular(path, MAX_DESCRIPTOR))


def _write(path, value):
    # The directory entry must survive a crash as well as the file contents.
    from amplifier_acp.maintenance_gate import checked
    checked(path)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(body.encode()) > MAX_DESCRIPTOR:
        raise ValueError('Context revision descriptor exceeds its bound')
    write_private(path, body)
    _sync(path.parent)
    _sync(path.parent.parent)


def _sync(directory):
    if os.name == 'nt':
        return  # Windows flushes the opened file; directory fsync is unavailable.
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _command(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
        raise ContextClearError('context-clear-command-required', refused=True)
    return hashlib.sha256(('context-clear:' + value).encode()).hexdigest()


def context_head(home, identity, history_cwd=None):
    path = _directory(home, identity) / 'context-head.json'
    if not path.exists(): return None
    value = _read(path)
    if history_cwd is not None and value.get('scope') != {'sessionId': identity, 'historyCwd': str(Path(history_cwd).resolve())}:
        raise ValueError('Context history belongs to another immutable workspace')
    return value.get('archiveId')


def segment(home, identity, archive):
    if not isinstance(archive, str) or len(archive) != 64 or any(c not in '0123456789abcdef' for c in archive):
        raise ValueError('Invalid context archive identity')
    directory = _directory(home, identity) / ('context-' + archive)
    value = _read(directory / 'segment.json')
    from amplifier_acp.history import stamp
    if value.get('archiveId') != archive or stamp(directory / 'transcript.jsonl')[1] != value.get('bytes'):
        raise ValueError('Preserved context segment changed; originals retained')
    index = directory / 'transcript.jsonl.sha256'
    if value.get('chunkBytes') != 65536 or stamp(index)[1] != 32 * ((value['bytes'] + 65535) // 65536):
        raise ValueError('Preserved context integrity index changed; originals retained')
    return directory, value


def context_receipt(home, identity, cwd, command):
    path = _directory(home, identity) / ('context-' + _command(command) + '.json')
    if not path.exists():
        return None
    row = _read(path)
    if row.get('scope') != {'sessionId': identity, 'historyCwd': str(Path(cwd).resolve())}:
        raise ContextClearError('context-clear-receipt-scope-mismatch', refused=True)
    return {key: value for key, value in row.items()
            if key in {'version', 'commandId', 'operation', 'status', 'result', 'reason', 'executed', 'createdAt', 'completedAt'}}


def guard_context_clear(home, identity):
    pending = _directory(home, identity) / 'pending-context-clear.json'
    if not pending.exists():
        return
    value = _read(pending)
    receipt = _read(pending.parent / ('context-' + value['archiveId'] + '.json'))
    # A crash after the terminal receipt but before pointer removal is proven
    # settled. Do not reapply effects or rewrite the receipt during a read.
    if receipt.get('status') == 'succeeded' and context_head(home, identity) == value['archiveId']:
        return
    raise ContextClearError('context-clear-unresolved-inspect-original-receipt')


def _scope(controls):
    getter = controls.coordinator.get_capability('web.history_workspace')
    cwd = getter() if callable(getter) else getter
    cwd = cwd or controls.coordinator.config.get('working_dir')
    return {'sessionId': controls.session.session_id, 'historyCwd': str(Path(cwd).resolve())}


def _control(controls):
    task = controls.tasks.record()
    return {'goal': controls.coordinator.session_state.get('goal'),
            'task': {key: task.get(key) for key in ('id', 'revision', 'status')} if task else None}


def review_clear(controls, args):
    if args:
        raise ContextClearError('context-clear-review-arguments', refused=True)
    scope = _scope(controls)
    guard_context_clear(app_home(), scope['sessionId'])
    store = SessionStore.for_app(app_home(), scope['historyCwd'])
    from amplifier_acp.history import inspect
    value = inspect(store.directory(scope['sessionId']))
    if value['revision'] is None:
        raise ContextClearError('context-clear-history-unavailable', refused=True)
    state = _control(controls)
    reason = None
    try:
        controls.require_idle()
        if state['task'] and state['task']['status'] == 'active':
            raise ValueError('Pause the active saved task before clearing context; its goal would otherwise return on resume.')
        if not value['available'] or value['diagnostics']:
            raise ValueError('Repair native history before clearing context.')
    except ValueError as exc:
        reason = str(exc)[:512]
    return {'historyRevision': value['revision'], 'controlRevision': _digest(state),
            'canClear': reason is None, 'goalPresent': state['goal'] is not None,
            'goalAction': 'clear', 'task': state['task'], **({'reason': reason} if reason else {})}


def _copy_segment(source, destination, *, create=True):
    """Stream exact canonical bytes into a private immutable before-image."""
    from amplifier_acp.history import stamp
    from amplifier_acp.maintenance_gate import checked
    checked(source); checked(destination)
    before, size = stamp(source)
    if create: destination.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    digest = hashlib.sha256()
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        with os.fdopen(fd, 'rb', closefd=False) as incoming, destination.open('xb') as out, destination.with_name(destination.name + '.sha256').open('xb') as index:
            os.chmod(destination, 0o600); os.chmod(index.name, 0o600)
            remaining = size
            while remaining:
                chunk = incoming.read(min(65536, remaining))
                if not chunk: raise ValueError('Canonical history changed during preservation')
                out.write(chunk); digest.update(chunk); index.write(hashlib.sha256(chunk).digest()); remaining -= len(chunk)
            if incoming.read(1): raise ValueError('Canonical history grew during preservation')
            out.flush(); os.fsync(out.fileno()); index.flush(); os.fsync(index.fileno())
    finally:
        os.close(fd)
    _sync(destination.parent); _sync(destination.parent.parent)
    if stamp(source)[0] != before or destination.stat().st_size != size:
        raise ValueError('Canonical history changed while preserving context')
    return {'sha256': digest.hexdigest(), 'bytes': size, 'chunkBytes': 65536}


async def clear_context(controls, args):
    required = {'commandId', 'expectedHistoryRevision', 'expectedControlRevision'}
    if set(args) != required:
        raise ContextClearError('context-clear-reviewed-revisions-required', refused=True)
    archive = _command(args['commandId'])
    if any(not isinstance(args[k], str) or len(args[k]) != 64 or any(c not in '0123456789abcdef' for c in args[k]) for k in required - {'commandId'}):
        raise ContextClearError('context-clear-invalid-revision', refused=True)
    scope = _scope(controls); root = _directory(app_home(), scope['sessionId'])
    path = root / ('context-' + archive + '.json')
    binding = _digest({'operation': 'context.clear', 'scope': scope, 'args': args})
    if path.exists():
        old = _read(path)
        if old.get('binding') != binding:
            raise ContextClearError('context-clear-command-conflict', refused=True)
        if old['status'] == 'succeeded': return old['result']
        raise ContextClearError(old.get('reason', 'context-clear-outcome-unknown'), refused=old['status'] == 'refused')
    guard_context_clear(app_home(), scope['sessionId'])
    row = {'version': 1, 'commandId': args['commandId'], 'operation': 'context.clear',
           'scope': scope, 'binding': binding, 'createdAt': time.time(), 'status': 'unknown'}
    # Validate before reserving the effect. Refused commands also retain exact
    # receipts; an old ID can never become a fresh clear after later work.
    review = review_clear(controls, {})
    reason = ('context-clear-preflight-refused' if not review['canClear'] else
              'context-clear-stale-review' if (review['historyRevision'], review['controlRevision']) !=
              (args['expectedHistoryRevision'], args['expectedControlRevision']) else None)
    if reason:
        _write(path, {**row, 'status': 'refused', 'executed': False, 'reason': reason})
        raise ContextClearError(reason, refused=True)
    if controls.coordinator.get_capability('live.checkpoint') is None:
        _write(path, {**row, 'status': 'refused', 'executed': False, 'reason': 'native-checkpoint-required'})
        raise ContextClearError('native-checkpoint-required', refused=True)
    _write(path, row)
    pending = root / 'pending-context-clear.json'
    _write(pending, {'archiveId': archive, 'commandId': args['commandId']})
    try:
        store = SessionStore.for_app(app_home(), scope['historyCwd'])
        source = store.directory(scope['sessionId']) / 'transcript.jsonl'
        previous = context_head(app_home(), scope['sessionId'])
        target = root / ('context-' + archive)
        captured = _copy_segment(source, target / 'transcript.jsonl')
        metadata = _copy_segment(source.parent / 'metadata.json', target / 'metadata.json', create=False)
        from amplifier_acp.history import stamp
        if stamp(source)[0] != args['expectedHistoryRevision']:
            raise ValueError('History changed after context review')
        _write(target / 'segment.json', {'version': 1, 'archiveId': archive, **scope, **captured,
                                         'sourceEpoch': previous, 'previousArchive': previous, 'metadata': metadata})
        # The durable exact before-image now exists. Context module effects and
        # its canonical checkpoint start only after this boundary.
        controls.coordinator.register_capability('native.context-clear-writing', True)
        await controls.coordinator.get('context').clear()
        controls.coordinator.session_state['goal'] = None
        await controls.checkpoint()
        if await controls.coordinator.get('context').get_messages():
            raise ValueError('Context module did not clear its active messages')
        result = {'cleared': True, 'historyPreserved': True, 'eventsPreserved': True,
                  'goalCleared': review['goalPresent'], 'archiveId': archive,
                  'previousHistoryRevision': args['expectedHistoryRevision'], 'historyRevision': stamp(source)[0]}
        _write(root / 'context-head.json', {'archiveId': archive, 'scope': scope})
        _write(path, {**row, 'status': 'succeeded', 'completedAt': time.time(), 'result': result})
        pending.unlink(); _sync(root)
        return result
    except BaseException:
        # No rollback/replay by inference. The exact before-image remains, and
        # this session alone is fenced until a proven owner outcome is available.
        raise ContextClearError('context-clear-outcome-unknown') from None
    finally:
        controls.coordinator.register_capability('native.context-clear-writing', False)


def passive_context_receipt(server, params):
    if set(params) != {'sessionId', 'cwd', 'commandId'}:
        raise ContextClearError('context-clear-receipt-selectors', refused=True)
    from amplifier_acp.relocation import home
    identity = validate_id(params['sessionId']); cwd = params['cwd']
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ContextClearError('context-clear-history-workspace-required', refused=True)
    directory = server.directory(str(Path(cwd).resolve()), identity)
    if not directory.is_dir():
        raise ContextClearError('context-clear-history-unavailable', refused=True)
    return {'receipt': context_receipt(home(server), identity, cwd, params['commandId'])}


def receipt_path(home, identity, operation_id):
    validate_id(identity)
    # Command IDs are protocol identities, not filesystem path components.
    filename = hashlib.sha256(operation_id.encode()).hexdigest() + '.json'
    return Path(home) / 'sessions' / identity / 'history-revisions' / filename


def same(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def recover_pending(home, workspace, identity):
    """Classify an interrupted atomic transcript save while holding writer ownership.

    Never overwrite a transcript that another owner may have changed. A crash
    before the save leaves the old context; a crash after it leaves the revision.
    Neither case starts another input or replays an external effect.
    """
    directory = receipt_path(home, identity, 'unused').parent
    pointer = directory.parent / 'pending-history-edit.json'
    if not pointer.exists():
        return
    operation_id = json.loads(pointer.read_text())['operationId']
    store = SessionStore.for_app(home, workspace)
    for path in [receipt_path(home, identity, operation_id)]:
        receipt = json.loads(path.read_text())
        if receipt.get('phase') != 'prepared':
            pointer.unlink(missing_ok=True)
            continue
        saved = store.load(identity)
        current = saved[0] if saved else []
        phase = 'committed' if same(current, receipt['nativeAfter']) else 'aborted' if same(current, receipt['nativeBefore']) else 'superseded'
        if phase == 'aborted':
            control = directory.parent / 'control-state.json'
            previous = receipt.get('controlBefore')
            if previous is not None and control.exists() and same(json.loads(control.read_text()), {**previous, 'goal': None}):
                write_private(control, json.dumps(previous))
        write_private(path, json.dumps({**receipt, 'phase': phase}, ensure_ascii=False))
        pointer.unlink(missing_ok=True)


async def rewind(controls, args):
    controls.require_idle()
    identity = controls.session.session_id
    path = receipt_path(app_home(), identity, args['operationId'])
    if path.exists():
        # Replaying a completed context mutation could discard new work. The
        # service owns command deduplication; worker calls are single admission.
        raise ValueError('This edit was already attempted. Reload its result before editing again.')
    pointer = path.parent.parent / 'pending-history-edit.json'
    if pointer.exists():
        raise ValueError('An interrupted edit needs the session to be reloaded before accepting more work.')
    source = copy.deepcopy(args['source'])
    source['status'] = 'idle'
    store = SessionStore.for_app(app_home(), source['workspace'])
    prepared = fork_session(app_home(), source, identity,
                            before_message_id=args['messageId'], prepare_only=True)
    context = controls.coordinator.get('context')
    before = copy.deepcopy(await context.get_messages())
    goal_before = copy.deepcopy(controls.coordinator.session_state.get('goal'))
    await controls.checkpoint()
    saved = store.load(identity)
    after = prepared['context']
    if not any(row.get('role') in {'system', 'developer'} for row in after):
        after = [row for row in before if row.get('role') in {'system', 'developer'}] + after
    keep_system = bool(saved and saved[1].get('preserve_system'))
    native_after = [row for row in after if keep_system or row.get('role') not in {'system', 'developer'}]
    result = {'messages': prepared['messages'], 'throughTurn': prepared['throughTurn'],
              'operationId': args['operationId'], 'eventsPreserved': True,
              'sharedHistoryOffset': source.get('sharedHistoryOffset', 0),
              'sharedHistoryUserTurnOffset': source.get('sharedHistoryUserTurnOffset', 0),
              'sharedHistoryTotal': source.get('sharedHistoryOffset', 0) + len(prepared['messages']) + 1}
    receipt = {'phase': 'prepared', 'messageId': args['messageId'], 'operationId': args['operationId'],
               'contextBefore': before, 'goalBefore': goal_before,
               'controlBefore': json.loads(controls.state_path().read_text()) if controls.state_path().exists() else None,
               'visibleBefore': source['messages'], 'executionBefore': source.get('execution'), 'nativeBefore': saved[0] if saved else [],
               'nativeAfter': native_after, 'result': result}
    write_private(path, json.dumps(receipt, ensure_ascii=False))
    write_private(pointer, json.dumps({'operationId': args['operationId']}))
    try:
        await context.set_messages(after)
        controls.coordinator.session_state['goal'] = None
        await controls.checkpoint()
    except BaseException:
        # Still under ownership; rollback failure leaves the prepared evidence
        # for classification on next mount, never an automatic input replay.
        await context.set_messages(before)
        controls.coordinator.session_state['goal'] = goal_before
        await controls.checkpoint()
        write_private(path, json.dumps({**receipt, 'phase': 'aborted'}, ensure_ascii=False))
        pointer.unlink(missing_ok=True)
        raise
    stamp = store.history(identity).transcript_path.stat()
    result['nativeRevision'] = [stamp.st_mtime_ns, stamp.st_size]
    write_private(path, json.dumps({**receipt, 'phase': 'committed'}, ensure_ascii=False))
    pointer.unlink(missing_ok=True)
    return result
