import asyncio
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_voice_record_idempotent_resume_and_busy(tmp_path):
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
 finally:await client.close()
