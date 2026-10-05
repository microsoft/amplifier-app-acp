import json,uuid
from io import StringIO
from pathlib import Path
import pytest
from amplifier_acp.server import Server,NATIVE
from amplifier_acp.message_metadata import MessageMetadata,reserve_quote
from amplifier_acp.history import page

@pytest.fixture
def selected(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    cwd=tmp_path/'cwd';cwd.mkdir();sid='source'
    server=Server({'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start']},StringIO(),message_principal=lambda identity:'account')
    server.initialized=True;server.extensions={NATIVE}
    directory=server.directory(str(cwd),sid);directory.mkdir(parents=True)
    rows=[{'role':'system','content':'Retained system'},
          {'role':'user','content':'First'},
          {'role':'assistant','content':'Answer','tool_calls':[{'id':'pending','type':'function','function':{'name':'fixture','arguments':'{}'}}]},
          {'role':'user','content':'Later'},
          {'role':'assistant','content':'Later answer'}]
    (directory/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (directory/'metadata.json').write_text(json.dumps({'session_id':sid,'working_dir':str(cwd),'bundle':'fixture-bundle'}))
    updates=page(directory)['updates'];refs={u.get('content',{}).get('text'):u['_meta']['amplifier.dev/history']['messageReference'] for u in updates if u.get('content',{}).get('type')=='text'}
    store=MessageMetadata(tmp_path/'app',sid,actor_id='account')
    for text in ('First','Answer','Later'):
        store.reaction(directory,refs[text],emoji='👍',present=True,command_id='reaction-'+text)
    foreign=MessageMetadata(tmp_path/'app',sid,actor_id='unrelated-account')
    foreign.reaction(directory,refs['First'],emoji='❤️',present=True,command_id='foreign-command')
    reserve_quote(store,directory,refs['Answer'],input_id='unknown-source-input',input_payload={'text':'Uncertain original'})
    return server,cwd,directory,refs,store

async def fork(selected,**changes):
    server,cwd,directory,refs,store=selected
    target=str(uuid.uuid4())
    args={'commandId':'fork-copy','targetSessionId':target,'expectedHistoryRevision':page(directory)['revision'],**changes}
    result=await server.dispatch('_amplifier/session',{'sessionId':'source','cwd':str(cwd),'operation':'fork','args':args})
    return target,args,result

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['complete','recovery'])
async def test_public_fork_preserves_only_same_account_retained_canonical_reactions(selected,mode):
    server,cwd,directory,refs,source=selected
    original=(directory/'transcript.jsonl').read_bytes();db_bytes=source.path.read_bytes()
    original_unknown=source.receipt('unknown-source-input')
    target,args,result=await fork(selected,throughMessageId=refs['First']['messageId'],mode=mode)
    assert result['creationConfirmed'] and result['messageAnnotations']=={'policy':'same-account-retained-canonical','messages':2,'reactions':2,'receiptsCopied':False}
    assert not server.sessions
    target_dir=server.directory(str(cwd),target)
    target_refs={u['content']['text']:u['_meta']['amplifier.dev/history']['messageReference'] for u in page(target_dir)['updates'] if u.get('content',{}).get('type')=='text'}
    copied=MessageMetadata(server.config['appHome'],target,actor_id='account')
    ids=[target_refs[text]['messageId'] for text in ('First','Answer')]
    assert all(item['mine']==['👍'] and item['counts']=={'👍':1} for item in copied.read(ids)['items'])
    assert 'Later' not in target_refs
    assert all(not item['mine'] for item in MessageMetadata(server.config['appHome'],target,actor_id='unrelated-account').read(ids)['items'])
    assert copied.receipt('reaction-First')['receipt'] is None
    assert copied.receipt('unknown-source-input')['receipt'] is None
    assert source.receipt('unknown-source-input')==original_unknown
    assert source.path.read_bytes()==db_bytes and (directory/'transcript.jsonl').read_bytes()==original
    if mode=='recovery':assert ids[0]!=refs['First']['messageId'] or ids[1]!=refs['Answer']['messageId']
    retry=await server.dispatch('_amplifier/session',{'sessionId':'source','cwd':str(cwd),'operation':'fork','args':args})
    assert retry['sessionId']==target and retry['replayed'] is False
    assert copied.read(ids)['items'][0]['revision']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('binding',['absent','different'])
async def test_account_refusal_precedes_target_creation(selected,binding):
    server,cwd,directory,refs,source=selected
    server.message_principal=None if binding=='absent' else lambda identity:'account' if identity=='source' else 'other-account'
    target=str(uuid.uuid4());args={'commandId':'refused','targetSessionId':target,'expectedHistoryRevision':page(directory)['revision']}
    with pytest.raises(ValueError,match='same trusted account'):
        await server.dispatch('_amplifier/session',{'sessionId':'source','cwd':str(cwd),'operation':'fork','args':args})
    assert not server.directory(str(cwd),target).exists()
    assert not (Path(server.config['appHome'])/'sessions'/target).exists()

