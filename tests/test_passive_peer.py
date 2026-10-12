import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_acp.history import page
from amplifier_acp.native.passive_peer import PassivePeer


def event():
    return {'id': 'peer:one', 'text': 'ORBIT **572** is ready', 'createdAt': '2026-10-06T20:00:00Z',
            'peerEnvelope': {'version': 1, 'requestId': 'peer:one', 'grantId': 'scope',
                             'mode': 'notify', 'senderSessionId': 'ahp-session:/source',
                             'recipientSessionId': 'ahp-session:/target'}}


@pytest.mark.asyncio
async def test_checkpoint_and_ack_loss_never_duplicate_context(tmp_path):
    rows = []
    async def add(row): rows.append(row)
    context = SimpleNamespace(get_messages=AsyncMock(side_effect=lambda: list(rows)), add_message=AsyncMock(side_effect=add))
    async def checkpoint(): (tmp_path/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    controls = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: context), checkpoint=AsyncMock(side_effect=OSError('disk')))
    acknowledged = False
    lose_ack = True
    async def bridge(operation, args):
        nonlocal acknowledged, lose_ack
        assert operation == 'coordination.notifications'
        if args.get('acknowledge'):
            if lose_ack:
                lose_ack = False
                raise ConnectionError('lost ack')
            acknowledged = True
        return {'notifications': [] if acknowledged else [event()]}
    hook = PassivePeer(controls, bridge)
    with pytest.raises(OSError): await hook.boundary('provider:request', {})
    assert not acknowledged and len(rows) == 1
    controls.checkpoint.side_effect = checkpoint
    with pytest.raises(ConnectionError): await hook.boundary('provider:request', {})
    await PassivePeer(controls, bridge).boundary('provider:request', {})
    await hook.boundary('provider:request', {})
    assert len(rows) == 1 and context.add_message.await_count == 1 and acknowledged
    assert 'agent-origin reference content' in rows[0]['content']
    public = page(tmp_path)['turnGroups'][0]
    assert public['recordedOnly'] is True
    assert public['updates'][0]['content']['text'] == event()['text']
    assert public['updates'][0]['_meta']['amplifier.dev/history']['inputOrigin'] == 'peer'
    # Arrival during a tool-running turn stays inline; it is not an input that
    # owns the eventual answer or changes the complete-turn paging boundary.
    human = {'role': 'user', 'content': 'Original task', 'metadata': {'amplifier_input': {'id': 'human-turn', 'kind': 'user', 'source': 'user'}}}
    ordered = [human, rows[0], {'role': 'assistant', 'content': 'Completed the original task'}]
    (tmp_path/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in ordered))
    groups = page(tmp_path, limit=1)['turnGroups']
    assert len(groups) == 1 and groups[0]['id'] == 'human-turn' and not groups[0].get('recordedOnly')
    assert [row['sessionUpdate'] for row in groups[0]['updates']] == ['user_message_chunk', 'user_message_chunk', 'agent_message_chunk']


@pytest.mark.asyncio
async def test_malformed_batch_is_rejected_before_any_append():
    controls = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: SimpleNamespace(get_messages=AsyncMock(return_value=[]), add_message=AsyncMock())))
    for bad in [None, {**event(), 'text': None}, event()]:
        bridge = AsyncMock(return_value={'notifications': [event(), bad]})
        with pytest.raises(ValueError): await PassivePeer(controls, bridge).boundary('provider:request', {})


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', [False, True])
async def test_installed_worker_delivers_on_natural_request_only(tmp_path, boundary):
    from client import Client
    from native_setup import configuration
    from pathlib import Path
    import yaml
    config, cwd = configuration(tmp_path)
    bundle = tmp_path/'fixture.yaml'; value = yaml.safe_load(bundle.read_text())
    value['providers'][0]['config'] = {'promptAuditFile': str(tmp_path/'provider-prompts.jsonl')}
    if boundary:
        import amplifier_module_context_managed
        value['session']['context'] = {'module': 'context-managed', 'source': str(Path(amplifier_module_context_managed.__file__).parent), 'config': {'engine': 'boundary', 'token_meter': 'actual'}}
    bundle.write_text(yaml.safe_dump(value))
    client = await Client.launch(config); inbox = []; acknowledgements = []
    async def response(row):
        assert row['method'] == '_amplifier/host', row
        assert row['params']['operation'] == 'coordination.notifications', row
        args = row['params']['args']
        if args.get('acknowledge'):
            acknowledgements.extend(args['acknowledge']); inbox.clear()
        return {'notifications': list(inbox), 'executionStarted': False}
    client.response_handler = response
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {
            'amplifier.dev/native': {'version': 1}, 'amplifier.dev/host': {'version': 1, 'features': {'peerNotifications': True}}}}})
        sid = (await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []}))['result']['sessionId']
        async def prompt(identity): return await client.request('session/prompt', {'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'Continue'}], '_meta': {'amplifier.dev/command': {'commandId': identity}}})
        assert 'result' in await prompt('first')
        before = len(client.requests)
        inbox.append(event())
        assert len(client.requests) == before  # Storing an inbox entry does not invoke the worker.
        assert 'result' in await prompt('second')
        assert 'result' in await prompt('third')
        recorded = [json.loads(line) for line in (tmp_path/'provider-prompts.jsonl').read_text().splitlines()]
        # Session naming also uses the fixture provider; it is not a chat turn.
        requests = [request for request in recorded if any(row.get('role') == 'user' and row.get('content') == 'Continue' for row in request)]
        assert len(requests) == 3
        assert not any('ORBIT' in str(row.get('content')) for row in requests[0])
        for request in requests[1:]:
            assert len([row for row in request if 'ORBIT' in str(row.get('content'))]) == 1
        assert acknowledgements == ['peer:one']
        starts = [row for row in client.events if row.get('params', {}).get('event', {}).get('type') == 'generation.started']
        assert len(starts) == 3
    finally: await client.close()
