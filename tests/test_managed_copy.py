import json,uuid
from pathlib import Path
import pytest
from amplifier_acp.server import Server
from amplifier_acp.managed_session import perform,proof,digest,runtime_scope
from amplifier_acp.session_lifecycle import Lifecycle
from native_setup import configuration
from test_managed_session import allocated
from client import Client

def target(tmp_path,config):
    _,params,cwd=allocated(tmp_path,config)
    return params,{'cwd':str(cwd),'commandId':params['commandId'],'allocationHash':params['allocationHash']}

def source(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
    config,_=configuration(tmp_path);values,args,cwd=allocated(tmp_path,config);server=Server(values,None);perform(server,args)
    from amplifier_foundation.session.history import SessionHistoryStore
    directory=server.directory(str(cwd),args['sessionId']);metadata=json.loads((directory/'metadata.json').read_text())
    SessionHistoryStore(directory,session_id=args['sessionId']).save([{'role':'user','content':'Original question','metadata':{'amplifier_input':{'id':'source-input','kind':'user'}}},{'role':'assistant','content':'Original reply'}],metadata)
    app=Path(values['appHome'])/'sessions'/args['sessionId']
    (app/'configuration.json').write_text(json.dumps({'tools':[{'module':'tool-filesystem','config':{'allowed_write_paths':[str(cwd)],'denied_write_paths':[str(cwd/'restricted')]}}]}))
    (app/'control-state.json').write_text(json.dumps({'selection':{'instance':'fixture','model':'copy-model'},'task':{'id':'must-not-copy'},'goal':'source-only'}))
    (cwd/'source.txt').write_text('Generated source file retained')
    return server,config,args,cwd,directory

def test_managed_history_copy_requires_fresh_proof_and_preserves_source(tmp_path,monkeypatch):
    server,config,args,cwd,directory=source(tmp_path,monkeypatch);owner=Lifecycle(server,args['sessionId'],str(cwd));revision=owner.inspect()['historyRevision'];before=(directory/'transcript.jsonl').read_bytes()
    selected,allocation=target(tmp_path,config);request={'commandId':'fork-copy','targetSessionId':selected['sessionId'],'expectedHistoryRevision':revision,'mode':'recovery','managedTarget':allocation}
    with pytest.raises(ValueError,match='fresh exact'):owner.perform('fork',{k:v for k,v in request.items() if k!='managedTarget'})
    result=owner.perform('fork',request);newcwd=Path(allocation['cwd']);assert result['creationConfirmed'] and result['generatedFilesCopied'] is False and result['historyCwd']==str(newcwd)
    assert proof(selected['sessionId'],str(newcwd),server.config)['result']['source']['commandId']=='fork-copy'
    assert (directory/'transcript.jsonl').read_bytes()==before and (cwd/'source.txt').exists() and not (newcwd/'source.txt').exists()
    assert owner.perform('fork',request)['sessionId']==selected['sessionId']
    controls=json.loads((Path(server.config['appHome'])/'sessions'/selected['sessionId']/'control-state.json').read_text());assert controls['selection']['model']=='copy-model' and 'task' not in controls and 'goal' not in controls
    copied=json.loads((Path(server.config['appHome'])/'sessions'/selected['sessionId']/'configuration.json').read_text());assert copied['tools'][0]['config']=={'allowed_write_paths':[str(newcwd)],'denied_write_paths':[str(newcwd/'restricted')]}
    assert runtime_scope(newcwd,selected['sessionId'],server.config)

def test_interrupted_managed_copy_preserves_unknown_allocation_and_never_replays(tmp_path,monkeypatch):
    server,config,args,cwd,directory=source(tmp_path,monkeypatch);owner=Lifecycle(server,args['sessionId'],str(cwd));selected,allocation=target(tmp_path,config)
    request={'commandId':'interrupted-copy','targetSessionId':selected['sessionId'],'expectedHistoryRevision':owner.inspect()['historyRevision'],'managedTarget':allocation}
    import amplifier_acp.managed_session as managed
    original=managed.complete_copy
    def fail(*args,**kwargs):raise OSError('lost before authoritative completion')
    monkeypatch.setattr(managed,'complete_copy',fail)
    with pytest.raises(OSError):owner.perform('fork',request)
    monkeypatch.setattr(managed,'complete_copy',original)
    assert owner.perform('fork',request)['receipt']['status']=='unknown'
    query={k:v for k,v in selected.items() if k in {'sessionId','cwd','commandId','allocationHash'}}|{'operation':'receipt'}
    assert perform(server,query)['status']=='unknown'
    with pytest.raises(ValueError,match='unresolved'):runtime_scope(allocation['cwd'],selected['sessionId'],server.config)
    assert (directory/'transcript.jsonl').exists()

def test_passive_configuration_clone_has_fresh_allocation_empty_history_and_exact_snapshot(tmp_path,monkeypatch):
    server,config,args,cwd,directory=source(tmp_path,monkeypatch)
    app=Path(server.config['appHome'])/'sessions'/args['sessionId'];(app/'effective-configuration.json').write_bytes((app/'configuration.json').read_bytes())
    reference=perform(server,{'operation':'configuration.snapshot','sessionId':args['sessionId'],'cwd':str(cwd)})
    selected,allocation=target(tmp_path,config)
    copied=perform(server,selected|{'operation':'clone','source':reference})
    assert copied['status']=='completed' and copied['result']['runtimeStarted'] is False
    assert copied['result']['source']=={'operation':'configuration-clone','sessionId':args['sessionId'],'configurationHash':reference['configurationHash']}
    dest=server.directory(allocation['cwd'],selected['sessionId']);assert (dest/'transcript.jsonl').read_bytes()==b''
    target_app=Path(server.config['appHome'])/'sessions'/selected['sessionId']
    assert json.loads((target_app/'configuration.json').read_text())['tools'][0]['config']['allowed_write_paths']==[allocation['cwd']]
    assert not json.loads((target_app/'control-state.json').read_text()).get('task')
    source_control=json.loads((app/'control-state.json').read_text());source_control['selection']['model']='changed';(app/'control-state.json').write_text(json.dumps(source_control))
    # Exact completed command reads its original receipt even when source changed.
    assert perform(server,selected|{'operation':'clone','source':reference})==copied
    next_selected,_=target(tmp_path,config)
    with pytest.raises(ValueError,match='changed'):perform(server,next_selected|{'operation':'clone','source':reference})
    assert not (Path(server.config['appHome'])/'sessions'/next_selected['sessionId']).exists()

@pytest.mark.asyncio
async def test_managed_defaults_resolves_only_exact_alias_without_session(tmp_path):
    config,_=configuration(tmp_path);values=json.loads(config.read_text());values['managedSessionRoots']=[str(tmp_path/'managed')];values['forkBundles']={'approved':values['bundle']};values['workerCommand']=['/impossible-worker'];config.write_text(json.dumps(values))
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        choices=(await client.request('_amplifier/managed',{'operation':'options'}))['result'];choice=choices['bundles'][0];assert choice=={'id':'approved','name':'approved','label':'approved','description':'Registered root bundle.','revision':digest(['approved',values['bundle']])}
        wrong=await client.request('_amplifier/managed',{'operation':'defaults','bundle':'approved','bundleRevision':'0'*64});assert 'error' in wrong
        result=await client.request('_amplifier/managed',{'operation':'defaults','bundle':'approved','bundleRevision':choice['revision']});assert 'result' in result,result
        body=result['result'];assert body['globalOnly'] and body['runtimeStarted'] is False and body['bundleSelection']['id']=='approved';assert body['providers'][0]['id']=='fixture';assert 'bundle' not in body
        assert not (Path(values['appHome'])/'sessions').exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_real_copied_filesystem_policy_uses_new_allocation_not_source(tmp_path):
    import os
    if not os.environ.get('FILESYSTEM_TOOL_SOURCE'):pytest.skip('Explicit actual filesystem source required')
    from test_managed_session import test_real_first_prompt_global_only_selection_resume_and_metadata
    await test_real_first_prompt_global_only_selection_resume_and_metadata(tmp_path)
    config=tmp_path/'config.json'
    # Fixture configuration chooses this filename publicly; locate by launcher fields.
    if not config.exists():config=next(p for p in tmp_path.glob('*.json') if 'managedSessionRoots' in json.loads(p.read_text()) and 'workerCommand' not in json.loads(p.read_text()))
    values=json.loads(config.read_text());server=Server(values,None)
    cwd=next((tmp_path/'managed/chats').glob('*/files'));sid=cwd.parent.name
    owner=Lifecycle(server,sid,str(cwd));selected,allocation=target(tmp_path,config)
    copied=owner.perform('fork',{'commandId':'mounted-policy-copy','targetSessionId':selected['sessionId'],'expectedHistoryRevision':owner.inspect()['historyRevision'],'managedTarget':allocation})
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        ready=await client.request('session/resume',{'sessionId':selected['sessionId'],'cwd':allocation['cwd'],'mcpServers':[]},timeout=120);assert 'result' in ready,ready
        for path,expected in [(Path(allocation['cwd'])/'copy-output.txt',True),(cwd/'must-not-write-source.txt',False)]:
            effect=await client.request('_amplifier/native',{'sessionId':selected['sessionId'],'operation':'tool.invoke','args':{'name':'write_file','arguments':{'file_path':str(path),'content':'copy owns only its new allocation'}}})
            assert effect['result']['result']['success'] is expected,effect
            assert path.exists() is expected
    finally:await client.close()

def test_full_native_archive_declares_external_host_managed_authority(tmp_path):
    from amplifier_acp.full_native_archive import source_config
    from types import SimpleNamespace
    value=source_config(SimpleNamespace(config={'managedSessionRoots':[str(tmp_path/'managed')]}),[])
    assert value['managedAllocations']=={'roots':[str(tmp_path/'managed')],'authority':'external-host-owner','included':False,'requiredForManagedActivation':True}
