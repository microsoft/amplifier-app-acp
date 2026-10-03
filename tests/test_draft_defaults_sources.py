import importlib.util,json,uuid
import pytest
from client import Client

@pytest.mark.asyncio
async def test_uninstalled_local_provider_default_source_without_session_or_other_modules(tmp_path):
 name='defaults_'+uuid.uuid4().hex
 module='provider-'+name
 package='amplifier_module_provider_'+name
 assert importlib.util.find_spec(package) is None
 source=tmp_path/'provider';source.mkdir();code=source/package;code.mkdir()
 (source/'pyproject.toml').write_text('[project]\nname="amplifier-module-'+module+'"\nversion="0.0.1"\nrequires-python=">=3.13"\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n')
 (code/'__init__.py').write_text("""class LocalProvider:
    def __init__(self,config=None):self.config=config or {}
    def get_info(self):return {'id':'local','display_name':'Uninstalled local provider','defaults':{'model':self.config.get('model','local-default')},'config_fields':[]}
    async def close(self):pass
    async def list_models(self):raise AssertionError('No network catalogs')
    async def complete(self,*args,**kwargs):raise AssertionError('No inference')
async def mount(*args,**kwargs):raise AssertionError('No native session mount')
""")
 home=tmp_path/'home';home.mkdir();(home/'settings.yaml').write_text('bundle:\n  app: []\n')
 workspace=tmp_path/'workspace';workspace.mkdir()
 bundle=tmp_path/'bundle.yaml'
 bundle.write_text('bundle:\n  name: custom-defaults\n  version: 1.0.0+amplifier-unified.snapshot.1\nproviders:\n  - module: '+module+'\n    source: ./provider\n    config:\n      model: selected-local\n  - module: provider-disabled\n    source: /absent/disabled\n    enabled: false\nhooks:\n  - module: hooks-do-not-activate\n    source: /absent/hooks\ntools:\n  - module: tool-do-not-activate\n    source: /absent/tools\n')
 config=tmp_path/'agent.json';config.write_text(json.dumps({'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(workspace)],'workerCommand':['/impossible/no-worker']}))
 client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  result=await client.request('_amplifier/admin',{'cwd':str(workspace),'operation':'configuration.defaults','args':{'bundle':str(bundle)}})
  assert result['result']['effective']['model']=='selected-local',result
  assert len(result['result']['providers'])==1
  assert not (home/'projects').exists() and not client.events
  assert importlib.util.find_spec(package) is None,'Preparation belongs only to the isolated metadata process'
 finally:await client.close()
