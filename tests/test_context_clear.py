"""Reviewed clears cross actual native checkpoints; no provider invocation."""
import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path

import jsonschema
import pytest
import yaml

from client import Client
from native_setup import configuration
from amplifier_acp.native.history_revision import _command


async def initialize(client):
    result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert result['result']['agentCapabilities']['_meta']['amplifier.dev/native']['contextClear']['version']==1


async def control(client,sid,operation,args=None):
    return await client.request('_amplifier/native',{'sessionId':sid,'operation':operation,'args':args or {}})


def schema(name,definition=None):
    value=json.loads((Path(__file__).parents[1]/'schemas'/name).read_text())
    return value['$defs'][definition] if definition else value


def seeded(tmp_path,**provider_config):
    config,cwd=configuration(tmp_path)
    values=json.loads(config.read_text());audit=tmp_path/'provider-calls'
    bundle=Path(values['bundle']);plan=yaml.safe_load(bundle.read_text())
    plan['providers'][0]['config']={'selectedModelAudit':str(audit),**provider_config};bundle.write_text(yaml.safe_dump(plan))
    sid=str(uuid.uuid4())
    from amplifier_acp.native.session_files import project_slug
    directory=Path(values['home'])/'projects'/project_slug(str(cwd))/'sessions'/sid
    from amplifier_foundation.session.history import SessionHistoryStore
    rows=[{'role':'user','content':'identical original'},
          {'role':'assistant','content':'retained answer','tool_calls':[{'id':'t1','function':{'name':'read','arguments':'{}'}}]},
          {'role':'tool','tool_call_id':'t1','content':'raw tool evidence'},
          {'role':'assistant','content':'finished'}]
    SessionHistoryStore(directory).save(rows,{'session_id':sid,'working_dir':str(cwd),'bundle':str(bundle)})
    (directory/'events.jsonl').write_bytes(b'{"retained":"all historical events"}\n')
    return config,cwd,sid,directory,rows,audit


async def opened(config,cwd,sid):
    client=await Client.launch(config);await initialize(client)
    resumed=await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
    assert 'result' in resumed,resumed
    return client


async def reviewed(client,sid,command):
    value=await control(client,sid,'context.clear.review');assert 'result' in value,value
    review=value['result'];jsonschema.validate(review,schema('context-clear.v1.json','reviewResult'))
    return review,{'commandId':command,'expectedHistoryRevision':review['historyRevision'],'expectedControlRevision':review['controlRevision']}


async def receipt(client,sid,cwd,command):
    result=await client.request('_amplifier/context/receipt',{'sessionId':sid,'cwd':str(cwd),'commandId':command})
    assert 'result' in result,result
    jsonschema.validate(result['result'],schema('context-clear-receipt.v1.json'))
    return result['result']['receipt']


async def pages(client,sid,cwd,limit=1):
    result=[];cursor=None
    for _ in range(50):
        response=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':limit,**({'cursor':cursor} if cursor else {})})
        assert 'result' in response,response
        page=response['result'];jsonschema.validate(page,schema('native-history-result.v1.json'))
        assert len(page['turnGroups'])<=limit
        result.extend(page['turnGroups']);cursor=page['nextCursor']
        if not cursor:return result
    raise AssertionError('Unbounded history cursor')


