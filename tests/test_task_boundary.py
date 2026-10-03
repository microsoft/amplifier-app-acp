import json
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_task_exact_receipt_preflight_and_bounded_history(tmp_path):
 config,cwd=configuration(tmp_path);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
  async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
  made=await control('task.create',{'commandId':'outer-create','expectedRevision':0,'objective':'Preserve one task','maxTurns':3})
  assert made['result']['task']['objective']=='Preserve one task',made
  assert 'history' not in made['result'] and made['result']['historyCount']==0
  refusal=await control('task.pause',{'commandId':'stale','expectedRevision':0})
  assert refusal['error']['data']=={'executed':False,'replayed':False,'reason':'task-preflight-refused'},refusal
  receipt=await control('task.receipt',{'commandId':'outer-create'})
  assert receipt['result']['receipt']['state']=='succeeded' and receipt['result']['receipt']['revision']==1
  assert not (await control('task.receipt',{'commandId':'stale'}))['result']['available']
  complete=await control('task.complete',{'commandId':'complete','expectedRevision':1,'condition':'Preserve one task','evidence':['explicit fixture evidence']});assert 'result' in complete,complete
  second=await control('task.create',{'commandId':'next','expectedRevision':2,'objective':'Next task','maxTurns':3});assert second['result']['historyCount']==1
  history=await control('task.history',{'limit':1});assert history['result']['items'][0]['objective']=='Preserve one task'
  await client.request('session/close',{'sessionId':sid})
  await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
  assert (await control('task.receipt',{'commandId':'outer-create'}))['result']['receipt']['revision']==1
 finally:await client.close()
