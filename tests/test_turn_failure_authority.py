"""Root generation evidence owns failure; idle and successful tools do not."""
import asyncio
import json
import pytest
from amplifier_acp.server import Server, Session
from amplifier_acp.worker import NativeError
from amplifier_acp.lifecycle_failure import read
from native_setup import configuration

@pytest.mark.asyncio
async def test_idle_then_failed_root_stays_pending_until_failure_and_keeps_tool_success(tmp_path):
    config,cwd=configuration(tmp_path)
    server=Server(json.loads(config.read_text()),None)
    updates=[]
    async def notify(method,params): updates.append((method,params))
    server.notify=notify
    session=Session(server,'root-failure',str(cwd))
    session.prompt=asyncio.get_running_loop().create_future()
    session.input_id='input-one'
    await session.observe({'type':'execution.event','event':{
        'id':'tool-one','sessionId':session.identity,'kind':'tool','label':'read_file',
        'phase':'completed','output':{'text':'original tool result'}}})
    await session.observe({'type':'session.idle','sessionId':session.identity})
    assert not session.prompt.done()
    await session.observe({'type':'generation.failed','sessionId':session.identity,
        'rootSessionId':session.identity,'input_ids':['input-one'],
        'error_type':'ProviderError','error_category':'provider_unavailable'})
    with pytest.raises(NativeError,match='generation failed'): await session.prompt
    assert read(server,session.identity)['inputId']=='input-one'
    tool=[params['update'] for method,params in updates if method=='session/update']
    assert tool[0]['status']=='completed' and tool[0]['toolCallId']=='root-failure:tool-one'

@pytest.mark.asyncio
@pytest.mark.parametrize('identity,root',[('child','root-failure'),('root-failure','other-root')])
async def test_child_or_foreign_root_failure_never_finishes_or_records_parent_failure(tmp_path,identity,root):
    config,cwd=configuration(tmp_path)
    server=Server(json.loads(config.read_text()),None)
    async def notify(*args): pass
    server.notify=notify
    session=Session(server,'root-failure',str(cwd))
    session.prompt=asyncio.get_running_loop().create_future();session.input_id='input-one'
    await session.observe({'type':'generation.failed','sessionId':identity,'rootSessionId':root,
        'input_ids':['input-one'],'error_type':'ProviderError','error_category':'provider_unavailable'})
    assert not session.prompt.done() and read(server,session.identity) is None
    session.prompt.cancel()
