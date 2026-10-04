"""Real candidate discovery and remount; no inference or account credentials."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_snapshot_composition_and_candidate_pin_preserve_history_and_receipts(tmp_path):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    import yaml
    original = yaml.safe_load(Path(values['bundle']).read_text())
    original['bundle']['version'] = '1.0.0'
    original['providers'][0]['config'] = {'selectedModelAudit': str(tmp_path / 'inference')}
    hook = Path(importlib.util.find_spec('amplifier_module_hook_context_intelligence').origin).parent
    original['hooks'] = [{'module': 'hook-context-intelligence', 'source': str(hook),
                          'config': {'destinations': {}}}]
    Path(values['bundle']).write_text(json.dumps(original))
    overlay = tmp_path / 'overlay.yaml'
    overlay.write_text(json.dumps({'bundle': {'name': 'overlay', 'version': '1.0.0'},
                                  'session': {'context': {'config': {'max_tokens': 8192}}}}))
    settings = Path(values['home']) / 'settings.yaml'
    settings.write_text(json.dumps({'bundle': {'app': [str(overlay)]}}))
    settings_before = settings.read_bytes()
    snapshot = copy.deepcopy(original)
    snapshot['bundle']['version'] = '1.0.0+amplifier-unified.snapshot.1'
    snapshot['session']['context']['config'] = {'max_tokens': 5555}
    target = tmp_path / 'snapshot.yaml'
    target.write_text(json.dumps(snapshot))
    client = await Client.launch(config)
    async def control(operation, args=None):
        response = await client.request('_amplifier/native', {'sessionId': sid, 'operation': operation, 'args': args or {}})
        assert 'result' in response, response
        return response['result']
    async def receipt(command):
        return (await client.request('_amplifier/bundle/receipt', {'sessionId': sid, 'cwd': str(cwd), 'commandId': command}))['result']['receipt']
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        sid = (await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []}))['result']['sessionId']
        assert (await control('configuration.inspect'))['plan']['session']['context']['config']['max_tokens'] == 8192
        pin = {'instance': 'fixture', 'model': 'fixture-model'}
        await control('provider.select', pin)
        await control('voice.transcript.record', {'callId': 'retained', 'itemId': 'original', 'role': 'user',
                      'text': 'Retained canonical history', 'append': False, 'commandId': 'retained-voice'})
        history = next((Path(values['home']) / 'projects').rglob('transcript.jsonl'))
        history_before = history.read_bytes()
        preview = await control('bundle.preview', {'bundle': values['bundle']})
        assert preview['composition'] == 'configured' and preview['appCapabilities'] == [str(overlay)]
        assert preview['modelCompatible'] and preview['modelCompatibility'] == {'status': 'advertised'}
        ordinary = await control('bundle.switch', {'bundle': values['bundle'], 'commandId': 'ordinary'})
        assert ordinary['applied'] and ordinary['configuration']['plan']['session']['context']['config']['max_tokens'] == 8192
        preview = await control('bundle.preview', {'bundle': str(target)})
        assert preview['composition'] == 'snapshot' and preview['appCapabilities'] == []
        switched = await control('bundle.switch', {'bundle': str(target), 'previewId': preview['previewId'], 'commandId': 'snapshot'})
        assert switched['applied'] and switched['configuration']['plan']['session']['context']['config']['max_tokens'] == 5555
        assert (await control('configuration.providers'))['selection'] == pin
        assert history.read_bytes() == history_before

        snapshot['providers'][0]['config'].update(fixtureModels=[{'id': 'replacement'}], fixtureDefaultModel='replacement')
        target.write_text(json.dumps(snapshot))
        before = await control('configuration.inspect')
        preview = await control('bundle.preview', {'bundle': str(target)})
        assert preview['modelCompatible'] is False
        assert preview['modelCompatibility'] == {'status': 'not-advertised'}
        args = {'bundle': str(target), 'previewId': preview['previewId'], 'commandId': 'needs-choice'}
        result = await control('bundle.switch', args)
        assert result['requiresModelChoice'] and result['applied'] is False
        assert (await control('configuration.inspect'))['configurationRevision'] == before['configurationRevision']
        assert (await control('configuration.providers'))['selection'] == pin
        exact = await receipt(args['commandId'])
        assert exact['status'] == 'succeeded' and exact['result'] == result
        assert await control('bundle.switch', args) == result
        # Discovery failure is not evidence of compatibility or model invalidity.
        snapshot['providers'][0]['config']['modelDiscoveryError'] = True
        target.write_text(json.dumps(snapshot))
        unavailable = await control('bundle.preview', {'bundle': str(target)})
        assert unavailable['modelCompatibility'] == {'status': 'unverified'} and unavailable['modelCompatible'] is False
        unknown_catalog = await control('bundle.switch', {'bundle': str(target), 'commandId': 'catalog-unavailable'})
        assert unknown_catalog['requiresModelChoice'] and unknown_catalog['applied'] is False
        assert (await control('configuration.inspect'))['configurationRevision'] == before['configurationRevision']
        assert (await control('configuration.providers'))['selection'] == pin
        del snapshot['providers'][0]['config']['modelDiscoveryError']
        target.write_text(json.dumps(snapshot))
        # An explicit separate command may reset the pin. No automatic fallback.
        reset = await control('bundle.switch', {'bundle': str(target), 'resetModel': True, 'commandId': 'explicit-reset'})
        assert reset['applied']
        providers = await control('configuration.providers')
        assert providers['selection'] is None and providers['effective']['model'] == 'replacement'
        assert (await control('configuration.providerModels', {'instance': 'fixture'}))['models'] == [{'id': 'replacement'}]
        assert history.read_bytes() == history_before and settings.read_bytes() == settings_before
        assert not (tmp_path / 'inference').exists()
    finally:
        await client.close()
    values['workerCommand'] = ['/impossible/passive-receipt-worker']
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert await receipt('needs-choice') == exact
        assert not client.events and history.read_bytes() == history_before
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('models,expected', [([], 'not-advertised'), ([{'id': 'kept'}], 'advertised'),
                                           ([{'id': 'duplicate'}, {'id': 'duplicate'}], 'unverified')])
async def test_candidate_catalog_is_bounded_not_a_model_validity_claim(tmp_path, models, expected):
    from amplifier_acp.native.provider_probe import query
    result = await query({'action': 'providers.compatibility', 'module': 'provider-fixture',
                          'config': {'fixtureModels': models}, 'model': 'kept'})
    assert result == {'status': expected}


@pytest.mark.asyncio
async def test_unsupported_discovery_does_not_infer_compatibility_from_default(monkeypatch):
    from amplifier_acp.native import provider_probe
    closed = []
    class Provider:
        def __init__(self, config=None): pass
        def get_info(self): return {'defaults': {'model': 'kept'}, 'config_fields': []}
        async def close(self): closed.append(True)
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    assert await provider_probe.query({'action': 'providers.compatibility', 'module': 'test', 'model': 'kept'}) == {'status': 'unverified'}
    assert closed == [True, True]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['oversized', 'malformed', 'timeout', 'cancel'])
async def test_probe_transport_is_bounded_and_cancelled_child_is_joined(tmp_path, monkeypatch, mode):
    import asyncio
    import sys
    from types import SimpleNamespace
    from amplifier_acp.native import bundle_model_compatibility as subject
    from amplifier_foundation import Bundle
    spawned = []
    launch = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    async def subprocess(*args, **kwargs):
        script = "import sys,time; sys.stdin.read(); " + {
            'oversized': "print('x'*100000,flush=True); time.sleep(30)",
            'malformed': "print('{\"status\":[]}',flush=True)",
            'timeout': 'time.sleep(30)', 'cancel': 'time.sleep(30)',
        }[mode]
        process = await launch(sys.executable, '-I', '-B', '-c', script, **kwargs)
        spawned.append(process)
        entered.set()
        return process
    monkeypatch.setattr(subject.asyncio, 'create_subprocess_exec', subprocess)
    monkeypatch.setattr(subject, 'TIMEOUT', .15 if mode == 'timeout' else 5)
    bundle = Bundle(name='bounded', providers=[{'module': 'provider-fixture'}])
    config = SimpleNamespace(registry_home=tmp_path, module_sources={}, launcher_module_sources={})
    task = asyncio.create_task(subject.inspect_selection(config, bundle, bundle.to_mount_plan(),
                               {'instance': 'fixture', 'model': 'fixture-model'}, tmp_path))
    await entered.wait()
    if mode == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    else:
        assert await task == {'status': 'unverified'}
    assert len(spawned) == 1 and spawned[0].returncode is not None


@pytest.mark.asyncio
async def test_exited_probe_leader_does_not_leave_child_holding_stdout(tmp_path, monkeypatch):
    import asyncio
    import os
    import signal
    import sys
    from types import SimpleNamespace
    from amplifier_acp.native import bundle_model_compatibility as subject
    from amplifier_foundation import Bundle
    launched = []
    launch = asyncio.create_subprocess_exec
    child_pid = tmp_path / 'child-pid'
    child = f"import os,time; from pathlib import Path; Path({str(child_pid)!r}).write_text(str(os.getpid())); time.sleep(30)"
    parent = f"import sys,subprocess; sys.stdin.read(); subprocess.Popen([sys.executable,'-I','-B','-c',{child!r}]); sys.exit(0)"
    async def subprocess(*args, **kwargs):
        process = await launch(sys.executable, '-I', '-B', '-c', parent, **kwargs)
        launched.append(process)
        return process
    monkeypatch.setattr(subject.asyncio, 'create_subprocess_exec', subprocess)
    monkeypatch.setattr(subject, 'TIMEOUT', 1)
    bundle = Bundle(name='bounded', providers=[{'module': 'provider-fixture'}])
    config = SimpleNamespace(registry_home=tmp_path, module_sources={}, launcher_module_sources={})
    try:
        assert await subject.inspect_selection(config, bundle, bundle.to_mount_plan(),
                    {'instance': 'fixture', 'model': 'fixture-model'}, tmp_path) == {'status': 'unverified'}
        assert launched[0].returncode == 0 and child_pid.exists()
        pid = int(child_pid.read_text())
        for _ in range(200):
            try: os.kill(pid, 0)
            except ProcessLookupError: break
            await asyncio.sleep(.01)
        else: pytest.fail('Probe child survived its parent and probe deadline')
    finally:
        # Test failure must also clean its own synthetic child; no state replay.
        if launched:
            try: os.killpg(launched[0].pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await launched[0].wait()
