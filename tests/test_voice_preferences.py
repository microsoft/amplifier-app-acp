import json,sqlite3
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.voice_preferences import VoicePreferences,projection

async def launch(tmp_path,enabled=True):
 cwd=tmp_path/'workspace';cwd.mkdir(exist_ok=True);home=tmp_path/'home';home.mkdir(exist_ok=True)
 config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminVoicePreferences':enabled,'workerCommand':['/never/start/voice-worker']}
 file=tmp_path/'native.json';file.write_text(json.dumps(config));client=await Client.launch(file)
 init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
 return client,cwd,config,init

async def call(client,cwd,operation,args={}):
 row=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':operation,'args':args});assert 'error' not in row,row
 return row['result']

@pytest.mark.asyncio
async def test_preferences_cas_shared_config_receipts_and_no_credential_access(tmp_path):
 client,cwd,config,init=await launch(tmp_path)
 assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['voice']['preferences']['cas']
 settings=Path(config['home'])/'settings.yaml';settings.write_text('other_config:\n  preserve: true\nvoice:\n  credential_source: private\n  unknown_future_setting: retained\n')
 try:
  before=await call(client,cwd,'voice.configuration');assert before['credentialAccess']=='disabled' and before['available'] is False
  assert before['preferenceFields']==['preferredModel','fallbackModel','voice','interruptions']
  assert before['fallbackModel']=='gpt-realtime-2.1' and before['fallbackBehavior']=='stored-only'
  assert before['models'][0]['voices'][0]['id']=='marin'
  args={'commandId':'save','expectedRevision':before['preferencesRevision'],'patch':{'preferredModel':'gpt-realtime-2.1','fallbackModel':'gpt-live-1','voice':'cedar','interruptions':False}}
  saved=await call(client,cwd,'voice.preferences.update',args);assert saved['status']=='succeeded',saved
  assert await call(client,cwd,'voice.preferences.update',args)==saved
  stale=await call(client,cwd,'voice.preferences.update',{**args,'commandId':'stale','patch':{'voice':'marin'}})
  assert stale['status']=='failed' and stale['executed'] is False and stale['reason']=='preferences_changed'
  after=await call(client,cwd,'voice.configuration');assert after['preferencesRevision']==saved['result']['preferencesRevision'] and after['voice']=='cedar'
  assert not client.events and not (Path(config['home'])/'projects').exists()
 finally:await client.close()
 client,cwd,_,_=await launch(tmp_path)
 try:assert await call(client,cwd,'voice.preferences.receipt',{'commandId':'save'})==saved
 finally:await client.close()
 import yaml
 data=yaml.safe_load(settings.read_text());assert data['other_config']=={'preserve':True} and data['voice']['unknown_future_setting']=='retained' and data['voice']['credential_source']=='private'
 assert not (Path(config['home'])/'keys.env').exists()

@pytest.mark.asyncio
async def test_disabled_preferences_and_held_receipt_without_setup_or_secret_read(tmp_path,monkeypatch):
 client,cwd,config,init=await launch(tmp_path,False)
 try:
  assert 'preferences' not in init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['voice']
  view=await call(client,cwd,'voice.configuration');assert view['preferenceFields']==[]
  denied=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'voice.preferences.update','args':{'commandId':'x'}});assert 'error' in denied
 finally:await client.close()
 from amplifier_acp.admin import Administration
 from amplifier_acp.native import voice_admin
 from test_admin_lifecycle import CONTEXT
 config['adminVoicePreferences']=True;owner=Administration(config)
 def forbidden(*args):raise AssertionError('Preferences must not access credentials')
 monkeypatch.setattr(voice_admin,'_load_keys',forbidden)
 try:
  initial=await owner.perform(str(cwd),'voice.configuration',{})
  updated=await owner.perform(str(cwd),'voice.preferences.update',{'commandId':'normal','expectedRevision':initial['preferencesRevision'],'patch':{'voice':'cedar'}})
  assert updated['status']=='succeeded' and owner.setup is None
  assert owner.lifecycle.perform('acquire',CONTEXT)['acquired']
  assert (await owner.perform(str(cwd),'voice.configuration',{}))['voice']=='cedar'
  assert await owner.perform(str(cwd),'voice.preferences.receipt',{'commandId':'normal'})==updated
  with pytest.raises(ValueError,match='intake is held'):await owner.perform(str(cwd),'voice.preferences.update',{'commandId':'blocked'})
 finally:await owner.close()

