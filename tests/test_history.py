import json
import pytest
from amplifier_acp.history import page,HistoryError


@pytest.mark.parametrize('visibility', ['internal', 'developer'])
@pytest.mark.parametrize('image', [
    {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'cHJpdmF0ZQ=='}},
    {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid/private.png'}},
    {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/private.png'}},
])
def test_private_images_do_not_enter_public_history(visibility, image):
    import copy
    from amplifier_acp.history import content_updates
    row = {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'First public text'},
        {**image, 'visibility': visibility},
        {'type': 'text', 'text': 'Last public text'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'cHVibGlj'}},
    ]}
    original = copy.deepcopy(row)
    updates, diagnostics = content_updates(row, 3)
    assert [update['content'] for update in updates] == [
        {'type': 'text', 'text': 'First public text'},
        {'type': 'text', 'text': 'Last public text'},
        {'type': 'image', 'data': 'cHVibGlj', 'mimeType': 'image/png'},
    ]
    assert diagnostics == [] and row == original


@pytest.mark.parametrize('visibility', [None, 'user'])
@pytest.mark.parametrize('image, expected', [
    ({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'YWJj'}},
     {'type': 'image', 'data': 'YWJj', 'mimeType': 'image/png'}),
    ({'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid/public.png'}},
     {'type': 'resource_link', 'uri': 'https://example.invalid/public.png', 'name': 'Image'}),
    ({'type': 'image_url', 'image_url': {'url': 'https://example.invalid/public.png'}},
     {'type': 'resource_link', 'uri': 'https://example.invalid/public.png', 'name': 'Image'}),
])
def test_public_image_projection_stays_exact(visibility, image, expected):
    from amplifier_acp.history import content_updates
    updates, diagnostics = content_updates({'role': 'user', 'content': [{**image, 'visibility': visibility}]}, 4)
    assert len(updates) == 1 and updates[0]['sessionUpdate'] == 'user_message_chunk'
    assert updates[0]['content'] == expected and diagnostics == []


def test_retained_public_original_wins_over_private_model_image():
    import copy
    from amplifier_acp.history import content_updates
    original = {'version': 1, 'inputId': 'input', 'capture': 'host-standard', 'blocks': [
        {'type': 'text', 'text': 'Literal original'},
        {'type': 'attachment', 'reference': {'owner': 'resources', 'id': 'resource://original-owner/picture'},
         'name': 'Original picture', 'mimeType': 'image/png'},
    ]}
    row = {'role': 'user', 'metadata': {
        'amplifier_input': {'version': 1, 'kind': 'user', 'source': 'user', 'id': 'input'},
        'amplifier_public_message': original,
    }, 'content': [{'type': 'image', 'visibility': 'internal', 'source': {'type': 'base64', 'data': 'cHJpdmF0ZQ=='}}]}
    before = copy.deepcopy(row)
    updates, diagnostics = content_updates(row, 0)
    assert [update['content'] for update in updates] == [
        {'type': 'text', 'text': 'Literal original'},
        {'type': 'resource_link', 'uri': 'resource://original-owner/picture', 'name': 'Original picture', 'mimeType': 'image/png'},
    ]
    assert diagnostics == [] and row == before

def history(path,count=7):
    path.mkdir();file=path/'transcript.jsonl'
    with file.open('w') as out:
        for i in range(count):
            for row in [{'role':'user','content':str(i),'metadata':{'amplifier_input':{'id':'turn-'+str(i)}}},
                        {'role':'assistant','content':'', 'tool_calls':[{'id':'t-'+str(i),'function':{'name':'read','arguments':'{}'}}]},
                        {'role':'tool','tool_call_id':'t-'+str(i),'content':'tool result'},
                        {'role':'assistant','content':'reply '+str(i)}]:out.write(json.dumps(row)+'\n')
    return file

