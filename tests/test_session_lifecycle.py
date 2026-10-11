import asyncio,base64,json,uuid
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle,guard_start

@pytest.fixture(autouse=True)
def isolated_writer(tmp_path,monkeypatch):monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
async def initialize(client):return await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
async def action(client,sid,cwd,op,args={}):return await client.request('_amplifier/session',{'sessionId':sid,'cwd':str(cwd),'operation':op,'args':args})
async def prompt(client,sid,text,identity):return await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':text}],'_meta':{'amplifier.dev/command':{'commandId':identity}}})
async def prepared(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config);await initialize(client)
    made=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});assert 'result' in made,made
    sid=made['result']['sessionId']
    return client,sid,cwd,config

@pytest.mark.asyncio
async def test_native_history_fork_keeps_exact_context_tools_and_configuration_without_authority(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        async def control(op,args):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        await control('budget.set',{'contextTokens':4321})
        await control('task.create',{'objective':'Source only','maxTurns':3,'commandId':'task','expectedRevision':0})
        await prompt(client,sid,'permission tool turn','turn1');await prompt(client,sid,'Second original turn','turn2')
        # Lifecycle copy requires confirmed release, not merely prompt completion.
        async with asyncio.timeout(10):
            while not (await client.request('_amplifier/retire',{'sessionId':sid}))['result']['retired']:
                await asyncio.sleep(.01)
        inspected=await action(client,sid,cwd,'inspect');assert 'result' in inspected,inspected
        revision=inspected['result']['historyRevision']
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));before=(native/'transcript.jsonl').read_bytes();(native/'events.jsonl').write_text('retained historical log\n')
        source_configuration=tmp_path/'app/sessions'/sid/'configuration.json'
        source_configuration.write_text(json.dumps({'tools':[{'module':'disabled-fixture','enabled':False,'config':{'preserved':True}}]}))
        target=str(uuid.uuid4());args={'commandId':'fork1','targetSessionId':target,'expectedHistoryRevision':revision,'throughMessageId':'turn1'}
        forked=await action(client,sid,cwd,'fork',args);assert forked.get('result',{}).get('creationConfirmed'),forked
        assert (native/'transcript.jsonl').read_bytes()==before
        assert (native/'events.jsonl').read_text()=='retained historical log\n'
        target_path=native.parent/target;copied=[json.loads(line) for line in (target_path/'transcript.jsonl').read_text().splitlines()]
        assert any(row.get('role')=='tool' for row in copied)
        assert not any(row.get('content')=='Second original turn' for row in copied)
        controls=json.loads((tmp_path/'app/sessions'/target/'control-state.json').read_text())
        plan=json.loads((tmp_path/'app/sessions'/target/'configuration.json').read_text());assert any(row.get('module')=='disabled-fixture' and row['config']['preserved'] for row in plan['tools'])
        assert controls['budget']['contextTokens']==4321 and not controls.get('task') and not controls.get('goal')
        assert not (target_path/'events.jsonl').exists() and not (target_path/'live-jobs').exists()
        duplicate=await action(client,sid,cwd,'fork',args);assert duplicate['result']['sessionId']==target and duplicate['result']['replayed'] is False
        conflicting=await action(client,sid,cwd,'fork',{**args,'targetSessionId':str(uuid.uuid4())});assert 'error' in conflicting
        resumed=await client.request('session/resume',{'sessionId':target,'cwd':str(cwd),'mcpServers':[]});assert 'result' in resumed,resumed
        assert (await prompt(client,target,'Independent new work','target1'))['result']['stopReason']=='end_turn'
        assert (native/'transcript.jsonl').read_bytes()==before
    finally:await client.close()

