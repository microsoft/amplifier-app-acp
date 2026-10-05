import asyncio,json,hashlib
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_voice_record_idempotent_resume_and_busy(tmp_path,monkeypatch):
 config,cwd=configuration(tmp_path);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
  async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
  args={'callId':'call-a','itemId':'item-1','role':'user','text':'spoken history','append':False,'commandId':'voice-1'}
  first=await control('voice.transcript.record',args);assert first['result']['executed'] is False,first
  assert (await control('voice.transcript.record',args))['result']['duplicate']
  conflict=await control('voice.transcript.record',{**args,'text':'different'});assert 'error' in conflict
  assert not any(e.get('method')=='session/update' and e['params']['update']['sessionUpdate']=='agent_message_chunk' for e in client.events)
  updated=await control('voice.transcript.record',{**args,'commandId':'voice-2','append':True,'text':' plus'});assert updated['result']['recorded']
  snap=await control('history.snapshot');rows=[r for r in snap['result']['messages'] if r.get('metadata',{}).get('amplifier_voice')];assert len(rows)==1 and rows[0]['content']=='spoken history plus'
  slow=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'slow turn'}]}));await asyncio.sleep(.15)
  busy=await control('voice.transcript.record',{**args,'itemId':'item-2','commandId':'voice-3'});assert busy['result']=={'recorded':False,'reason':'busy','retryable':True,'executed':False},busy
  await client.request('session/cancel',{'sessionId':sid});await slow
  await client.request('session/close',{'sessionId':sid})
  resumed=await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]});assert 'result' in resumed,resumed
  snap=await control('history.snapshot');rows=[r for r in snap['result']['messages'] if r.get('metadata',{}).get('amplifier_voice')];assert rows[0]['content']=='spoken history plus'
  # Fork consumes native history and retains metadata without submitting its text.
  assert rows[0]['metadata']['amplifier_voice']['recordedOnly']
  await client.request('session/close',{'sessionId':sid})
  settings=json.loads(config.read_text());monkeypatch.setenv('AMPLIFIER_HOME',settings['home'])
  from amplifier_acp.native.host.storage import SessionStore
  from amplifier_acp.native.session_store import fork_session
  store=SessionStore.for_app(settings['appHome'],cwd);transcript=store.directory(sid)/'transcript.jsonl';checksum=hashlib.sha256(transcript.read_bytes()).hexdigest()
  fork_session(settings['appHome'],{'id':sid,'workspace':str(cwd),'status':'idle','messages':[]},'voice-fork')
  forked=store.load('voice-fork')[0]
  voices=[r for r in forked if r.get('metadata',{}).get('amplifier_voice')]
  assert len(voices)==1 and voices[0]['content']=='spoken history plus'
  assert voices[0]['metadata']['amplifier_voice']['recordedOnly']
  assert hashlib.sha256(transcript.read_bytes()).hexdigest()==checksum
 finally:await client.close()

@pytest.mark.asyncio
async def test_voice_delegation_plain_input_and_persisted_delivery(tmp_path):
 from test_public_message_runtime import open_fixture
 client,sid,cwd,config=await open_fixture(tmp_path)
 try:
  delivery={'version':1,'callId':'call-plain','sourceItemIds':['spoken-one'],'context':[{'role':'user','text':'Good morning'},{'role':'assistant','text':'Good morning. How can I help?'}]}
  result=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'What can you help with?'}], '_meta':{'amplifier.dev/command':{'commandId':'voice-plain'},'amplifier.dev/input':{'inputOrigin':'voice','voiceDelivery':delivery}}})
  assert result.get('result',{}).get('stopReason')=='end_turn',result
  history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd)})
  updates=history['result']['updates']
  user=[row for row in updates if row['sessionUpdate']=='user_message_chunk']
  assert user[0]['content']['text']=='What can you help with?'
  assert user[0]['_meta']['amplifier.dev/history']['voiceDelivery']['callId']=='call-plain'
  assert any(row['_meta']['amplifier.dev/history'].get('voiceDelivery') for row in updates if row['sessionUpdate']=='agent_message_chunk')
  observed=(tmp_path/'provider-prompts.jsonl').read_text()
  assert 'Good morning' in observed and 'What can you help with?' in observed
  typed=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'A typed follow-up'}],'_meta':{'amplifier.dev/command':{'commandId':'typed'}}})
  assert 'result' in typed,typed
  bound=await client.request('_amplifier/native',{'sessionId':sid,'operation':'voice.delivery.record','args':{'callId':'call-plain','inputId':'typed','commandId':'typed-delivery'}})
  assert bound.get('result',{}).get('recorded'),bound
  history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd)})
  group=next(g for g in history['result']['turnGroups'] if g['id']=='typed')
  assert group['updates'][0]['_meta']['amplifier.dev/history']['voiceDelivery']['source']=='chat'
 finally:await client.close()
