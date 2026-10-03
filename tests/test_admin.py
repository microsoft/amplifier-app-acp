import json
from pathlib import Path
import pytest
from client import Client

@pytest.mark.asyncio
async def test_admin_scoped_configuration_without_execution(tmp_path):
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();outside=tmp_path/'outside';outside.mkdir();app=tmp_path/'app'
 config=tmp_path/'agent.json';config.write_text(json.dumps({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)],'workerCommand':['/no/worker/allowed']}))
 client=await Client.launch(config)
 try:
  init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['version']==1
  async def admin(op,args={},cwd=workspace):
   response=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':op,'args':args});assert 'result' in response,response;return response['result']
  denied=await client.request('_amplifier/admin',{'cwd':str(outside),'operation':'modules.save','args':{'section':'tools','module':'tool-test','config':{}}})
  assert 'error' in denied and not app.exists() and not (outside/'.amplifier').exists()
  link=workspace/'escape';link.symlink_to(outside,target_is_directory=True)
  denied=await client.request('_amplifier/admin',{'cwd':str(link),'operation':'providers.list','args':{}})
  assert 'error' in denied
  saved=await admin('modules.save',{'scope':'project','section':'hooks','module':'hooks-test','config':{'enabled':True}})
  assert saved['modules'][0]['module']=='hooks-test'
  rows=await admin('modules.list',{'scope':'project'});assert rows['modules'][0]['config']=={'enabled':True}
  defaults=await admin('bundle.default',{'scope':'workspace','bundle':'work'})
  assert defaults['bundleDefaults']['effective']=='work'
  bundles=await admin('bundles.list');assert 'registeredBundles' in bundles and 'bundleDefaults' in bundles
  provider=await admin('providers.save',{'id':'fixture','module':'provider-fixture','config':{'default_model':'offline'}})
  assert provider['providers'][0]['id']=='fixture'
  listed=await admin('providers.list');assert listed['providers'][0]['id']=='fixture'
  assert not (home/'projects').exists(), 'Admin must not create native conversations'
  assert not any(e['method']=='session/update' for e in client.events)
 finally:await client.close()

@pytest.mark.asyncio
async def test_admin_requires_launcher_enablement(tmp_path):
 config=tmp_path/'agent.json';config.write_text('{}');client=await Client.launch(config)
 try:
  response=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  assert 'admin' not in response['result']['agentCapabilities']['_meta']['amplifier.dev/native']
  denied=await client.request('_amplifier/admin',{'cwd':str(tmp_path),'operation':'modules.list','args':{}})
  assert denied['error']['code']==-32601
 finally:await client.close()

@pytest.mark.asyncio
async def test_draft_defaults_probe_inspects_bundle_without_session(tmp_path):
 from native_setup import configuration
 config,workspace=configuration(tmp_path)
 values=json.loads(config.read_text());values.update(adminWorkspaceRoots=[str(workspace)],workerCommand=['/no/worker/allowed']);config.write_text(json.dumps(values))
 client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  response=await client.request('_amplifier/admin',{'cwd':str(workspace),'operation':'configuration.defaults','args':{'bundle':values['bundle']}})
  assert response['result']['effective']['model']=='fixture-model',response
  assert not (Path(values['home'])/'projects').exists()
 finally:await client.close()

@pytest.mark.asyncio
async def test_voice_admin_existing_key_store_private_boundary_and_restart(tmp_path,monkeypatch):
 monkeypatch.delenv('OPENAI_API_KEY',raising=False);monkeypatch.delenv('AMPLIFIER_VOICE_API_KEY',raising=False)
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();app=tmp_path/'app';config=tmp_path/'agent.json'
 values={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)],'workerCommand':['/no/worker/allowed']};config.write_text(json.dumps(values))
 client=await Client.launch(config)
 async def initialize():await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
 async def admin(op,args={}):return await client.request('_amplifier/admin',{'cwd':str(workspace),'operation':op,'args':args})
 try:
  await initialize()
  assert 'error' in await admin('voice.credential')
  assert not app.exists(),'Denied credential request must cause no file/probe side effect'
  assert (await admin('voice.configuration'))['result']['available'] is False
 finally:await client.close()
 values['adminVoiceCredentials']=True;config.write_text(json.dumps(values));client=await Client.launch(config)
 try:
  await initialize()
  selected=await admin('voice.configure',{'source':'private','apiKey':'synthetic-voice-key-not-an-account'})
  assert selected['result']['available'] and selected['result']['privateKeyAvailable']
  assert 'synthetic-voice' not in json.dumps(selected)
  key=await admin('voice.credential');assert key['result']['apiKey']=='synthetic-voice-key-not-an-account'
  assert (home/'keys.env').is_file() and (home/'keys.env').stat().st_mode&0o777==0o600
  assert not (home/'projects').exists()
  assert 'error' in await admin('voice.configure',{'source':'environment'})
  assert (await admin('voice.configuration'))['result']['source']=='private'
  assert not client.events
 finally:await client.close()
 client=await Client.launch(config)
 try:
  await initialize();assert (await admin('voice.credential'))['result']['apiKey']=='synthetic-voice-key-not-an-account'
 finally:await client.close()
