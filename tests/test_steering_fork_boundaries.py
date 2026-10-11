import copy
import json
import pytest
from amplifier_acp.native.session_store import user_boundaries, _input_identity
from amplifier_acp.native.automatic_history import display_message
from amplifier_acp.native.host.storage import SessionStore
from amplifier_acp.native.session_store import fork_session


def test_canonical_steering_before_later_input_keeps_both_boundaries():
    rows = [{'role': 'user', 'content': 'Correction', 'metadata': {'amplifier_input': {'version': 1, 'kind': 'steer', 'source': 'user', 'id': 'steer-id'}}},
            {'role': 'assistant', 'content': 'Accepted correction'},
            {'role': 'user', 'content': 'Next request', 'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'source': 'user', 'id': 'next-id'}}}]
    before = copy.deepcopy(rows)
    assert user_boundaries(rows, [{'role':'user','text':'Correction','inputId':'steer-id'}, {'role':'user','text':'Next request','inputId':'next-id'}]) == [0, 2]
    assert rows == before
    with pytest.raises(ValueError, match='transcript changed'):
        user_boundaries([*rows, rows[0]], [{'role':'user','text':'Correction','inputId':'steer-id'}])


@pytest.mark.parametrize('kind', ['service', ['steer'], None])
def test_non_user_boundaries_never_gain_identity(kind):
    row = {'role':'user','content':'Same text','metadata':{'amplifier_input':{'version':1,'kind':kind,'id':'input'}}}
    assert _input_identity(row) is None
    with pytest.raises(ValueError, match='transcript changed'):
        user_boundaries([row], [{'role':'user','text':'Same text','inputId':'input','nativeMessageId':'bound'}])


def saved_history(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    rows = [{'role': 'user', 'content': 'First'},
            {'role': 'assistant', 'content': 'First answer'},
            {'role': 'user', 'content': 'Second'},
            {'role': 'assistant', 'content': 'Second answer'}]
    source = {'id': 'original', 'title': 'Original', 'workspace': str(workspace),
              'bundle': 'work', 'status': 'idle', 'messages': []}
    return SessionStore.for_app(tmp_path, workspace), rows, source


@pytest.mark.parametrize('target', ['earlier-steer', 'later-input'])
@pytest.mark.parametrize('bound', [False, True])
def test_main_099_steering_cut_preserves_original(tmp_path, target, bound):
    """Exercise main 0.20.99's boundary variants through the actual Native store."""
    store, rows, source = saved_history(tmp_path)
    rows[0]['metadata'] = {'amplifier_input': {
        'version': 1, 'kind': 'steer', 'source': 'user', 'id': 'steer-id',
        'target_generation_id': 'earlier-generation'}}
    source['messages'] = [display_message(row, i, source) for i, row in enumerate(rows)]
    source['messages'][0]['inputId'] = 'steer-id'
    if bound:
        source['messages'][0]['nativeMessageId'] = source['messages'][0]['id']
    source['messages'][0]['id'] = 'steer-web'
    store.save('original', rows, {}, preserve_system=True)
    original = copy.deepcopy(source)
    path = store.directory('original') / 'transcript.jsonl'
    original_bytes = path.read_bytes()
    cut = 0 if target == 'earlier-steer' else 2
    fork_session(tmp_path, source, 'fork', before_message_id=source['messages'][cut]['id'])
    assert store.load('fork')[0] == rows[:cut]
    assert store.load('fork')[1]['fork']['jobs_replayed'] is False
    assert source == original and path.read_bytes() == original_bytes


@pytest.mark.parametrize('change', ['duplicate', 'different-id', 'service', 'version', 'malformed-kind'])
def test_main_099_conflicting_steering_creates_no_fork(tmp_path, change):
    store, rows, source = saved_history(tmp_path)
    marker = {'version': 1, 'kind': 'steer', 'source': 'user', 'id': 'steer-id'}
    rows[0]['metadata'] = {'amplifier_input': marker}
    source['messages'] = [display_message(row, i, source) for i, row in enumerate(rows)]
    source['messages'][0]['inputId'] = 'steer-id'
    if change == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif change == 'different-id':
        marker['id'] = 'changed-id'
    elif change == 'service':
        marker['kind'] = 'service'
    elif change == 'malformed-kind':
        marker['kind'] = ['steer']
    else:
        marker['version'] = 99
    store.save('original', rows, {}, preserve_system=True)
    original = copy.deepcopy(source)
    path = store.directory('original') / 'transcript.jsonl'
    before = path.read_bytes()
    with pytest.raises(ValueError, match='transcript changed'):
        fork_session(tmp_path, source, 'fork', before_message_id=source['messages'][2]['id'])
    assert not store.directory('fork').exists()
    assert not (tmp_path / 'sessions' / 'fork').exists()
    assert source == original and path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['turn1', 'turn2'])
@pytest.mark.parametrize('grouped', [False, True])
async def test_actual_protocol_edit_before_or_after_saved_steering(tmp_path, target, grouped):
    from client import Client
    from test_session_lifecycle import prepared, prompt, action, initialize
    client, sid, cwd, config = await prepared(tmp_path)
    try:
        if grouped:
            await prompt(client, sid, 'Original root request', 'root')
        await prompt(client, sid, 'Original steering request', 'turn1')
        await prompt(client, sid, 'Original later request', 'turn2')
        await client.close()
        # Seed imported steering only after its writer is closed; all subsequent
        # operations use the public protocol and actual isolated worker.
        native = next((tmp_path / 'home/projects').glob('*/sessions/' + sid))
        transcript = native / 'transcript.jsonl'
        rows = [json.loads(line) for line in transcript.read_text().splitlines()]
        original = next(row for row in rows if (row.get('metadata') or {}).get('amplifier_input', {}).get('id') == 'turn1')
        original['metadata']['amplifier_input'].update(kind='steer', target_generation_id='prior-generation')
        if grouped:
            original['metadata'].update(inputOrigin='steering', activeInputId='root')
        transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        events = native / 'events.jsonl'
        events.write_text('Retained original event history\n')
        client = await Client.launch(config)
        await initialize(client)
        resumed = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        revision = (await action(client, sid, cwd, 'inspect'))['result']['historyRevision']
        prefix = await client.request('_amplifier/history/before', {'sessionId': sid, 'cwd': str(cwd), 'beforeTurnId': target, 'expectedRevision': revision, 'limit': 50, 'includeEditContext': True})
        assert 'result' in prefix, prefix
        expected_retained = next(reversed(prefix['result']['turnGroups']), {}).get('id')
        params = {'sessionId': sid, 'operation': 'history.revise', 'args': {
            'operationId': 'edit-once', 'inputId': 'replacement-once',
            'targetMessageId': target, 'expectedHistoryRevision': revision,
            'text': 'Explicit revised request'}}
        result = await client.request('_amplifier/native', params)
        assert result.get('result', {}).get('stopReason') == 'end_turn', result
        proof = next(row['params']['event'] for row in client.events if row.get('params', {}).get('event', {}).get('type') == 'history.revised')
        assert proof['retainedThroughTurnId'] == expected_retained
        saved = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert any(row.get('content') == 'Explicit revised request' for row in saved)
        assert not any(row.get('content') == 'Original later request' for row in saved)
        assert (original in saved) == (target == 'turn2')
        duplicate = await client.request('_amplifier/native', params)
        assert duplicate['error']['data']['outcome'] == 'already-admitted'
        assert events.read_text() == 'Retained original event history\n'
        receipt = await client.request('_amplifier/history/edit', {'sessionId': sid, 'cwd': str(cwd), 'operationId': 'edit-once'})
        assert receipt['result']['receipt']['status'] == 'admitted'
    finally:
        await client.close()
