import json,os,uuid
from pathlib import Path
import pytest
from amplifier_acp.server import Server
from amplifier_acp.managed_session import digest,perform,proof,runtime_scope
from native_setup import configuration
from client import Client

def allocated(tmp_path,config):
    root=tmp_path/'managed';sid=str(uuid.uuid4());cwd=root/'chats'/sid/'files';cwd.mkdir(parents=True)
    marker={'version':1,'id':sid,'workspace':str(cwd),'creationReceipt':'create-managed','owner':'amplifier-unified'};marker['allocationHash']=digest(marker)
    (cwd.parent/'managed-chat.json').write_text(json.dumps(marker))
    values=json.loads(config.read_text());values['managedSessionRoots']=[str(root)];config.write_text(json.dumps(values))
    params={'operation':'create','sessionId':sid,'cwd':str(cwd),'commandId':'create-managed','allocationHash':marker['allocationHash'],'title':'Private work'}
    return values,params,cwd

def test_passive_creation_exact_proof_and_no_arbitrary_marker_authority(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'leases'))
    config,_=configuration(tmp_path);values,params,cwd=allocated(tmp_path,config);values['workerCommand']=['/impossible/worker'];server=Server(values,None)
    result=perform(server,params);assert result['status']=='completed' and not result['result']['runtimeStarted'];assert not server.sessions
    assert proof(params['sessionId'],str(cwd),values)['result']==result['result']
    assert perform(server,params)==result
    with pytest.raises(ValueError,match='conflicts'):perform(server,params|{'title':'different'})
    directory=server.directory(str(cwd),params['sessionId']);assert (directory/'transcript.jsonl').exists()
    from amplifier_acp.native.shared_settings import settings_paths,routing_dirs
    assert set(settings_paths(cwd,session_id=params['sessionId']))=={'global','session'}
    assert routing_dirs(cwd)==[Path(values['home'])/'routing']
    from amplifier_acp.admin import Administration
    admin=Administration(dict(values,adminWorkspaceRoots=[str(tmp_path/'workspace')],adminPermissions=True))
    assert admin.workspace(str(cwd))==str(cwd)
    from amplifier_acp.permissions import Permissions
    with pytest.raises(ValueError,match='scope'):Permissions(values).scope(str(cwd),'project')
    saved=json.loads((cwd.parent/'managed-chat.json').read_text());saved['owner']='other';(cwd.parent/'managed-chat.json').write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='identity'):runtime_scope(cwd,params['sessionId'],values)

def test_unproven_marker_or_partial_native_creation_cannot_mount_or_retry(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'leases'))
    config,_=configuration(tmp_path);values,params,cwd=allocated(tmp_path,config);server=Server(values,None)
    with pytest.raises(ValueError,match='no native creation'):runtime_scope(cwd,params['sessionId'],values)
    from amplifier_acp.native.shared_settings import settings_paths
    assert 'project' in settings_paths(cwd) # A copied marker alone never changes policy.
    import amplifier_acp.managed_session as owner
    original=owner.write
    def interrupted(path,value):
        original(path,value)
        if path.name=='managed-creation.json':raise OSError('lost process after reservation')
    monkeypatch.setattr(owner,'write',interrupted)
    with pytest.raises(OSError):perform(server,params)
    monkeypatch.setattr(owner,'write',original)
    assert perform(server,params)['status']=='unknown'
    assert not server.directory(str(cwd),params['sessionId']).exists()
    with pytest.raises(ValueError,match='unresolved'):runtime_scope(cwd,params['sessionId'],values)

