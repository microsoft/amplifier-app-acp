"""Connection identity separates credentials and sources, not workspace labels."""
import asyncio
import json
import pytest
from amplifier_acp.native.preferences import SettingsStore
from amplifier_acp.native.setup import SetupManager
from amplifier_acp.native.provider_catalog import ProviderCatalog, configuration_key, model_key

def test_configuration_identity_materializes_secrets_but_isolates_inputs(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-first')
    reference={'api_key':'${OPENAI_API_KEY}','default_model':'model'}
    base=configuration_key(tmp_path,'provider-openai',reference,'source')
    assert base==configuration_key(tmp_path,'provider-openai',{'api_key':'private-first','default_model':'model'},'source')
    assert base==configuration_key(tmp_path,'provider-openai',{**reference,'priority':20},'source')
    assert base!=configuration_key(tmp_path/'other','provider-openai',reference,'source')
    assert base!=configuration_key(tmp_path,'provider-openai',reference,'other-source')
    assert base!=configuration_key(tmp_path,'provider-openai',{**reference,'base_url':'https://other.invalid'},'source')
    assert base!=configuration_key(tmp_path,'provider-openai',{**reference,'default_model':'other'},'source')
    monkeypatch.setenv('OPENAI_API_KEY','private-rotated')
    assert base!=configuration_key(tmp_path,'provider-openai',reference,'source')
    token=tmp_path/'credential.json';token.write_text('first')
    before=configuration_key(tmp_path,'provider-fixture',{'token_file_path':str(token)},'source')
    token.write_text('second')
    assert before!=configuration_key(tmp_path,'provider-fixture',{'token_file_path':str(token)},'source')
    assert 'private' not in base and len(base)==64
    assert configuration_key(tmp_path,'provider-fixture',{'access_token':'x'*8000},'source')
    assert configuration_key(tmp_path,'provider-fixture',{'token_file_path':'x'*8000},'source')


async def test_worker_advertises_digest_for_actual_mounted_config(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from amplifier_acp.native.runtime_controls import RuntimeControls
    from amplifier_acp.native.provider_catalog import mounted_catalog_keys
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret')
    home=tmp_path/'app'
    receipt=home/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text(json.dumps([{
        'name':'amplifier-module-provider-openai','version':'1.0',
        'directUrl':{'url':'https://github.com/microsoft/amplifier-module-provider-openai',
            'vcs_info':{'vcs':'git','requested_revision':'a'*40,'commit_id':'a'*40}}}]))
    (receipt/'runtime-sources.json').write_text(json.dumps({'amplifier-module-provider-openai':{
        'url':'https://github.com/microsoft/amplifier-module-provider-openai','ref':'main'}}))
    SettingsStore(home).update(tmp_path,'global',lambda settings:settings.update(config={'providers':[
        {'id':'setup','module':'provider-openai','config':{'api_key':'${OPENAI_API_KEY}','default_model':'fixture-model'}}]}))
    manager=SetupManager(home)
    config={'api_key':'fixture-secret','default_model':'fixture-model'}
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={'model':'fixture-model'}))
    providers={'mounted':provider}
    loop=SimpleNamespace(root_provider=None,_select_provider=lambda rows:provider)
    plan={'providers':[{'instance_id':'mounted','module':'provider-openai','config':config}]}
    sources={'mounted':'git+https://github.com/microsoft/amplifier-module-provider-openai@main'}
    capabilities={'web.provider_catalog':{'home':str(home),'workspace':str(tmp_path),'sources':sources,
        'keys':mounted_catalog_keys(home,tmp_path,plan['providers'],sources)}}
    coordinator=SimpleNamespace(config=plan,session_state={},
        get=lambda name:{'providers':providers,'orchestrator':loop}.get(name),
        get_capability=capabilities.get,register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(session_id='fixture',coordinator=coordinator,config=plan),SimpleNamespace(generation=None,queued_inputs=0))
    result=await controls.perform('configuration.providers')
    row=result['providers'][0]
    assert row['sharedCatalogKey']==manager.catalog_key({'id':'setup'},str(tmp_path))
    assert 'fixture-secret' not in str(result)
    previous=row['sharedCatalogKey']
    monkeypatch.setenv('OPENAI_API_KEY','rotated-secret')
    rotated=await controls.perform('configuration.providers')
    assert 'sharedCatalogKey' not in rotated['providers'][0]
    assert manager.catalog_key({'id':'setup'},str(tmp_path))!=previous
    monkeypatch.setenv('OPENAI_API_KEY','fixture-secret')
    (home/'updates').mkdir(parents=True,exist_ok=True)
    (home/'updates/active.json').write_text('{"current":"11111111111111111111111111111111"}')
    upgraded=await controls.perform('configuration.providers')
    assert 'sharedCatalogKey' not in upgraded['providers'][0]
    assert manager.catalog_key({'id':'setup'},str(tmp_path))!=previous
    await controls.close()


