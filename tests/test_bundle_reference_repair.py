import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from amplifier_acp.admin import Administration
from amplifier_acp.native.session_files import project_slug


@pytest.fixture
def repair(tmp_path, monkeypatch):
    home, app, workspace = (tmp_path / name for name in ('home', 'app', 'workspace'))
    for path in (home, app, workspace):
        path.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'leases'))
    config = {'home': str(home), 'appHome': str(app), 'adminWorkspaceRoots': [str(tmp_path)],
              'adminMaintenance': True, 'maintenanceFullNative': True,
              'maintenanceExternalWriters': 'foundation-cooperative', 'workerCommand': ['/must-not-run']}
    owner = Administration(config)

    def seed(sid, reference='Work', **extra):
        directory = home / 'projects' / project_slug(workspace) / 'sessions' / sid
        directory.mkdir(parents=True)
        metadata = {'session_id': sid, 'working_dir': str(workspace), 'bundle_name': reference,
                    'name': 'Retained chat', 'provider': 'original', 'model': 'kept-model',
                    'custom': {'untouched': True}, **extra}
        (directory / 'metadata.json').write_text(json.dumps(metadata))
        (directory / 'transcript.jsonl').write_text('{"role":"user","content":"Do not replay me"}\n')
        (directory / 'events.jsonl').write_text('{"kept":"event"}\n')
        return directory

    async def call(operation, args=None):
        return await owner.perform(str(workspace), 'maintenance.bundleReferences.' + operation, args or {})

    return owner, home, workspace, seed, call


@pytest.mark.asyncio
async def test_review_repair_preservation_and_exact_receipt_without_replay(repair):
    owner, home, workspace, seed, call = repair
    a, b = seed('root-one'), seed('root-two', 'bundle:Work', bundle='bundle:Work')
    seed('child_one', parent_id='root-one')
    seed('internal-one', session_visibility='internal')
    seed('already', 'work')
    seed('custom', './Work')
    seed('independent-fork', parent_id='root-one', _independent_fork=True)
    before = {p: p.read_bytes() for p in home.rglob('*') if p.is_file()}
    preview = await call('preview')
    assert preview['counts'] == {'eligible': 3}
    assert preview['workerStarted'] is False
    assert all(path.read_bytes() == raw for path, raw in before.items())
    args = {'previewHash': preview['previewHash'], 'commandId': 'original-repair'}
    result = await call('apply', args)
    assert result['counts'] == {'changed': 3} and result['complete']
    assert result['workReplayed'] is False
    for directory in (a, b):
        old = json.loads(before[directory / 'metadata.json'])
        expected = {**old, 'bundle_name': 'work'}
        if 'bundle' in old:
            expected['bundle'] = 'work'
        assert json.loads((directory / 'metadata.json').read_text()) == expected
        assert (directory / 'metadata.json.backup').read_bytes() == before[directory / 'metadata.json']
    assert all(path.read_bytes() == raw for path, raw in before.items() if path.name != 'metadata.json')
    # Later manual edits cannot be overwritten by repeating the same command.
    path = a / 'metadata.json'
    changed = {**json.loads(path.read_text()), 'bundle_name': 'user-custom'}
    path.write_text(json.dumps(changed))
    again = await call('apply', args)
    assert again['reportHash'] == result['reportHash'] and again['replayed'] is False
    assert json.loads(path.read_text()) == changed
    receipt = await owner.perform(str(workspace), 'maintenance.receipt', {'commandId': 'original-repair'})
    assert receipt['receipt']['state'] == 'succeeded'
    with pytest.raises(ValueError, match='conflict'):
        await call('apply', {**args, 'previewHash': 'a' * 64})
    await owner.close()


@pytest.mark.asyncio
async def test_changed_metadata_busy_writer_and_partial_report(repair):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    owner, home, workspace, seed, call = repair
    changed, busy = seed('changed'), seed('busy')
    preview = await call('preview')
    value = json.loads((changed / 'metadata.json').read_text())
    (changed / 'metadata.json').write_text(json.dumps({**value, 'name': 'Changed after review'}))
    lease = SharedSessionStore(str(workspace), 'busy').acquire(app='test-active')
    try:
        result = await call('apply', {'previewHash': preview['previewHash'], 'commandId': 'mixed'})
        assert result['counts'] == {'busy': 1, 'changed-since-preview': 1}
        assert not result['complete']
        assert json.loads((busy / 'metadata.json').read_text())['bundle_name'] == 'Work'
    finally:
        lease.release()
    await owner.close()


