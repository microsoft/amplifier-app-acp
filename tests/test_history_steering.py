"""Recorded steering remains within its original turn, with exact row references."""
import json
import pytest
from amplifier_acp.history import page, forward, message_id

ORIGINAL = 'fae0e058-e6ef-4f41-9717-a9c3dd2cd95e'
STEER = 'bd01da78-5019-4c83-b2fc-020fc6bb98de'
QUEUE = 'b6f95283-92e4-4a7f-b424-44353fbcd66d'

def user(identity, text, **facts):
    marker = {'version': 1, 'kind': 'steer' if identity == STEER else 'user', 'id': identity, 'source': 'user'}
    if identity == STEER:
        marker['target_generation_id'] = '2e6302dd-2445-4b9d-a443-209c3687e4ce'
    return {'role': 'user', 'content': text, 'metadata': {'amplifier_input': marker, 'amplifier_public_message': {'version': 1, 'inputId': identity, 'capture': 'host-standard', 'blocks': [{'type': 'text', 'text': text}]}, **facts}}

def fixture(tmp_path, target=ORIGINAL):
    rows = [user(ORIGINAL, 'Run checkpoint then welcome', _seq=1),
            {'role': 'assistant', 'content': 'I will run the checkpoint', 'tool_calls': [{'id': 'checkpoint', 'function': {'name': 'shell', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'checkpoint', 'content': 'checkpoint done'},
            user(STEER, 'One short sentence; mention a document', inputOrigin='steering', activeInputId=target, _seq=4),
            {'role': 'assistant', 'content': 'Welcome; bring a document.'},
            user(QUEUE, 'Suggest four-word title', _seq=6),
            {'role': 'assistant', 'content': 'Amplifier Unified Welcome Checkpoint'}]
    data = b''.join((json.dumps(row)+'\n').encode() for row in rows)
    path = tmp_path/'transcript.jsonl';path.write_bytes(data)
    return rows, data, path

def test_recorded_steering_groups_page_and_forward_without_rewriting(tmp_path):
    rows, data, path = fixture(tmp_path)
    result = page(tmp_path, include_public_rows=True)
    assert [g['id'] for g in result['turnGroups']] == [ORIGINAL, QUEUE]
    assert all(g['state'] == 'unknown' for g in result['turnGroups'])  # No execution outcome is invented.
    projected = result['updates']
    assert [u['_meta']['amplifier.dev/history']['turnId'] for u in projected] == [ORIGINAL]*6+[QUEUE]*2
    assert list(forward(tmp_path)) == [{**u, '_meta': {'amplifier.dev/history': {k:v for k,v in u['_meta']['amplifier.dev/history'].items() if k!='rowLocator'}}} for u in projected]
    steering = next(u for u in projected if u['_meta']['amplifier.dev/history'].get('nativeInput', {}).get('id') == STEER)
    assert steering['_meta']['amplifier.dev/history']['activeInputId'] == ORIGINAL
    position = 0
    for row in rows:
        matching = [u for u in projected if u['_meta']['amplifier.dev/history']['position'] == position]
        assert all(u['_meta']['amplifier.dev/history']['messageId'] == message_id(row, position) for u in matching)
        position += len((json.dumps(row)+'\n').encode())
    newest = page(tmp_path, limit=1)
    assert [g['id'] for g in newest['turnGroups']] == [QUEUE]
    previous = page(tmp_path, cursor=newest['nextCursor'], limit=1)
    assert [g['id'] for g in previous['turnGroups']] == [ORIGINAL]
    assert len(previous['updates']) == 6
    assert previous['nextCursor'] is None
    assert path.read_bytes() == data

@pytest.mark.parametrize('target', [None, '', 'unrelated-input', 3, 'x'*8193])
def test_unassociated_steering_preserves_unknown_and_paging_bound(tmp_path, target):
    _, data, path = fixture(tmp_path, target)
    complete = page(tmp_path)
    assert [g['id'] for g in complete['turnGroups']] == [ORIGINAL, STEER, QUEUE]
    cursor = None; ids = []
    while True:
        result = page(tmp_path, cursor=cursor, limit=1)
        assert len(result['turnGroups']) == 1
        assert result['turnGroups'][0]['state'] == 'unknown'
        ids[:0] = [result['turnGroups'][0]['id']]
        cursor = result['nextCursor']
        if cursor is None: break
    assert ids == [ORIGINAL, STEER, QUEUE]
    assert path.read_bytes() == data

def test_orphan_steering_does_not_invent_original_turn(tmp_path):
    row = user(STEER, 'Preserved orphan', inputOrigin='steering', activeInputId=ORIGINAL)
    (tmp_path/'transcript.jsonl').write_text(json.dumps(row)+'\n')
    group = page(tmp_path, limit=1)['turnGroups'][0]
    assert group['id'] == STEER and group['state'] == 'unknown'

def test_recorded_peer_steering_keeps_origin_and_original_turn(tmp_path):
    rows, _, path = fixture(tmp_path)
    rows[3]['metadata'].update(inputOrigin='peer', peerEnvelope={'mode':'steer','requestId':STEER})
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    result = page(tmp_path)
    assert [g['id'] for g in result['turnGroups']] == [ORIGINAL, QUEUE]
    projected = next(u['_meta']['amplifier.dev/history'] for u in result['updates']
                     if u['_meta']['amplifier.dev/history'].get('nativeInput',{}).get('id') == STEER)
    assert projected['inputOrigin'] == 'peer' and projected['activeInputId'] == ORIGINAL
    rows[3]['metadata']['peerEnvelope']['mode'] = 'queue'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    assert [g['id'] for g in page(tmp_path)['turnGroups']] == [ORIGINAL, STEER, QUEUE]

@pytest.mark.parametrize('field,value', [('inputOrigin', 'unknown'), ('kind', 'user')])
def test_conflicting_recorded_steering_facts_never_join(tmp_path, field, value):
    rows, _, path = fixture(tmp_path)
    if field == 'kind': rows[3]['metadata']['amplifier_input']['kind'] = value
    else: rows[3]['metadata'][field] = value
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    assert [g['id'] for g in page(tmp_path)['turnGroups']] == [ORIGINAL, STEER, QUEUE]

@pytest.mark.asyncio
async def test_actual_source_acp_passive_page_retains_original_and_steering_locators(tmp_path):
    import asyncio
    import sys
    from pathlib import Path
    from client import Client
    from amplifier_acp.native.session_files import project_slug
    cwd = tmp_path/'workspace';cwd.mkdir()
    sid = '612cd35e-81ab-4877-9ba4-2559b6226295'
    home = tmp_path/'native-home';app = tmp_path/'native-app'
    directory = home/'projects'/project_slug(str(cwd))/'sessions'/sid;directory.mkdir(parents=True)
    _, data, path = fixture(directory)
    config = tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(app),'workerCommand':['/must-not-start-for-passive-history']}))
    # Use the candidate source with existing dependencies, without installing it
    # into or changing the qualification runtime.
    source = str(Path(__file__).resolve().parents[1]/'src')
    bootstrap = f'import sys,runpy;sys.path.insert(0,{source!r});runpy.run_module("amplifier_acp",run_name="__main__")'
    process = await asyncio.create_subprocess_exec(sys.executable,'-I','-B','-c',bootstrap,'--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=1048576)
    client = Client(process)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        params = {'sessionId':sid,'cwd':str(cwd),'limit':2,'includePublicRows':True}
        response = await client.request('_amplifier/history',params)
        assert 'error' not in response, response
        result = response['result']
        assert [g['id'] for g in result['turnGroups']] == [ORIGINAL, QUEUE]
        assert len(result['publicRows']) == 3
        steering = next(u for u in result['turnGroups'][0]['updates'] if u['_meta']['amplifier.dev/history'].get('nativeInput',{}).get('id')==STEER)
        locator = steering['_meta']['amplifier.dev/history']['rowLocator']
        exact = await client.request('_amplifier/history',{**params,'rowLocator':locator,'expectedRevision':result['revision']})
        assert 'error' not in exact, exact
        assert 'One short sentence; mention a document' in json.dumps(exact['result'])
        assert path.read_bytes() == data
        assert not any(e.get('params',{}).get('event',{}).get('type')=='runtime.ready' for e in client.events)
    finally:
        assert await client.close() == ''
