"""Actual Core + Foundation + loop-live through a subprocess ACP connection."""
import asyncio,importlib.util,json,os
from pathlib import Path
import pytest
from client import Client
from schema_check import validate

@pytest.mark.asyncio
async def test_native_graph_turn_resume_ownership_and_controls(tmp_path):
    home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir()
    provider=Path(__file__).parent/'fixtures/provider'
    context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
    bundle=tmp_path/'fixture.yaml'
    bundle.write_text('''bundle:\n  name: offline-native\n  version: 1.0.0+amplifier-unified.snapshot.1\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '''+str(context)+'''\nproviders:\n  - module: provider-fixture\n    source: '''+str(provider.resolve())+'''\n    config: {}\n''')
    (home/'settings.yaml').write_text('bundle:\n  app: []\n')
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(tmp_path/'app'),'bundle':str(bundle),'startupTimeout':90}))
    client=await Client.launch(config)
    try:
        init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['loadSession']
        validate('InitializeResponse',init['result'])
        new=await client.request('session/new',{'cwd':str(workspace),'mcpServers':[]},timeout=120)
        assert 'result' in new,new
        validate('NewSessionResponse',new['result'])
        sid=new['result']['sessionId']
        control=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.inspect'})
        assert control['result']['plan']['session']['orchestrator']['module']=='loop-live'
        result=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'offline first turn'}],'_meta':{'amplifier.dev/command':{'commandId':'stable-first-turn'}}})
        assert result.get('result')=={'stopReason':'end_turn'},result
        validate('PromptResponse',result['result'])
        assert any('Native graph reply' in json.dumps(r) for r in client.events)
        path=home/'projects'/('-'+str(workspace).replace('/','-').lstrip('-'))/'sessions'/sid/'transcript.jsonl'
        assert path.exists();before=path.read_bytes()
        assert b'offline first turn' in before
        duplicate=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'offline first turn'}],'_meta':{'amplifier.dev/command':{'commandId':'stable-first-turn'}}})
        assert duplicate['error']['data']['replayed'] is False
        permission=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'permission please'}]})
        assert 'result' in permission,permission
        assert any(r['method']=='session/request_permission' for r in client.requests)
        assert any('Native graph reply: permission please' in json.dumps(r) for r in client.events)
        assert any(r.get('method')=='session/update' and r['params']['update']['sessionUpdate']=='tool_call' for r in client.events)
        before=path.read_bytes()
        passive=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(workspace),'limit':50})
        assert passive['result']['turnGroups'][0]['id']=='stable-first-turn'

        await client.request('session/close',{'sessionId':sid})
        count=len(client.events)
        result=await client.request('session/resume',{'sessionId':sid,'cwd':str(workspace),'mcpServers':[]})
        assert 'result' in result,result
        assert not any(r.get('method')=='session/update' and r['params']['update']['sessionUpdate'] in {'user_message_chunk','agent_message_chunk'} for r in client.events[count:])
        assert path.read_bytes()==before
        await client.request('session/close',{'sessionId':sid})
        count=len(client.events)
        loaded=await client.request('session/load',{'sessionId':sid,'cwd':str(workspace),'mcpServers':[]})
        assert 'result' in loaded,loaded
        assert any('offline first turn' in json.dumps(r) for r in client.events[count:])
        missing=await client.request('session/resume',{'sessionId':'nonexistent','cwd':str(workspace),'mcpServers':[]})
        assert 'error' in missing
        for event in client.events:
            if event['method']=='session/update':validate('SessionNotification',event['params'])
            elif event['method']=='session/request_permission':validate('RequestPermissionRequest',event['params'])
    finally:
        stderr=await client.close()
        (tmp_path/'stderr.log').write_text(stderr)
        (tmp_path/'events.json').write_text(json.dumps(client.events,indent=2))