@pytest.mark.asyncio
async def test_passive_frozen_export_tombstone_restore_and_receipt_without_worker(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    await prompt(client,sid,'First visible message','turn1');await client.close()
    value=json.loads(config.read_text());value['workerCommand']=['/impossible-worker'];config.write_text(json.dumps(value));client=await Client.launch(config);await initialize(client)
    try:
        inspected=(await action(client,sid,cwd,'inspect'))['result'];revision=inspected['historyRevision']
        exported=await action(client,sid,cwd,'export',{'commandId':'export1','expectedHistoryRevision':revision,'format':'markdown','scope':'all'});assert 'result' in exported,exported
        snapshot=exported['result'];assert snapshot['summary']['format']=='markdown' and snapshot['summary']['scope']=='all' and snapshot['summary']['minimal'] is False
        assert snapshot['summary']['requestedBoundaries']=={} and snapshot['summary']['resolvedBoundaries']['fromMessageId'].startswith('native:')
        data=await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId'],'limit':16})
        parts=[]
        while True:
            part=data['result'];parts.append(base64.b64decode(part['data']))
            if part['nextOffset'] is None:break
            data=await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId'],'offset':part['nextOffset'],'limit':16})
        assert 'First visible message' in b''.join(parts).decode();assert part['summary']==snapshot['summary']
        ranged=await action(client,sid,cwd,'export',{'commandId':'export-minimal','expectedHistoryRevision':revision,'format':'markdown','scope':'from','fromMessageId':'turn1','minimal':True})
        assert ranged['result']['summary']['scope']=='from' and ranged['result']['summary']['minimal'] is True
        assert ranged['result']['summary']['requestedBoundaries']=={'fromMessageId':'turn1'}
        assert ranged['result']['summary']['resolvedBoundaries']['fromMessageId']==snapshot['summary']['resolvedBoundaries']['fromMessageId']
        preview=(await action(client,sid,cwd,'delete.preview'))['result'];assert preview['summary']['canonicalFilesDeleted']==0
        args={'commandId':'delete1','expectedHistoryRevision':revision,'confirmationToken':preview['confirmationToken']}
        deleted=(await action(client,sid,cwd,'delete',args))['result'];assert deleted['deleted'] and 'events.jsonl' in deleted['preserved']
        saved=(await action(client,sid,cwd,'receipt',{'commandId':'delete1'}))['result']['receipt'];assert saved['status']=='completed'
        assert (await action(client,sid,cwd,'delete',args))['result']['replayed'] is False
        resume=await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]});assert 'removed from discovery' in resume['error']['message']
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50});assert history['result']['updates']
        restored=(await action(client,sid,cwd,'restore',{'commandId':'restore1','expectedHistoryRevision':revision}))['result'];assert restored['deleted'] is False
        standard=await client.request('session/delete',{'sessionId':sid,'_meta':{'amplifier.dev/native':{'historyCwd':str(cwd),'commandId':'standard-delete'}}});assert standard.get('result')=={},standard
        assert (await client.request('session/delete',{'sessionId':sid,'_meta':{'amplifier.dev/native':{'historyCwd':str(cwd)}}}))['result']=={}
    finally:await client.close()

@pytest.mark.asyncio
async def test_export_during_owned_work_keeps_execution_and_exact_saved_snapshot(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        await prompt(client,sid,'Saved before active work','saved')
        running=asyncio.create_task(prompt(client,sid,'slow active export','active'))
        async with asyncio.timeout(10):
            while not any(r.get('params',{}).get('event',{}).get('activeInputId')=='active' for r in client.events):await asyncio.sleep(.01)
        revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision']
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));before=(native/'transcript.jsonl').read_bytes()
        args={'commandId':'active-export','expectedHistoryRevision':revision,'format':'markdown'}
        exported=await action(client,sid,cwd,'export',args);assert 'result' in exported,exported
        snapshot=exported['result'];page=(await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId']}))['result']
        frozen=base64.b64decode(page['data']);assert b'Saved before active work' in frozen
        assert not running.done() and (native/'transcript.jsonl').read_bytes()==before
        refused=await action(client,sid,cwd,'restore',{'commandId':'blocked-restore','expectedHistoryRevision':revision})
        assert refused['error']['data']['executed'] is False
        await client.request('session/cancel',{'sessionId':sid});await running
        again=(await action(client,sid,cwd,'export',args))['result']
        assert again['snapshotId']==snapshot['snapshotId'] and again['replayed'] is False
        page=(await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId']}))['result']
        assert base64.b64decode(page['data'])==frozen
    finally:await client.close()

@pytest.mark.asyncio
async def test_export_refuses_changed_capture_and_unsettled_history_before_receipt(tmp_path,monkeypatch):
    client,sid,cwd,config=await prepared(tmp_path)
    await prompt(client,sid,'Stable saved history','saved');await client.close()
    import amplifier_acp.session_lifecycle as lifecycle
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd))
    revision=owner.inspect()['historyRevision'];args={'commandId':'raced-export','expectedHistoryRevision':revision,'format':'json'}
    original=lifecycle.regular
    def changed(path,maximum=1024*1024):
        body=original(path,maximum)
        if Path(path).name=='transcript.jsonl':
            with Path(path).open('ab') as stream:stream.write(b'{"role":"user","content":"Concurrent saved input"}\n')
        return body
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle,'regular',changed)
        with pytest.raises(ValueError,match='changed during capture'):owner.perform('export',args)
    assert owner.saved(args['commandId']) is None
    revision=owner.inspect()['historyRevision'];pending=owner.app/'history-edit-pending.json';pending.write_text('{}')
    with pytest.raises(ValueError,match='unresolved'):owner.perform('export',{**args,'expectedHistoryRevision':revision})
    assert owner.saved(args['commandId']) is None and not (owner.app/'exports').exists()

