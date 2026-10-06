import json
import pytest
from amplifier_acp.history import page,HistoryError

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

def test_exact_old_source_refuses_duplicate_changed_and_oversized_originals(tmp_path):
    from amplifier_acp.history import exact_message, inspect
    p=tmp_path/'session';file=history(p,75)
    (p/'metadata.json').write_text('{}')
    original=file.read_bytes();revision=inspect(p)['revision']
    selected=exact_message(p,'turn-0',revision)
    assert selected['message']['text']=='0' and selected['complete'] and not selected['execution']
    assert selected['message']['_meta']['amplifier.dev/history']['authorization']=='unverified-native-history'
    assert file.read_bytes()==original
    with file.open('a') as out:out.write(json.dumps({'role':'user','content':'duplicate','metadata':{'amplifier_input':{'id':'turn-0'}}})+'\n')
    with pytest.raises(ValueError,match='changed'):exact_message(p,'turn-0',revision)
    with pytest.raises(ValueError,match='ambiguous'):exact_message(p,'turn-0',inspect(p)['revision'])
    file.write_text(json.dumps({'role':'user','content':'x'*65536,'metadata':{'amplifier_input':{'id':'large'}}})+'\n')
    with pytest.raises(ValueError,match='complete-source byte bound'):exact_message(p,'large',inspect(p)['revision'])

@pytest.mark.asyncio
async def test_exact_source_over_stdio_is_not_limited_to_recent_turns_or_a_worker(tmp_path):
    from client import Client
    from amplifier_acp.native.session_files import project_slug
    home=tmp_path/'home';cwd=tmp_path/'cwd';cwd.mkdir();sid='33333333-3333-4333-8333-333333333333'
    directory=home/'projects'/project_slug(str(cwd))/'sessions'/sid;directory.parent.mkdir(parents=True)
    file=history(directory,75);(directory/'metadata.json').write_text(json.dumps({'session_id':sid,'working_dir':str(cwd)}))
    original=file.read_bytes();config=tmp_path/'config.json'
    config.write_text(json.dumps({'home':str(home),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start-for-exact-history']}))
    client=await Client.launch(config)
    try:
        initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['history']['exactMessageRead']['version']==1
        scope={'sessionId':sid,'cwd':str(cwd)}
        revision=(await client.request('_amplifier/history/inspect',scope))['result']['revision']
        result=await client.request('_amplifier/history/message',{**scope,'messageId':'turn-0','expectedRevision':revision})
        assert result['result']['message']['text']=='0' and result['result']['execution'] is False
        assert file.read_bytes()==original and not client.events
        denied=await client.request('_amplifier/history/message',{**scope,'messageId':'turn-0','expectedRevision':'changed'})
        assert 'error' in denied
    finally:await client.close()

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


@pytest.mark.asyncio
@pytest.mark.parametrize('configuration_bytes', [0, 1024 * 1024])
async def test_missing_workspace_is_explicit_exact_passive_history_not_runtime_access(tmp_path, configuration_bytes):
    from client import Client
    from amplifier_acp.native.session_files import project_slug
    home=tmp_path/'home';cwd=tmp_path/'gone';sid='22222222-2222-4222-8222-222222222222'
    directory=home/'projects'/project_slug(str(cwd))/'sessions'/sid;directory.parent.mkdir(parents=True)
    file=history(directory,2);metadata=directory/'metadata.json'
    metadata.write_text(json.dumps({'session_id':sid,'working_dir':str(cwd),'config':{'context':'x' * configuration_bytes}}))
    originals=(file.read_bytes(),metadata.read_bytes())
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start-for-passive-history']}))
    client=await Client.launch(config)
    try:
        initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['history']['missingWorkspaceRead']['execution'] is False
        request={'sessionId':sid,'cwd':str(cwd)}
        assert 'error' in await client.request('_amplifier/history',request)
        stamp=await client.request('_amplifier/history/inspect',{**request,'readOnlyMissingWorkspace':True})
        assert stamp['result']['available']
        result=await client.request('_amplifier/history',{**request,'readOnlyMissingWorkspace':True,'expectedRevision':stamp['result']['revision'],'limit':1})
        assert result['result']['turnGroups'][0]['id']=='turn-1'
        assert 'error' in await client.request('session/load',{**request,'mcpServers':[]})
        assert not cwd.exists() and not client.events
        assert originals==(file.read_bytes(),metadata.read_bytes())
        metadata.write_text(json.dumps({'session_id':sid,'working_dir':str(tmp_path/'different')}))
        assert 'error' in await client.request('_amplifier/history',{**request,'readOnlyMissingWorkspace':True})
        metadata.write_text(json.dumps({'session_id':sid,'working_dir':str(cwd),'config':'x' * (8 * 1024 * 1024)}))
        assert 'error' in await client.request('_amplifier/history',{**request,'readOnlyMissingWorkspace':True})
        metadata.write_text('[]')
        assert 'error' in await client.request('_amplifier/history',{**request,'readOnlyMissingWorkspace':True})
        metadata.write_bytes(originals[1]);cwd.symlink_to(tmp_path/'elsewhere')
        assert 'error' in await client.request('_amplifier/history',{**request,'readOnlyMissingWorkspace':True})
    finally:await client.close()