@pytest.mark.asyncio
async def test_retained_memory_source_survives_actual_clear_without_execution_or_new_authority(tmp_path):
    config,cwd,sid,directory,rows,audit=seeded(tmp_path)
    rows[0]['metadata']={'inputOrigin':'ui','amplifier_input':{'version':1,'kind':'user','source':'user','id':'saved-memory-source'}}
    history=directory/'transcript.jsonl'
    history.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    original=history.read_bytes()
    client=await opened(config,cwd,sid)
    try:
        _,args=await reviewed(client,sid,'clear-memory')
        cleared=await control(client,sid,'context.clear',args)
        assert 'result' in cleared,cleared
        archive_id=cleared['result']['archiveId']
        assert history.read_bytes()==b''
    finally: await client.close()
    values=json.loads(config.read_text());values['workerCommand']=['/must-not-start-for-memory-source'];config.write_text(json.dumps(values))
    cold=await Client.launch(config);await initialize(cold)
    async def read(identity='saved-memory-source'):
        revision=(await cold.request('_amplifier/history/inspect',{'sessionId':sid,'cwd':str(cwd)}))['result']['revision']
        return await cold.request('_amplifier/history/message',{'sessionId':sid,'cwd':str(cwd),'messageId':identity,'expectedRevision':revision})
    try:
        source=await read()
        assert 'result' in source,source
        result=source['result'];message=result['message'];meta=message['_meta']['amplifier.dev/history']
        assert message['id']=='saved-memory-source' and message['text']=='identical original'
        assert result['execution'] is False and meta['historicalOnly'] is True and meta['mutationAuthority']=='none'
        assert meta['preservedSource']=={'kind':'context-clear','archiveId':archive_id,'sha256':hashlib.sha256(original).hexdigest(),'complete':True}
        assert 'error' in await read('archive:'+archive_id+':saved-memory-source')
        revisions=Path(values['appHome'])/'sessions'/sid/'history-revisions'
        receipt_file=revisions/('context-'+archive_id+'.json')
        receipt_bytes=receipt_file.read_bytes()
        for key, value in [('status','unknown'),('scope',{'sessionId':sid,'historyCwd':'/another/workspace'})]:
            changed=json.loads(receipt_bytes);changed[key]=value;receipt_file.write_text(json.dumps(changed))
            assert 'error' in await read()
            receipt_file.write_bytes(receipt_bytes)
        descriptor_file=revisions/('context-'+archive_id)/'segment.json'
        descriptor_bytes=descriptor_file.read_bytes();changed=json.loads(descriptor_bytes)
        changed.update(previousArchive=archive_id,sourceEpoch=archive_id);descriptor_file.write_text(json.dumps(changed))
        assert 'error' in await read()
        descriptor_file.write_bytes(descriptor_bytes)
        # The same identity in the active segment must never shadow the original.
        history.write_text(json.dumps(rows[0])+'\n')
        assert 'error' in await read()
        history.write_bytes(b'')
        archive=Path(values['appHome'])/'sessions'/sid/'history-revisions'/('context-'+archive_id)/'transcript.jsonl'
        saved=archive.read_bytes();archive.write_bytes(saved.replace(b'identical',b'different'))
        assert 'error' in await read()
        archive.write_bytes(saved)
        assert 'result' in await read()
        assert history.read_bytes()==b'' and not audit.exists() and not cold.events
    finally: await cold.close()