@pytest.mark.asyncio
async def test_real_first_prompt_global_only_selection_resume_and_metadata(tmp_path):
    config,_=configuration(tmp_path);values,params,cwd=allocated(tmp_path,config)
    # A deliberately invalid local module would prevent mount if inherited.
    (cwd/'.amplifier').mkdir();(cwd/'.amplifier/settings.yaml').write_text('bundle:\n  active: /missing/inherited-bundle\n')
    (cwd/'.amplifier/settings.local.yaml').write_text('overrides:\n  providers:\n    invalid: true\n')
    if os.environ.get('FILESYSTEM_TOOL_SOURCE'):
        bundle=tmp_path/'fixture.yaml';bundle.write_text(bundle.read_text()+'tools:\n  - module: tool-filesystem\n    source: '+str(Path(os.environ['FILESYSTEM_TOOL_SOURCE']).resolve())+'\n')
        (Path(values['home'])/'settings.yaml').write_text('bundle:\n  app: []\noverrides:\n  tool-filesystem:\n    config:\n      denied_write_paths: ['+str(cwd/'global-denied')+']\n')
        (cwd/'.amplifier/settings.local.yaml').write_text('overrides:\n  tool-filesystem:\n    config:\n      allowed_write_paths: ['+str(tmp_path/'must-not-grant')+']\n      denied_write_paths: ['+str(cwd/'local-denied')+']\n')
    params['selection']={'instance':'fixture','model':'fixture-custom'}
    cold=dict(values,workerCommand=['/impossible/worker']);coldfile=tmp_path/'cold.json';coldfile.write_text(json.dumps(cold))
    client=await Client.launch(coldfile)
    try:
        init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['managedCreation']['passive']
        ordinary=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});assert 'error' in ordinary and 'no native creation' in ordinary['error']['message']
        reply=await client.request('_amplifier/managed',params);assert reply.get('result',{}).get('status')=='completed',reply
        sid=params['sessionId'];location=reply['result']['result']['location']
    finally:await client.close()
    native=Server(values,None).directory(str(cwd),sid);original=(native/'transcript.jsonl').read_bytes()
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        restored=await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]},timeout=120)
        assert 'result' in restored,restored
        assert (native/'transcript.jsonl').read_bytes()==original
        assert any(o.get('currentValue') is not None for o in restored['result'].get('configOptions',[]))
        result=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'managed first explicit turn'}]})
        assert result.get('result',{}).get('stopReason')=='end_turn',result
        controls=json.loads((Path(values['appHome'])/'sessions'/sid/'control-state.json').read_text());assert controls['selection']==params['selection']
        assert json.loads((native/'metadata.json').read_text())['location']==location
        if os.environ.get('FILESYSTEM_TOOL_SOURCE'):
            mounted=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.inspect'})
            policy=next(t['config'] for t in mounted['result']['plan']['tools'] if t['module']=='tool-filesystem')
            assert policy['allowed_write_paths']==[str(cwd)]
            assert policy['denied_write_paths']==[str(cwd/'global-denied')]
            for path,expected in [(cwd/'global-denied/file.txt',False),(tmp_path/'must-not-grant/file.txt',False),(cwd/'local-denied/file.txt',True)]:
                effect=await client.request('_amplifier/native',{'sessionId':sid,'operation':'tool.invoke','args':{'name':'write_file','arguments':{'file_path':str(path),'content':'isolated managed test'}}})
                assert effect['result']['result']['success'] is expected,effect
                assert path.exists() is expected
    finally:await client.close()


def test_exact_bundle_alias_revision_and_replaced_directory(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'leases'))
    config,_=configuration(tmp_path);values,params,cwd=allocated(tmp_path,config);values['forkBundles']={'approved':values['bundle']};server=Server(values,None)
    with pytest.raises(ValueError,match='revision'):perform(server,params|{'bundle':'approved','bundleRevision':'0'*64})
    with pytest.raises(ValueError,match='typed'):perform(server,params|{'selection':{'instance':'fixture','model':'model','key':'secret'}})
    assert not (Path(values['appHome'])/'sessions'/params['sessionId']).exists()
    out=perform(server,params|{'bundle':'approved','bundleRevision':digest(['approved',values['bundle']])});assert out['status']=='completed'
    cwd.rename(cwd.with_name('original-files'));cwd.mkdir()
    with pytest.raises(ValueError,match='directory identity'):runtime_scope(cwd,params['sessionId'],values)

@pytest.mark.asyncio
async def test_cancellation_joins_native_creation_before_gate_can_release(tmp_path,monkeypatch):
    import asyncio,threading
    import amplifier_acp.managed_session as module
    entered=threading.Event();release=threading.Event()
    def effect(*_):entered.set();release.wait(5);return {'status':'completed'}
    monkeypatch.setattr(module,'perform',effect)
    task=asyncio.create_task(module.dispatch(None,{}));await asyncio.to_thread(entered.wait,5);task.cancel();await asyncio.sleep(.02);assert not task.done();release.set()
    with pytest.raises(asyncio.CancelledError):await task
