import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from client import Client
from amplifier_acp.transfer_lifecycle import TransferLifecycle
from amplifier_acp.admin_lifecycle import AdminFenced,context
from test_admin_lifecycle import CONTEXT,start

IDENTITY={'installationId':'installed','dataScope':CONTEXT['dataScope'],'ownerId':'platform-owner','instanceId':CONTEXT['instanceId'],'releaseDigest':'a'*64}
SERVICE={**CONTEXT,'purpose':'service-stop','serviceIdentity':IDENTITY}
def proof(outcome='unchanged'):
 observed={**IDENTITY,**({'instanceId':'new-host'} if outcome=='ready' else {})}
 return {'verified':True,**{k:CONTEXT[k] for k in ('fenceId','commandId','dataScope')},'outcome':outcome,'instanceId':observed['instanceId'],'receiptId':'platform-receipt','kind':'service-lifecycle','serviceOutcome':'resumed' if outcome=='ready' else 'stop-refused','expected':dict(IDENTITY),'observed':observed,**({'resumeCommandId':'resume','exitReceiptId':'exit','readyReceiptId':'ready'} if outcome=='ready' else {'refusalReceiptId':'refused'})}

@pytest.mark.asyncio
async def test_admin_service_stop_exact_identity_no_maintenance_authority_and_platform_proof(tmp_path):
 client,cwd,values,_=await start(tmp_path)
 try:
  async def life(op,args):return await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':op,'args':args})
  bad={**SERVICE,'serviceIdentity':{**IDENTITY,'dataScope':'other'}}
  assert 'error' in await life('acquire',bad)
  held=(await life('acquire',SERVICE))['result'];assert held['lease']['serviceIdentity']==IDENTITY
  authorization={k:held['lease'][k] for k in ('fenceId','commandId','leaseId')}
  blocked=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'maintenance.archive.create','args':{'commandId':'never','parts':['shared-configuration'],'privateContentReviewed':True},'_meta':{'amplifier.dev/admin':authorization}})
  assert blocked['error']['data']['executed'] is False
  for invalid in [None,{'verified':True,**CONTEXT,'outcome':'unchanged','receiptId':'old-recovery'}, {**proof(),'observed':{**IDENTITY,'releaseDigest':'b'*64}}, {**proof('ready'),'exitReceiptId':None}]:
   outcome='ready' if invalid and invalid.get('outcome')=='ready' else 'unchanged'
   assert 'error' in await life('release',{**authorization,'outcome':outcome,'proof':invalid})
  assert (await life('inspect',{}))['result']['intakeClosed']
  release={**authorization,'outcome':'unchanged','proof':proof()}
  assert (await life('release',release))['result']['released']
  assert (await life('release',release))['result']['released']
  assert 'error' in await life('release',{**release,'proof':{**proof(),'receiptId':'different'}})
  assert not (Path(values['home'])/'projects').exists()
 finally:await client.close()

@pytest.mark.asyncio
async def test_replacement_admin_requires_fresh_native_lease_and_exact_resumed_proof(tmp_path):
 client,cwd,_,config=await start(tmp_path)
 held=(await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':SERVICE}))['result']
 old={k:held['lease'][k] for k in ('fenceId','commandId','leaseId')}
 client.process.kill();await client.process.wait();await client.reader
 replacement=await Client.launch(config)
 try:
  await replacement.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  async def life(op,args):return await replacement.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':op,'args':args})
  assert 'error' in await life('release',{**old,'outcome':'unchanged','proof':{'kind':'admission-refused'}})
  new=(await life('acquire',SERVICE))['result'];assert new['adminInstanceId']!=held['adminInstanceId']
  auth={k:new['lease'][k] for k in ('fenceId','commandId','leaseId')}
  assert (await life('release',{**auth,'outcome':'ready','proof':proof('ready')}))['result']['released']
 finally:await replacement.close()

def test_transfer_service_binding_exact_release_receipt_and_live_acquisition_rollback(tmp_path):
 owner=TransferLifecycle(SimpleNamespace(config={'home':str(tmp_path)}))
 original=copy.deepcopy(SERVICE);assert owner.control('acquire',original)['acquired'];original['serviceIdentity']['ownerId']='changed'
 assert owner.inspect()['fence']['serviceIdentity']==IDENTITY
 with pytest.raises(AdminFenced):owner.control('release',{**SERVICE,'outcome':'unchanged','proof':{'verified':True,**CONTEXT,'outcome':'unchanged','receiptId':'old'}})
 result=owner.control('release',{**SERVICE,'outcome':'ready','proof':proof('ready')});assert result['released']
 assert owner.control('release',{**SERVICE,'outcome':'ready','proof':proof('ready')})['released']
 with pytest.raises(AdminFenced):owner.control('release',{**SERVICE,'outcome':'ready','proof':{**proof('ready'),'readyReceiptId':'changed'}})
 other={**SERVICE,'fenceId':'fresh','commandId':'fresh-command'};assert owner.control('acquire',other)['acquired']
 assert owner.control('release',{**other,'outcome':'unchanged','proof':{'kind':'admission-refused'}})['released']
 with pytest.raises(ValueError):context({**CONTEXT,'serviceIdentity':IDENTITY})