@pytest.mark.asyncio
async def test_stale_and_busy_refuse_without_target_effects(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        await prompt(client,sid,'saved','turn1');revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision'];target=str(uuid.uuid4())
        await prompt(client,sid,'changed','turn2')
        stale=await action(client,sid,cwd,'fork',{'commandId':'fork1','targetSessionId':target,'expectedHistoryRevision':revision});assert 'error' in stale
        assert not (tmp_path/'app/sessions'/target).exists()
        running=asyncio.create_task(prompt(client,sid,'slow','turn3'))
        async with asyncio.timeout(10):
            while not any(r.get('params',{}).get('event',{}).get('activeInputId')=='turn3' for r in client.events):await asyncio.sleep(.01)
        busy=await action(client,sid,cwd,'fork',{'commandId':'fork2','targetSessionId':target,'expectedHistoryRevision':revision});assert busy['error']['data']['executed'] is False
        await client.request('session/cancel',{'sessionId':sid});await running
    finally:await client.close()

@pytest.mark.asyncio
async def test_interrupted_fork_is_fenced_and_unknown_is_never_replayed(tmp_path,monkeypatch):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'source','turn1');await client.close()
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd));revision=owner.inspect()['historyRevision'];target=str(uuid.uuid4())
    from amplifier_foundation.session.history import SessionHistoryStore
    original=SessionHistoryStore.save
    def fail(*args,**kwargs):raise OSError('fixture interrupted native write')
    monkeypatch.setattr(SessionHistoryStore,'save',fail)
    args={'commandId':'fork1','targetSessionId':target,'expectedHistoryRevision':revision}
    with pytest.raises(OSError):owner.perform('fork',args)
    monkeypatch.setattr(SessionHistoryStore,'save',original)
    result=owner.perform('fork',args);assert result['receipt']['status']=='unknown' and result['replayed'] is False
    with pytest.raises(ValueError,match='unresolved'):guard_start(server,target,str(cwd))


def test_selected_snapshot_rejects_symlink_and_corruption_without_event_reads(tmp_path):
    from amplifier_acp.session_lifecycle import canonical
    from amplifier_acp.history import stamp
    directory=tmp_path/'session';directory.mkdir();transcript=directory/'transcript.jsonl'
    transcript.write_text('{"role":"user","content":"original"}\n');(directory/'metadata.json').write_text('{"session_id":"fixture"}')
    # A FIFO event log would block any accidental attempt to hydrate all history.
    import os
    os.mkfifo(directory/'events.jsonl')
    rows,_,_=canonical(directory,stamp(transcript)[0]);assert rows[0][1]['content']=='original'
    transcript.write_text('malformed\n')
    with pytest.raises(ValueError):canonical(directory,stamp(transcript)[0])
    transcript.unlink();transcript.symlink_to(directory/'metadata.json')
    with pytest.raises(ValueError):canonical(directory,'unused')


@pytest.mark.asyncio
async def test_detailed_export_freezes_owner_observations_and_recovers_exact_bytes(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        await prompt(client,sid,'Visible original **text**','detail-turn')
        async with asyncio.timeout(10):
            while not (await client.request('_amplifier/retire',{'sessionId':sid}))['result']['retired']:
                await asyncio.sleep(.01)
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));file=native/'transcript.jsonl'
        rows=[json.loads(line) for line in file.read_text().splitlines()]
        for row in rows:
            if row.get('role')=='user':row.setdefault('metadata',{})['createdAt']='2026-10-11T10:00:00Z'
        file.write_text(''.join(json.dumps(row)+'\n' for row in rows));before=file.read_bytes()
        revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision']
        context={'version':1,'source':'host-retained-observations','work':[{'anchor':'detail-turn','status':'completed','toolCalls':2,'modelCalls':3,'durationSeconds':1.25}],
                 'artifacts':[{'id':'private-artifact','title':'Report','kind':'markdown','messageId':'detail-turn'}],'omissions':[]}
        args={'commandId':'detailed-export','expectedHistoryRevision':revision,'format':'json','includeExecution':True,'includeTimestamps':True,'includeConversationId':False,'includeReferenceIds':False,'exportContext':context}
        reply=await action(client,sid,cwd,'export',args);assert 'result' in reply,reply
        snapshot=reply['result'];page=(await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId']}))['result'];body=base64.b64decode(page['data']);content=json.loads(body)
        assert snapshot['summary']['executionSummaryCount']==1 and snapshot['summary']['artifactCount']==1
        assert snapshot['filename']=='conversation.json'
        assert any(row.get('timestamp')=='2026-10-11T10:00:00+00:00' for row in content['messages'])
        assert any(row.get('workSummaries',[{}])[0].get('modelCalls')==3 for row in content['messages'])
        assert sid.encode() not in body and b'private-artifact' not in body and b'Visible original **text**' in body
        assert file.read_bytes()==before
        file.write_bytes(before+b'{"role":"user","content":"Later change"}\n')
        repeated=(await action(client,sid,cwd,'export',args))['result'];assert repeated['snapshotId']==snapshot['snapshotId'] and repeated['replayed'] is False
        again=(await action(client,sid,cwd,'export.read',{'snapshotId':snapshot['snapshotId']}))['result'];assert base64.b64decode(again['data'])==body
        assert 'error' in await action(client,sid,cwd,'export',{**args,'includeExecution':False})
    finally:await client.close()
