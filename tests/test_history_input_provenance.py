"""Keep recorded v1 input provenance without upgrading legacy or malformed facts."""
import asyncio
import json

import pytest

from amplifier_acp.history import content_updates, forward, page, provenance
from client import Client
from test_steering_protocol import opened, entered, release, steer


@pytest.mark.parametrize('kind', ['user', 'steer'])
def test_exact_recorded_version_survives_all_history_projections(tmp_path, kind):
    marker = {'version': 1, 'kind': kind, 'id': 'recorded', 'source': 'user'}
    row = {'role': 'user', 'content': 'Preserved text', 'metadata': {
        'amplifier_input': marker, 'inputOrigin': 'steering' if kind == 'steer' else 'user',
        'activeInputId': 'original' if kind == 'steer' else 'recorded'}}
    path = tmp_path/'transcript.jsonl';path.write_text(json.dumps(row)+'\n')
    before = path.read_bytes()
    for update in [*content_updates(row, 0)[0], *page(tmp_path)['updates'], *forward(tmp_path)]:
        facts = update['_meta']['amplifier.dev/history']
        assert facts['nativeInput'] == marker
        assert facts['activeInputId'] == row['metadata']['activeInputId']
        assert facts['authorization'] == 'unverified-native-history'
        assert update['content']['text'] == 'Preserved text'
    assert path.read_bytes() == before


@pytest.mark.parametrize('version', [None, True, False, 1.0, '1', 0, 2, {}, []])
def test_invalid_or_unknown_version_never_becomes_supported_identity(version):
    marker = {'version': version, 'kind': 'steer', 'id': 'recorded', 'source': 'user'}
    result = provenance({'metadata': {'amplifier_input': marker}})
    assert result['nativeInput'] == {k: v for k, v in marker.items() if k != 'version'}
    assert result['authorization'] == 'unverified-native-history'


def test_legacy_and_malformed_identity_facts_are_not_upgraded():
    legacy = {'kind': 'user', 'id': 'old', 'source': 'user'}
    assert provenance({'metadata': {'amplifier_input': legacy}})['nativeInput'] == legacy
    assert provenance({'role': 'user', 'content': 'Old unmarked text'}) == {
        'authorization': 'unverified-native-history', 'inputOrigin': 'unknown'}
    malformed = provenance({'metadata': {'amplifier_input': {
        'version': True, 'id': 1, 'kind': ['steer'], 'source': 'user'}}})
    assert malformed['nativeInput'] == {'source': 'user'}


@pytest.mark.asyncio
async def test_actual_two_corrections_and_next_turn_keep_version_and_original_group(tmp_path):
    client, sid, cwd = await opened(tmp_path)
    try:
        prompt = asyncio.create_task(client.request('session/prompt', {
            'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'steering-apply'}],
            '_meta': {'amplifier.dev/command': {'commandId': 'turn-one'}}}))
        await entered(tmp_path, 'tool')
        for identity in ['first', 'second']:
            result = await steer(client, sid, {'inputId': identity, 'activeInputId': 'turn-one',
                                               'text': 'Owned correction '+identity})
            assert result['result']['accepted'], result
        release(tmp_path, 'tool')
        assert (await prompt)['result']['stopReason'] == 'end_turn'
        result = await client.request('session/prompt', {
            'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'Next ordinary turn'}],
            '_meta': {'amplifier.dev/command': {'commandId': 'turn-two'}}})
        assert result['result']['stopReason'] == 'end_turn'
    finally:
        await client.close()

    path = next((tmp_path/'home/projects').glob('*/sessions/*/transcript.jsonl'))
    before = path.read_bytes()
    canonical = [json.loads(line) for line in before.splitlines()]
    saved = {r['metadata']['amplifier_input']['id']: r for r in canonical
             if r.get('role') == 'user' and 'amplifier_input' in r.get('metadata', {})}
    assert set(saved) == {'turn-one', 'first', 'second', 'turn-two'}
    for identity, row in saved.items():
        marker = row['metadata']['amplifier_input']
        assert type(marker['version']) is int and marker['version'] == 1
        assert marker['kind'] == ('steer' if identity in {'first', 'second'} else 'user')

    client = await Client.launch(tmp_path/'config.json')
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {
            '_meta': {'amplifier.dev/native': {'version': 1}}}})
        response = await client.request('_amplifier/history', {'sessionId': sid, 'cwd': str(cwd), 'limit': 50})
        assert 'result' in response, response
        history = response['result']
        assert [group['id'] for group in history['turnGroups']] == ['turn-one', 'turn-two']
        loaded = await client.request('session/load', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in loaded, loaded
        replay = [e['params']['update'] for e in client.events if e.get('method') == 'session/update']
        for updates in [history['updates'], replay]:
            inputs = [u for u in updates if u.get('sessionUpdate') == 'user_message_chunk']
            assert len(inputs) == 4
            for update in inputs:
                facts = update['_meta']['amplifier.dev/history']; marker = facts['nativeInput']
                identity = marker['id']
                assert marker == {key: saved[identity]['metadata']['amplifier_input'][key]
                                  for key in ['version', 'kind', 'id', 'source']}
                assert facts['turnId'] == ('turn-two' if identity == 'turn-two' else 'turn-one')
                assert facts['messageReference']['sessionId'] == sid
                if identity in {'first', 'second'}:assert facts['activeInputId'] == 'turn-one'
                if 'amplifier_public_message' not in saved[identity]['metadata']:
                    assert 'rowLocator' not in facts
        assert path.read_bytes() == before
    finally:
        await client.close()
