import asyncio,json,uuid
import pytest
from amplifier_acp.native.execution_events import ExecutionEvents,CURRENT_CALL
from amplifier_acp.model_detail import read

@pytest.mark.parametrize('keys', [('raw','raw'), ('raw_request','raw_response')])
def test_recorded_wire_details_redacted_bounded_and_scoped(tmp_path,keys):
    sid='session-one';events=[];observer=ExecutionEvents(sid,events.append,tmp_path/'sessions'/sid/'model-calls')
    call='llm:'+str(uuid.uuid4())
    observer.publish({'id':call,'kind':'llm','sessionId':sid,'turnId':'turn','phase':'running'})
    token=CURRENT_CALL.set(call)
    try:
        observer.hook(sid,'llm:request',{keys[0]:{'authorization':'Bearer private','messages':[{'role':'user','content':'hello'}]}})
        observer.hook(sid,'llm:response',{keys[1]:{'output':'world','api_key':'secret'}})
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

def test_delegate_header_stays_with_calls_across_interim_messages_and_revisions():
    events=[];observer=ExecutionEvents('root',events.append)
    observer.turn_id='turn'
    observer.lifecycle({'type':'child.updated','sessionId':'review','agent':'Reviewer','status':'running','report':'Large report must not enter call headers'})
    observer.publish({'id':'llm:one','kind':'llm','sessionId':'review','provider':'fixture','model':'review-model','phase':'running'})
    observer.publish({'id':'tool:later','kind':'tool','sessionId':'review','phase':'running'})
    observer.publish({'id':'tool:later','kind':'tool','phase':'completed'})
    node=events[-1]['event']
    assert node['delegate']['id']=='worker:review'
    assert node['delegate']['label']=='Reviewer'
    assert node['delegate']['model']=='review-model'
    assert node['sessionId']=='review'
    assert 'summary' not in node['delegate']
    observer.publish({'id':'root-call','kind':'llm','sessionId':'root'})
    assert 'delegate' not in events[-1]['event']

def test_standard_acp_tool_updates_keep_delegate_header(tmp_path):
    from types import SimpleNamespace
    from amplifier_acp.server import Session
    messages=[]
    async def notify(method,params):messages.append((method,params))
    server=SimpleNamespace(config={'home':str(tmp_path)},extensions=set(),notify=notify)
    session=Session(server,'root',str(tmp_path))
    header={'id':'worker:review','sessionId':'review','label':'Reviewer'}
    async def observe():
        for phase in ('running','completed'):
            await session.observe({'type':'execution.event','event':{'id':'tool:one','kind':'tool','sessionId':'review','toolCallId':'one','label':'read','phase':phase,'delegate':header,'startedAt':12,'endedAt':13 if phase=='completed' else None}})
    asyncio.run(observe())
    assert [p['update']['sessionUpdate'] for _,p in messages]==['tool_call','tool_call_update']
    assert all(p['update']['_meta']['amplifier.dev/execution']['delegate']==header for _,p in messages)
    assert all(p['update']['toolCallId']=='review:one' for _,p in messages)

@pytest.mark.parametrize('negotiated',[False,True])
def test_delegate_report_forwarding_is_negotiated_and_input_bound(tmp_path,negotiated):
    from types import SimpleNamespace
    from amplifier_acp.server import Session,NATIVE
    messages=[]
    async def notify(method,params):messages.append((method,params))
    server=SimpleNamespace(config={'home':str(tmp_path)},extensions={NATIVE} if negotiated else set(),notify=notify)
    session=Session(server,'root',str(tmp_path));session.input_id='original-input'
    report={'id':'worker:review','kind':'worker','sessionId':'review','label':'Reviewer','revision':2,'phase':'completed','summary':'**Review complete.**'}
    asyncio.run(session.observe({'type':'execution.event','event':report}))
    assert messages==([('_amplifier/event',{'sessionId':'root','event':{'type':'execution.event','event':report,'activeInputId':'original-input'}})] if negotiated else [])
