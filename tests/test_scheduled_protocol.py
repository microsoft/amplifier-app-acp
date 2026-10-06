import asyncio,json,uuid
import pytest
from client import Client
from native_setup import configuration

async def init(client):
    return await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})

@pytest.mark.asyncio
async def test_scheduled_stream_settlement_duplicate_and_monitor_goal(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config)
    try:
        initialized=await init(client)
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['scheduledTurns']['completion']=='settled'
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        made=await control('task.create',{'objective':'A saved ongoing task','maxTurns':3,'commandId':'fixture-task','expectedRevision':0})
        assert 'result' in made,made
        task=made['result']['task']
        args={'inputId':'schedule:fixture:run:1','text':'Finite monitor sample','kind':'monitor','taskId':task['id'],'taskRevision':task['revision']}
        result=await control('schedule.submit',args)
        assert result['result']=={'accepted':True,'inputId':args['inputId'],'stopReason':'end_turn',
                                 'usage':{'inputTokens':8,'outputTokens':5,'totalTokens':13}},result
        assert any(r.get('params',{}).get('update',{}).get('sessionUpdate')=='agent_message_chunk' for r in client.events)
        assert any(r.get('params',{}).get('event',{}).get('type')=='generation.finished' for r in client.events)
        again=await control('schedule.submit',args)
        assert again['error']['data']['outcome']=='already-admitted',again
        rejected=await control('schedule.submit',{**args,'inputId':'schedule:fixture:run:2','taskRevision':-1})
        assert rejected['result']['accepted'] is False,rejected
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        assert [u['content']['text'] for u in history['result']['updates'] if u['sessionUpdate']=='user_message_chunk']==['Finite monitor sample']
    finally:await client.close()

@pytest.mark.asyncio
async def test_separate_process_configuration_clone_preserves_modules_not_execution(tmp_path):
    config,cwd=configuration(tmp_path);a=await Client.launch(config);b=await Client.launch(config)
    try:
        await init(a);await init(b)
        sid=(await a.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(op,args={}):return await a.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        await control('provider.select',{'instance':'fixture','model':'other-model'})
        await control('budget.set',{'contextTokens':4321})
        await control('task.create',{'objective':'Do not copy this task','maxTurns':3,'commandId':'source-task','expectedRevision':0})
        source=(await control('configuration.snapshot'))['result']
        identity=str(uuid.uuid4());params={'sessionId':identity,'cwd':str(cwd),'mcpServers':[],'source':source,'commandId':'scheduled-create:fixture'}
        cloned=await b.request('_amplifier/session/new',params)
        assert cloned.get('result',{}).get('sessionId')==identity,cloned
        assert cloned['result']['_meta']['amplifier.dev/native']['creationConfirmed']
        model=next(o for o in cloned['result']['configOptions'] if o['id']=='model')
        assert next(o['name'] for group in model['options'] for o in group['options'] if o['value']==model['currentValue']).endswith('other-model')
        target=await b.request('_amplifier/native',{'sessionId':identity,'operation':'task.get'})
        assert not target['result'].get('task'),target
        controls=json.loads((tmp_path/'app/sessions'/identity/'control-state.json').read_text())
        assert controls['budget']['contextTokens']==4321
        duplicate=await b.request('_amplifier/session/new',params)
        assert 'error' in duplicate and 'already exists' in duplicate['error']['message']
        await control('budget.set',{'contextTokens':9876})
        stale=await b.request('_amplifier/session/new',{**params,'sessionId':str(uuid.uuid4())})
        assert 'changed' in stale.get('error',{}).get('message',''),stale
    finally:await a.close();await b.close()
