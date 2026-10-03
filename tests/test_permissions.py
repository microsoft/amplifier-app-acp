import asyncio,json,sqlite3
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.permissions import Permissions,Invalid

async def launch(tmp_path,enabled=True):
 cwd=tmp_path/'workspace';cwd.mkdir(exist_ok=True);home=tmp_path/'home';home.mkdir(exist_ok=True)
 config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminPermissions':enabled,'workerCommand':['/never/start/permissions-worker']}
 file=tmp_path/'native.json';file.write_text(json.dumps(config));client=await Client.launch(file)
 init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
 return client,cwd,config,init

async def call(client,cwd,operation,args=None):
 row=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':operation,'args':args or {}});assert 'error' not in row,row
 return row['result']

def save(owner,cwd,identity,scope='global',**extra):
 before=owner.perform(cwd,'permissions.get',{'scope':scope})
 return owner.perform(cwd,'permissions.save',{'commandId':identity,'scope':scope,'expectedRevision':before['revision'],'allowed':['output'],'denied':['private'],**extra})

async def test_actual_passive_scopes_cas_source_explanation_and_restart(tmp_path):
 client,cwd,config,init=await launch(tmp_path)
 assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['permissions']['pathsPerList']==64
 settings=Path(config['home'])/'settings.yaml';settings.write_text('other: preserve\nproviders:\n  - module: provider-test\n    config:\n      api_key: secret-never-projected\n')
 try:
  before=await call(client,cwd,'permissions.get');assert 'secret-never-projected' not in json.dumps(before)
  args={'commandId':'save','scope':'global','expectedRevision':before['revision'],'allowed':['output'],'denied':['private']}
  result=await call(client,cwd,'permissions.save',args);assert result['status']=='succeeded'
  assert await call(client,cwd,'permissions.save',args)==result
  stale=await call(client,cwd,'permissions.save',{**args,'commandId':'stale','allowed':[]});assert stale['status']=='failed' and stale['executed'] is False
  for scope in ['project','local']:
   view=await call(client,cwd,'permissions.get',{'scope':scope})
   written=await call(client,cwd,'permissions.save',{'commandId':scope,'scope':scope,'expectedRevision':view['revision'],'allowed':[scope],'denied':[]});assert written['status']=='succeeded'
  effective=await call(client,cwd,'permissions.get',{'scope':'global'})
  assert effective['allowed']==[str(cwd/'output')]
  assert effective['effective']['sources']=={'allowed':'local','denied':'local'}
  assert effective['effective']['allowed']==[str(cwd),str(cwd/'local')]
  assert effective['effective']['coverage']=='module-default-settings-only'
  assert not client.events and not (Path(config['home'])/'projects').exists()
  assert 'secret-never-projected' in settings.read_text()
 finally:await client.close()
 client,_,_,_=await launch(tmp_path)
 try:assert await call(client,cwd,'permissions.receipt',{'commandId':'save'})==result
 finally:await client.close()

async def test_competing_process_cas_and_workspace_receipt_binding(tmp_path):
 a,cwd,_,_=await launch(tmp_path);b,_,_,_=await launch(tmp_path)
 try:
  view=await call(a,cwd,'permissions.get',{'scope':'project'})
  async def change(client,identity):return await call(client,cwd,'permissions.save',{'commandId':identity,'scope':'project','expectedRevision':view['revision'],'allowed':[identity],'denied':[]})
  results=await asyncio.gather(change(a,'one'),change(b,'two'))
  assert sorted(r['status'] for r in results)==['failed','succeeded']
  outside=tmp_path/'outside';outside.mkdir()
  refused=await a.request('_amplifier/admin',{'cwd':str(outside),'operation':'permissions.get','args':{}});assert 'error' in refused
  alias=cwd/'escape';alias.symlink_to(outside,target_is_directory=True)
  refused=await a.request('_amplifier/admin',{'cwd':str(alias),'operation':'permissions.get','args':{}});assert 'error' in refused
  nested=cwd/'nested';nested.mkdir()
  refused=await a.request('_amplifier/admin',{'cwd':str(nested),'operation':'permissions.receipt','args':{'commandId':'one'}});assert 'error' in refused
 finally:await a.close();await b.close()

