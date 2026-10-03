import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_acp.native.managed_models import CatalogUnavailable,discover,page
from native_setup import configuration
from client import Client

@pytest.mark.asyncio
async def test_selected_bundle_models_are_passive_paged_and_exact(tmp_path):
    config,_=configuration(tmp_path);values=json.loads(config.read_text());values['managedSessionRoots']=[str(tmp_path/'managed')];values['forkBundles']={'approved':values['bundle']};values['workerCommand']=['/impossible-worker'];config.write_text(json.dumps(values))
    import yaml
    bundle=Path(values['bundle']);document=yaml.safe_load(bundle.read_text());document['providers'][0]['instance_id']='bundle-only'
    document['providers'][0]['config']={'fixtureModels':[{'id':'first-model','defaults':{'api_key':'private','reasoning_effort':'low'}},{'id':'second-model','display_name':'Second model'}]}
    # An unrelated enabled source cannot be prepared by selected discovery.
    document['providers'].append({'module':'provider-never-import','source':'/impossible-source','config':{}})
    bundle.write_text(yaml.safe_dump(document))
    client=await Client.launch(config)
    try:
        init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['managedCreation']['models']['maxPageSize']==100
        choice=(await client.request('_amplifier/managed',{'operation':'options'}))['result']['bundles'][0]
        query={'operation':'models','bundle':'approved','bundleRevision':choice['revision'],'instance':'bundle-only','limit':1}
        first=await client.request('_amplifier/managed',query);assert 'result' in first,first;first=first['result']
        assert first['models'][0]['id']=='first-model' and first['modelsSupported'] is True and first['total']==2
        assert first['runtimeStarted'] is False and first['globalOnly'] is True and first['bundleSelection']['id']=='approved'
        assert 'private' not in json.dumps(first) and 'bundle' not in first
        second=(await client.request('_amplifier/managed',query|{'cursor':first['nextCursor']}))['result'];assert second['models'][0]['id']=='second-model';assert 'nextCursor' not in second
        assert 'error' in await client.request('_amplifier/managed',query|{'instance':'absent'})
        assert 'error' in await client.request('_amplifier/managed',query|{'bundleRevision':'0'*64})
        assert 'error' in await client.request('_amplifier/managed',query|{'limit':101})
        document['providers'][0]['config']['fixtureModels'].append({'id':'third-model'});bundle.write_text(yaml.safe_dump(document))
        changed=await client.request('_amplifier/managed',query|{'cursor':first['nextCursor']});assert 'error' in changed
        assert not (Path(values['appHome'])/'sessions').exists() and not (tmp_path/'managed').exists()
        assert not (Path(values['home'])/'projects').exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_model_discovery_bounds_unknown_support_and_safe_projection():
    assert (await discover(object(),'none','provider-none'))=={'instance':'none','module':'provider-none','modelsSupported':False,'models':[]}
    async def many():return [{'id':str(i)} for i in range(1001)]
    with pytest.raises(CatalogUnavailable,match='oversized'):await discover(SimpleNamespace(list_models=many),'x','module')
    async def large():return [{'id':'x','display_name':'x'*524288}]
    with pytest.raises(CatalogUnavailable,match='oversized'):await discover(SimpleNamespace(list_models=large),'x','module')
    async def invalid():return [{'id':'same'},{'id':'same'}]
    with pytest.raises(CatalogUnavailable,match='invalid'):await discover(SimpleNamespace(list_models=invalid),'x','module')
    result={'instance':'x','modelsSupported':True,'models':[{'id':'a'},{'id':'b'}]};first=page(result,['scope'],1)
    with pytest.raises(CatalogUnavailable,match='cursor-invalid'):page(result,['another'],1,first['nextCursor'])
    with pytest.raises(CatalogUnavailable,match='changed'):page(result|{'models':[{'id':'a'},{'id':'c'}]},['scope'],1,first['nextCursor'])

@pytest.mark.asyncio
async def test_failed_catalog_does_not_expose_provider_error(tmp_path):
    import yaml
    config,_=configuration(tmp_path);values=json.loads(config.read_text());values['managedSessionRoots']=[str(tmp_path/'managed')];values['workerCommand']=['/impossible-worker'];config.write_text(json.dumps(values));bundle=Path(values['bundle']);document=yaml.safe_load(bundle.read_text());document['providers'][0]['config']['modelDiscoveryError']=True;bundle.write_text(yaml.safe_dump(document))
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        value=await client.request('_amplifier/managed',{'operation':'models','instance':'fixture'});assert 'error' in value and 'private detail' not in json.dumps(value)
    finally:await client.close()
