"""Actual Core/Foundation ACP controls; all discovery/inference uses inert providers."""
import asyncio
import json
from pathlib import Path
import pytest
import yaml
from client import Client
from native_setup import configuration

NATIVE='amplifier.dev/native'


def fixture(tmp_path):
    config,cwd=configuration(tmp_path)
    values=json.loads(config.read_text()); bundle=Path(values['bundle']); doc=yaml.safe_load(bundle.read_text())
    source=doc['providers'][0]['source']; audit=tmp_path/'selections'; discovery=tmp_path/'discovery'
    catalogs={}
    rows=[]
    for instance in ('personal','work'):
        catalogs[instance]=tmp_path/(instance+'.json')
        catalogs[instance].write_text(json.dumps([{'id':instance+'-default'}, {'id':instance+'-new','display_name':'Discovered '+instance,
            'defaults':{'supported_reasoning_efforts':['quiet','thorough']},'context_window':8192}]))
        rows.append({'module':'provider-fixture','source':source,'instance_id':instance,'config':{
            'fixtureLabel':instance,'fixtureDefaultModel':instance+'-default','fixtureModelsFile':str(catalogs[instance]),
            'modelDiscoveryAudit':str(discovery),'requestSelectionAudit':str(audit)}})
    doc['providers']=rows;bundle.write_text(yaml.safe_dump(doc))
    values['moduleSources']={'provider-fixture':source}
    config.write_text(json.dumps(values))
    return config,cwd,audit,discovery,catalogs


def choice(options,instance,model):
    option=next(row for row in options if row['id']=='model')
    return next(row['value'] for group in option['options'] if group['group']==instance
                for row in group['options'] if row['_meta'][NATIVE]['model']==model)


def updates(client):
    return [e['params']['update']['configOptions'] for e in client.events if e.get('method')=='session/update' and e['params']['update']['sessionUpdate']=='config_option_update']


@pytest.mark.asyncio
async def test_lazy_discovery_atomic_standard_commit_effort_and_cold_resume(tmp_path):
    config,cwd,audit,discovery,catalogs=fixture(tmp_path); client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{NATIVE:{'version':1}}}})
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(operation,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':operation,'args':args})
        # Passive until a deliberate catalog read; initialization never enumerates every provider.
        catalog=(await control('configuration.providers'))['result']
        assert not audit.exists() and not discovery.exists()
        initial_effective=catalog['effective']; initial_files={p:p.read_bytes() for p in (tmp_path/'app/sessions'/sid).glob('*.json')}
        loaded=await control('configuration.providerModels',{'provider':'work'})
        assert loaded['result']['supported'] is True
        assert discovery.read_text().splitlines()==['work'] and not audit.exists()
        assert (await control('configuration.providers'))['result']['effective']==initial_effective
        assert all(p.read_bytes()==body for p,body in initial_files.items())
        model_value=choice(updates(client)[-1],'work','work-new')
        await control('configuration.providerModels',{'provider':'work'})
        assert discovery.read_text().splitlines()==['work'], 'The existing single-flight cache owns discovery'
        assert choice(updates(client)[-1],'work','work-new')==model_value
        selected=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':model_value})
        assert 'result' in selected,selected
        assert selected['result']['configOptions']==updates(client)[-1]
        effective=(await control('configuration.providers'))['result']
        assert effective['selection']=={'instance':'work','model':'work-new'}
        effort=next(r for r in selected['result']['configOptions'] if r['category']=='thought_level')
        thorough=next(r['value'] for r in effort['options'] if r['name']=='thorough')
        changed=await client.request('session/set_config_option',{'sessionId':sid,'configId':'reasoning_effort','value':thorough})
        assert 'result' in changed,changed
        prompt=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'deliberate offline selected request'}]})
        assert prompt['result']['stopReason']=='end_turn'
        assert {'instance':'work','model':'work-new','effort':'thorough'} in [json.loads(s) for s in audit.read_text().splitlines()]
        # Failure/refresh never silently unpins or substitutes a model.
        catalogs['work'].write_text(json.dumps({'error':True}))
        failed=await control('configuration.providerModels',{'provider':'work','refresh':True})
        assert 'error' in failed
        assert choice(updates(client)[-1],'work','work-new')==model_value
        assert (await control('configuration.providers'))['result']['selection']=={'instance':'work','model':'work-new','effort':'thorough'}
        assert next(g for r in updates(client)[-1] if r['id']=='model' for g in r['options'] if g['group']=='work')['_meta'][NATIVE]['catalogState']=='unavailable'
    finally:await client.close()
    # Reconnect: effective saved pin remains represented without discovery or replay.
    count=len(discovery.read_text().splitlines()); old_requests=audit.read_bytes(); client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}})
        loaded=await client.request('session/load',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert 'result' in loaded,loaded
        # Explicit standard change starts the cold handle, without remote discovery.
        replay=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':model_value})
        assert 'result' in replay,replay
        assert choice(replay['result']['configOptions'],'work','work-new')==model_value
        assert len(discovery.read_text().splitlines())==count and audit.read_bytes()==old_requests
    finally:await client.close()


@pytest.mark.asyncio
async def test_removed_option_and_old_effort_refuse_without_effect_other_client_can_prompt(tmp_path):
    config,cwd,audit,discovery,catalogs=fixture(tmp_path);client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{NATIVE:{'version':1}}}})
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        await control('configuration.providerModels',{'provider':'work'})
        old=choice(updates(client)[-1],'work','work-new')
        selected=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':old})
        effort=next(r for r in selected['result']['configOptions'] if r['id']=='reasoning_effort')
        old_effort=next(r['value'] for r in effort['options'] if r['name']=='thorough')
        await client.request('session/set_config_option',{'sessionId':sid,'configId':'reasoning_effort','value':old_effort})
        await control('configuration.providerModels',{'provider':'personal'})
        alternate=choice(updates(client)[-1],'personal','personal-new')
        await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':alternate})
        assert (await control('configuration.providers'))['result']['selection']=={'instance':'personal','model':'personal-new'}
        stale_effort=await client.request('session/set_config_option',{'sessionId':sid,'configId':'reasoning_effort','value':old_effort})
        assert stale_effort['error']['code']==-32602
        catalogs['work'].write_text(json.dumps([{'id':'replacement'}]))
        await control('configuration.providerModels',{'provider':'work','refresh':True})
        before=(await control('configuration.providers'))['result']['selection']
        stale=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':old})
        assert stale['error']['code']==-32602
        assert (await control('configuration.providers'))['result']['selection']==before
        # Merely browsing a different provider is neither a backend draft nor a prompt guard.
        prompt=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'ordinary offline request while browsing'}]})
        assert prompt['result']['stopReason']=='end_turn'
        assert all(json.loads(s)['model']!='replacement' for s in audit.read_text().splitlines())
        replacement=choice(updates(client)[-1],'work','replacement')
        result=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':replacement})
        assert 'result' in result,result
        assert (await control('configuration.providers'))['result']['selection']=={'instance':'work','model':'replacement'}
        assert not any(r['category']=='thought_level' for r in result['result']['configOptions'])
        invalid=await client.request('session/set_config_option',{'sessionId':sid,'configId':'reasoning_effort','value':'unadvertised'})
        assert invalid['error']['code']==-32602
        reset=await client.request('session/set_config_option',{'sessionId':sid,'configId':'model','value':'__default__'})
        assert 'result' in reset,reset
        assert (await control('configuration.providers'))['result']['pinned'] is False
    finally:await client.close()
