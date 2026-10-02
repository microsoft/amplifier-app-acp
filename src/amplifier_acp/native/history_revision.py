"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import copy


import hashlib


import json


from pathlib import Path


from .host.config import app_home, write_private


from .host.storage import SessionStore


from .session_store import fork_session


from .session_files import validate_id


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
