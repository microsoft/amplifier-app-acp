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
  applied=await control('configuration.apply',{'config':plan});assert applied['result']['remounted'],applied
  inspected=await control('configuration.inspect');assert inspected['result']['plan']['session']['context']['config']['max_tokens']==4321
  import copy
  invalid=copy.deepcopy(plan);invalid['session']['context']['module']='context-does-not-exist';invalid['session']['context']['source']=str(tmp_path/'does-not-exist')
  failed=await control('configuration.apply',{'config':invalid});assert failed['error']['data']['ready'],failed
  reply=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'after remount'}]});assert reply['result']['stopReason']=='end_turn'
  slow=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'slow turn'}]}))
  await asyncio.sleep(.1)
  queued=asyncio.create_task(control('configuration.apply',{'config':plan,'whenIdle':True}));await asyncio.sleep(.1)
  cancelled=await control('configuration.cancel');assert cancelled['result']['cancelled']
  assert (await queued)['result']=={'cancelled':True,'applied':False}
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
