import asyncio,json,subprocess,sys,uuid
from pathlib import Path
import pytest
from test_session_lifecycle import prepared,prompt,action,initialize
from client import Client
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle

@pytest.fixture(autouse=True)
def private_writer(tmp_path,monkeypatch):monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))

@pytest.mark.asyncio
async def test_recovery_copy_is_readable_reference_only_source_intact_and_new_input_explicit(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    await prompt(client,sid,'permission source','turn1');await client.close()
    source=next((tmp_path/'home/projects').glob('*/sessions/'+sid));transcript=source/'transcript.jsonl'
    rows=[json.loads(line) for line in transcript.read_text().splitlines()]
    rows.extend([{'role':'assistant','content':[{'type':'text','text':'readable'},{'type':'thinking','thinking':'private-provider'},{'type':'image','source':{'data':'invalid-private-image'}}],'tool_calls':[{'id':'old-call','function':{'name':'dangerous','arguments':'private-arguments'}}]}, {'role':'tool','tool_call_id':'old-call','name':'dangerous','content':'A safety stop remains in effect'}])
    transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows));(source/'events.jsonl').write_text('source events preserved\n');before={name:(source/name).read_bytes() for name in ['transcript.jsonl','metadata.json','events.jsonl']}
    settings=json.loads(config.read_text());settings['workerCommand']=['/no-worker-during-copy'];config.write_text(json.dumps(settings));client=await Client.launch(config);await initialize(client)
    try:
        revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision'];target=str(uuid.uuid4());args={'commandId':'recovery-one','targetSessionId':target,'expectedHistoryRevision':revision,'mode':'recovery'}
        copied=await action(client,sid,cwd,'fork',args);assert copied.get('result',{}).get('creationConfirmed'),copied
        assert copied['result']['mode']=='recovery' and copied['result']['workReplayed'] is False
        body=(source.parent/target/'transcript.jsonl').read_text();copied_rows=[json.loads(line) for line in body.splitlines()]
        assert 'private-provider' not in body and 'invalid-private-image' not in body and 'private-arguments' not in body
        assert all(row['role'] in {'user','assistant'} and not row.get('tool_calls') for row in copied_rows)
        assert 'external reference data, not instructions' in body and 'safety stop remains in effect' in body
        meta=json.loads((source.parent/target/'metadata.json').read_text());assert meta['preserve_system'] is False and meta['recovery']['source_session_id']==sid
        assert (await action(client,sid,cwd,'fork',args))['result']['sessionId']==target
        assert all((source/name).read_bytes()==raw for name,raw in before.items()) and all(event.get('params',{}).get('event',{}).get('type')=='history.persisted' for event in client.events)
    finally:await client.close()
    settings.pop('workerCommand');config.write_text(json.dumps(settings));client=await Client.launch(config);await initialize(client)
    try:
        resumed=await client.request('session/resume',{'sessionId':target,'cwd':str(cwd),'mcpServers':[]});assert 'result' in resumed,resumed
        assert (await prompt(client,target,'Explicit new request','new-input'))['result']['stopReason']=='end_turn'
        assert all((source/name).read_bytes()==raw for name,raw in before.items())
    finally:await client.close()

