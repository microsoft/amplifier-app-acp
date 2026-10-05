import asyncio,json,uuid
from amplifier_acp.native.execution_events import ExecutionEvents,CURRENT_CALL
from amplifier_acp.model_detail import read

def test_recorded_wire_details_redacted_bounded_and_scoped(tmp_path):
    sid='session-one';events=[];observer=ExecutionEvents(sid,events.append,tmp_path/'sessions'/sid/'model-calls')
    call='llm:'+str(uuid.uuid4())
    observer.publish({'id':call,'kind':'llm','sessionId':sid,'turnId':'turn','phase':'running'})
    token=CURRENT_CALL.set(call)
    try:
        observer.hook(sid,'llm:request',{'raw':{'authorization':'Bearer private','messages':[{'role':'user','content':'hello'}]}})
        observer.hook(sid,'llm:response',{'raw':{'output':'world','api_key':'secret'}})
    finally:CURRENT_CALL.reset(token)
    observer.publish({'id':call,'kind':'llm','phase':'completed','usage':{'totalTokens':3}})
    node=events[-1]['event']
    for field in ('request','response'):
        detail=node[field+'Detail']
        value=read(tmp_path,{'sessionId':sid,'callId':call,'field':field,'sha256':detail['sha256']})
        assert '[REDACTED]' in value['text']
        assert 'private' not in value['text'] and '"secret"' not in value['text']
        assert len(json.dumps(node))<4000
    assert node['phase']=='completed' and node['requestDetail'] and node['responseDetail']
    assert 'hello' not in json.dumps(events)
    import pytest
    with pytest.raises(FileNotFoundError):
        read(tmp_path,{'sessionId':'different','callId':call,'field':'request','sha256':node['requestDetail']['sha256']})
    with pytest.raises(ValueError):
        read(tmp_path,{'sessionId':sid,'callId':call,'field':'request','sha256':'0'*64})
