import asyncio,json,sys
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_real_cancel_ownership_and_fresh_continuation(tmp_path):
    config,cwd=configuration(tmp_path)
    a=await Client.launch(config);b=await Client.launch(config)
    try:
        for client in [a,b]:await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        new=await a.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
        pending=asyncio.create_task(a.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'slow turn'}]},timeout=30))
        for _ in range(100):
            if any(r.get('params',{}).get('event',{}).get('type')=='generation.started' for r in a.events):break
            await asyncio.sleep(.01)
        competing=await b.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert competing.get('error',{}).get('data',{}).get('code')=='session_busy',competing
        await a.send({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':sid}})
        assert (await pending)['result']['stopReason']=='cancelled'
        await a.request('session/close',{'sessionId':sid})
        resumed=await b.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert 'result' in resumed,resumed
        result=await b.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'fresh explicit turn'}]})
        assert result['result']['stopReason']=='end_turn'
    finally:
        (tmp_path/'first.stderr').write_text(await a.close());(tmp_path/'second.stderr').write_text(await b.close())

@pytest.mark.asyncio
async def test_actual_mcp_stdio_and_tool_execution(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[{'name':'offline','command':sys.executable,'args':[str(Path(__file__).parent/'fixtures/mcp_server.py')],'env':[]}]})
        assert 'result' in new,new
        sid=new['result']['sessionId']
        catalog=await client.request('_amplifier/native',{'sessionId':sid,'operation':'catalog.inspect'})
        name=next(t['name'] for t in catalog['result']['tools'] if 'echo' in t['name'])
        result=await client.request('_amplifier/native',{'sessionId':sid,'operation':'tool.invoke','args':{'name':name,'arguments':{'text':'ACP stdio MCP proof'}}})
        assert 'ACP stdio MCP proof' in json.dumps(result),result
        assert 'error' not in result,result
    finally:(tmp_path/'stderr.log').write_text(await client.close())

@pytest.mark.asyncio
async def test_settled_worker_retires_and_reopens_without_replay(tmp_path):
    config,cwd=configuration(tmp_path,idleSeconds=1);client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        new=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});sid=new['result']['sessionId']
        first=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'first retained turn'}]})
        assert first['result']['stopReason']=='end_turn'
        for _ in range(100):
            if any(r.get('params',{}).get('event',{}).get('type')=='runtime.closed' for r in client.events):break
            await asyncio.sleep(.05)
        assert any(r.get('params',{}).get('event',{}).get('type')=='runtime.closed' for r in client.events)
        count=len(client.events)
        rejected=await client.request('_amplifier/native',{'sessionId':sid,'operation':'operations.cancel','args':{'processId':'old'},'_meta':{'amplifier.dev/native':{'requireExisting':True}}})
        assert rejected.get('error',{}).get('code')==-32008,rejected
        assert rejected['error']['data']=={'executed':False,'replayed':False}
        assert not any(r.get('params',{}).get('event',{}).get('type')=='runtime.ready' for r in client.events[count:])
        second=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'second explicit turn'}]})
        assert second['result']['stopReason']=='end_turn'
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        visible=[u['content']['text'] for u in history['result']['updates'] if u['sessionUpdate']=='user_message_chunk']
        assert visible==['first retained turn','second explicit turn']
    finally:await client.close()