@pytest.mark.asyncio
async def test_actual_clear_preserves_exact_bytes_goal_and_cold_receipt_after_lost_reply(tmp_path):
    boundary=tmp_path/'clear-boundary'
    config,cwd,sid,directory,_,audit=seeded(tmp_path,contextClearAudit=str(boundary))
    client=await opened(config,cwd,sid)
    try:
        assert (await control(client,sid,'goals.set',{'condition':'explicit fixture goal','maxTurns':2}))['result']['goal']
        before=(directory/'transcript.jsonl').read_bytes();events=(directory/'events.jsonl').read_bytes()
        review,args=await reviewed(client,sid,'clear-1');assert review['goalPresent'] and review['canClear']
        # Send once, deliberately register no response future: the reply is lost
        # to this consumer even though the native owner finishes durably.
        await client.send({'jsonrpc':'2.0','id':9000,'method':'_amplifier/native','params':{'sessionId':sid,'operation':'context.clear','args':args}})
        saved=None
        async with asyncio.timeout(15):
            while not saved or saved['status']!='succeeded':
                saved=await receipt(client,sid,cwd,'clear-1')
                if not saved or saved['status']!='succeeded':await asyncio.sleep(.01)
        result=saved['result'];jsonschema.validate(result,schema('context-clear.v1.json','applyResult'))
        assert result['goalCleared'] and (directory/'transcript.jsonl').read_bytes()==b''
        archive=tmp_path/'app/sessions'/sid/'history-revisions'/('context-'+result['archiveId'])/'transcript.jsonl'
        assert archive.read_bytes()==before and (directory/'events.jsonl').read_bytes()==events
        assert (archive.parent/'metadata.json').read_bytes()==(boundary/'metadata.json').read_bytes()
        assert archive.read_bytes()==(boundary/'transcript.jsonl').read_bytes()
        assert (await control(client,sid,'usage.inspect'))['result']['messages']==0
        assert (await control(client,sid,'goals.get'))['result']['goal'] is None
        retained=await pages(client,sid,cwd);assert len(retained)==1 and retained[0]['historicalOnly']
        duplicate=await control(client,sid,'context.clear',args);assert duplicate['result']==result
        conflict=await control(client,sid,'context.clear',{**args,'expectedControlRevision':'0'*64})
        assert conflict['error']['data']['executed'] is False
    finally:await client.close()
    cold_config=json.loads(config.read_text());cold_config['workerCommand']=['/must-not-start-for-context-receipt'];config.write_text(json.dumps(cold_config))
    cold=await Client.launch(config);await initialize(cold)
    try:
        assert await receipt(cold,sid,cwd,'clear-1')==saved
        assert await pages(cold,sid,cwd)==retained
        other=tmp_path/'other-workspace';other.mkdir()
        wrong=await cold.request('_amplifier/context/receipt',{'sessionId':sid,'cwd':str(other),'commandId':'clear-1'})
        assert wrong['error']['data']['executed'] is False
        assert not cold.events
    finally:await cold.close()
    cold_config.pop('workerCommand');config.write_text(json.dumps(cold_config))
    resumed=await opened(config,cwd,sid)
    try:
        assert (await control(resumed,sid,'usage.inspect'))['result']['messages']==0
        assert (await control(resumed,sid,'goals.get'))['result']['goal'] is None
        assert await pages(resumed,sid,cwd)==retained
        loaded=await resumed.request('session/load',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert 'result' in loaded,loaded
        restored=[r['params']['update'] for r in resumed.events if r.get('method')=='session/update' and r.get('params',{}).get('update',{}).get('_meta',{}).get('amplifier.dev/history',{}).get('historicalOnly')]
        assert restored and restored[0]['_meta']['amplifier.dev/history']['turnId']==retained[0]['id']
        assert (await control(resumed,sid,'usage.inspect'))['result']['messages']==0
    finally:await resumed.close()
    assert not audit.exists()


@pytest.mark.asyncio
async def test_review_stale_goal_task_guard_and_legacy_no_effect(tmp_path):
    config,cwd,sid,directory,_,audit=seeded(tmp_path);client=await opened(config,cwd,sid)
    try:
        before=(directory/'transcript.jsonl').read_bytes()
        legacy=await control(client,sid,'context.clear');assert legacy['error']['data']['executed'] is False
        _,args=await reviewed(client,sid,'stale')
        await control(client,sid,'goals.set',{'condition':'newer goal'})
        refused=await control(client,sid,'context.clear',args)
        assert refused['error']['data']=={'reason':'context-clear-stale-review','replayed':False,'executed':False}
        assert (await receipt(client,sid,cwd,'stale'))['status']=='refused'
        created=await control(client,sid,'task.create',{'objective':'keep task','commandId':'task1','expectedRevision':0})
        assert 'result' in created,created
        review,blocked=await reviewed(client,sid,'task-blocked')
        assert not review['canClear'] and 'Pause' in review['reason']
        assert (await control(client,sid,'context.clear',blocked))['error']['data']['executed'] is False
        assert (directory/'transcript.jsonl').read_bytes()==before and not audit.exists()
        task=created['result']['task']
        paused=await control(client,sid,'task.pause',{'commandId':'pause-task','expectedRevision':task['revision']})
        assert 'result' in paused,paused
        review,allowed=await reviewed(client,sid,'paused-clear');assert review['canClear'] and review['task']['status']=='paused'
        assert (await control(client,sid,'context.clear',allowed))['result']['cleared']
        assert (await control(client,sid,'task.get'))['result']['task']['status']=='paused'
    finally:await client.close()


@pytest.mark.asyncio
async def test_voice_epoch_identity_and_selected_history_archive_coverage(tmp_path):
    import base64,io,tarfile
    from test_maintenance import admin,initialized
    from test_archive_plan import plan
    config,cwd,sid,directory,_,audit=seeded(tmp_path)
    values=json.loads(config.read_text());values.update(adminWorkspaceRoots=[str(cwd)],adminMaintenance=True,maintenanceExternalWriters='stopped')
    config.write_text(json.dumps(values));client=await opened(config,cwd,sid)
    try:
        voice={'callId':'same-call','itemId':'same-item','role':'user','text':'same spoken words','append':False}
        first=(await control(client,sid,'voice.transcript.record',{**voice,'commandId':'spoken-1'}))['result']
        before=(directory/'transcript.jsonl').read_bytes()
        _,args=await reviewed(client,sid,'voice-clear')
        cleared=(await control(client,sid,'context.clear',args))['result']
        second=(await control(client,sid,'voice.transcript.record',{**voice,'commandId':'spoken-2'}))['result']
        assert first['historyTurnId']!=second['historyTurnId'] and first['nativeMessageId']!=second['nativeMessageId']
        groups=await pages(client,sid,cwd)
        assert groups[0]['id']==second['historyTurnId']
        assert any(row['id']==first['historyTurnId'] and row['historicalOnly'] for row in groups)
    finally:await client.close()
    values['workerCommand']=['/impossible/archive-worker'];config.write_text(json.dumps(values))
    archive,_=await initialized(config);call=admin(archive,cwd)
    try:
        # Both selected archive APIs include preservation even without the
        # separately selectable configuration/control-state tree.
        legacy=await call('maintenance.preview',{'sessions':[{'nativeSessionId':sid,'cwd':str(cwd)}],'parts':['session-history'],'privateContentReviewed':True})
        assert len(legacy['inventory']['files'])>=4
        preview=await plan(call,sessions=[{'nativeSessionId':sid,'cwd':str(cwd)}])
        held=await call('maintenance.acquire',{'commandId':'archive-held','previewHash':preview['previewHash']})
        captured=await call('maintenance.snapshot',{'commandId':'archive-context','previewHash':preview['previewHash'],'leaseId':held['leaseId']})
        body=bytearray();offset=0
        while True:
            chunk=await call('maintenance.artifact.read',{'artifactId':captured['artifactId'],'sha256':captured['sha256'],'offset':offset,'maxBytes':65536})
            body.extend(base64.b64decode(chunk['data']))
            if chunk['nextOffset'] is None:break
            offset=chunk['nextOffset']
        with tarfile.open(fileobj=io.BytesIO(body)) as tar:
            prefix=f'history-revisions/{sid}/'
            assert tar.extractfile(prefix+'context-'+cleared['archiveId']+'/transcript.jsonl').read()==before
            assert json.load(tar.extractfile(prefix+'context-'+cleared['archiveId']+'.json'))['status']=='succeeded'
            assert json.load(tar.extractfile(prefix+'context-head.json'))['archiveId']==cleared['archiveId']
        await call('maintenance.release',{'leaseId':held['leaseId']})
        assert not audit.exists()
    finally:await archive.close()


@pytest.mark.asyncio
async def test_identical_rows_across_clears_stable_views_and_archived_targets_refused(tmp_path):
    config,cwd,sid,directory,rows,audit=seeded(tmp_path)
    from amplifier_foundation.session.history import SessionHistoryStore
    original_ids=[];archived_ids=[]
    for index in range(3):
        client=await opened(config,cwd,sid)
        try:
            active=await pages(client,sid,cwd);original_ids.append(active[0]['id'])
            assert not active[0].get('historicalOnly')
            _,args=await reviewed(client,sid,'clear-'+str(index));result=await control(client,sid,'context.clear',args)
            assert 'result' in result,result
            values=await pages(client,sid,cwd);assert len(values)==index+1
            archived_ids=[r['id'] for r in values]
            message=values[0]['updates'][0]['_meta']['amplifier.dev/history']['messageId']
            assert message.startswith('archive:')
        finally:await client.close()
        # An independent, explicit owned fixture write uses identical content;
        # it is not a prompt or retry and cannot reuse synthetic epoch identity.
        SessionHistoryStore(directory).save(rows,{'session_id':sid,'working_dir':str(cwd)})
    assert len(set(original_ids))==3
    client=await opened(config,cwd,sid)
    try:
        values=await pages(client,sid,cwd);assert [r['id'] for r in values[1:]]==archived_ids
        rev=(await client.request('_amplifier/history/inspect',{'sessionId':sid,'cwd':str(cwd)}))['result']['revision']
        before=(directory/'transcript.jsonl').read_bytes()
        for operation,fields in [('export',{'format':'json','scope':'from','fromMessageId':message}),('fork',{'targetSessionId':str(uuid.uuid4()),'throughMessageId':message})]:
            response=await client.request('_amplifier/session',{'sessionId':sid,'cwd':str(cwd),'operation':operation,'args':{'commandId':'archived-'+operation,'expectedHistoryRevision':rev,**fields}})
            assert 'error' in response and 'viewing-only' in response['error']['message'],response
        edited=await control(client,sid,'history.revise',{'operationId':'archive-edit','inputId':'archive-edit-turn','targetMessageId':message,'expectedHistoryRevision':rev,'text':'never admit'})
        assert edited['result']['executed'] is False,edited
        # Old, unqualified pre-clear synthetic IDs cannot accidentally name a
        # same-offset, same-content active record either.
        from amplifier_acp.session_lifecycle import canonical,bound
        active_rows,_,_=canonical(directory,rev,app_home=tmp_path/'app')
        with pytest.raises(ValueError,match='unavailable'):bound(active_rows,original_ids[0])
        assert (directory/'transcript.jsonl').read_bytes()==before and not audit.exists()
    finally:await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['preserved','canonical','head'])