def test_tail_pages_preserve_whole_turns_and_stable_ids(tmp_path):
    p=tmp_path/'session';history(p)
    a=page(p,limit=2);assert [r['id'] for r in a['turnGroups']]==['turn-5','turn-6']
    assert all(len(r['updates'])==4 for r in a['turnGroups'])
    b=page(p,cursor=a['nextCursor'],limit=2);assert [r['id'] for r in b['turnGroups']]==['turn-3','turn-4']
    c=page(p,cursor=b['nextCursor'],limit=2);d=page(p,cursor=c['nextCursor'],limit=2)
    assert [r['id'] for r in d['turnGroups']]==['turn-0'];assert d['nextCursor'] is None
    assert all(u['_meta']['amplifier.dev/history']['turnId']=='turn-5' for u in a['turnGroups'][0]['updates'])

def test_cursor_rejects_changed_history_and_traversal(tmp_path):
    p=tmp_path/'session';file=history(p);a=page(p,limit=1)
    with file.open('a') as out:out.write('{"role":"user","content":"later"}\n')
    with pytest.raises(HistoryError,match='changed'):page(p,cursor=a['nextCursor'])
    with pytest.raises(HistoryError,match='cursor'):page(p,cursor='../../anything')

def test_no_events_or_worker_needed_and_bytes_bounded(tmp_path):
    p=tmp_path/'session';history(p,2000);(p/'events.jsonl').symlink_to('/not/readable')
    a=page(p,limit=1,max_bytes=100_000);assert len(a['turnGroups'])==1
    (p/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'x'*2100000})+'\n')
    with pytest.raises(HistoryError,match='oversized'):page(p,limit=1)

def test_backup_and_malformed_records_are_explicit(tmp_path):
    p=tmp_path/'session';file=history(p);file.rename(p/'transcript.jsonl.backup')
    assert page(p)['diagnostics'][0]['code']=='backup_read'
    file.write_text('not-json\n')
    with pytest.raises(HistoryError,match='malformed'):page(p)

def test_passive_revision_never_reads_history_and_metadata_never_grants_authority(tmp_path,monkeypatch):
    from amplifier_acp.history import inspect
    p=tmp_path/'session';file=history(p,1)
    row={'role':'user','content':'scheduled content','metadata':{'amplifier_input':{'id':'stable','kind':'user','source':'user'},'inputOrigin':'schedule','scheduledRunId':'run1','questionId':'question1','createdAt':123}}
    file.write_text(json.dumps(row)+'\n')
    with monkeypatch.context() as patch:
        patch.setattr(type(file),'open',lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError('passive inspect read contents')))
        revision=inspect(p)['revision']
    result=page(p,expected_revision=revision);meta=result['updates'][0]['_meta']['amplifier.dev/history']
    assert meta['scheduledRunId']=='run1' and meta['questionId']=='question1' and meta['nativeInput']['id']=='stable'
    assert meta['authorization']=='unverified-native-history'
    assert meta['inputOrigin']=='schedule' and meta['createdAt']==123
    file.write_text(json.dumps({'role':'user','content':'legacy'})+'\n')
    with pytest.raises(HistoryError,match='changed'):page(p,expected_revision=revision)
    assert page(p)['updates'][0]['_meta']['amplifier.dev/history']['inputOrigin']=='unknown'
    assert inspect(tmp_path/'missing')['available'] is False


@pytest.mark.asyncio
async def test_passive_inspection_over_stdio_never_launches_configured_worker(tmp_path):
    from client import Client
    from amplifier_acp.native.session_files import project_slug
    home=tmp_path/'home';cwd=tmp_path/'cwd';cwd.mkdir();sid='11111111-1111-4111-8111-111111111111'
    directory=home/'projects'/project_slug(str(cwd))/'sessions'/sid;directory.parent.mkdir(parents=True)
    history(directory,1)
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'workerCommand':['/must-not-start-for-passive-history']}))
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        stamp=await client.request('_amplifier/history/inspect',{'sessionId':sid,'cwd':str(cwd)})
        assert stamp['result']['available'] and len(stamp['result']['revision'])==64
        result=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'expectedRevision':stamp['result']['revision'],'limit':1})
        assert result['result']['turnGroups'][0]['id']=='turn-0'
        assert not any(row.get('params',{}).get('event',{}).get('type')=='runtime.ready' for row in client.events)
    finally:await client.close()
