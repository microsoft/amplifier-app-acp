import json
from types import SimpleNamespace
import pytest
from client import Client
from native_setup import configuration
from amplifier_acp.native.capacity import CapacityController, policy_snapshot


def test_policy_retains_denial_and_rejects_corruption_without_disabling_budget():
    policy=policy_snapshot({'revision':3,'enabled':True,'maxTotalTokens':200})
    assert policy_snapshot(policy,{'budgetRevision':3,'reasons':['Prior limit reached']})['lastDenial']['reasons']==['Prior limit reached']
    for bad in [{'revision':False}, {'enabled':'no'}, {'enabled':True}, {'maxTotalTokens':float('inf')}, {'maxTotalTokens':1.5}, {'warningFraction':0}, {'unknownPolicy':'ignore'}]:
        with pytest.raises(ValueError):policy_snapshot(bad)
    with pytest.raises(ValueError):policy_snapshot(policy,{'budgetRevision':2,'reasons':['stale']})


@pytest.mark.asyncio
async def test_budget_edits_are_revision_checked_atomic_and_idempotent():
    writes=[];controls=SimpleNamespace(tasks=SimpleNamespace(record=lambda:None),persist=lambda **kw:writes.append(kw))
    c=CapacityController(controls)
    args={'commandId':'first','expectedRevision':0,'enabled':True,'maxTotalTokens':100}
    first=await c.perform('capacity.set',args);assert first['revision']==1 and len(writes)==1
    assert (await c.perform('capacity.set',args))['duplicate'] is True and len(writes)==1
    with pytest.raises(ValueError,match='different contents'):await c.perform('capacity.set',{**args,'maxTotalTokens':200})
    with pytest.raises(ValueError,match='revision changed'):await c.perform('capacity.set',{**args,'commandId':'stale'})
    def fail(**kw):raise OSError('disk full')
    controls.persist=fail
    with pytest.raises(OSError):await c.perform('capacity.set',{'commandId':'failed','expectedRevision':1,'enabled':False})
    assert c.snapshot()['enabled'] is True and c.snapshot()['revision']==1 and 'failed' not in c.receipts


@pytest.mark.asyncio
async def test_denied_admission_is_persisted_and_missing_host_cannot_bypass_enabled_budget():
    writes=[];c=CapacityController(SimpleNamespace(persist=lambda **kw:writes.append(kw)))
    c.policy.update(enabled=True,maxTotalTokens=100)
    with pytest.raises(ValueError,match='unavailable'):await c.guard({'id':'one'})
    async def deny(row):return {'allowed':False,'budgetRevision':0,'reasons':['Limit reached']}
    c.admit=deny
    with pytest.raises(ValueError,match='paused future'):await c.guard({'id':'one'})
    assert writes==[{'task_only':True}] and c.snapshot()['lastDenial']['reasons']==['Limit reached']


@pytest.mark.asyncio
async def test_installed_worker_sends_owner_policy_and_cold_inspection_starts_no_worker(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config)
    init={'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/host':{'version':1,'features':{'capacity':True}}}}}
    try:
        initialized=await client.request('initialize',init)
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['taskState']['capacity']['passivePolicy'] is True
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        saved=await client.request('_amplifier/native',{'sessionId':sid,'operation':'capacity.set','args':{'commandId':'budget-one','expectedRevision':0,'enabled':True,'maxTotalTokens':10}})
        assert saved['result']['revision']==1
        async def deny(request):
            assert request['params']['operation']=='capacity.admit'
            args=request['params']['args'];assert args['call']['rootSessionId']==sid and args['budget']['revision']==1 and args['budget']['maxTotalTokens']==10
            return {'allowed':False,'budgetRevision':1,'reasons':['Owned test budget reached']}
        client.response_handler=deny
        result=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'Do not call the provider when admission is refused.'}]})
        assert client.requests and ('error' in result or result.get('result',{}).get('stopReason')!='end_turn')
    finally:await client.close()
    values=json.loads(config.read_text());values['workerCommand']=['/must/not/start'];config.write_text(json.dumps(values));cold=await Client.launch(config)
    try:
        await cold.request('initialize',init)
        value=await cold.request('_amplifier/task/inspect',{'sessionId':sid,'cwd':str(cwd)})
        budget=value['result']['capacity']['budget'];assert budget['revision']==1 and budget['enabled'] is True and budget['lastDenial']['reasons']==['Owned test budget reached']
        assert not cold.requests and not cold.events
        path=tmp_path/'app'/'sessions'/sid/'control-state.json';state=json.loads(path.read_text());state['capacity']['enabled']='false';path.write_text(json.dumps(state))
        assert 'error' in await cold.request('_amplifier/task/inspect',{'sessionId':sid,'cwd':str(cwd)})
    finally:await cold.close()