def test_implicit_unqualified_and_local_override_do_not_match_bundle_source(tmp_path):
    source='git+https://github.com/microsoft/amplifier-module-provider-openai@main'
    def key(source=None):return configuration_key(tmp_path,'provider-openai',{},source,home=tmp_path)
    assert key()!=key(source)
    receipt=tmp_path/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text(json.dumps([{
        'name':'amplifier-module-provider-openai','version':'1.0',
        'directUrl':{'url':'file:///fixture/local-provider','dir_info':{'editable':True}}}]))
    assert key()!=key(source)


async def test_persistent_catalog_is_invalidated_by_runtime_receipt_change(tmp_path):
    home=tmp_path/'app';cache=ProviderCatalog(home/'catalog.json')
    identity=configuration_key(tmp_path,'provider-fixture',{},'same-branch',home=home)
    await cache.get(model_key(identity),lambda:asyncio.sleep(0,result={'models':[{'id':'old'}]}))
    receipt=home/'updates/baseline-runtime';receipt.mkdir(parents=True)
    (receipt/'runtime-installed.json').write_text('{"provider-fixture":"new-commit"}')
    newer=configuration_key(tmp_path,'provider-fixture',{},'same-branch',home=home)
    restarted=ProviderCatalog(home/'catalog.json')
    assert newer!=identity and restarted.peek(model_key(newer)) is None


async def test_rotated_credential_file_never_relabels_mounted_provider(tmp_path):
    from types import SimpleNamespace
    from amplifier_acp.native.runtime_controls import RuntimeControls
    from amplifier_acp.native.provider_catalog import mounted_catalog_keys
    token=tmp_path/'credential.json';token.write_text('old-token')
    row={'instance_id':'fixture','module':'provider-fixture','config':{'token_file_path':str(token)}}
    sources={'fixture':'source'}
    captured={'home':str(tmp_path),'workspace':str(tmp_path),'sources':sources,
              'keys':mounted_catalog_keys(tmp_path,tmp_path,[row],sources)}
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(defaults={}))
    coordinator=SimpleNamespace(config={'providers':[row]},session_state={},
        get=lambda name:{'providers':{'fixture':provider},'orchestrator':SimpleNamespace(root_provider=None,_select_provider=lambda rows:provider)}.get(name),
        get_capability=lambda name:captured if name=='web.provider_catalog' else None,register_capability=lambda *args:None)
    controls=RuntimeControls(SimpleNamespace(session_id='file-token',coordinator=coordinator,config=coordinator.config),SimpleNamespace(generation=None,queued_inputs=0))
    assert 'sharedCatalogKey' in (await controls.perform('configuration.providers'))['providers'][0]
    token.write_text('new-token')
    assert 'sharedCatalogKey' not in (await controls.perform('configuration.providers'))['providers'][0]
    await controls.close()


def test_connection_identity_shares_absolute_settings_but_isolates_nested_relative_files(tmp_path):
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir()
    config={'base_url':'https://fixture.invalid','api_key':'synthetic'}
    assert configuration_key(one,'provider-test',config)==configuration_key(two,'provider-test',config)
    relative={**config,'auth':{'token_file':'token.txt'}}
    (one/'token.txt').write_text('synthetic-one');(two/'token.txt').write_text('synthetic-two')
    first=configuration_key(one,'provider-test',relative)
    assert first!=configuration_key(two,'provider-test',relative)
    (one/'token.txt').write_text('changed-fixture')
    assert first!=configuration_key(one,'provider-test',relative)

async def test_independent_workers_share_only_matching_catalogs_and_failed_refresh_is_not_fresh(tmp_path):
    from amplifier_acp.native.provider_catalog import SharedProviderCatalog
    first,second=SharedProviderCatalog(tmp_path),SharedProviderCatalog(tmp_path)
    calls=[]
    async def load():calls.append(1);return {'models':[{'id':'actual'}]}
    async def fail():raise ValueError('Connection unavailable')
    key=model_key('same-qualified-identity')
    await first.get(key,load)
    assert (await second.get(key,load))['models']==[{'id':'actual'}] and len(calls)==1
    with pytest.raises(ValueError):await second.get(key,fail,refresh=True)
    assert not second.fresh(key)
    assert not first.fresh(key)
    with pytest.raises(ValueError):await first.get(key,load)
    with pytest.raises(ValueError):await second.get(key,load)
    assert SharedProviderCatalog(tmp_path).peek(key) is None
    await second.get(key,load,refresh=True)
    assert second.fresh(key) and len(calls)==2
