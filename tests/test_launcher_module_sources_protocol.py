"""Actual fresh ACP/Core/Foundation selected-source proof, no provider requests."""
import importlib.util,json,os,shutil,sys
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration

MAIN='git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@main'

@pytest.mark.asyncio
async def test_exact_launcher_source_wins_fresh_worker_and_restart(tmp_path,monkeypatch):
    source=os.environ.get('CHATGPT_PROVIDER_SOURCE')
    if not source:pytest.skip('Set CHATGPT_PROVIDER_SOURCE to qualified1a provider source')
    if source=='installed':source=str(Path(importlib.util.find_spec('amplifier_module_provider_openai_chatgpt').origin).parent.parent)
    source=Path(source).resolve()
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE','1')
    config,cwd=configuration(tmp_path)
    values=json.loads(config.read_text());home=Path(values['home']);settings=home/'settings.yaml'
    settings.write_text(settings.read_text()+'sources:\n  modules:\n    provider-openai-chatgpt: '+MAIN+'\n')
    original_settings=settings.read_bytes()
    bundle=Path(values['bundle'])
    bundle.write_text(bundle.read_text()+'\n  - module: provider-openai-chatgpt\n    source: '+MAIN+'\n    instance_id: unavailable-chatgpt\n    config:\n      auth_mode: chatgpt_codex\n      login_on_mount: false\n      token_file_path: '+str(tmp_path/'absent-tokens.json')+'\n')
    original_bundle=bundle.read_bytes()
    values['registryHome']=str(tmp_path/'candidate-registry')
    audit=tmp_path/'forbidden-effects';guard=tmp_path/'guard.py'
    # Do not pre-import or add the selected provider to sys.path: actual
    # Foundation activation and Core's canonical source guard must choose it.
    guard.write_text('''import sys,runpy
from pathlib import Path
def observe(event,args):
 if event in {'socket.connect','socket.getaddrinfo'}:
  Path('''+repr(str(audit))+''').write_text('network attempt')
  raise AssertionError('No network or auth permitted')
sys.addaudithook(observe)
runpy.run_module('amplifier_acp.native.runtime_worker',run_name='__main__')
''')
    values['workerCommand']=[sys.executable,str(guard)]
    sid=None
    paths=[]
    for n in range(2):
        selected=tmp_path/('sealed-source-'+str(n));selected.mkdir()
        shutil.copytree(source/'amplifier_module_provider_openai_chatgpt',selected/'amplifier_module_provider_openai_chatgpt',ignore=shutil.ignore_patterns('__pycache__'))
        # Foundation's local source resolver accepts a package/root without a build.
        values['moduleSources']={'provider-openai-chatgpt':str(selected)}
        config.write_text(json.dumps(values))
        client=await Client.launch(config)
        try:
            init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
            assert 'result' in init,init
            created=await client.request('session/new' if sid is None else 'session/resume',{'cwd':str(cwd),'mcpServers':[],**({'sessionId':sid} if sid else {})})
            assert 'result' in created,created
            sid=sid or created['result']['sessionId']
            # Resume registers identity without a worker. Explicit selected
            # configuration inspection starts the actual native mount.
            providers=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.providers','args':{}})
            assert 'result' in providers,providers
            assert {r['id'] for r in providers['result']['providers']}=={'fixture','unavailable-chatgpt'}
            inspected=await client.request('_amplifier/runtime',{'sessionId':sid,'cwd':str(cwd),'surface':'mounted','refresh':True,'limit':100})
            assert 'result' in inspected,inspected
            result=inspected['result'];proof=result['moduleSourceOverrides']
            assert proof['configuredOverrides']==1 and proof['truncated'] is False
            assert proof['resolutions']==[{'module':'provider-openai-chatgpt','originalSource':MAIN,'effectiveSource':str(selected),'authority':'trusted-launcher','override':True}]
            actual=next(r for r in result['items'] if r['kind']=='providers' and r['id']=='unavailable-chatgpt')
            loaded=Path(actual['implementations'][0]['loadedFrom'])
            assert loaded.is_relative_to(selected),actual
            paths.append(str(loaded))
            fixture=next(r for r in result['items'] if r['kind']=='providers' and r['id']=='fixture')
            assert Path(fixture['implementations'][0]['loadedFrom']).is_relative_to(Path(__file__).parent/'fixtures/provider')
            assert not audit.exists() and not (tmp_path/'absent-tokens.json').exists()
            assert settings.read_bytes()==original_settings and bundle.read_bytes()==original_bundle
        finally:await client.close()
    assert paths[0]!=paths[1] # no prepared/resolver/cache alias to prior selected code
