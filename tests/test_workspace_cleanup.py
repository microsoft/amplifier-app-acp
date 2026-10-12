import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_acp.maintenance import Maintenance
from amplifier_acp.maintenance_gate import Gate
from amplifier_acp.workspace_cleanup import WorkspaceCleanup, Refused
from test_private_store_snapshot import launch, call, hold
from client import Client


def selected(target):
    return {'workspaceId': 'workspace-one', 'path': str(target), 'revision': 'held-owner-census-one',
            'coverage': 'all-configured-owners-held', 'registeredPaths': [str(target)],
            'protectedPaths': [], 'sessions': []}


def fixture(tmp_path):
    root = tmp_path.resolve()
    native = root / 'native'
    native.mkdir()
    config = {'home': str(native), 'appHome': str(root / 'app'), 'maintenanceExternalWriters': 'stopped'}
    admin = SimpleNamespace(config=config, lifecycle=SimpleNamespace(fence={
        'purpose': 'recovery', 'fenceId': 'held', 'commandId': 'original',
        'leaseId': 'lease', 'adminInstanceId': 'instance'}), workspace=lambda p: p)
    owner = Maintenance(admin)
    target = root / 'project'
    target.mkdir()
    (target / 'notes.txt').write_text('original user files')
    return owner, WorkspaceCleanup(owner), target


@pytest.mark.asyncio
async def test_installed_protocol_removes_exact_folder_once_and_retains_history(tmp_path):
    client, cwd, config, launch_path = await launch(tmp_path)
    await client.close()
    config['maintenanceExternalWriters'] = 'stopped'
    launch_path.write_text(json.dumps(config))
    client = await Client.launch(launch_path)
    await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
    target = cwd / 'registered'
    target.mkdir()
    (target / 'uncommitted.txt').write_text('user work reviewed for permanent removal')
    history = Path(config['home']) / 'retained-history.jsonl'
    history.write_text('original transcript')
    from amplifier_acp.native.session_files import project_slug
    session_id = 'b192df31-2867-45c1-bf8a-383917542c78'
    native_directory = Path(config['home']) / 'projects' / project_slug(str(target)) / 'sessions' / session_id
    native_directory.mkdir(parents=True)
    metadata = json.dumps({'session_id': session_id})
    (native_directory / 'metadata.json').write_text(metadata)
    (native_directory / 'transcript.jsonl').write_text('canonical conversation')
    linked = cwd / 'other-file.txt'
    linked.write_text('outside selection')
    (target / 'link.txt').symlink_to(linked)
    try:
        auth = await hold(client, cwd)
        selection = selected(target)
        # The product catalog has not yet discovered this physical history.
        # Native must still acquire its writer lease and retain its contents.
        selection['sessions'] = []
        preview = await call(client, cwd, 'maintenance.workspace.preview', {'selection': selection, 'reviewed': True}, auth)
        args = {'commandId': 'remove-once', 'previewHash': preview['previewHash'], 'selection': selection, 'confirmationPath': str(target)}
        result = await call(client, cwd, 'maintenance.workspace.remove', args, auth)
        assert result['removed'] and result['historyPreserved'] and result['canonicalFilesChanged'] == 0
        assert not target.exists() and history.read_text() == 'original transcript' and linked.read_text() == 'outside selection'
        assert (native_directory / 'metadata.json').read_text() == metadata
        assert (native_directory / 'transcript.jsonl').read_text() == 'canonical conversation'
        target.mkdir()
        (target / 'new.txt').write_text('new work must not be deleted')
        repeated = await call(client, cwd, 'maintenance.workspace.remove', args, auth)
        assert repeated['replayed'] is False and (target / 'new.txt').exists()
        receipt = await call(client, cwd, 'maintenance.receipt', {'commandId': 'remove-once'})
        assert receipt['receipt']['state'] == 'succeeded'
        assert not client.events
    finally:
        await client.close()


