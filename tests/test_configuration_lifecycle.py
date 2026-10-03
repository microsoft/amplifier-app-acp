import asyncio,json
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_apply_remount_and_cancel_queued_configuration(tmp_path):
 config,cwd=configuration(tmp_path);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
  async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
  selected=await control('provider.select',{'instance':'fixture','model':'custom-fixture-model'})
  assert 'result' in selected,selected
  configs=[e['params']['update']['configOptions'] for e in client.events if e.get('method')=='session/update' and e['params']['update']['sessionUpdate']=='config_option_update']
  model=next(row for row in configs[-1] if row['id']=='model')
  assert next(row for row in model['options'] if row['value']==model['currentValue'])['name'].endswith(' / custom-fixture-model')
  inspected=await control('configuration.inspect');plan=inspected['result']['plan'];plan['session']['context']['config']={'max_tokens':4321}
  applied=await control('configuration.apply',{'config':plan,'expectedConfigurationRevision':inspected['result']['configurationRevision']});assert applied['result']['remounted'],applied
  inspected=await control('configuration.inspect');assert inspected['result']['plan']['session']['context']['config']['max_tokens']==4321
  import copy
  invalid=copy.deepcopy(plan);invalid['session']['context']['module']='context-does-not-exist';invalid['session']['context']['source']=str(tmp_path/'does-not-exist')
  failed=await control('configuration.apply',{'config':invalid,'expectedConfigurationRevision':inspected['result']['configurationRevision']});assert failed['error']['data']['ready'],failed
  reply=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'after remount'}]});assert reply['result']['stopReason']=='end_turn'
  slow=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'slow turn'}]}))
  await asyncio.sleep(.1)
  current=await control('configuration.inspect')
  queued=asyncio.create_task(control('configuration.apply',{'config':plan,'whenIdle':True,'commandId':'pending-apply','expectedConfigurationRevision':current['result']['configurationRevision']}))
  for _ in range(100):
   pending=(await control('configuration.inspect'))['result']['pendingConfiguration']
   if pending:break
   await asyncio.sleep(.01)
  assert pending=={'phase':'queued','commandId':'pending-apply'}
  wrong=await control('configuration.cancel',{'pendingCommandId':'previous-apply'});assert not wrong['result']['cancelled']
  assert not queued.done()
  cancelled=await control('configuration.cancel',{'pendingCommandId':pending['commandId']});assert cancelled['result']['cancelled']
  assert (await queued)['result']=={'cancelled':True,'applied':False}
  assert (await control('configuration.inspect'))['result']['pendingConfiguration'] is None
  transitions=[e['params']['event']['pendingConfiguration'] for e in client.events if e.get('method')=='_amplifier/event' and e['params']['event'].get('type')=='configuration.pending']
  assert {'phase':'queued','commandId':'pending-apply'} in transitions and transitions[-1] is None
  await client.request('session/cancel',{'sessionId':sid});assert (await slow)['result']['stopReason']=='cancelled'
 finally:await client.close()

@pytest.mark.asyncio
async def test_native_provider_signin_status_cancel_prevents_idle_retirement(tmp_path):
 config,cwd=configuration(tmp_path,idleSeconds=1);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
  async def control(op):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':{'provider':'fixture'}})
  started=await control('configuration.providerLogin');assert started['result']['phase']=='waiting'
  await asyncio.sleep(1.2)
  status=await control('configuration.providerLoginStatus');assert status['result']['loginId']==started['result']['loginId']
  cancelled=await control('configuration.providerLoginCancel');assert cancelled['result']['phase']=='cancelled'
 finally:await client.close()

@pytest.mark.asyncio
async def test_configuration_cas_preserves_newer_plan_without_remount_or_write(tmp_path):
 config,cwd=configuration(tmp_path);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
  async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
  first=(await control('configuration.inspect'))['result'];assert first['configurationRevision']==(await control('configuration.inspect'))['result']['configurationRevision']
  import copy
  plan=copy.deepcopy(first['plan']);plan['session']['context']['config']={'max_tokens':1234}
  made=await control('configuration.apply',{'config':plan,'expectedConfigurationRevision':first['configurationRevision']});assert made['result']['remounted'],made
  current=made['result']['configuration'];assert current['configurationRevision']!=first['configurationRevision']
  ready_before=sum(e.get('params',{}).get('event',{}).get('type')=='runtime.ready' for e in client.events)
  files={p:p.read_bytes() for p in (tmp_path/'app/sessions'/sid).glob('*.json')}
  missing=await control('configuration.apply',{'config':first['plan']});assert missing['error']['code']==-32602
  conflict=await control('configuration.apply',{'config':first['plan'],'expectedConfigurationRevision':first['configurationRevision']})
  assert conflict['error']['code']==-32009 and conflict['error']['data']=={'reason':'configuration-conflict','configurationRevision':current['configurationRevision'],'executed':False,'replayed':False}
  assert sum(e.get('params',{}).get('event',{}).get('type')=='runtime.ready' for e in client.events)==ready_before
  assert all(p.read_bytes()==raw for p,raw in files.items()) and not (tmp_path/'app/sessions'/sid/'configuration-transaction.json').exists()
  actual=(await control('configuration.inspect'))['result'];assert actual['plan']['session']['context']['config']['max_tokens']==1234
 finally:await client.close()

@pytest.mark.asyncio
async def test_waiting_configuration_checks_revision_after_idle_acquisition(tmp_path):
 from amplifier_acp.server import Session,RpcError
 from types import SimpleNamespace
 server=SimpleNamespace(config={'appHome':str(tmp_path/'app')})
 session=Session(server,'fixture',str(tmp_path));revision='a'*64;calls=[]
 class Worker:
  async def control(self,operation,args=None):
   calls.append(operation)
   assert operation=='configuration.inspect','Stale queued draft must not reach mutation'
   return {'configurationRevision':revision}
  async def close(self):pytest.fail('Conflict must not remount')
 session.worker=Worker();await session.lock.acquire()
 task=asyncio.create_task(session.apply_configuration({'config':{},'expectedConfigurationRevision':revision,'whenIdle':True}))
 while session.config_cancel is None:await asyncio.sleep(0)
 revision='b'*64;session.lock.release()
 with pytest.raises(RpcError) as failure:await task
 assert failure.value.code==-32009 and failure.value.data['configurationRevision']==revision
 assert calls==['configuration.inspect'] and not session.configuration_journal().exists()
