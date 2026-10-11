import json
from pathlib import Path
import pytest
from amplifier_acp.history_cleanup import HistoryCleanup, Refused
from amplifier_acp.native.session_files import project_slug
from amplifier_acp.session_lifecycle import guard_start
from amplifier_acp.workspace_cleanup import digest
from test_workspace_cleanup import fixture as workspace_fixture


def fixture(tmp_path, monkeypatch):
    owner, _, cwd = workspace_fixture(tmp_path)
    monkeypatch.setenv('AMPLIFIER_HOME', str(owner.home))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'checkpoints'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'captures'))
    cleanup = HistoryCleanup(owner)
    project = project_slug(str(cwd))
    directory = owner.home / 'projects' / project / 'sessions'
    for sid, parent in [('root-one', None), ('child-one', 'root-one'), ('other-one', None)]:
        root = directory / sid
        root.mkdir(parents=True)
        (root / 'metadata.json').write_text(json.dumps({'session_id': sid, 'working_dir': str(cwd), 'parent_id': parent, 'title': sid}))
        (root / 'transcript.jsonl').write_text('{"role":"user","content":"preserve unless selected"}\n')
    outside = cwd / 'outside.txt'
    outside.write_text('Never remove the link target')
    (directory / 'root-one' / 'link').symlink_to(outside)
    (directory / '.lifecycle-root-one.lock').write_text('stable lifecycle lock')
    for sid in ['root-one', 'child-one']:
        app = owner.app / 'sessions' / sid
        (app / 'history-revisions').mkdir(parents=True)
        (app / 'history-revisions' / 'retained.jsonl').write_text('previous context')
        (app / 'original-receipt.json').write_text('completed control receipt')
        capture = tmp_path / 'captures' / project / 'sessions' / sid
        capture.mkdir(parents=True)
        (capture / 'record.json').write_text('capture data')
    selection = {'project': project, 'nativeSessionId': 'root-one'}
    return owner, cleanup, cwd, directory, selection


def review(cleanup, selection):
    census = cleanup.census('scope', {'selection': selection})
    owners = {'version': 1, 'coverage': 'all-configured-owners-held', 'revision': digest(['product references']), 'nativeRevision': census['revision']}
    preview = cleanup.preview('scope', {'selection': selection, 'ownerCensus': owners, 'reviewed': True})
    args = {'commandId': 'remove-once', 'previewHash': preview['previewHash'], 'ownerCensus': owners, 'confirmationId': preview['confirmationId']}
    return census, preview, args


@pytest.mark.asyncio
async def test_family_removal_preserves_workspace_stable_locks_receipts_and_original_result(tmp_path, monkeypatch):
    owner, cleanup, cwd, directory, selection = fixture(tmp_path, monkeypatch)
    from amplifier_foundation.session.shared_state import SharedSessionStore
    store = SharedSessionStore(str(cwd), 'root-one')
    held = store.acquire(app='fixture')
    try:
        held.write(messages=[{'role': 'user', 'content': 'saved checkpoint'}], bundle='fixture')
    finally:
        held.release()
    lock = store._lock_path.stat()
    census, preview, args = review(cleanup, selection)
    assert [row['nativeSessionId'] for row in census['sessions']] == ['child-one', 'root-one']
    assert preview['permanent'] and preview['workspacePreserved'] and preview['sessionCount'] == 2
    removed = await owner.perform('scope', 'maintenance.history.remove', args)
    assert removed['receipt']['state'] == 'succeeded' and removed['removedBytes'] == preview['inventory']['bytes']
    assert not (directory / 'root-one').exists() and not (directory / 'child-one').exists()
    marker = json.loads((owner.app / 'sessions/root-one/lifecycle.json').read_text())
    assert marker['deleted'] is True and marker['historyCwd'] == str(cwd)
    assert (directory / 'other-one' / 'transcript.jsonl').is_file()
    assert (directory / '.lifecycle-root-one.lock').read_text() == 'stable lifecycle lock'
    assert (cwd / 'notes.txt').read_text() == 'original user files'
    assert (cwd / 'outside.txt').read_text() == 'Never remove the link target'
    assert not store.checkpoint_path.exists() and store._lock_path.stat().st_ino == lock.st_ino
    assert (owner.app / 'sessions/root-one/original-receipt.json').read_text() == 'completed control receipt'
    assert not (owner.app / 'sessions/root-one/history-revisions').exists()
    assert not (tmp_path / 'captures' / selection['project'] / 'sessions/root-one').exists()
    with pytest.raises(ValueError, match='permanently removed'):
        guard_start(owner, 'root-one', str(cwd))
    # Recreated files cannot be deleted by querying/repeating the original ID.
    (directory / 'root-one').mkdir()
    (directory / 'root-one' / 'new.txt').write_text('new bytes')
    repeated = await owner.perform('scope', 'maintenance.history.remove', args)
    assert repeated['receipt'] == removed['receipt'] and repeated['replayed'] is False
    assert (directory / 'root-one' / 'new.txt').read_text() == 'new bytes'