@pytest.mark.parametrize('reason', ['changed-file', 'changed-census', 'wrong-confirmation', 'expired', 'native-writer'])
def test_stale_reviews_and_writers_refuse_before_deletion(tmp_path, reason):
    owner, cleanup, target = fixture(tmp_path)
    selection = selected(target)
    review = cleanup.preview('scope', {'selection': selection, 'reviewed': True})
    args = {'commandId': 'remove', 'previewHash': review['previewHash'], 'selection': selection, 'confirmationPath': str(target)}
    if reason == 'changed-file':
        (target / 'notes.txt').write_text('new content after review')
    elif reason == 'changed-census':
        selection['revision'] = 'new-owner-census'
    elif reason == 'wrong-confirmation':
        args['confirmationPath'] = target.name
    elif reason == 'expired':
        # Change time, not the integrity-bound saved preview.
        import unittest.mock
        with unittest.mock.patch('amplifier_acp.workspace_cleanup.time.time', return_value=review['expiresAt'] + 1):
            with pytest.raises(Refused):
                cleanup.remove('scope', args)
        assert target.exists()
        return
    elif reason == 'native-writer':
        with Gate(owner.config):
            with pytest.raises(Refused):
                cleanup.remove('scope', args)
        assert target.exists()
        return
    with pytest.raises(Refused):
        cleanup.remove('scope', args)
    assert target.exists()
    with owner.db() as db:
        assert db.execute('SELECT count(*) FROM workspace_cleanup_attempts').fetchone()[0] == 0


@pytest.mark.parametrize('reason', ['native-home', 'app-home', 'workspace-local-state', 'overlapping-workspace', 'retained-resource', 'missing-history', 'incomplete-owners', 'unregistered', 'external-writers', 'linked-parent'])
def test_protected_or_unproven_scopes_cannot_be_reviewed(tmp_path, reason):
    owner, cleanup, target = fixture(tmp_path)
    selection = selected(target)
    if reason == 'native-home': selection['path'] = str(owner.home)
    elif reason == 'app-home': selection['path'] = str(owner.app)
    elif reason == 'workspace-local-state': (target / '.amplifier').mkdir()
    elif reason == 'overlapping-workspace': selection['registeredPaths'].append(str(target / 'child'))
    elif reason == 'retained-resource': selection['protectedPaths'].append(str(target / 'notes.txt'))
    elif reason == 'missing-history': selection['sessions'] = [{'nativeSessionId': 'missing-session', 'cwd': str(target)}]
    elif reason == 'incomplete-owners': selection['coverage'] = 'some-owners'
    elif reason == 'unregistered': selection['registeredPaths'] = []
    elif reason == 'external-writers': owner.config['maintenanceExternalWriters'] = 'foundation-cooperative'
    elif reason == 'linked-parent':
        link = target.parent / 'linked'
        link.symlink_to(target.parent, target_is_directory=True)
        selection['path'] = str(link / target.name)
    with pytest.raises(ValueError): cleanup.preview('scope', {'selection': selection, 'reviewed': True})
    assert (target / 'notes.txt').exists()


@pytest.mark.asyncio
async def test_interrupted_removal_keeps_quarantine_and_refuses_new_preview_or_replay(tmp_path, monkeypatch):
    owner, cleanup, target = fixture(tmp_path)
    selection = selected(target)
    review = cleanup.preview('scope', {'selection': selection, 'reviewed': True})
    args = {'commandId': 'uncertain-remove', 'previewHash': review['previewHash'], 'selection': selection, 'confirmationPath': str(target)}
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(args)
        raise OSError('simulated interrupted deletion')
    monkeypatch.setattr('amplifier_acp.workspace_cleanup.shutil.rmtree', fail)
    with pytest.raises(RuntimeError):
        await owner.perform('scope', 'maintenance.workspace.remove', args)
    assert len(attempts) == 1 and not target.exists()
    quarantine = next(target.parent.glob('.unified-cleanup-*'))
    assert (quarantine / 'notes.txt').read_text() == 'original user files'
    _, receipt = owner.receipt('scope', 'uncertain-remove')
    assert receipt['state'] == 'unknown'
    repeated = await owner.perform('scope', 'maintenance.workspace.remove', args)
    assert repeated['receipt']['state'] == 'unknown' and len(attempts) == 1
    # Restored/new material never makes an uncertain command eligible for retry.
    target.mkdir()
    (target / 'new.txt').write_text('preserve')
    with pytest.raises(Refused): cleanup.preview('scope', {'selection': selection, 'reviewed': True})
    with pytest.raises(Refused): cleanup.remove('scope', {**args, 'commandId': 'different-command'})
    assert (target / 'new.txt').exists() and (quarantine / 'notes.txt').exists()
