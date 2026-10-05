import asyncio,json,sys
from pathlib import Path
from unittest.mock import Mock,AsyncMock
import pytest
from amplifier_acp.admin import Administration,AdminPreflightRefusal
from client import Client

@pytest.mark.asyncio
@pytest.mark.parametrize("case",["relative-cwd","missing-cwd","outside-cwd","unknown-operation","args-not-object","home","appHome","sessionId","session_id","cwd","workspace-mismatch","settings-escape","oversize","unserializable"])
async def test_preflight_refusal_before_any_owner_admission(tmp_path,monkeypatch,case):
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();other=workspace/'other';other.mkdir();outside=tmp_path/'outside';outside.mkdir();app=tmp_path/'app'
 owner=Administration({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)]})
 owner.lifecycle.enter=Mock(side_effect=AssertionError('must not admit'))
 owner.initialize=Mock(side_effect=AssertionError('must not initialize'))
 owner.execute=AsyncMock(side_effect=AssertionError('must not dispatch'))
 cwd=str(workspace);operation='providers.list';args={}
 if case=='relative-cwd':cwd='relative'
 elif case=='missing-cwd':cwd=str(tmp_path/'missing')
 elif case=='outside-cwd':cwd=str(outside)
 elif case=='unknown-operation':operation='unsupported'
 elif case=='args-not-object':args=[]
 elif case in ['home','appHome','sessionId','session_id','cwd']:args={case:'untrusted'}
 elif case=='workspace-mismatch':args={'workspace':str(other)}
 elif case=='settings-escape':(workspace/'.amplifier').symlink_to(outside,target_is_directory=True)
 elif case=='oversize':args={'value':'x'*(512*1024+1)}
 elif case=='unserializable':args={'value':object()}
 try:
  with pytest.raises(AdminPreflightRefusal) as captured:await owner.perform(cwd,operation,args)
  assert captured.value.data['executed'] is False
  assert captured.value.data['replayed'] is False
  assert captured.value.data['reason']=='native-admin-preflight-refused'
  owner.lifecycle.enter.assert_not_called();owner.initialize.assert_not_called();owner.execute.assert_not_called()
  assert not owner.requests and not owner.effects and owner.setup is None and not app.exists()
 finally:await owner.close()

@pytest.mark.asyncio
async def test_post_dispatch_value_error_has_no_preeffect_claim(tmp_path):
 workspace=tmp_path/'workspace';workspace.mkdir()
 owner=Administration({'adminWorkspaceRoots':[str(workspace)]})
 error=ValueError('owner failed after dispatch')
 owner.execute=AsyncMock(side_effect=error)
 try:
  with pytest.raises(ValueError) as captured:await owner.perform(str(workspace),'providers.list',{})
  assert captured.value is error and not hasattr(error,'data')
  owner.execute.assert_awaited_once()
  assert not owner.requests and not owner.effects
 finally:await owner.close()

@pytest.mark.asyncio
async def test_source_acp_serializes_workspace_refusal(tmp_path):
 home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir();other=workspace/'other';other.mkdir();app=tmp_path/'app'
 config=tmp_path/'native.json';config.write_text(json.dumps({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)],'workerCommand':['/no/execution/worker']}))
 source=str(Path(__file__).resolve().parents[1]/'src')
 code='import sys;sys.path.insert(0,'+repr(source)+');from amplifier_acp.server import main;main()'
 process=await asyncio.create_subprocess_exec(sys.executable,'-I','-B','-c',code,'--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 client=Client(process)
 try:
  init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  assert 'result' in init
  result=await client.request('_amplifier/admin',{'cwd':str(workspace),'operation':'providers.list','args':{'workspace':str(other)}})
  assert result['error']['message']=='Workspace selector disagrees with authorized cwd.'
  assert result['error']['data']=={'executed':False,'replayed':False,'reason':'native-admin-preflight-refused','operation':'providers.list'}
  assert not app.exists() and not list(home.iterdir()) and not client.events
 finally:await client.close()
