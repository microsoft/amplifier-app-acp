import asyncio,json
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.admin import Administration

CONTEXT={'fenceId':'fence-one','commandId':'coordinator-command','purpose':'recovery','instanceId':'host-one','dataScope':'owned-data'}

async def start(tmp_path):
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();config=tmp_path/'config.json'
 values={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(workspace)],'adminMaintenance':True,'workerCommand':['/impossible/admin-worker']};config.write_text(json.dumps(values));client=await Client.launch(config)
 initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
 assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['lifecycle']['heldIntake']
 return client,workspace,values,config

@pytest.mark.asyncio
async def test_protocol_held_admin_intake_cross_process_and_recovery_exception(tmp_path):
 client,cwd,values,config=await start(tmp_path);other=await Client.launch(config)
 try:
  await other.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  async def life(op,args={},peer=client):return await peer.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':op,'args':args})
  async def admin(op,args={},peer=client,auth=None):return await peer.request('_amplifier/admin',{'cwd':str(cwd),'operation':op,'args':args,**({'_meta':{'amplifier.dev/admin':auth}} if auth else {})})
  denied=await client.request('_amplifier/admin/lifecycle',{'cwd':str(tmp_path),'operation':'acquire','args':CONTEXT});assert 'error' in denied
  held=(await life('acquire',CONTEXT))['result'];assert held['acquired'] and held['work']['requests']==0
  refused=await admin('modules.save',{'section':'tools','module':'tool-never','config':{}});assert refused['error']['data']['executed'] is False
  assert (await life('acquire',CONTEXT,other))['result']['reason']=='other-native-admin-writer'
  assert (await admin('modules.save',{'section':'tools','module':'tool-never','config':{}},other))['error']['data']['executed'] is False
  assert (await admin('maintenance.preview',{'sessions':[],'parts':['native-settings'],'privateContentReviewed':True}))['error']['data']['reason']=='admin-quiescence-fenced'
  lease=held['lease'];authorization={key:lease[key] for key in ('fenceId','commandId','leaseId')}
  preview=await admin('maintenance.preview',{'sessions':[],'parts':['native-settings'],'privateContentReviewed':True},auth=authorization)
  assert preview['result']['coverage']=='explicit-selected-native-only',preview
  acquired=(await admin('maintenance.acquire',{'commandId':'snapshot-lease','previewHash':preview['result']['previewHash']},auth=authorization))['result']
  release={**authorization,'outcome':'unchanged','proof':{'verified':True,**CONTEXT,'outcome':'unchanged','receiptId':'exact-owner-receipt'}}
  assert 'error' in await life('release',release),'Active native maintenance lease must block administration release'
  snapshot=await admin('maintenance.snapshot',{'commandId':'snapshot','leaseId':acquired['leaseId'],'previewHash':preview['result']['previewHash']},auth=authorization);assert snapshot['result']['receipt']['state']=='succeeded',snapshot
  await admin('maintenance.release',{'leaseId':acquired['leaseId']},auth=authorization)
  assert (await life('release',release))['result']['released']
  assert (await admin('modules.save',{'section':'tools','module':'tool-fixture','config':{}}))['result']['modules']
  assert not (Path(values['home'])/'projects').exists()
 finally:await client.close();await other.close()

@pytest.mark.asyncio
async def test_post_response_login_and_queued_request_are_owned_until_completion(tmp_path):
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();owner=Administration({'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(workspace)]})
 ready=asyncio.Event();finish=asyncio.Event()
 class Setup:
  logins={}
  async def close(self):
   for row in self.logins.values():row['task'].cancel()
   await asyncio.gather(*(r['task'] for r in self.logins.values()),return_exceptions=True)
 owner.setup=Setup()
 async def authorized(cwd,op,args):
  if op=='providers.login':
   owner.setup.logins['fixture']={'task':asyncio.create_task(finish.wait())};return {'phase':'waiting'}
  ready.set();await finish.wait();return {'completed':True}
 owner.authorized=authorized
 try:
  reply=await owner.perform(str(workspace),'providers.login',{});assert reply['phase']=='waiting'
  busy=owner.lifecycle.perform('acquire',CONTEXT);assert busy['acquired'] is False and busy['work']['providerSignIns']==1
  finish.set();await asyncio.sleep(0);await asyncio.sleep(0)
  finish.clear();request=asyncio.create_task(owner.perform(str(workspace),'modules.validate',{}));await ready.wait()
  busy=owner.lifecycle.perform('acquire',CONTEXT);assert busy['work']['requests']==1
  request.cancel();await asyncio.sleep(0)
  assert owner.lifecycle.perform('acquire',CONTEXT)['work']['requests']==1,'Caller cancellation cannot free a continuing effect'
  finish.set();await asyncio.gather(request,return_exceptions=True)
  held=owner.lifecycle.perform('acquire',CONTEXT);assert held['acquired']
 finally:finish.set();await owner.close()

@pytest.mark.asyncio
async def test_admin_process_death_releases_os_gate_but_does_not_supply_host_release_proof(tmp_path):
 client,cwd,values,config=await start(tmp_path)
 held=await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':CONTEXT});assert held['result']['acquired']
 client.process.kill();await client.process.wait();await client.reader
 replacement=await Client.launch(config)
 try:
  await replacement.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  inspected=await replacement.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'inspect','args':{}});assert inspected['result']['adminInstanceId']!=held['result']['adminInstanceId'];assert inspected['result']['lease'] is None
  next_=await replacement.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':{**CONTEXT,'commandId':'new-proof'}});assert next_['result']['acquired']
  # A new native process proves availability only, never the old host's outcome.
  assert 'receiptId' not in next_['result'] and not (Path(values['home'])/'projects').exists()
 finally:await replacement.close()