@pytest.mark.asyncio
async def test_interrupted_annotation_publish_preserves_unknown_without_retry(selected,monkeypatch):
    import amplifier_acp.message_copy as copy
    server,cwd,directory,refs,source=selected
    original=source.receipt('unknown-source-input');target=str(uuid.uuid4())
    args={'commandId':'interrupted','targetSessionId':target,'expectedHistoryRevision':page(directory)['revision']}
    calls=[]
    def interrupted(*args):calls.append(True);raise OSError('fixture interrupted annotation publication')
    monkeypatch.setattr(copy,'publish',interrupted)
    params={'sessionId':'source','cwd':str(cwd),'operation':'fork','args':args}
    with pytest.raises(OSError):await server.dispatch('_amplifier/session',params)
    retry=await server.dispatch('_amplifier/session',params)
    assert retry['receipt']['status']=='unknown' and retry['replayed'] is False and calls==[True]
    from amplifier_acp.session_lifecycle import guard_start
    with pytest.raises(ValueError,match='unresolved'):guard_start(server,target,str(cwd))
    assert source.receipt('unknown-source-input')==original

@pytest.mark.asyncio
async def test_copy_without_annotations_creates_no_adjunct(selected):
    server,cwd,directory,refs,source=selected
    with source._db(create=True) as db:db.execute('DELETE FROM annotations')
    target,args,result=await fork(selected)
    assert result['messageAnnotations']['messages']==0
    assert not MessageMetadata(server.config['appHome'],target,actor_id='account').path.exists()

@pytest.mark.asyncio
async def test_more_than_fifty_annotated_rows_copy_in_bounded_batches(selected):
    server,cwd,directory,refs,source=selected
    rows=[{'role':'user','content':'Repeated identical row'} for _ in range(51)]
    (directory/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    updates=page(directory,limit=100)['updates']
    for index,update in enumerate(updates):
        source.reaction(directory,update['_meta']['amplifier.dev/history']['messageReference'],emoji='✅',present=True,command_id='many-'+str(index))
    target,args,result=await fork(selected)
    assert result['messageAnnotations']['messages']==51
    target_updates=page(server.directory(str(cwd),target),limit=100)['updates']
    copied=MessageMetadata(server.config['appHome'],target,actor_id='account')
    ids=[row['_meta']['amplifier.dev/history']['messageReference']['messageId'] for row in target_updates]
    assert len(set(ids))==51
    for start in range(0,len(ids),50):
        assert all(row['mine']==['✅'] for row in copied.read(ids[start:start+50])['items'])

@pytest.mark.asyncio
async def test_real_annotation_byte_budget_refuses_before_target(selected,monkeypatch):
    import amplifier_acp.message_copy as copy
    server,cwd,directory,refs,source=selected
    monkeypatch.setattr(copy,'MAX_COPY_BYTES',128)
    target=str(uuid.uuid4())
    with pytest.raises(ValueError,match='one MiB'):
        await server.dispatch('_amplifier/session',{'sessionId':'source','cwd':str(cwd),'operation':'fork','args':{'commandId':'budget','targetSessionId':target,'expectedHistoryRevision':page(directory)['revision']}})
    assert not server.directory(str(cwd),target).exists()
    assert not (Path(server.config['appHome'])/'sessions'/target).exists()

@pytest.mark.asyncio
async def test_persisted_target_mismatch_is_unknown_and_does_not_copy_reactions(selected,monkeypatch):
    from amplifier_foundation.session.history import SessionHistoryStore
    server,cwd,directory,refs,source=selected
    original=SessionHistoryStore.save
    def altered(store,rows,*args,**kwargs):
        rows=[{**row,'content':'Fixture changed persisted row'} if row.get('role')=='user' else row for row in rows]
        return original(store,rows,*args,**kwargs)
    monkeypatch.setattr(SessionHistoryStore,'save',altered)
    target=str(uuid.uuid4());args={'commandId':'mismatch','targetSessionId':target,'expectedHistoryRevision':page(directory)['revision']}
    params={'sessionId':'source','cwd':str(cwd),'operation':'fork','args':args}
    with pytest.raises(ValueError,match='Persisted target rows differ from prepared copy'):await server.dispatch('_amplifier/session',params)
    assert not MessageMetadata(server.config['appHome'],target,actor_id='account').path.exists()
    assert (await server.dispatch('_amplifier/session',params))['receipt']['status']=='unknown'

@pytest.mark.asyncio
async def test_bundle_change_remaps_retained_indices_after_system_removal(selected):
    from amplifier_acp.configuration_clone import fingerprint
    server,cwd,directory,refs,source=selected
    server.config['forkBundles']={'reviewed':'fixture-bundle'}
    target,args,result=await fork(selected,bundle='reviewed',bundleRevision=fingerprint(['reviewed','fixture-bundle']))
    rows=[json.loads(line) for line in (server.directory(str(cwd),target)/'transcript.jsonl').read_text().splitlines()]
    assert rows[0]['role']=='user' and not any(row['role']=='system' for row in rows)
    target_updates=page(server.directory(str(cwd),target))['updates']
    ids=[row['_meta']['amplifier.dev/history']['messageReference']['messageId'] for row in target_updates if row.get('content',{}).get('text') in {'First','Answer','Later'}]
    assert all(row['mine']==['👍'] for row in MessageMetadata(server.config['appHome'],target,actor_id='account').read(ids)['items'])
    assert result['messageAnnotations']['messages']==3
