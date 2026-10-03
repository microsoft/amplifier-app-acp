"""Effective launcher source identity across setup, probe and mounted catalogs."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_acp.native.setup import SetupManager, provider_source
from amplifier_acp.native.provider_catalog import ProviderCatalog, configuration_key, model_key, mounted_catalog_keys

MAIN='git+https://example.invalid/provider@main'

@pytest.fixture
def catalog_setup(tmp_path):
    row={'id':'test','module':'provider-test','source':MAIN,'config':{}}
    config=SimpleNamespace(providers=[row],module_sources={},launcher_module_sources={},settings={})
    manager=SetupManager.__new__(SetupManager)
    manager.home=tmp_path/'unused-home'
    manager.config=lambda workspace:config
    manager.catalog=ProviderCatalog()
    return manager,config,tmp_path

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['providers.models','providers.schema'])
async def test_changing_override_invalidates_models_and_schema_and_matches_mounted(catalog_setup,action):
    manager,config,workspace=catalog_setup
    row=config.providers[0];original=copy.deepcopy(row)
    config.launcher_module_sources={'provider-test':str(workspace/'a')}
    key_a=manager.catalog_key({'id':'test'},workspace)
    manager.catalog.put(model_key(key_a),{'models':[{'id':'model-from-a'}],'providerMetadata':{'marker':'a'}})
    config.launcher_module_sources={'provider-test':str(workspace/'b')}
    key_b=manager.catalog_key({'id':'test'},workspace)
    assert key_b!=key_a
    assert key_b==mounted_catalog_keys(manager.home,workspace,[row],{'test':str(workspace/'b')})['test']
    calls=[]
    async def probe(request,args,cwd):
        calls.append(request)
        return {'models':[{'id':'model-from-b'}],'providerMetadata':{'marker':'b'}}
    manager.probe=probe
    result=await manager.cached_probe(action,{'id':'test'},workspace)
    assert result['providerMetadata']['marker']=='b' and calls==[action]
    assert row==original and not manager.home.exists()
    await manager.catalog.close()

@pytest.mark.asyncio
async def test_change_during_discovery_is_refused_and_old_result_discarded(catalog_setup):
    manager,config,workspace=catalog_setup
    config.launcher_module_sources={'provider-test':str(workspace/'a')}
    old=model_key(manager.catalog_key({'id':'test'},workspace))
    async def probe(*args):
        config.launcher_module_sources={'provider-test':str(workspace/'b')}
        return {'models':[{'id':'a'}]}
    manager.probe=probe
    with pytest.raises(ValueError,match='changed during discovery'):
        await manager.cached_probe('providers.models',{'id':'test'},workspace)
    assert not manager.catalog.fresh(old)
    await manager.catalog.close()

def test_default_unlisted_and_original_provenance_are_preserved(catalog_setup):
    manager,config,workspace=catalog_setup
    row=config.providers[0]
    baseline=manager.catalog_key({'id':'test'},workspace)
    assert baseline==configuration_key(workspace,'provider-test',{},MAIN,home=manager.home)
    config.launcher_module_sources={'provider-other':str(workspace/'other')}
    assert manager.catalog_key({'id':'test'},workspace)==baseline
    configured='git+https://user:credential-sentinel@host.invalid/p@main?token=credential-sentinel#secret=credential-sentinel'
    config.module_sources={'provider-test':configured}
    config.launcher_module_sources['provider-test']=str(workspace/'selected')
    public=manager.provider_rows(workspace)[0]
    assert public['source']==MAIN
    assert public['sourceSelection']=={'originalSource':'git+https://host.invalid/p@main','effectiveSource':str(workspace/'selected'),'authority':'trusted-launcher','override':True}
    assert 'credential-sentinel' not in json.dumps(public)
    assert provider_source(config,'provider-test',row)==str(workspace/'selected')
    assert config.module_sources['provider-test']==configured and row['source']==MAIN

@pytest.mark.asyncio
async def test_probe_payload_uses_same_effective_source_as_cache(catalog_setup,tmp_path):
    import sys
    manager,config,workspace=catalog_setup
    selected=str(tmp_path/'selected')
    config.launcher_module_sources={'provider-test':selected}
    child=tmp_path/'probe.py'
    child.write_text("import json,sys\nr=json.load(sys.stdin)\nassert r['source']=="+repr(selected)+"\nprint(json.dumps({'info':{},'configSchema':{'fields':[]}}))\n")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.probe('providers.schema',{'id':'test','module':'provider-test'},workspace)
    assert result['providerMetadata']['module']=='provider-test'
    assert not manager.home.exists()
