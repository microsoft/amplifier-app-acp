import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_acp.module_sources import ENV, configure, validate, public_source
from amplifier_acp.native.host.config import read_config
from amplifier_acp.native.host.session import module_source
from amplifier_acp.native.host.components import HostComponents, ComponentResolver

MAIN='git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@main'

@pytest.mark.parametrize('value',[None, [], {'Bad ID':'/tmp'}, {'provider-test':'relative'}, {'provider-test':'/not/a/real/directory'}, {'provider-test':False}, {f'tool-{i}':'/tmp' for i in range(65)}, {'provider-test':'git+https://user:secret@github.com/a/b@'+'a'*40}, {'provider-test':'git+https://github.com/a/b@main'}, {'provider-test':'git+https://github.com/a/b@'+'a'*40+'?token=secret'}, {'provider-test':'git+https://github.com/a/b@'+'a'*40+'#subdirectory=../escape'}, {'provider-test':'git+https://github.com/a/b@'+'a'*40+'#secret'}])
def test_invalid_maps_refuse_without_disclosing_values(value):
    with pytest.raises(ValueError) as caught:validate(value)
    assert 'secret' not in str(caught.value)

def test_scoped_override_and_default_policy(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir()
    source=tmp_path/'selected';source.mkdir()
    settings=home/'settings.yaml';settings.write_text('sources:\n  modules:\n    provider-test: '+MAIN+'\n')
    before=settings.read_bytes()
    monkeypatch.setenv(ENV,json.dumps({'provider-test':str(source)}))
    config=read_config(workspace,shared_home=home,home=tmp_path/'app')
    assert config.module_sources['provider-test']==MAIN
    assert module_source(config,False,'provider-test','bundle-source')==str(source)
    assert module_source(config,True,'provider-test','saved-snapshot-source')==str(source)
    assert module_source(config,False,'provider-other','unchanged')=='unchanged'
    assert settings.read_bytes()==before
    configure({})
    baseline=read_config(workspace,shared_home=home,home=tmp_path/'app')
    assert not baseline.launcher_module_sources
    assert module_source(baseline,False,'provider-test','bundle-source')==MAIN
    assert module_source(baseline,True,'provider-test','saved-snapshot-source')=='saved-snapshot-source'
    assert settings.read_bytes()==before

def test_source_identity_validation_redaction_and_worker_authority(tmp_path):
    exact='git+https://github.com/microsoft/module@'+'a'*40+'#subdirectory=modules/provider-test'
    assert validate({'provider-test':exact})=={'provider-test':exact}
    env={ENV:'inherited-untrusted-worker-policy'}
    assert configure({},env)=={} and env[ENV]=='{}'
    assert public_source('git+https://user:secret@github.com/a/b@main?token=private#secret=value')=='git+https://github.com/a/b@main'
    assert 'secret' not in public_source('https://user:secret@github.com/a/b?key=secret')

@pytest.mark.asyncio
async def test_dynamic_children_preserve_exact_overrides_not_sibling_sources():
    calls=[]
    class Resolver:
        def resolve(self,module,source):calls.append((module,source));return source
        async def async_resolve(self,module,source):return self.resolve(module,source)
    policy=HostComponents({'provider-test':'host-source','tool-other':'other-source'})
    resolver=ComponentResolver(Resolver(),policy,{'provider-test':'operator-source'})
    assert resolver.resolve('provider-test','saved-source')=='operator-source'
    assert await resolver.async_resolve('provider-test','saved-source')=='operator-source'
    assert resolver.resolve('tool-other','different')=='other-source'
    assert resolver.resolve('tool-unlisted','unchanged')=='unchanged'


def test_full_archive_retains_selector_as_external_launcher_provenance(tmp_path):
    from amplifier_acp.full_native_archive import source_config
    result=source_config(SimpleNamespace(config={'moduleSources':{'provider-test':str(tmp_path)}}),[])
    assert result['moduleSourceOverrides']=={'sources':{'provider-test':str(tmp_path)},'authority':'trusted-launcher','sourceBodies':'only-within-declared-native-roots','automaticRestore':False}

@pytest.mark.asyncio
async def test_setup_metadata_uses_override_without_old_import_or_model_calls(tmp_path,monkeypatch):
    from amplifier_acp.native import provider_probe
    from amplifier_foundation import Bundle
    monkeypatch.setenv(ENV,json.dumps({'provider-test':str(tmp_path)}))
    seen=[]
    async def prepare(bundle,**kwargs):seen.append(bundle.providers[0]['source'])
    monkeypatch.setattr(Bundle,'prepare',prepare)
    class Provider:
        def __init__(self,config=None):pass
        def get_info(self):return {'config_fields':[]}
        async def list_models(self):raise AssertionError('No model catalogue calls')
        async def close(self):pass
    def provider_class(module):
        assert seen==[str(tmp_path)] # source activation precedes class import
        return Provider
    monkeypatch.setattr(provider_probe,'provider_class',provider_class)
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path))
    result=await provider_probe.query({'module':'provider-test','source':MAIN,'registryHome':str(tmp_path),'action':'providers.schema'})
    assert result['configSchema']=={'fields':[]}

@pytest.mark.asyncio
async def test_invalid_launcher_refuses_before_acp_initialization(tmp_path):
    import asyncio,sys
    config=tmp_path/'invalid.json';config.write_text(json.dumps({'moduleSources':{'provider-test':'git+https://user:private-value@github.com/a/b@main'}}))
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','amplifier_acp','--config',str(config),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await asyncio.wait_for(process.communicate(),10)
    assert process.returncode!=0 and out==b''
    assert b'private-value' not in err