async def test_actual_worker_crash_is_scoped_unknown_without_replay(tmp_path,phase):
    config,cwd,sid,directory,_,audit=seeded(tmp_path,contextClearCrash=phase)
    client=await opened(config,cwd,sid)
    try:
        before=(directory/'transcript.jsonl').read_bytes();_,args=await reviewed(client,sid,'crash-clear')
        response=await control(client,sid,'context.clear',args);assert 'error' in response,response
        saved=await receipt(client,sid,cwd,'crash-clear');assert saved['status']=='unknown' and 'result' not in saved
        segment=tmp_path/'app/sessions'/sid/'history-revisions'/('context-'+_command('crash-clear'))/'transcript.jsonl'
        assert segment.read_bytes()==before
        assert (directory/'transcript.jsonl').read_bytes()==(before if phase=='preserved' else b'')
        for operation in ['goals.get','context.clear.review']:
            refused=await control(client,sid,operation);assert refused['error']['data']['outcome']=='unknown'
        unrelated=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});assert 'result' in unrelated,unrelated
    finally:await client.close()
    cold=await Client.launch(config);await initialize(cold)
    try:
        assert await receipt(cold,sid,cwd,'crash-clear')==saved
        refused=await cold.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert refused['error']['data']['outcome']=='unknown'
    finally:await cold.close()
    assert not audit.exists()


