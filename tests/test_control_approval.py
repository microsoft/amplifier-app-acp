"""Approval responses settle admitted work; they must not queue behind that work."""
import asyncio
import json

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_correlated_approval_reply_bypasses_admission_lock_without_new_activation(monkeypatch):
    from amplifier_acp.native import runtime_worker
    worker = runtime_worker.Worker()
    replies = []
    monkeypatch.setattr(runtime_worker, 'publish', replies.append)
    async def forbidden():
        pytest.fail('An approval response must not reacquire native mutation ownership')
    monkeypatch.setattr(worker, 'acquire_for_mutation', forbidden)
    first = asyncio.get_running_loop().create_future()
    other = asyncio.get_running_loop().create_future()
    worker.approvals = {'first': (first, ['allow', 'deny']), 'other': (other, ['deny'])}
    await worker.command_lock.acquire()
    try:
        await asyncio.wait_for(worker.command({'op': 'approval', 'id': 'reply', 'approval_id': 'first', 'decision': 'allow'}), .5)
        assert first.result() == 'allow' and not other.done()
        assert replies[-1]['result'] == {'accepted': True}
        for identity, approval, decision in [('duplicate', 'first', 'allow'), ('unoffered', 'other', 'allow'), ('unknown', 'absent', 'deny')]:
            await asyncio.wait_for(worker.command({'op': 'approval', 'id': identity, 'approval_id': approval, 'decision': decision}), .5)
            assert replies[-1]['id'] == identity and 'error' in replies[-1]
        assert not other.done() and worker.command_lock.locked()
    finally:
        worker.command_lock.release()
        other.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize('choice,expected', [('allow', 'allow'), ('deny', 'deny'), ('not-offered', 'deny'), (None, 'deny')])
async def test_actual_idle_tool_control_permission_settles_without_provider_call(tmp_path, choice, expected):
    config, cwd = configuration(tmp_path)
    audit = tmp_path / 'provider-calls'
    values = json.loads(config.read_text())
    from pathlib import Path
    bundle = Path(values['bundle'])
    bundle.write_text(bundle.read_text().replace('config: {}', 'config:\n      selectedModelAudit: ' + str(audit)))
    client = await Client.launch(config)
    requests = []
    async def answer(row):
        assert row['method'] == 'session/request_permission'
        requests.append(row['params'])
        return {'outcome': {'outcome': 'selected', 'optionId': choice}} if choice is not None else {'outcome': {'outcome': 'cancelled'}}
    client.response_handler = answer
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        sid = (await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []}))['result']['sessionId']
        result = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'tool.invoke', 'args': {'name': 'fixture_permit', 'arguments': {}}}, timeout=10)
        assert result['result']['result']['output']['decision'] == expected, result
        assert len(requests) == 1 and requests[0]['sessionId'] == sid
        assert requests[0]['toolCall']['toolCallId']
        assert not audit.exists(), 'Direct tool control must not start a provider turn'
        assert not any(e.get('params', {}).get('event', {}).get('type') == 'generation.started' for e in client.events)
        # The original control retains ownership until checkpoint/parking; once
        # settled it must not leave an orphan approval blocking retirement.
        retired = await client.request('_amplifier/retire', {'sessionId': sid})
        assert retired['result']['retired'] is True, retired
    finally:
        await client.close()
