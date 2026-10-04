"""Cold hydration preserves reviewed canonical authority until explicit work."""
import asyncio
import json
import uuid

import pytest

from client import Client
from test_session_lifecycle import action, initialize, prepared, prompt


async def retire(client, sid):
    async with asyncio.timeout(10):
        while not (await client.request('_amplifier/retire', {'sessionId': sid}))['result']['retired']:
            await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('copy', [False, True])
async def test_cold_root_and_fork_preserve_reviewed_bytes_then_edit_once(tmp_path, monkeypatch, copy):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))
    client, sid, cwd, config = await prepared(tmp_path)
    source = sid
    try:
        assert 'result' in await prompt(client, sid, 'Retained first request', 'turn1')
        voice = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'voice.transcript.record', 'args': {
            'callId': 'retained-call', 'itemId': 'retained-item', 'role': 'user',
            'text': 'Retained spoken history', 'append': False, 'commandId': 'voice-original'}})
        assert voice['result']['recorded'], voice
        assert 'result' in await prompt(client, sid, 'Original second request', 'turn2')
        await retire(client, sid)
        source_path = next((tmp_path/'home/projects').glob('*/sessions/'+sid))
        source_bytes = (source_path/'transcript.jsonl').read_bytes()
        if copy:
            revision = (await action(client, sid, cwd, 'inspect'))['result']['historyRevision']
            target = str(uuid.uuid4())
            result = await action(client, sid, cwd, 'fork', {'commandId': 'fork-once',
                'targetSessionId': target, 'expectedHistoryRevision': revision})
            assert result['result']['creationConfirmed'], result
            sid = target
        await client.close()
        client = await Client.launch(config)
        await initialize(client)
        native = source_path.parent/sid
        (native/'events.jsonl').write_text('Retained event evidence\n')
        before = {name: (native/name).read_bytes() for name in ('transcript.jsonl', 'metadata.json', 'events.jsonl')}
        revision = (await action(client, sid, cwd, 'inspect'))['result']['historyRevision']
        resumed = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        # Initial preparation and idle retirement checkpoints must both be no-ops.
        await retire(client, sid)
        assert {name: (native/name).read_bytes() for name in before} == before
        assert (await action(client, sid, cwd, 'inspect'))['result']['historyRevision'] == revision
        await client.close()
        client = await Client.launch(config)
        await initialize(client)
        reopened = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in reopened, reopened
        assert {name: (native/name).read_bytes() for name in before} == before
        args = {'operationId': 'edit-once', 'inputId': 'replacement-once', 'targetMessageId': 'turn2',
                'expectedHistoryRevision': revision, 'text': 'Explicit corrected second request'}
        result = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'history.revise', 'args': args})
        assert result.get('result', {}).get('stopReason') == 'end_turn', result
        saved = (native/'transcript.jsonl').read_text()
        assert 'Explicit corrected second request' in saved and 'Original second request' not in saved
        assert 'Retained first request' in saved and 'Retained spoken history' in saved
        assert (native/'events.jsonl').read_bytes() == before['events.jsonl']
        duplicate = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'history.revise', 'args': args})
        assert duplicate['error']['data']['outcome'] == 'already-admitted'
        if copy:
            assert (source_path/'transcript.jsonl').read_bytes() == source_bytes
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_external_canonical_change_before_cold_mount_still_revokes_edit(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))
    client, sid, cwd, config = await prepared(tmp_path)
    try:
        await prompt(client, sid, 'Original request', 'turn1')
        await retire(client, sid)
        await client.close()
        client = await Client.launch(config)
        await initialize(client)
        revision = (await action(client, sid, cwd, 'inspect'))['result']['historyRevision']
        native = next((tmp_path/'home/projects').glob('*/sessions/'+sid))
        with (native/'transcript.jsonl').open('a') as stream:
            stream.write(json.dumps({'role': 'assistant', 'content': 'External retained change'})+'\n')
        changed = (native/'transcript.jsonl').read_bytes()
        resumed = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        result = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'history.revise', 'args': {
            'operationId': 'stale-edit', 'inputId': 'never-admitted', 'targetMessageId': 'turn1',
            'expectedHistoryRevision': revision, 'text': 'Must not replace external history'}})
        assert result['result']['accepted'] is False and result['result']['executed'] is False, result
        assert (native/'transcript.jsonl').read_bytes() == changed
    finally:
        await client.close()