@pytest.mark.parametrize('patch',[{'preferredModel':{}},{'preferredModel':'unsupported'},{'fallbackModel':'unsupported'},{'voice':'quartz','preferredModel':'gpt-realtime-2.1'},{'interruptions':1},{'apiKey':'forbidden'},{}])
def test_invalid_preferences_are_conclusive_no_effect(tmp_path,patch):
 config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')};owner=VoicePreferences(config)
 before=projection({})
 result=owner.perform(str(tmp_path),'voice.preferences.update',{'commandId':'invalid','expectedRevision':before['preferencesRevision'],'patch':patch})
 assert result['status']=='failed' and result['executed'] is False
 assert not (tmp_path/'home/settings.yaml').exists()

def test_lost_postwrite_ack_uses_atomic_marker_without_replaying(tmp_path,monkeypatch):
 config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')};owner=VoicePreferences(config);before=projection({})
 update=owner.store.update
 def fail(*args,**kwargs):update(*args,**kwargs);raise OSError('fixture post-write acknowledgement loss')
 monkeypatch.setattr(owner.store,'update',fail)
 args={'commandId':'lost','expectedRevision':before['preferencesRevision'],'patch':{'voice':'quartz'}}
 with pytest.raises(OSError):owner.perform(str(tmp_path),'voice.preferences.update',args)
 raw=(tmp_path/'home/settings.yaml').read_bytes()
 assert owner.perform(str(tmp_path),'voice.preferences.update',args)['status']=='unknown'
 receipt=VoicePreferences(config).perform(str(tmp_path),'voice.preferences.receipt',{'commandId':'lost'})
 assert receipt['status']=='succeeded' and receipt['result']['voice']=='quartz'
 assert (tmp_path/'home/settings.yaml').read_bytes()==raw
 with sqlite3.connect(owner.path) as con:assert json.loads(con.execute('SELECT body FROM commands WHERE id=?',('lost',)).fetchone()[0])['status']=='unknown'
 next_owner=VoicePreferences(config)
 changed=next_owner.perform(str(tmp_path),'voice.preferences.update',{'commandId':'model-change','expectedRevision':receipt['result']['preferencesRevision'],'patch':{'preferredModel':'gpt-realtime-2.1'}})
 assert changed['status']=='succeeded' and changed['result']['voice']=='marin'
 # The next native write preserves proven earlier completion before replacing its marker.
 assert next_owner.perform(str(tmp_path),'voice.preferences.receipt',{'commandId':'lost'})['status']=='succeeded'

@pytest.mark.asyncio
async def test_competing_native_admin_processes_cannot_overwrite_stale_default(tmp_path):
 import asyncio
 first,cwd,config,_=await launch(tmp_path);second,_,_,_=await launch(tmp_path)
 try:
  before=await call(first,cwd,'voice.configuration')
  async def save(client,identity,voice):return await call(client,cwd,'voice.preferences.update',{'commandId':identity,'expectedRevision':before['preferencesRevision'],'patch':{'voice':voice}})
  # Requests are sent through two distinct ACP processes sharing the real settings owner.
  results=await asyncio.gather(save(first,'client-one','cedar'),save(second,'client-two','alloy'))
  assert sorted(row['status'] for row in results)==['failed','succeeded']
  rejected=next(row for row in results if row['status']=='failed');assert rejected['reason']=='preferences_changed' and rejected['executed'] is False
  winner=next(row for row in results if row['status']=='succeeded')
  assert (await call(first,cwd,'voice.configuration'))['voice']==winner['result']['voice']
 finally:await first.close();await second.close()