@pytest.mark.parametrize('change', ['transcript', 'new-child', 'owners', 'confirmation', 'expired', 'writer', 'native-gate'])
@pytest.mark.asyncio
async def test_changed_review_or_writer_refuses_without_removing_anything(tmp_path, monkeypatch, change):
    owner, cleanup, cwd, directory, selection = fixture(tmp_path, monkeypatch)
    _, preview, args = review(cleanup, selection)
    from contextlib import ExitStack
    from amplifier_foundation.session.shared_state import SharedSessionStore
    from amplifier_acp.maintenance_gate import Gate
    with ExitStack() as stack:
        if change == 'transcript': (directory / 'root-one/transcript.jsonl').write_text('new user input')
        elif change == 'new-child':
            child = directory / 'new-child'
            child.mkdir()
            (child / 'metadata.json').write_text(json.dumps({'working_dir': str(cwd), 'parent_id': 'root-one'}))
        elif change == 'owners': args['ownerCensus']['revision'] = 'b' * 64
        elif change == 'confirmation': args['confirmationId'] = 'other'
        elif change == 'expired': monkeypatch.setattr('amplifier_acp.history_cleanup.time.time', lambda: preview['expiresAt'] + 1)
        elif change == 'writer': stack.callback(SharedSessionStore(str(cwd), 'child-one').acquire(app='busy-fixture').release)
        elif change == 'native-gate': stack.enter_context(Gate(owner.config))
        with pytest.raises(ValueError): await owner.perform('scope', 'maintenance.history.remove', args)
    assert (directory / 'root-one').exists() and (directory / 'child-one').exists()
    assert not (owner.app / 'sessions/root-one/history-removal.json').exists()
    receipt = await owner.perform('scope', 'maintenance.receipt', {'commandId': args['commandId']})
    assert receipt['receipt']['state'] == 'refused' and receipt['receipt']['executed'] is False


@pytest.mark.parametrize('invalid', ['linked-session', 'wrong-workspace', 'traversal', 'duplicate-native-id', 'pending-edit', 'transfer', 'external-writers', 'incomplete-census', 'metadata-array', 'control-project'])
def test_unsafe_or_unproven_native_selection_stays_intact(tmp_path, monkeypatch, invalid):
    owner, cleanup, cwd, directory, selection = fixture(tmp_path, monkeypatch)
    if invalid == 'linked-session': (directory / 'linked').symlink_to(directory / 'other-one', target_is_directory=True)
    elif invalid == 'wrong-workspace': (directory / 'child-one/metadata.json').write_text(json.dumps({'working_dir': str(cwd.parent), 'parent_id': 'root-one'}))
    elif invalid == 'traversal': selection['project'] = '../projects'
    elif invalid == 'duplicate-native-id': (owner.home / 'projects/other/sessions/root-one').mkdir(parents=True)
    elif invalid == 'pending-edit': (owner.app / 'sessions/root-one/history-edit-pending.json').write_text('{}')
    elif invalid == 'transfer': (directory / 'root-one/transfer-fence.json').write_text('{}')
    elif invalid == 'external-writers': owner.config['maintenanceExternalWriters'] = 'foundation-cooperative'
    elif invalid == 'control-project': selection['project'] = 'bad\nproject'
    elif invalid == 'metadata-array': (directory / 'root-one/metadata.json').write_text('[]')
    with pytest.raises((ValueError, RuntimeError)):
        if invalid == 'incomplete-census':
            cleanup.preview('scope', {'selection': selection, 'ownerCensus': {}, 'reviewed': True})
        else: review(cleanup, selection)
    assert (directory / 'root-one').exists() and (cwd / 'notes.txt').is_file()


