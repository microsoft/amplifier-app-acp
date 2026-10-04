"""Fresh ACP consumers learn opaque configuration choices from standard events."""
import json
from pathlib import Path
import sys

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_fresh_public_events_enable_selection_without_a_warmup_control(tmp_path):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle = Path(values['bundle'])
    audit = tmp_path / 'provider-audit'
    bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines()
        if not line.strip().startswith('source:')).replace('config: {}',
        'config:\n      selectedModelAudit: ' + str(audit)) + '\n')
    client = await Client.launch(config)
    try:
        # Ordinary ACP client: no native control or configuration warm-up read.
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}})
        created = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in created, created
        sid = created['result']['sessionId']
        assert not audit.exists(), 'Reading configuration must not call a model'
        first = await client.request('session/prompt', {'sessionId': sid,
            'prompt': [{'type': 'text', 'text': 'first deliberate offline turn'}]})
        assert first['result']['stopReason'] == 'end_turn'
        options = [event for event in client.events if event.get('method') == 'session/update'
            and event['params']['sessionId'] == sid
            and event['params']['update']['sessionUpdate'] == 'config_option_update']
        assert len(options) == 1, 'Initialization must publish real choices exactly once'
        advertised = options[0]['params']['update']['configOptions']
        assert advertised == created['result']['configOptions']
        first_chunk = next(index for index, event in enumerate(client.events)
            if event.get('method') == 'session/update'
            and event['params']['update']['sessionUpdate'] == 'agent_message_chunk')
        assert client.events.index(options[0]) < first_chunk
        model = next(option for option in advertised if option['category'] == 'model')
        selected = next(choice for choice in model['options'] if choice['name'].endswith(' / fixture-model'))
        before = audit.read_text().splitlines()
        assert 'fixture-model' not in before
        changed = await client.request('session/set_config_option', {'sessionId': sid,
            'configId': model['id'], 'value': selected['value']})
        assert next(row for row in changed['result']['configOptions']
            if row['id'] == model['id'])['currentValue'] == selected['value']
        second = await client.request('session/prompt', {'sessionId': sid,
            'prompt': [{'type': 'text', 'text': 'second deliberate offline turn'}]})
        assert second['result']['stopReason'] == 'end_turn'
        after = audit.read_text().splitlines()
        assert after.count('fixture-model') == 1, 'The next real Core request must use the advertised selection'
        assert len([event for event in client.events if event.get('method') == 'session/update'
            and event['params']['update']['sessionUpdate'] == 'config_option_update']) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_initialization_without_configured_choices_does_not_emit_empty_update(tmp_path):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle = Path(values['bundle'])
    bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines()
        if not line.strip().startswith('source:')) + '\n')
    script = '''import asyncio
from amplifier_core.models import ProviderInfo
from amplifier_module_provider_fixture import FixtureProvider
def info(self):
    return ProviderInfo(id='fixture', display_name='No configured default', defaults={})
FixtureProvider.get_info = info
from amplifier_acp.native.runtime_worker import Worker, configure_protocol
configure_protocol()
asyncio.run(Worker().run())
'''
    values['workerCommand'] = [sys.executable, '-I', '-B', '-c', script]
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}})
        created = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        assert created['result']['configOptions'] == [], created
        assert not any(event.get('method') == 'session/update'
            and event['params']['update']['sessionUpdate'] == 'config_option_update' for event in client.events)
    finally:
        await client.close()
