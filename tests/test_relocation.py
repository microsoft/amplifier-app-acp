import asyncio,hashlib,json,subprocess,uuid
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_same_native_history_survives_worktree_relocation_and_fresh_process_resume(tmp_path):
    config,cwd=configuration(tmp_path)
    subprocess.run(['git','init',str(cwd)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(cwd),'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','--allow-empty','-m','fixture'],check=True,capture_output=True)
    target=tmp_path/'worktree';subprocess.run(['git','-C',str(cwd),'worktree','add','-b','test',str(target)],check=True,capture_output=True)
    async def launch():
        c=await Client.launch(config);await c.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}});return c
    client=await launch();next_client=None
    try:
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'Before worktree handoff'}]})
        original=list((tmp_path/'home/projects').glob('*/sessions/'+sid+'/transcript.jsonl'))[0]
        async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        initial=await control('session.relocation.inspect');assert initial['result']['executionRevision']==0
        result=await control('session.relocate',{'commandId':'move1','target':str(target),'expectedExecutionRevision':0})
        assert result.get('result',{}).get('applied'),result
        assert result['result']['historyCwd']==str(cwd) and result['result']['executionDirectory']==str(target)
        locations=list((tmp_path/'home/projects').glob('*/sessions/'+sid));assert len(locations)==1 and locations[0]==original.parent
        snapshot=(await control('configuration.snapshot'))['result']
        assert snapshot['executionDirectory']==str(target) and snapshot['locationRevision']==1
        clone=await client.request('_amplifier/session/new',{'sessionId':str(uuid.uuid4()),'cwd':str(cwd),'source':snapshot,'mcpServers':[],'commandId':'relocated-clone'})
        assert clone.get('result',{}).get('_meta',{}).get('amplifier.dev/native',{}).get('executionDirectory')==str(target),clone
        await client.request('session/close',{'sessionId':clone['result']['sessionId']})
        passive=await client.request('_amplifier/relocation',{'sessionId':sid,'historyCwd':str(cwd)});assert not passive['result']['blocked']
        await client.request('session/close',{'sessionId':sid});await client.close();client=None
        next_client=await launch()
        resumed=await next_client.request('session/resume',{'sessionId':sid,'cwd':str(target),'mcpServers':[],'_meta':{'amplifier.dev/native':{'historyCwd':str(cwd)}}})
        assert resumed['result']['_meta']['amplifier.dev/native']['executionDirectory']==str(target),resumed
        reply=await next_client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'After worktree handoff'}]})
        assert reply['result']['stopReason']=='end_turn'
        history=await next_client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        assert [r['content']['text'] for r in history['result']['updates'] if r['sessionUpdate']=='user_message_chunk']==['Before worktree handoff','After worktree handoff']
        stale=await next_client.request('_amplifier/native',{'sessionId':sid,'operation':'session.relocate','args':{'commandId':'move2','target':str(cwd),'expectedExecutionRevision':0}})
        assert 'revision changed' in stale.get('error',{}).get('message','')
    finally:
        if client:await client.close()
        if next_client:await next_client.close()

@pytest.mark.asyncio
async def test_passive_unknown_fence_does_not_mount_or_replay(tmp_path):
    config,cwd=configuration(tmp_path);sid='fixture-unknown';directory=tmp_path/'app/sessions'/sid;directory.mkdir(parents=True)
    receipt={'commandId':'unknown1','signature':'x','status':'remounting','historyCwd':str(cwd),'from':str(cwd),'target':str(tmp_path),'expectedExecutionRevision':0}
    (directory/'execution-relocation.json').write_text(json.dumps(receipt))
    c=await Client.launch(config)
    try:
        await c.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        result=await c.request('_amplifier/relocation/reconcile',{'sessionId':sid,'historyCwd':str(cwd),'commandId':'unknown1'})
        assert result['result']['blocked'] and result['result']['reconciled'] is False
        assert not (tmp_path/'home/projects').exists()
        assert not any(e.get('method')=='session/update' for e in c.events)
    finally:await c.close()