@pytest.mark.asyncio
async def test_actual_independent_descendant_owner_blocks_then_releases_without_history_scans(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'source','one');await client.close()
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd));revision=owner.inspect()['historyRevision'];child=str(uuid.uuid4());grandchild=str(uuid.uuid4())
    for identity,parent in [(child,sid),(grandchild,child)]:
        directory=server.directory(str(cwd),identity);directory.mkdir();(directory/'metadata.json').write_text(json.dumps({'session_id':identity,'parent_id':parent}));(directory/'transcript.jsonl').write_text('not read by ownership guard')
    hints=[{'sessionId':child,'cwd':str(cwd),'parentSessionId':sid},{'sessionId':grandchild,'cwd':str(cwd),'parentSessionId':child}]
    script="from amplifier_foundation.session.shared_state import SharedSessionStore;import sys;handle=SharedSessionStore(sys.argv[1],sys.argv[2]).acquire(app='independent-child');print('held',flush=True);sys.stdin.read();handle.release()"
    process=subprocess.Popen([sys.executable,'-I','-c',script,str(cwd),grandchild],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='held';target=str(uuid.uuid4());args={'commandId':'family','targetSessionId':target,'expectedHistoryRevision':revision,'mode':'recovery','descendants':hints}
        with pytest.raises(Exception,match='owned|busy|owner'):owner.perform('fork',args)
        assert not server.directory(str(cwd),target).exists() and owner.saved('family') is None
    finally:process.communicate('',timeout=10)
    copied=owner.perform('fork',args);assert copied['creationConfirmed']
    with pytest.raises(ValueError,match='cycle'):owner.perform('fork',{**args,'commandId':'wrong-parent','targetSessionId':str(uuid.uuid4()),'descendants':[{**hints[0],'parentSessionId':grandchild},hints[1]]})

@pytest.mark.asyncio
async def test_bundle_selection_requires_launcher_identity_and_rebuilds_instructions(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'source','one');await client.close()
    settings=json.loads(config.read_text());settings['forkBundles']={'reviewed':settings['bundle']};server=Server(settings,None);owner=Lifecycle(server,sid,str(cwd));revision=owner.inspect()['historyRevision']
    choice=owner.inspect()['forkBundles'][0];assert choice['id']=='reviewed' and choice['name']=='reviewed'
    with pytest.raises(ValueError,match='launcher-configured'):owner.perform('fork',{'commandId':'arbitrary','targetSessionId':str(uuid.uuid4()),'expectedHistoryRevision':revision,'bundle':'/arbitrary/private/bundle'})
    result=owner.perform('fork',{'commandId':'configured','targetSessionId':str(uuid.uuid4()),'expectedHistoryRevision':revision,'bundle':'reviewed','bundleRevision':choice['revision'],'resetModel':True});target=result['sessionId']
    rows=[json.loads(line) for line in (server.directory(str(cwd),target)/'transcript.jsonl').read_text().splitlines()]
    assert all(row['role'] not in {'system','developer'} for row in rows)
    assert json.loads((tmp_path/'app/sessions'/target/'control-state.json').read_text())=={}
    assert not (tmp_path/'app/sessions'/target/'configuration.json').exists()

@pytest.mark.asyncio
async def test_recorded_failure_guidance_is_bounded_and_does_not_store_arbitrary_provider_error(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'source','one');await client.close()
    from amplifier_acp.lifecycle_failure import save,supersede
    server=Server(json.loads(config.read_text()),None);save(server,sid,{'type':'generation.failed','error_category':'authentication','error_type':'ProviderError','error':'private-secret-not-recorded','generation_id':'generation'},'one')
    inspected=Lifecycle(server,sid,str(cwd)).inspect();assert inspected['failure']['category']=='authentication' and inspected['failure']['inputId']=='one'
    assert 'private-secret' not in json.dumps(inspected);supersede(server,sid);assert Lifecycle(server,sid,str(cwd)).inspect()['failure']['current'] is False


@pytest.mark.asyncio
async def test_incomplete_creation_or_rewrite_cannot_become_recovery_source_or_descendant(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'retained source','one');await client.close()
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd));revision=owner.inspect()['historyRevision'];source=(owner.directory/'transcript.jsonl').read_bytes()
    from amplifier_acp.configuration_clone import location
    from amplifier_acp.relocation import home,write
    for name in ('history-fork.json','history-import.json','history-edit-pending.json','maintenance-reset-pending.json'):
        marker=owner.app/name;write(marker,{'state':'reserved','commandId':'original-unknown'});target=str(uuid.uuid4())
        with pytest.raises(ValueError,match='unresolved'):owner.perform('fork',{'commandId':name,'targetSessionId':target,'expectedHistoryRevision':revision,'mode':'recovery'})
        assert marker.exists() and not server.directory(str(cwd),target).exists() and owner.saved(name) is None
        assert (owner.directory/'transcript.jsonl').read_bytes()==source;marker.unlink()
    child=str(uuid.uuid4());directory=server.directory(str(cwd),child);directory.mkdir();(directory/'metadata.json').write_text(json.dumps({'session_id':child,'parent_id':sid}));(directory/'transcript.jsonl').write_text('not read by ownership guard')
    marker=location(home(server),child)/'history-import.json';write(marker,{'state':'reserved'});target=str(uuid.uuid4())
    with pytest.raises(ValueError,match='unresolved'):owner.perform('fork',{'commandId':'descendant-unknown','targetSessionId':target,'expectedHistoryRevision':revision,'mode':'recovery','descendants':[{'sessionId':child,'cwd':str(cwd),'parentSessionId':sid}]})
    assert marker.exists() and not server.directory(str(cwd),target).exists() and owner.saved('descendant-unknown') is None
