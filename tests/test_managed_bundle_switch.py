"""Bundle switches preserve the authenticated configuration scope across remount."""
import json
from pathlib import Path
import sys

import pytest

from client import Client
from native_setup import configuration
from test_managed_session import allocated


@pytest.mark.asyncio
@pytest.mark.parametrize('managed', [False, True])
async def test_actual_switch_keeps_scope_history_selection_and_exact_receipt(tmp_path, managed):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    if managed:
        values, creation, cwd = allocated(tmp_path, config)
        # Neither the preview nor the remount may inherit workspace settings.
        (cwd / '.amplifier').mkdir()
        (cwd / '.amplifier/settings.yaml').write_text('bundle:\n  active: /missing/local-root\n')
    else:
        creation = None
    bundle = Path(values['bundle'])
    original = '\n'.join(line for line in bundle.read_text().splitlines()
                         if not line.strip().startswith('source:')) + '\n'
    audit = tmp_path / 'provider-audit'
    original = original.replace('config: {}', 'config:\n      selectedModelAudit: ' + str(audit))
    bundle.write_text(original)
    target = tmp_path / 'target.yaml'
    target.write_text(original.replace('module: context-simple', 'module: context-simple\n    config: {max_tokens: 7777}'))
    shared = Path(values['home']) / 'settings.yaml'
    settings_before = shared.read_bytes()
    client = await Client.launch(config)
    async def control(operation, args=None):
        return await client.request('_amplifier/native', {'sessionId': sid, 'operation': operation, 'args': args or {}})
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        if managed:
            created = await client.request('_amplifier/managed', creation)
            assert created.get('result', {}).get('status') == 'completed', created
            sid = creation['sessionId']
            loaded = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        else:
            loaded = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
            sid = loaded['result']['sessionId']
        assert 'result' in loaded, loaded
        selected = {'instance': 'fixture', 'model': 'fixture-model'}
        assert (await control('provider.select', selected))['result']['selection'] == selected
        recorded = await control('voice.transcript.record', {'callId': 'retained', 'itemId': 'original', 'role': 'user',
            'text': 'Retained canonical words', 'append': False, 'commandId': 'retained-voice'})
        assert recorded['result']['recorded']
        transcript = next((Path(values['home']) / 'projects').rglob('transcript.jsonl'))
        before = transcript.read_bytes()
        preview = (await control('bundle.preview', {'bundle': str(target)}))['result']
        command = {'commandId': 'switch-exact', 'bundle': str(target), 'previewId': preview['previewId']}
        result = await control('bundle.switch', command)
        assert result.get('result', {}).get('applied') is True, result
        assert result['result']['configuration']['plan']['session']['context']['config']['max_tokens'] == 7777
        assert (await control('configuration.providers'))['result']['selection'] == selected
        assert transcript.read_bytes() == before and shared.read_bytes() == settings_before
        receipt_params = {'sessionId': sid, 'cwd': str(cwd), 'commandId': command['commandId']}
        receipt = (await client.request('_amplifier/bundle/receipt', receipt_params))['result']['receipt']
        assert receipt['status'] == 'succeeded' and receipt['result'] == result['result']

        # A genuinely changed preview still refuses before activating anything.
        preview = (await control('bundle.preview', {'bundle': str(bundle)}))['result']
        bundle.write_text(original.replace('module: context-simple', 'module: context-simple\n    config: {max_tokens: 5555}'))
        stale = await control('bundle.switch', {'commandId': 'stale-review', 'bundle': str(bundle), 'previewId': preview['previewId']})
        assert stale['error']['data']['executed'] is False, stale
        assert transcript.read_bytes() == before
        assert (await control('configuration.inspect'))['result']['plan']['session']['context']['config']['max_tokens'] == 7777
        assert not audit.exists()
    finally:
        await client.close()
    # The exact original result remains readable without starting any worker.
    values['workerCommand'] = ['/impossible/passive-bundle-receipt-worker']
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert (await client.request('_amplifier/bundle/receipt', receipt_params))['result']['receipt'] == receipt
        assert not client.events and not audit.exists()
        assert transcript.read_bytes() == before and shared.read_bytes() == settings_before
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_configuration_change_during_managed_switch_rolls_back_without_replay(tmp_path):
    """A real settings change after preflight still invalidates ResolvedRoot."""
    config, _ = configuration(tmp_path)
    values, creation, cwd = allocated(tmp_path, config)
    bundle = Path(values['bundle'])
    original = '\n'.join(line for line in bundle.read_text().splitlines()
                         if not line.strip().startswith('source:')) + '\n'
    audit = tmp_path / 'provider-audit'
    original = original.replace('config: {}', 'config:\n      selectedModelAudit: ' + str(audit))
    bundle.write_text(original)
    target = tmp_path / 'target.yaml'
    target.write_text(original.replace('module: context-simple', 'module: context-simple\n    config: {max_tokens: 7777}'))
    # Inject only the external settings edit, at the actual transaction boundary.
    # The native journal, Core/Foundation mount, rollback and receipt stay real.
    script = '''import asyncio, os
from pathlib import Path
from amplifier_acp.native.bundle_selection import BundleTransaction
original_select = BundleTransaction.select
def select(self, bundle, **kwargs):
    original_select(self, bundle, **kwargs)
    settings = Path(os.environ['AMPLIFIER_HOME']) / 'settings.yaml'
    settings.write_text(settings.read_text() + 'review_revision: changed-during-switch\\n')
BundleTransaction.select = select
from amplifier_acp.native.runtime_worker import Worker, configure_protocol
configure_protocol()
asyncio.run(Worker().run())
'''
    values['workerCommand'] = [sys.executable, '-I', '-B', '-c', script]
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    sid = creation['sessionId']
    async def control(operation, args=None):
        return await client.request('_amplifier/native', {'sessionId': sid, 'operation': operation, 'args': args or {}})
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert (await client.request('_amplifier/managed', creation))['result']['status'] == 'completed'
        assert 'result' in await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert (await control('voice.transcript.record', {'callId': 'retained', 'itemId': 'original', 'role': 'user',
            'text': 'Keep this history through rollback', 'append': False, 'commandId': 'retained-voice'}))['result']['recorded']
        transcript = next((Path(values['home']) / 'projects').rglob('transcript.jsonl'))
        before = transcript.read_bytes()
        plan = (await control('configuration.inspect'))['result']['plan']
        failed = await control('bundle.switch', {'commandId': 'concurrent-change', 'bundle': str(target)})
        assert failed['error']['data']['outcome'] == 'unknown'
        assert (await control('configuration.inspect'))['result']['plan'] == plan
        assert transcript.read_bytes() == before
        assert not (Path(values['appHome']) / 'sessions' / sid / 'bundle-transition.json').exists()
        assert 'changed-during-switch' in (Path(values['home']) / 'settings.yaml').read_text()
        params = {'sessionId': sid, 'cwd': str(cwd), 'commandId': 'concurrent-change'}
        receipt = (await client.request('_amplifier/bundle/receipt', params))['result']['receipt']
        assert receipt['status'] == 'unknown' and 'result' not in receipt
        assert receipt['diagnosticReceipt'] == failed['error']['data']['diagnosticReceipt']
        diagnostic = Path(values['appHome']) / 'logs' / 'workers' / receipt['diagnosticReceipt']
        detail = json.loads(json.loads(diagnostic.read_text().splitlines()[0])['failure'])
        assert detail['errorType'] == 'ValueError'
        assert 'bundle configuration changed' in detail['message']
        assert 'bundle configuration changed' not in json.dumps(receipt)
        assert not audit.exists()
    finally:
        await client.close()
    values['workerCommand'] = ['/impossible/passive-bundle-receipt-worker']
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert (await client.request('_amplifier/bundle/receipt', params))['result']['receipt'] == receipt
        assert not client.events and not audit.exists()
        assert transcript.read_bytes() == before
    finally:
        await client.close()