def test_segment_page_does_not_open_events_or_unselected_segments_and_is_bounded(tmp_path,monkeypatch):
    from amplifier_acp.native.history_revision import _write,_copy_segment
    from amplifier_acp.history import page,HistoryError
    directory=tmp_path/'native/session';directory.mkdir(parents=True);(directory/'transcript.jsonl').write_bytes(b'')
    root=tmp_path/'app/sessions/session/history-revisions';archive='a'*64
    source=tmp_path/'source';source.write_text(''.join(json.dumps({'role':'user','content':str(i)})+'\n' for i in range(4000)))
    snapshot=root/('context-'+archive);captured=_copy_segment(source,snapshot/'transcript.jsonl')
    _write(snapshot/'segment.json',{'archiveId':archive,'sourceEpoch':None,'previousArchive':None,**captured})
    _write(root/('context-'+archive+'.json'),{'status':'succeeded'})
    _write(root/'context-head.json',{'archiveId':archive});(snapshot/'events.jsonl').symlink_to('/must-not-open')
    first=page(directory,app_home=tmp_path/'app',limit=2,max_bytes=70000)
    assert len(first['turnGroups'])==2 and first['nextCursor']
    second=page(directory,app_home=tmp_path/'app',limit=2,cursor=first['nextCursor'],max_bytes=70000)
    assert first['turnGroups'][0]['id']!=second['turnGroups'][0]['id']
    # Exact-byte backup restoration changes every inode/ctime. Group identity
    # and bounded integrity verification must not depend on those old values.
    import shutil
    restored=tmp_path/'restored';shutil.copytree(root,restored,symlinks=True)
    shutil.rmtree(root);restored.rename(root)
    assert page(directory,app_home=tmp_path/'app',limit=2,max_bytes=70000)['turnGroups']==first['turnGroups']
    preserved=snapshot/'transcript.jsonl';original=preserved.read_bytes()
    preserved.write_bytes(original[:-3]+b'X'+original[-2:])
    with pytest.raises(HistoryError,match='changed'):page(directory,app_home=tmp_path/'app',limit=2,max_bytes=70000)
    preserved.write_bytes(original)
    with pytest.raises(HistoryError,match='oversized'):page(directory,app_home=tmp_path/'app',limit=200,max_bytes=10)
    (directory/'transcript.jsonl').write_text('{"role":"user","content":"new"}\n')
    with pytest.raises(HistoryError,match='changed'):page(directory,app_home=tmp_path/'app',cursor=first['nextCursor'])

