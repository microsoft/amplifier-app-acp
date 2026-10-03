"""Opt-in actual mounted filesystem policy, no provider account or network effects."""
import asyncio,json,os
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.skipif(not os.environ.get('FILESYSTEM_TOOL_SOURCE'),reason='Actual filesystem module source must be explicitly configured')
async def test_active_policy_is_unchanged_then_reloads_at_next_execution(tmp_path):
 config,cwd=configuration(tmp_path,adminPermissions=True,adminWorkspaceRoots=[str(tmp_path/'workspace')])
 tool=Path(os.environ['FILESYSTEM_TOOL_SOURCE']).resolve(strict=True)
 bundle=tmp_path/'fixture.yaml';gates=tmp_path/'gates';gates.mkdir()
 text=bundle.read_text().replace('    config: {}','    config:\n      gateDirectory: '+str(gates))
 text+='tools:\n  - module: tool-filesystem\n    source: '+str(tool)+'\n    config:\n      denied_write_paths: ['+str(cwd/'bundle-private')+']\n'
 bundle.write_text(text);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
  made=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});assert 'result' in made,made;sid=made['result']['sessionId']
  async def mounted():
   row=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.inspect'});assert 'result' in row,row
   return next(t['config'] for t in row['result']['plan']['tools'] if t['module']=='tool-filesystem')
  before=await mounted()
  task=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}]}))
  for _ in range(500):
   if (gates/'tool.entered').exists():break
   await asyncio.sleep(.01)
  assert (gates/'tool.entered').exists(),'Actual provider/tool boundary was not reached'
  initial=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'permissions.get','args':{'scope':'project'}})
  saved=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'permissions.save','args':{'scope':'project','commandId':'live-save','expectedRevision':initial['result']['revision'],'allowed':[str(cwd/'outputs')],'denied':[str(cwd/'private')]}})
  assert saved['result']['status']=='succeeded',saved
  assert await mounted()==before,'Active tool authority must not mutate during a running turn'
  (gates/'tool.release').touch();result=await task;assert 'result' in result,result
  next_turn=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'after permission update'}]});assert 'result' in next_turn,next_turn
  current=await mounted()
  assert current['allowed_write_paths']==[str(cwd),str(cwd/'outputs')]
  assert set(current['denied_write_paths'])=={str(cwd/'bundle-private'),str(cwd/'private')}
  assert saved['result']['result']['application']=='next-idle-execution'
  for folder in ['private','bundle-private']:
   denied=await client.request('_amplifier/native',{'sessionId':sid,'operation':'tool.invoke','args':{'name':'write_file','arguments':{'file_path':str(cwd/folder/'must-not-exist.txt'),'content':'fixture forbidden'}}})
   assert 'result' in denied and denied['result']['result']['success'] is False,denied
   assert not (cwd/folder/'must-not-exist.txt').exists()
  allowed=await client.request('_amplifier/native',{'sessionId':sid,'operation':'tool.invoke','args':{'name':'write_file','arguments':{'file_path':str(cwd/'outputs'/'allowed.txt'),'content':'fixture allowed'}}})
  assert allowed['result']['result']['success'] is True,allowed
  assert (cwd/'outputs'/'allowed.txt').read_text()=='fixture allowed'
 finally:
  (gates/'tool.release').touch();await client.close()
