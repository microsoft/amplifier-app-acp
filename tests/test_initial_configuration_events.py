"""Fresh ACP consumers learn opaque configuration choices from standard events."""
import json
from pathlib import Path
import yaml

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
        selected = next(choice for group in model['options'] for choice in group['options'] if choice.get('_meta',{}).get('amplifier.dev/native',{}).get('model')=='fixture-model')
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
async def test_empty_provider_remains_visible_without_inventing_model_choices(tmp_path):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    bundle = Path(values['bundle'])
    bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines()
        if not line.strip().startswith('source:')) + '\n')
    # Both mounted and isolated catalog discovery see the same empty provider.
    # A monkeypatch in just the worker misses the independent catalog process.
    plan = yaml.safe_load(bundle.read_text())
    plan['providers'][0]['config'] = {'fixtureDefaultModel': None, 'fixtureModels': []}
    bundle.write_text(yaml.safe_dump(plan))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}})
        created = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        options = created['result']['configOptions']
        model = next(option for option in options if option['id'] == 'model')
        providers = [group for group in model['options'] if group['group']]
        assert len(providers) == 1 and providers[0]['options'] == []
        assert model['currentValue'] == '__default__'
        assert [choice['value'] for group in model['options'] for choice in group['options']] == ['__default__']
        updates = [event['params']['update']['configOptions'] for event in client.events
            if event.get('method') == 'session/update'
            and event['params']['update']['sessionUpdate'] == 'config_option_update']
        assert updates == [options]
    finally:
        await client.close()