async def test_disabled_and_held_read_only_routes(tmp_path):
 client,cwd,config,init=await launch(tmp_path,False)
 try:
  assert 'permissions' not in init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']
  assert 'error' in await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'permissions.get','args':{}})
 finally:await client.close()
 from amplifier_acp.admin import Administration
 from test_admin_lifecycle import CONTEXT
 config['adminPermissions']=True;owner=Administration(config)
 try:
  before=await owner.perform(str(cwd),'permissions.get',{})
  result=await owner.perform(str(cwd),'permissions.save',{'commandId':'saved','expectedRevision':before['revision'],'allowed':[],'denied':[]})
  assert owner.setup is None
  assert owner.lifecycle.perform('acquire',CONTEXT)['acquired']
  assert (await owner.perform(str(cwd),'permissions.get',{}))['revision']==result['result']['revision']
  assert await owner.perform(str(cwd),'permissions.receipt',{'commandId':'saved'})==result
  with pytest.raises(ValueError,match='intake is held'):await owner.perform(str(cwd),'permissions.save',{'commandId':'blocked'})
 finally:await owner.close()

@pytest.mark.parametrize('patch',[{'allowed':['x']*65},{'denied':['x'*4097]},{'allowed':['bad\npath']},{'allowed':'not-list'},{'denied':[None]}])
def test_bounded_paths_fail_without_effect(tmp_path,patch):
 owner=Permissions({'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')})
 result=save(owner,str(tmp_path),'invalid',**patch)
 assert result['status']=='failed' and result['executed'] is False
 assert not (tmp_path/'home/settings.yaml').exists()

def test_exact_unknown_receipt_recovers_without_write_and_preserves_prior_marker(tmp_path,monkeypatch):
 config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')};owner=Permissions(config);update=owner.store.update
 def lose_ack(*args,**kwargs):update(*args,**kwargs);raise OSError('post-write fixture loss')
 monkeypatch.setattr(owner.store,'update',lose_ack)
 with pytest.raises(OSError):save(owner,str(tmp_path),'lost')
 raw=(tmp_path/'home/settings.yaml').read_bytes()
 recovered=Permissions(config).perform(str(tmp_path),'permissions.receipt',{'commandId':'lost'});assert recovered['status']=='succeeded'
 assert (tmp_path/'home/settings.yaml').read_bytes()==raw
 save(Permissions(config),str(tmp_path),'next',allowed=[])
 assert Permissions(config).perform(str(tmp_path),'permissions.receipt',{'commandId':'lost'})==recovered

async def test_symlinked_settings_refuse_before_effect(tmp_path):
 client,cwd,config,_=await launch(tmp_path)
 outside=tmp_path/'outside';outside.mkdir();(cwd/'.amplifier').symlink_to(outside,target_is_directory=True)
 try:
  response=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'permissions.get','args':{'scope':'project'}});assert 'error' in response
  assert not list(outside.iterdir())
 finally:await client.close()

def test_postwrite_projection_failure_is_unknown_not_pre_effect_refusal(tmp_path,monkeypatch):
 config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')};owner=Permissions(config)
 base=owner.perform(str(tmp_path),'permissions.get',{})
 def fail(*args,**kwargs):raise Invalid('fixture projection error after replacement')
 monkeypatch.setattr(owner,'view',fail)
 with pytest.raises(Invalid):owner.perform(str(tmp_path),'permissions.save',{'commandId':'post-write','expectedRevision':base['revision'],'allowed':[],'denied':['private']})
 with sqlite3.connect(owner.path) as con:assert json.loads(con.execute('SELECT body FROM commands WHERE id=?',('post-write',)).fetchone()[0])['status']=='unknown'
 assert Permissions(config).perform(str(tmp_path),'permissions.receipt',{'commandId':'post-write'})['status']=='succeeded'

def test_effective_revision_includes_shadowed_scope_changes(tmp_path):
 owner=Permissions({'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')})
 save(owner,str(tmp_path),'local','local',allowed=['local'])
 before=owner.perform(str(tmp_path),'permissions.get',{'scope':'local'})
 save(owner,str(tmp_path),'global','global',allowed=['global'])
 after=owner.perform(str(tmp_path),'permissions.get',{'scope':'local'})
 assert before['revision']==after['revision']
 assert before['effective']['allowed']==after['effective']['allowed']
 assert before['effective']['revision']!=after['effective']['revision']