@pytest.mark.asyncio
async def test_interrupted_removal_is_unknown_and_cannot_be_replayed_under_another_command(tmp_path, monkeypatch):
    owner, cleanup, cwd, directory, selection = fixture(tmp_path, monkeypatch)
    _, _, args = review(cleanup, selection)
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(args)
        raise OSError('fixture lost storage')
    monkeypatch.setattr('amplifier_acp.history_cleanup.shutil.rmtree', interrupted)
    with pytest.raises(RuntimeError, match='never replay'):
        await owner.perform('scope', 'maintenance.history.remove', args)
    assert len(calls) == 1
    saved = await owner.perform('scope', 'maintenance.history.remove', args)
    assert saved['receipt']['state'] == 'unknown' and saved['replayed'] is False and len(calls) == 1
    with pytest.raises(Refused, match='original history removal'):
        cleanup.census('scope', {'selection': {'project': selection['project']}})
    with pytest.raises(ValueError):
        await owner.perform('scope', 'maintenance.history.remove', {**args, 'commandId': 'must-not-repeat'})
    assert len(calls) == 1 and list(directory.glob('.history-cleanup-*'))
    assert (owner.app / 'sessions/root-one/history-removal.json').is_file()
    assert (cwd / 'notes.txt').is_file()


def test_permanent_marker_blocks_restoring_or_modifying_recreated_history(tmp_path, monkeypatch):
    owner, cleanup, cwd, directory, selection = fixture(tmp_path, monkeypatch)
    from types import SimpleNamespace
    from amplifier_acp.session_lifecycle import Lifecycle
    marker = owner.app / 'sessions/root-one/history-removal.json'
    marker.write_text('{}')
    view = SimpleNamespace(app=marker.parent)
    for operation in ['restore', 'fork', 'delete.standard', 'legacyCopy.apply', 'managedFiles.preview']:
        with pytest.raises(ValueError, match='cannot be restored'):
            Lifecycle.perform(view, operation, {})


@pytest.mark.asyncio
async def test_installed_protocol_reviews_and_removes_exact_saved_family_without_starting_runtime(tmp_path, monkeypatch):
    from client import Client
    from test_private_store_snapshot import launch, call, hold, release
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'checkpoints'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'captures'))
    client, cwd, config, config_path = await launch(tmp_path)
    await client.close()
    config['maintenanceExternalWriters'] = 'stopped'
    config_path.write_text(json.dumps(config))
    sid = 'installed-original'
    project = project_slug(str(cwd))
    saved = Path(config['home']) / 'projects' / project / 'sessions' / sid
    saved.mkdir(parents=True)
    (saved / 'metadata.json').write_text(json.dumps({'session_id': sid, 'working_dir': str(cwd), 'name': 'Saved conversation'}))
    (saved / 'transcript.jsonl').write_text('{"role":"user","content":"saved original"}\n')
    (cwd / 'project.txt').write_text('Keep my work')
    client = await Client.launch(config_path)
    try:
        initialized = await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        cap = initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['historyCleanup']
        assert cap['workspacePreserved'] and cap['receipt'] and cap['automaticReplay'] is False
        auth = await hold(client, cwd)
        selection = {'project': project, 'nativeSessionId': sid}
        census = await call(client, cwd, 'maintenance.history.census', {'selection': selection}, auth)
        owners = {'version': 1, 'coverage': 'all-configured-owners-held', 'revision': digest(['installed owner']), 'nativeRevision': census['revision']}
        preview = await call(client, cwd, 'maintenance.history.preview', {'selection': selection, 'ownerCensus': owners, 'reviewed': True}, auth)
        assert preview['title'] == 'Saved conversation'
        args = {'commandId': 'installed-history-removal', 'previewHash': preview['previewHash'], 'ownerCensus': owners, 'confirmationId': preview['confirmationId']}
        result = await call(client, cwd, 'maintenance.history.remove', args, auth)
        assert result['receipt']['state'] == 'succeeded' and not saved.exists()
        repeat = await call(client, cwd, 'maintenance.history.remove', args, auth)
        assert repeat['receipt'] == result['receipt'] and repeat['replayed'] is False
        await release(client, cwd, auth)
        assert (cwd / 'project.txt').read_text() == 'Keep my work'
        assert client.events == []
    finally:
        stderr = await client.close()
        assert 'must-not-start' not in stderr
