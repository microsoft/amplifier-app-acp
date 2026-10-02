import json
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_partial_application_bridge_does_not_claim_or_read_unavailable_features(tmp_path):
 config,cwd=configuration(tmp_path);client=await Client.launch(config)
 try:
  await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/host':{'version':1,'name':'Fixture shell','appControl':{'operations':['list_actions','dispatch']}}}}})
  new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
  catalog=await client.request('_amplifier/native',{'sessionId':sid,'operation':'catalog.inspect','args':{}})
  tool=next(t for t in catalog['result']['tools'] if t['name']=='app_control')
  assert tool['inputSchema']['properties']['operation']['enum']==['list_actions','dispatch']
  assert 'Fixture shell' in tool['description'] and 'Amplifier Unified' not in tool['description']
  reply=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'ordinary prompt'}]});assert 'result' in reply,reply
  assert not [r for r in client.requests if r['method']=='_amplifier/host'],client.requests
 finally:await client.close()

def test_generic_behavior_defaults_are_explicit(monkeypatch):
 from amplifier_acp.native.builtin_behaviors import app_behaviors,SHELL_BEHAVIOR_URI,IMAGEGEN_BEHAVIOR_URI
 monkeypatch.delenv('AMPLIFIER_ACP_HOST_FEATURES',raising=False);assert app_behaviors({})==[]
 assert app_behaviors({'bundle':{'app':['my:behavior']}})==['my:behavior']
 monkeypatch.setenv('AMPLIFIER_ACP_HOST_FEATURES',json.dumps({'unifiedShell':True}))
 assert app_behaviors({})==[SHELL_BEHAVIOR_URI]
 assert IMAGEGEN_BEHAVIOR_URI not in app_behaviors({})
