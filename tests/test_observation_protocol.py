import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_observation_typed_admission_settles_without_tools_or_replay(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config);approved=False;calls=[]
    async def reply(row):
        assert row['method']=='_amplifier/host' and row['params']['operation']=='observation.admit'
        calls.append(row['params']['args']);return {'admitted':approved}
    client.response_handler=reply
    try:
        initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/host':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['observationTurns']['completion']=='settled'
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(op,args):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        task=(await control('task.create',{'objective':'Observe fixture outcome','maxTurns':3,'commandId':'task1','expectedRevision':0}))['result']['task']
        args={'inputId':'observation:watch1:result1','watchId':'watch1','taskId':task['id'],'taskRevision':task['revision'],'outcome':{'status':'changed','facts':['fixture']}}
        rejected=await control('observation.submit',args);assert rejected['result']['accepted'] is False
        approved=True
        settled=await control('observation.submit',args);assert settled['result']['stopReason']=='end_turn',settled
        assert settled['result']['accepted'] is True
        assert any(r.get('params',{}).get('update',{}).get('sessionUpdate')=='agent_message_chunk' for r in client.events)
        duplicate=await control('observation.submit',args);assert duplicate['error']['data']['outcome']=='already-admitted'
        assert len(calls)==2
        assert not any(r.get('params',{}).get('update',{}).get('sessionUpdate')=='tool_call' for r in client.events)
    finally:await client.close()
