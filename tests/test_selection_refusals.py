"""Only owner-proven pre-effect selection/policy denials claim executed:false."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,option', [
    ('generations.check', 'adminGenerations'),
    ('maintenance.preview', 'adminMaintenance'),
    ('history.import.begin', 'adminHistoryImport'),
    ('permissions.save', 'adminPermissions'),
    ('voice.preferences.update', 'adminVoicePreferences'),
    ('voice.credential', 'adminVoiceCredentials'),
    ('voice.configure', 'adminVoiceCredentials'),
])
async def test_disabled_admin_operation_refuses_before_owner_initialization(tmp_path, operation, option):
    home = tmp_path / 'home'
    home.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    app = tmp_path / 'app'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'home': str(home), 'appHome': str(app),
        'adminWorkspaceRoots': [str(workspace)], 'workerCommand': ['/impossible/worker']}))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {
            '_meta': {'amplifier.dev/native': {'version': 1}}}})
        denied = await client.request('_amplifier/admin', {'cwd': str(workspace),
            'operation': operation, 'args': {}})
        assert denied['error']['code'] == -32000
        assert denied['error']['data'] == {'executed': False, 'replayed': False,
            'reason': 'native-admin-launcher-disabled', 'operation': operation, 'launcherOption': option}
        assert not app.exists() and not list(home.iterdir()) and not list(workspace.iterdir())
        assert not client.events
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_actual_provider_selection_refusal_and_post_effect_failure_are_distinct(tmp_path):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle = Path(values['bundle'])
    audit = tmp_path / 'provider-audit'
    bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines()
        if not line.strip().startswith('source:')).replace('config: {}',
        'config:\n      selectedModelAudit: ' + str(audit)) + '\n')
    # Inject a persistence failure only after the real selector mutation.
    script = '''import asyncio
from amplifier_acp.native.runtime_controls import RuntimeControls
persist = RuntimeControls.persist
def fail_after_selection(self, *args, **kwargs):
    if (self.selection or {}).get('model') == 'fixture-persist-failure':
        raise OSError('fixture persistence failure after selector changed')
    return persist(self, *args, **kwargs)
RuntimeControls.persist = fail_after_selection
from amplifier_acp.native.runtime_worker import Worker, configure_protocol
configure_protocol()
asyncio.run(Worker().run())
'''
    values['workerCommand'] = [sys.executable, '-I', '-B', '-c', script]
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {
            '_meta': {'amplifier.dev/native': {'version': 1}}}})
        new = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        sid = new['result']['sessionId']
        async def control(operation, args=None):
            return await client.request('_amplifier/native', {'sessionId': sid,
                'operation': operation, 'args': args or {}})
        selected = {'instance': 'fixture', 'model': 'original-model'}
        assert (await control('provider.select', selected))['result']['selection'] == selected
        original = (await control('configuration.providers'))['result']['selection']
        state = Path(values['appHome']) / 'sessions' / sid / 'control-state.json'
        before = state.read_bytes()
        for args in [{'instance': 'absent', 'model': 'any'}, {'instance': 'fixture', 'model': ' '},
                     {'instance': ['invalid'], 'model': 'any'}]:
            refused = await control('provider.select', args)
            assert refused['error']['data'] == {'executed': False, 'replayed': False,
                'reason': 'provider-selection-invalid'}
            assert (await control('configuration.providers'))['result']['selection'] == original
            assert state.read_bytes() == before
        failed = await control('provider.select', {'instance': 'fixture', 'model': 'fixture-persist-failure'})
        assert 'error' in failed and failed['error'].get('data', {}).get('executed') is not False
        assert (await control('configuration.providers'))['result']['selection']['model'] == 'fixture-persist-failure'
        assert state.read_bytes() == before and not audit.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['native', 'standard'])
async def test_busy_selection_refusal_precedes_session_start(tmp_path, boundary):
    from amplifier_acp.server import Session, RpcError
    session = Session(SimpleNamespace(config={'appHome': str(tmp_path / 'app')}), 'fixture', str(tmp_path))
    async def forbidden():
        pytest.fail('Busy selection must not start or mutate a worker')
    session.start = forbidden
    await session.lock.acquire()
    try:
        with pytest.raises(RpcError) as failure:
            if boundary == 'native':
                await session.configuration_control('provider.select', {'instance': 'fixture', 'model': 'model'})
            else:
                await session.configure('model', '0')
        assert failure.value.data == {'executed': False, 'replayed': False, 'reason': 'provider-selection-busy'}
        assert not (tmp_path / 'app').exists()
    finally:
        session.lock.release()


@pytest.mark.asyncio
async def test_worker_owned_work_refuses_selection_before_effects(tmp_path, monkeypatch):
    from amplifier_acp.native.runtime_controls import RuntimeControls
    from amplifier_acp.native.host.model_selection import ProviderSelectionRefusal
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    loop = SimpleNamespace(root_provider=None)
    coordinator = SimpleNamespace(config={}, session_state={}, register_capability=lambda *args: None,
        get=lambda key: loop if key == 'orchestrator' else None, get_capability=lambda key: None)
    controls = RuntimeControls(SimpleNamespace(session_id='fixture', coordinator=coordinator),
        SimpleNamespace(generation=object(), queued_inputs=0))
    with pytest.raises(ProviderSelectionRefusal) as failure:
        await controls.perform('provider.select', {'instance': 'fixture', 'model': 'model'})
    assert failure.value.data == {'executed': False, 'replayed': False, 'reason': 'provider-selection-busy'}
    assert loop.root_provider is None and not controls.state_path().exists()