@pytest.mark.asyncio
async def test_alias_local_missing_and_conflicting_references_are_preserved(repair):
    owner, home, workspace, seed, call = repair
    conflict = seed('conflict', bundle='different')
    missing = seed('missing')
    (missing / 'metadata.json').rename(missing / 'metadata.json.backup')
    seed('target')
    (home / 'settings.yaml').write_text('bundle:\n  added:\n    Work: /custom-alias\n')
    preview = await call('preview')
    assert preview['counts'] == {'reference-conflict': 1, 'missing-primary': 1, 'alias-conflict': 1}
    (home / 'settings.yaml').write_text('bundle:\n  added: {}\n')
    local = workspace / 'Work'
    local.mkdir()
    (local / 'bundle.md').write_text('custom bundle')
    assert (await call('preview'))['counts']['local-bundle'] == 1
    assert json.loads((conflict / 'metadata.json').read_text())['bundle_name'] == 'Work'
    await owner.close()


@pytest.mark.asyncio
async def test_reference_change_page_bounds_authority_and_unknown_receipt(repair):
    owner, home, workspace, seed, call = repair
    seed('one')
    seed('two')
    preview = await call('preview')
    page = await call('page', {'previewHash': preview['previewHash'], 'limit': 1})
    assert len(page['sessions']) == 1 and page['nextCursor'] == 1
    assert 'metadataHash' not in page['sessions'][0]
    (home / 'settings.yaml').write_text('bundle:\n  added:\n    work: /changed-source\n')
    result = await call('apply', {'previewHash': preview['previewHash'], 'commandId': 'source-changed'})
    assert result['counts'] == {'reference-changed': 2}
    with pytest.raises(ValueError):
        await call('page', {'previewHash': '../outside'})
    with pytest.raises(ValueError):
        await call('page', {'previewHash': preview['previewHash'], 'limit': 1000})
    owner.config['maintenanceFullNative'] = False
    with pytest.raises(ValueError, match='Full native'):
        await call('preview')
    owner.config['maintenanceFullNative'] = True
    # A crash left an original receipt: inspection/retry never calls repair.
    from amplifier_acp.configuration_clone import fingerprint
    args = {'previewHash': preview['previewHash'], 'commandId': 'lost'}
    operation = 'maintenance.bundleReferences.apply'
    signature = fingerprint({'workspace': str(workspace), 'operation': operation, 'args': args})
    owner.maintenance.claim(str(workspace), signature, {'commandId': 'lost', 'operation': operation, 'state': 'running'})
    assert (await call('apply', args))['receipt']['state'] == 'unknown'
    await owner.close()


@pytest.mark.asyncio
async def test_cancellation_joins_original_write_and_retains_native_lease(repair, monkeypatch):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.shared_state import SharedSessionStore, SessionBusyError
    owner, home, workspace, seed, call = repair
    seed('joined')
    preview = await call('preview')
    entered, finish = threading.Event(), threading.Event()
    original = SessionHistoryStore._save_metadata_unlocked

    def slow(self, value):
        entered.set()
        assert finish.wait(5)
        return original(self, value)

    monkeypatch.setattr(SessionHistoryStore, '_save_metadata_unlocked', slow)
    task = asyncio.create_task(call('apply', {'previewHash': preview['previewHash'], 'commandId': 'cancelled'}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        with pytest.raises(SessionBusyError):
            SharedSessionStore(str(workspace), 'joined').acquire(app='competing-writer')
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    receipt = await owner.perform(str(workspace), 'maintenance.receipt', {'commandId': 'cancelled'})
    assert receipt['receipt']['state'] == 'succeeded'
    assert receipt['receipt']['result']['counts'] == {'changed': 1}
    await owner.close()


@pytest.mark.asyncio
async def test_linked_metadata_and_unsupported_lease_identity_never_change(repair):
    owner, home, workspace, seed, call = repair
    linked = seed('linked')
    outside = home / 'retained-metadata.json'
    (linked / 'metadata.json').rename(outside)
    (linked / 'metadata.json').symlink_to(outside)
    seed('old_root', _independent_fork=True)
    before = outside.read_bytes()
    preview = await call('preview')
    assert preview['counts'] == {'unreadable': 1, 'unsupported-lease-identity': 1}
    result = await call('apply', {'previewHash': preview['previewHash'], 'commandId': 'protected'})
    assert not result['complete'] and outside.read_bytes() == before
    await owner.close()


def test_public_repair_schema_requires_one_exact_review_or_report():
    from jsonschema import Draft202012Validator, ValidationError
    schema = json.loads((Path(__file__).parents[1] / 'schemas/native-maintenance-request.v1.json').read_text())
    branches = [item for item in schema['oneOf'] if item.get('properties', {}).get('operation', {}).get('const', '').startswith('maintenance.bundleReferences.')]
    validator = Draft202012Validator({'oneOf': branches})
    request = {'cwd': '/owned/workspace', 'operation': 'maintenance.bundleReferences.page', 'args': {'previewHash': 'a' * 64, 'limit': 1}}
    validator.validate(request)
    for args in ({}, {'previewHash': 'a' * 64, 'reportHash': 'b' * 64}, {'previewHash': 'a' * 64, 'cursor': -1}, {'reportHash': 'b' * 64, 'limit': 101}):
        with pytest.raises(ValidationError):
            validator.validate({**request, 'args': args})