@pytest.mark.asyncio
async def test_full_native_archive_contains_exact_context_revision_authority(tmp_path):
    import io,tarfile
    from test_private_store_snapshot import hold,call,prepare
    from test_maintenance_store_snapshot import capture
    from test_maintenance import initialized
    config,cwd,sid,directory,_,audit=seeded(tmp_path)
    client=await opened(config,cwd,sid)
    try:
        _,args=await reviewed(client,sid,'full-clear')
        assert 'result' in await control(client,sid,'context.clear',args)
    finally:await client.close()
    root=tmp_path/'app/sessions'/sid/'history-revisions'
    originals={p.relative_to(tmp_path/'app'):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    values=json.loads(config.read_text());values.update(adminWorkspaceRoots=[str(cwd)],adminMaintenance=True,maintenanceFullNative=True,
        maintenanceExternalWriters='stopped',workerCommand=['/impossible/full-archive-worker'],
        maintenanceFullNativeRoots={'checkpoint':os.environ['AMPLIFIER_SESSION_STATE_HOME'],'events':os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'],'sources':os.environ['AMPLIFIER_SOURCE_STORE'],'bundle':values['bundle']})
    config.write_text(json.dumps(values));archive,_=await initialized(config)
    try:
        auth=await hold(archive,cwd);preview=await prepare(archive,cwd,auth,parts=['full-native-authority'])
        _,raw=await capture(archive,cwd,auth,preview,'context-full')
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for relative,expected in originals.items():assert tar.extractfile('app-home/'+str(relative)).read()==expected
        assert not audit.exists()
    finally:await archive.close()


@pytest.mark.asyncio
async def test_canonical_revision_change_refuses_without_clear_effect(tmp_path):
    config,cwd,sid,directory,_,audit=seeded(tmp_path);client=await opened(config,cwd,sid)
    try:
        _,args=await reviewed(client,sid,'history-stale')
        spoken=await control(client,sid,'voice.transcript.record',{'commandId':'new-voice','callId':'call','itemId':'item','role':'user','text':'new native history','append':False})
        assert 'result' in spoken,spoken
        before=(directory/'transcript.jsonl').read_bytes()
        result=await control(client,sid,'context.clear',args)
        assert result['error']['data']['reason']=='context-clear-stale-review' and result['error']['data']['executed'] is False
        assert (directory/'transcript.jsonl').read_bytes()==before and not audit.exists()
        assert (await receipt(client,sid,cwd,'history-stale'))['status']=='refused'
    finally:await client.close()
