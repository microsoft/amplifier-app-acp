import hashlib
import json
import time
import uuid
from types import SimpleNamespace
import pytest
from amplifier_acp.backup_cleanup import Backups, Refused
from amplifier_acp.maintenance import Maintenance
from test_workspace_cleanup import fixture as workspace_fixture

def fixture(tmp_path):
    owner, _, target = workspace_fixture(tmp_path)
    backups = Backups(owner)
    def create(name):
        artifact = uuid.uuid4().hex
        directory = owner.root / 'artifacts' / artifact
        directory.mkdir(parents=True)
        raw = ('private archive ' + name).encode()
        (directory / 'snapshot.tar').write_bytes(raw)
        description = {'artifactId': artifact, 'workspace': 'scope',
                       'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        (directory / 'description.json').write_text(json.dumps(description))
        owner.save('scope', name, {'commandId': name, 'operation': 'maintenance.snapshot',
                                  'state': 'succeeded', 'settledAt': time.time(), 'result': description})
        return artifact, directory
    old, old_path = create('old')
    newest, newest_path = create('new')
    return owner, backups, old, old_path, newest, newest_path, create

@pytest.mark.asyncio
async def test_backup_retirement_keeps_newest_history_and_original_receipt(tmp_path):
    owner, backups, old, path, newest, new_path, _ = fixture(tmp_path)
    history = owner.home / 'transcript.jsonl'
    history.write_text('retained original')
    catalog = backups.inspect('scope', {})
    assert [r['removable'] for r in catalog['items']] == [False, True]
    preview = backups.preview('scope', {'artifactId': old, 'reviewed': True})
    args = {'commandId': 'remove-old', 'previewHash': preview['previewHash'], 'confirmationId': old}
    result = await owner.perform('scope', 'maintenance.backups.remove', args)
    assert result['removed'] and result['canonicalFilesChanged'] == 0
    assert not path.exists() and new_path.exists() and history.read_text() == 'retained original'
    path.mkdir(); (path / 'replacement').write_text('do not replay')
    duplicate = await owner.perform('scope', 'maintenance.backups.remove', args)
    assert duplicate['replayed'] is False and (path / 'replacement').exists()

@pytest.mark.parametrize('reason', ['newest', 'restore-reference', 'changed', 'confirmation', 'expired', 'foreign', 'linked', 'active-capture', 'unknown-snapshot'])
@pytest.mark.asyncio
async def test_backup_review_refuses_changed_protected_or_unproven_selection(tmp_path, monkeypatch, reason):
    owner, backups, old, path, newest, _, _ = fixture(tmp_path)
    if reason == 'newest':
        with pytest.raises(Refused): backups.preview('scope', {'artifactId': newest, 'reviewed': True})
        return
    preview = backups.preview('scope', {'artifactId': old, 'reviewed': True})
    args = {'commandId': 'remove-old', 'previewHash': preview['previewHash'], 'confirmationId': old}
    if reason == 'restore-reference':
        plans = owner.root / 'restore-plans'; plans.mkdir()
        (plans / ('a' * 64 + '.json')).write_text(json.dumps({'artifactId': old}))
    if reason == 'changed': (path / 'new-file').write_text('new evidence')
    if reason == 'confirmation': args['confirmationId'] = newest
    if reason == 'expired': monkeypatch.setattr('amplifier_acp.backup_cleanup.time.time', lambda: preview['expiresAt'] + 1)
    if reason == 'linked':
        outside = tmp_path / 'outside'; outside.write_text('preserve')
        (path / 'snapshot.tar').unlink(); (path / 'snapshot.tar').symlink_to(outside)
    if reason == 'active-capture': owner.capture_scope = SimpleNamespace(active=lambda: True)
    if reason == 'unknown-snapshot': owner.save('scope', 'unknown', {'commandId': 'unknown', 'operation': 'maintenance.snapshot', 'state': 'unknown'})
    with pytest.raises(ValueError): await owner.perform('foreign' if reason == 'foreign' else 'scope', 'maintenance.backups.remove', args)
    assert path.exists()
    assert owner.receipt('foreign' if reason == 'foreign' else 'scope', 'remove-old')[1]['state'] == 'refused'

@pytest.mark.asyncio
async def test_interrupted_backup_removal_retains_quarantine_and_never_repeats(tmp_path, monkeypatch):
    owner, backups, old, path, newest, new_path, _ = fixture(tmp_path)
    preview = backups.preview('scope', {'artifactId': old, 'reviewed': True})
    args = {'commandId': 'remove-old', 'previewHash': preview['previewHash'], 'confirmationId': old}
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(args)
        raise OSError('interrupted removal')
    monkeypatch.setattr('amplifier_acp.backup_cleanup.shutil.rmtree', fail)
    with pytest.raises(RuntimeError): await owner.perform('scope', 'maintenance.backups.remove', args)
    assert next((owner.root / 'artifacts').glob('.backup-cleanup-*')).is_dir()
    assert new_path.exists() and not path.exists()
    result = await owner.perform('scope', 'maintenance.backups.remove', args)
    assert result['receipt']['state'] == 'unknown' and len(attempts) == 1
    with pytest.raises(Refused): backups.inspect('scope', {})

@pytest.mark.asyncio
async def test_installed_backup_cleanup_uses_real_snapshot_receipts_without_worker(tmp_path):
    from test_private_store_snapshot import launch, call, hold, prepare
    from client import Client
    client, cwd, config, launch_path = await launch(tmp_path)
    await client.close()
    config['maintenanceExternalWriters'] = 'stopped'
    launch_path.write_text(json.dumps(config))
    client = await Client.launch(launch_path)
    try:
        initialized = await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['backupCleanup']['newestProtected']
        auth = await hold(client, cwd)
        snapshots = []
        for identity in ('older', 'newest'):
            review = await prepare(client, cwd, auth, parts=['native-preference-receipts'], identity=identity)
            lease = await call(client, cwd, 'maintenance.acquire', {'commandId': identity+'-lease', 'previewHash': review['previewHash']}, auth)
            snapshots.append(await call(client, cwd, 'maintenance.snapshot', {'commandId': identity+'-snapshot', 'previewHash': review['previewHash'], 'leaseId': lease['leaseId']}, auth))
            await call(client, cwd, 'maintenance.release', {'leaseId': lease['leaseId']}, auth)
        catalog = await call(client, cwd, 'maintenance.backups.inspect', {}, auth)
        assert len(catalog['items']) == 2 and catalog['items'][0]['removable'] is False
        review = await call(client, cwd, 'maintenance.backups.preview', {'artifactId': snapshots[0]['artifactId'], 'reviewed': True}, auth)
        removed = await call(client, cwd, 'maintenance.backups.remove', {'commandId': 'remove-older', 'previewHash': review['previewHash'], 'confirmationId': snapshots[0]['artifactId']}, auth)
        assert removed['removed'] and removed['recoveryReferencesProtected'] and not client.events
    finally:
        await client.close()
