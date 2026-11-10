"""Standard descriptors are passed to a supplied native owner, never installed here."""
import io
import pytest

from amplifier_acp.server import Server, RpcError
from amplifier_acp.mcp import owned_servers
from test_execution_owner import owner


HTTP = {'type':'http','name':'context','url':'https://context.example/mcp',
        'headers':[{'name':'Authorization','value':'Bearer synthetic-secret'}]}
STDIO = {'name':'local','command':'/owned/server','args':['--stdio'],
         'env':[{'name':'SYNTHETIC','value':'one'}]}


async def test_owner_negotiates_and_receives_both_standard_transports(tmp_path):
    backend=owner();backend.mcp_capabilities={'http':True}
    backend.attach.side_effect=lambda identity,cwd,**kwargs:identity
    server=Server({},io.StringIO(),execution_owner=backend)
    initialized=await server.dispatch('initialize',{})
    assert initialized['agentCapabilities']['mcpCapabilities']=={'http':True,'sse':False}
    await server.dispatch('session/new',{'cwd':str(tmp_path),'mcpServers':[HTTP,STDIO]})
    expected={'context':{'type':'http','url':HTTP['url'],'headers':{'Authorization':'Bearer synthetic-secret'}},
              'local':{'command':'/owned/server','args':['--stdio'],'env':{'SYNTHETIC':'one'}}}
    backend.create.assert_awaited_once_with(str(tmp_path),mcp_servers=expected)
    await server.dispatch('session/resume',{'sessionId':'saved','cwd':str(tmp_path),'mcpServers':[HTTP]})
    backend.attach.assert_awaited_once_with('saved',str(tmp_path),mcp_servers={'context':expected['context']})
    backend.prompt.assert_not_called()


async def test_old_owner_does_not_advertise_or_receive_mcp_configuration(tmp_path):
    backend=owner();server=Server({},io.StringIO(),execution_owner=backend)
    initialized=await server.dispatch('initialize',{})
    assert 'mcpCapabilities' not in initialized['agentCapabilities']
    with pytest.raises(RpcError,match='not supported'):
        await server.dispatch('session/new',{'cwd':str(tmp_path),'mcpServers':[HTTP]})
    backend.create.assert_not_called()


@pytest.mark.parametrize('servers',[None,[HTTP,HTTP],[{**HTTP,'url':'relative'}],
    [{**HTTP,'headers':HTTP['headers']*2}],[{**STDIO,'command':'relative'}],
    [{**STDIO,'env':STDIO['env']*2}],[{**HTTP,'type':'sse'}]])
async def test_malformed_descriptors_fail_before_native_creation(tmp_path,servers):
    backend=owner();backend.mcp_capabilities={'http':True}
    server=Server({},io.StringIO(),execution_owner=backend);await server.dispatch('initialize',{})
    with pytest.raises(ValueError):
        await server.dispatch('session/new',{'cwd':str(tmp_path),'mcpServers':servers})
    backend.create.assert_not_called();assert not server.sessions


def test_http_requires_actual_owner_capability():
    with pytest.raises(ValueError,match='HTTP MCP'):
        owned_servers([HTTP],http=False)
    assert owned_servers([STDIO])['local']['command']=='/owned/server'
