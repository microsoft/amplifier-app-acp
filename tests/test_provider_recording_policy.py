import json
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.provider_recording_policy import ProviderRecording, projection
from amplifier_acp.native.provider_recording import apply_provider_recording, recording_enabled, RAW_MODULES


def test_policy_preserves_legacy_fields_and_each_provider_opt_out(tmp_path):
    owner = ProviderRecording({'appHome': str(tmp_path)})
    owner.root.mkdir()
    file = owner.root / 'config.json'
    file.write_text(json.dumps({'providerRequests': False, 'enabled': True, 'destinations': [{'name': 'retained'}]}))
    initial = owner.perform('provider.recording.get', {})
    command = {'commandId': 'enable', 'expectedRevision': initial['revision'], 'enabled': True}
    saved = owner.perform('provider.recording.configure', command)
    assert saved['status'] == 'succeeded' and recording_enabled(tmp_path)
    before = file.read_bytes()
    assert owner.perform('provider.recording.configure', command) == saved
    assert file.read_bytes() == before
    assert json.loads(before)['destinations'] == [{'name': 'retained'}]
    stale = owner.perform('provider.recording.configure', {**command, 'commandId': 'stale'})
    assert stale['status'] == 'failed' and stale['executed'] is False
    assert file.read_bytes() == before
    for module in [*RAW_MODULES, 'provider-litellm']:
        key = 'raw_debug' if module == 'provider-litellm' else 'raw'
        plan = {'providers': [{'module': module}, {'module': module, 'config': {key: False}}]}
        apply_provider_recording(plan, home=tmp_path)
        assert plan['providers'][0]['config'][key] is True
        assert plan['providers'][1]['config'][key] is False
        apply_provider_recording(plan, enabled=False)
        assert key not in plan['providers'][0]['config']
    latest = owner.perform('provider.recording.get', {})
    disabled = owner.perform('provider.recording.configure', {'commandId': 'disable', 'expectedRevision': latest['revision'], 'enabled': False})
    assert disabled['status'] == 'succeeded' and not recording_enabled(tmp_path)
    assert ProviderRecording({'appHome': str(tmp_path)}).receipt('enable') == saved
    with pytest.raises(ValueError, match='different arguments'):
        owner.perform('provider.recording.configure', {**command, 'enabled': False})


def test_unknown_write_is_reconciled_from_marker_not_replayed(tmp_path, monkeypatch):
    from amplifier_acp import provider_recording_policy as module
    owner = ProviderRecording({'appHome': str(tmp_path)})
    command = {'commandId': 'lost', 'expectedRevision': projection({})['revision'], 'enabled': True}
    write = module.atomic_write
    def lost(*args, **kwargs):
        write(*args, **kwargs)
        raise OSError('Acknowledgement lost after atomic policy write')
    monkeypatch.setattr(module, 'atomic_write', lost)
    with pytest.raises(OSError):
        owner.perform('provider.recording.configure', command)
    raw = (owner.root / 'config.json').read_bytes()
    result = owner.receipt('lost')
    assert result['status'] == 'succeeded' and result['replayed'] is False
    assert owner.perform('provider.recording.configure', command) == result
    assert (owner.root / 'config.json').read_bytes() == raw
    monkeypatch.setattr(module, 'atomic_write', write)
    owner.perform('provider.recording.configure', {'commandId': 'next', 'expectedRevision': result['result']['revision'], 'enabled': False})
    assert owner.receipt('lost') == result


def test_no_marker_does_not_guess_completion_or_repeat_write(tmp_path, monkeypatch):
    from amplifier_acp import provider_recording_policy as module
    owner = ProviderRecording({'appHome': str(tmp_path)})
    def failed(*args, **kwargs):
        raise OSError('Before publication')
    monkeypatch.setattr(module, 'atomic_write', failed)
    command = {'commandId': 'unknown', 'expectedRevision': projection({})['revision'], 'enabled': True}
    with pytest.raises(OSError):
        owner.perform('provider.recording.configure', command)
    assert owner.perform('provider.recording.configure', command)['status'] == 'unknown'
    assert not (owner.root / 'config.json').exists()


@pytest.mark.parametrize('raw', ['[]', '{"providerRequests":1}', '{"providerRequests":true,"providerRequests":false}', 'x' * 65537])
def test_malformed_policy_is_retained_and_not_overwritten(tmp_path, raw):
    owner = ProviderRecording({'appHome': str(tmp_path)})
    owner.root.mkdir()
    file = owner.root / 'config.json'
    file.write_text(raw)
    with pytest.raises(ValueError):
        owner.perform('provider.recording.get', {})
    assert not recording_enabled(tmp_path)
    assert file.read_text() == raw


@pytest.mark.asyncio
async def test_admin_policy_is_passive_launcher_gated_and_fenced(tmp_path):
    from amplifier_acp.admin import Administration
    from test_admin_lifecycle import CONTEXT
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    config = {'home': str(tmp_path / 'home'), 'appHome': str(tmp_path / 'app'), 'adminWorkspaceRoots': [str(workspace)]}
    owner = Administration(config)
    with pytest.raises(ValueError, match='launcher enablement'):
        await owner.perform(str(workspace), 'provider.recording.get', {})
    config['adminProviderRecording'] = True
    before = await owner.perform(str(workspace), 'provider.recording.get', {})
    assert not Path(config['appHome']).exists() and owner.setup is None
    command = {'commandId': 'saved', 'expectedRevision': before['revision'], 'enabled': True}
    saved = await owner.perform(str(workspace), 'provider.recording.configure', command)
    assert owner.lifecycle.perform('acquire', CONTEXT)['acquired']
    assert (await owner.perform(str(workspace), 'provider.recording.get', {}))['enabled']
    assert await owner.perform(str(workspace), 'provider.recording.receipt', {'commandId': 'saved'}) == saved
    with pytest.raises(ValueError, match='intake is held'):
        await owner.perform(str(workspace), 'provider.recording.configure', {**command, 'commandId': 'fenced'})
    await owner.close()
    file = tmp_path / 'native.json'; file.write_text(json.dumps(config))
    client = await Client.launch(file)
    try:
        init = await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        capability = init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['providerRecording']
        assert capability['receipt'] is True and capability['application'] == 'next-runtime-preparation'
        result = await client.request('_amplifier/admin', {'cwd': str(workspace), 'operation': 'provider.recording.receipt', 'args': {'commandId': 'saved'}})
        assert result['result'] == saved and not client.events
    finally:
        await client.close()
