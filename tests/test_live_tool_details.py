"""Public Native hook -> standard ACP detail boundary; no private event-log read."""
import json
from types import SimpleNamespace
import pytest
from amplifier_acp.native.execution_events import ExecutionEvents, public_tool_detail, TOOL_DETAIL_BYTES
from amplifier_acp.server import Session


def test_small_details_and_credential_fields():
    value={'path':'/workspace/a.txt','nested':{'api_key':'private','authorization':'Bearer private','max_tokens':42},'items':[1,True,None]}
    expected={**value,'nested':{'api_key':'[REDACTED]','authorization':'[REDACTED]','max_tokens':42}}
    assert public_tool_detail(value)==expected
    assert value['nested']['api_key']=='private'

@pytest.mark.parametrize('value', ['😀'*1000000, {'rows':list(range(100000))}, [[[[[[[[[[1]]]]]]]]]], float('nan'), 1 << 10000])
def test_detail_bounds(value):
    output=public_tool_detail(value)
    assert len(json.dumps(output,ensure_ascii=True).encode()) < TOOL_DETAIL_BYTES
    assert output is None or 'truncated' in str(output)


def test_cycles_unknown_objects_and_no_full_walk():
    value={};value['self']=value
    assert 'truncated' in public_tool_detail(value)
    class NeverSerialize:
        def __repr__(self):raise AssertionError('repr is forbidden')
        def model_dump(self):raise AssertionError('model dump is forbidden')
    assert 'Unsupported' in public_tool_detail(NeverSerialize())
    class ShortWalk(dict):
        def items(self):
            for n in range(100):
                if n>64:raise AssertionError('unbounded walk')
                yield str(n), 'value'
    assert 'truncated' in public_tool_detail(ShortWalk())

@pytest.mark.asyncio
@pytest.mark.parametrize('sid',['root','child'])
@pytest.mark.parametrize('terminal',['tool:post','tool:error'])
async def test_authoritative_hook_to_standard_acp(sid,terminal):
    wire=[]
    async def notify(method,params):wire.append({'method':method,**params})
    server=SimpleNamespace(config={'home':'/unused'},extensions=set(),notify=notify)
    session=Session.__new__(Session)
    session.server=server;session.identity='root';session.tools=set()
    session.coordination=SimpleNamespace(observe=lambda row:False)
    events=[];observer=ExecutionEvents('root',events.append)
    observer.hook(sid,'tool:pre',{'tool_call_id':'original-id','tool_name':'read_file','tool_input':{'path':'a.txt','secret':'private'}})
    observer.hook(sid,terminal,{'tool_call_id':'original-id','tool_name':'read_file','result':{'success':False,'output':'file body','error':{'message':'failed'}},'error':{'message':'dispatch failed'}})
    for row in events:await session.observe(row)
    first,last=[row['update'] for row in wire]
    assert first['sessionUpdate']=='tool_call' and first['status']=='in_progress'
    assert first['title']=='read_file'
    assert first['toolCallId']==last['toolCallId']==sid+':original-id'
    assert first['rawInput']=={'path':'a.txt','secret':'[REDACTED]'}
    assert last['sessionUpdate']=='tool_call_update' and last['status']=='failed'
    expected={'message':'dispatch failed'} if terminal=='tool:error' else {'success':False,'output':'file body','error':{'message':'failed'}}
    assert last['rawOutput']==expected
    assert json.loads(last['content'][0]['content']['text'])==expected
    assert last['content'][0]['type']=='content'
    assert not any(k in observer.calls[(sid,'original-id')] for k in ('input','output'))

@pytest.mark.asyncio
async def test_terminal_first_and_concurrent_same_original_id():
    wire=[]
    async def notify(method,params):wire.append(params['update'])
    session=Session.__new__(Session);session.identity='root';session.tools=set()
    session.server=SimpleNamespace(extensions=set(),notify=notify)
    session.coordination=SimpleNamespace(observe=lambda row:False)
    events=[];observer=ExecutionEvents('root',events.append)
    for sid in ('root','child-a','child-b'):
        observer.hook(sid,'tool:post',{'tool_call_id':'same','result':{'success':True,'output':sid},'tool_input':{'value':sid}})
    for row in events:await session.observe(row)
    assert {row['toolCallId'] for row in wire}=={'root:same','child-a:same','child-b:same'}
    assert all(row['status']=='completed' and row['rawOutput']['success'] is True and row['rawInput'] for row in wire)


def test_wrong_session_and_missing_id_are_ignored():
    events=[];observer=ExecutionEvents('root',events.append)
    observer.hook('root','tool:pre',{'session_id':'elsewhere','tool_call_id':'a','tool_input':{'a':1}})
    observer.hook('root','tool:post',{'result':{'output':'not attributable'}})
    assert not events

@pytest.mark.asyncio
async def test_fresh_source_acp_actual_native_loop_tool_details(tmp_path):
    import asyncio, sys
    from pathlib import Path
    import amplifier_acp
    from native_setup import configuration
    from client import Client
    package=str(Path(amplifier_acp.__file__).resolve().parent.parent)
    bootstrap="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module(sys.argv.pop(1),run_name='__main__')"
    command=[sys.executable,'-I','-B','-c',bootstrap,package]
    config,cwd=configuration(tmp_path,workerCommand=[*command,'amplifier_acp.native.runtime_worker'])
    process=await asyncio.create_subprocess_exec(*command,'amplifier_acp','--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=1048576)
    client=Client(process)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        created=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]})
        sid=created['result']['sessionId']
        reply=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'permission'}]})
        assert reply.get('result',{}).get('stopReason')=='end_turn',reply
        calls=[row['params']['update'] for row in client.events if row.get('method')=='session/update' and row['params']['update']['sessionUpdate'] in {'tool_call','tool_call_update'}]
        start=next(row for row in calls if row.get('title')=='fixture_permit')
        end=next(row for row in calls if row['toolCallId']==start['toolCallId'] and row['status']=='completed')
        assert start['toolCallId']==sid+':permit-call'
        assert start['rawInput']=={}
        assert end['rawOutput']['output']=={'decision':'deny'}
        assert json.loads(end['content'][0]['content']['text'])==end['rawOutput']
    finally:
        await client.close()
