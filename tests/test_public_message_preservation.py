"""Source composition tests: real contexts/store, no provider or installed-worker claim."""
import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from amplifier_acp import history
from amplifier_acp.public_message import (
    META, ENVELOPE, COPY_SOURCE, HISTORY_ONLY, PendingOriginals, validate,
    locator, public_row, read_row, supports_context, CAPABILITY, transport,
)


def envelope(identity='input', blocks=None):
    return {'version': 1, 'inputId': identity, 'capture': 'host-standard',
            'blocks': blocks if blocks is not None else [{'type': 'text', 'text': '  literal\n'}]}


def card(index=0):
    return {'type': 'attachment', 'reference': {'owner': 'resources',
            'id': f'resource://original-scope/{index}?opaque=unchanged'},
            'name': f'Literal label {index}', 'mimeType': 'image/png', 'size': 123}


def row(identity='input', blocks=None, content='EXPANDED SCAFFOLDING'):
    return {'role': 'user', 'content': content, 'metadata': {
        'amplifier_input': {'version': 1, 'id': identity, 'kind': 'user', 'source': 'user'},
        ENVELOPE: envelope(identity, blocks)}}


def save(directory, rows):
    from amplifier_foundation.session.history import SessionHistoryStore
    directory.mkdir(parents=True, exist_ok=True)
    SessionHistoryStore(directory).save(rows, {'session_id': directory.name, 'bundle': 'work'}, preserve_system=True)


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(inputId='other'), lambda e: e.update(version=True),
    lambda e: e.update(untrusted='extra'), lambda e: e['blocks'].append({'type': 'range', 'text': 'x'}),
    lambda e: e['blocks'].append({**card(), 'payload': 'base64'}),
    lambda e: e['blocks'].append({**card(), 'size': True}),
])
def test_strict_transport_before_effects(mutate):
    value=envelope();mutate(value)
    server=SimpleNamespace(config={},extensions={'amplifier.dev/native'})
    with pytest.raises(ValueError):transport(server,{META:value},'input')


def test_runtime_negotiation_and_literal_snapshot():
    assert CAPABILITY['runtimeNegotiated'] is True and 'supported' not in CAPABILITY
    original=envelope(blocks=[{'type':'text','text':' '},card(),{'type':'text','text':''}])
    value=validate(original,'input');original['blocks'][1]['name']='changed'
    assert value['blocks'][1]['name']=='Literal label 0'
    assert transport(SimpleNamespace(config={},extensions=set()),{META:value},'input') is None
    assert transport(SimpleNamespace(config={},extensions={'amplifier.dev/native'}),{},'input') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['simple','managed-boundary'])
async def test_exact_admission_same_row_request_boundary_and_cold_resume(tmp_path,kind):
    from amplifier_acp.native.input_provenance import Context
    from amplifier_foundation.session.history import SessionHistoryStore
    if kind=='simple':
        from amplifier_module_context_simple import SimpleContextManager
        context=SimpleContextManager()
    else:
        from amplifier_module_context_managed.boundary import BoundaryContextManager
        context=BoundaryContextManager({'token_meter':'estimated'})
    assert supports_context(context)
    pending=PendingOriginals();pending.stage('input',envelope())
    wrapper=Context(context,tmp_path/'app'/'input-provenance.sqlite3',pending)
    await wrapper.add_message({'role':'user','content':'unrelated','metadata':{'amplifier_input':{'id':'other'}}})
    assert pending.get('input') is not None
    canonical=row();canonical['metadata'].pop(ENVELOPE)
    await wrapper.add_message(canonical)
    assert pending.get('input') is None
    rows=await context.get_messages()
    assert rows[-1]['content']=='EXPANDED SCAFFOLDING' and rows[-1]['metadata'][ENVELOPE]==envelope()
    request=await context.get_messages_for_request()
    assert all(ENVELOPE not in item.get('metadata',{}) for item in request)
    assert request[-1]['content']=='EXPANDED SCAFFOLDING'
    assert rows[-1]['metadata'][ENVELOPE]==envelope()
    directory=tmp_path/'session';save(directory,rows)
    assert SessionHistoryStore(directory).load_messages()==rows
    assert not (tmp_path/'app'/'input-provenance.sqlite3').exists()
    assert {p.name for p in directory.iterdir()} <= {'transcript.jsonl','metadata.json','.metadata.lock'}


@pytest.mark.asyncio
async def test_failed_insertion_keeps_pending_and_no_replay(tmp_path):
    from amplifier_acp.native.input_provenance import Context
    class Failing:
        async def add_message(self,_):raise RuntimeError('not inserted')
    pending=PendingOriginals();pending.stage('input',envelope())
    with pytest.raises(RuntimeError):await Context(Failing(),tmp_path/'inputs.sqlite3',pending).add_message(row())
    assert pending.get('input')==envelope()
    with pytest.raises(ValueError):pending.stage('input',envelope())
    assert not supports_context(Failing())


def test_paginated_cards_no_total_cap_and_exact_row_survives_append(tmp_path):
    directory=tmp_path/'session';rows=[row(str(i),[card(i)]) for i in range(61)]
    save(directory,rows);source=(directory/'transcript.jsonl').read_bytes()
    seen=[];cursor=None
    while True:
        result=history.page(directory,limit=7,cursor=cursor,include_public_rows=True)
        seen.extend(result['publicRows'])
        assert all('messageReference' not in u['_meta']['amplifier.dev/history'] for u in result['updates'])
        cursor=result['nextCursor']
        if cursor is None:
            assert result['publicRowsComplete'];break
        assert not result['publicRowsComplete']
    assert len(seen)==61
    selected=seen[0]['locator']
    with (directory/'transcript.jsonl').open('ab') as stream:stream.write(b'{"role":"assistant","content":"later"}\n')
    assert read_row(directory,selected)['publicRows'][0]==seen[0]
    assert (directory/'transcript.jsonl').read_bytes().startswith(source)
    with pytest.raises(history.HistoryError):read_row(directory,{**selected,'historyEpoch':'changed','messageId':'native:changed:'+selected['messageId'].split(':')[-1]})
    with pytest.raises(history.HistoryError):read_row(directory,{**selected,'position':selected['position']+1})
    with pytest.raises(history.HistoryError):read_row(directory,{**selected,'messageId':'native:'+('0'*24)})


def test_public_projection_order_quote_and_legacy_unchanged(tmp_path):
    from amplifier_acp.native.automatic_history import display_message
    original=row(blocks=[{'type':'text','text':' A'},card(),{'type':'text','text':'B\n'}])
    updates,issues=history.content_updates(original,0)
    assert not issues
    assert [u['_meta']['amplifier.dev/history']['originalBlockIndex'] for u in updates]==[0,1,2]
    assert updates[1]['content']['uri']==card()['reference']['id']
    assert display_message(original,0,{'id':'session'})['text']==' AB\n'
    assert history.message_reference(tmp_path,original,0,None)['sourceDigest'] == history.message_reference(tmp_path,{'role':'user','content':' AB\n'},0,None)['sourceDigest']
    legacy={'role':'user','content':'  legacy\n'}
    assert history.content_updates(legacy,0)[0][0]['content']=={'type':'text','text':'  legacy\n'}
    assert 'publicMessage' not in display_message(legacy,0,{'id':'session'})


def test_invalid_original_or_lineage_is_explicit(tmp_path):
    directory=tmp_path/'session';invalid=row();invalid['metadata'][COPY_SOURCE]={'invalid':True}
    save(directory,[invalid]);result=history.page(directory,include_public_rows=True)
    assert result['publicRows']==[] and not result['publicRowsComplete']
    assert {'code':'invalid_public_row','position':0} in result['diagnostics']
    invalid['metadata'][ENVELOPE]['inputId']='different'
    save(directory,[invalid]);result=history.page(directory,include_public_rows=True)
    assert result['updates']==[] and any(d['code']=='invalid_public_message' for d in result['diagnostics'])


def server_fixture(tmp_path,rows):
    identity=str(uuid.uuid4());cwd=tmp_path/'cwd';cwd.mkdir()
    server=SimpleNamespace(config={'appHome':str(tmp_path/'app')},sessions={},message_principal=None)
    server.directory=lambda _,sid:tmp_path/'history'/sid
    save(server.directory(str(cwd),identity),rows)
    return server,identity,str(cwd)


@pytest.mark.parametrize('mode',['complete','recovery'])
def test_real_fork_lineage_immediate_source_and_original_unchanged(tmp_path,mode):
    from amplifier_acp.session_lifecycle import Lifecycle
    rows=[{'role':'system','content':'policy'},row(blocks=[card()],content=[]),
          {'role':'assistant','content':'answer','tool_calls':[{'id':'tool','function':{'name':'read','arguments':'{}'}}]},
          {'role':'tool','tool_call_id':'tool','content':'receipt'}]
    server,identity,cwd=server_fixture(tmp_path,rows)
    source=server.directory(cwd,identity)/'transcript.jsonl';before=source.read_bytes()
    lifecycle=Lifecycle(server,identity,cwd)
    target=str(uuid.uuid4());args={'mode':mode,'commandId':'fork','targetSessionId':target,'expectedHistoryRevision':history.inspect(source.parent)['revision']}
    prepared=lifecycle.prepare_fork(args);result=lifecycle.fork(args,prepared)
    assert result['creationConfirmed'] and result['workReplayed'] is False
    first=history.page(server.directory(cwd,target),include_public_rows=True)['publicRows'][0]
    assert first['copySource']['source']==locator(identity,rows[1],len((json.dumps(rows[0],ensure_ascii=False)+'\n').encode()),None)
    assert first['copySource']['disposition']==('history-only' if mode=='recovery' else 'retained')
    if mode=='recovery':
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_module_context_simple.request_view import request_view
        copied=SessionHistoryStore(server.directory(cwd,target)).load_messages()
        assert all(ENVELOPE not in r.get('metadata',{}) for r in request_view(copied))
        assert not any(r.get('metadata',{}).get(HISTORY_ONLY) for r in request_view(copied))
    next_lifecycle=Lifecycle(server,target,cwd);next_target=str(uuid.uuid4())
    args={**args,'commandId':'next','targetSessionId':next_target,'expectedHistoryRevision':result['historyRevision']}
    next_lifecycle.fork(args,next_lifecycle.prepare_fork(args))
    second=history.page(server.directory(cwd,next_target),include_public_rows=True)['publicRows'][0]
    assert second['copySource']['source']==first['locator']
    assert second['publicMessage']==first['publicMessage']
    assert source.read_bytes()==before
    with pytest.raises(ValueError,match='already exists'):next_lifecycle.prepare_fork(args)


def test_export_literal_text_and_original_descriptor(tmp_path):
    from amplifier_acp.session_lifecycle import Lifecycle
    rows=[row(blocks=[{'type':'text','text':'A'},card(),{'type':'text','text':' B'}])]
    server,identity,cwd=server_fixture(tmp_path,rows)
    body,_=Lifecycle(server,identity,cwd).prepare_export({'expectedHistoryRevision':history.inspect(server.directory(cwd,identity))['revision']})
    message=json.loads(body)['messages'][0]
    assert message['text']=='A B' and message['publicMessage']==rows[0]['metadata'][ENVELOPE]
    assert message['attachments']==[{**card(),'omittedPayload':True}]


@pytest.mark.asyncio
async def test_default_work_real_mount_measured_request_and_summary(tmp_path,monkeypatch):
    from pathlib import Path
    import yaml
    from amplifier_foundation import Bundle
    from amplifier_module_context_managed import mount
    from amplifier_acp.native.runtime_worker import Worker
    from amplifier_acp.native.input_provenance import Context
    from unittest.mock import AsyncMock
    # Exact current Work source snapshot; no preparation/install or provider call.
    work=Path(__file__).resolve().parents[3]/'outputs/native-public-originals-source-20261004-r01/reference/work'
    behavior=yaml.safe_load((work/'behaviors/work-local.yaml').read_text())
    session=yaml.safe_load((work/'bundles/work-session.yaml').read_text())
    root=(work/'bundle.md').read_text().split('---',2)[1]
    assert 'work:bundles/work-session.yaml' in root
    assert session['includes']==[{'bundle':'work:behaviors/work-local.yaml'}]
    base=Bundle(name='work',session=session['session'])
    plan=base.compose(Bundle(name='work-local',session=behavior['session'])).to_mount_plan()
    assert plan['session']['context']['module']=='context-managed'
    config=plan['session']['context']['config']
    assert config['engine']=='boundary' and config['token_meter']=='actual'
    mounted={};capabilities={}
    async def mount_context(name,value):mounted[name]=value
    coordinator=SimpleNamespace(hooks=None,mount=mount_context,get=mounted.get,
        get_capability=capabilities.get,register_capability=lambda k,v:capabilities.__setitem__(k,v),
        register_contributor=lambda *args:None)
    await mount(coordinator,config)
    context=mounted['context'];assert supports_context(context)
    worker=Worker.__new__(Worker);worker.session=SimpleNamespace(coordinator=coordinator)
    worker.public_originals=PendingOriginals()
    wrapped=Context(context,tmp_path/'inputs.sqlite3',worker.public_originals)
    mounted['context']=wrapped
    canonical=row();canonical['metadata'].pop(ENVELOPE)
    worker.stage_original('input',envelope())
    await wrapped.add_message(canonical)
    history_only=row('reference',[card()],content=[]);history_only['metadata'][HISTORY_ONLY]=True
    await wrapped.add_message(history_only)
    snapshots=[]
    async def count_view(view):
        snapshots.append(copy.deepcopy(view))
        return {'dispatch':object(),'budget_decision':{'estimated_input_tokens':20,'input_limit_tokens':10000,
            'measurement':{'kind':'provider_count','source':'fixture','input_tokens':20}}}
    measured=await capabilities['context.measured_request_view'](provider=None,retain_contents=[],count_view=count_view)
    assert snapshots and all(ENVELOPE not in str(v) and COPY_SOURCE not in str(v) and HISTORY_ONLY not in str(v) for v in snapshots)
    assert 'EXPANDED SCAFFOLDING' in str(measured) and 'resource://original-scope' not in str(measured)
    assert (await wrapped.get_messages())[0]['metadata'][ENVELOPE]==envelope()
    # Actual summary call gets the same shared view, with enough human turns
    # to exercise a real summary boundary rather than a protected-tail no-op.
    context.config.update(max_tokens=6000,summarize_trigger=0.2)
    rows=[row('first'),{'role':'assistant','content':'evidence '*1000},row('middle'),
          {'role':'assistant','content':'progress '*200},row('last')]
    await context.set_messages(rows)
    async def complete(request):
        assert ENVELOPE not in str(request) and COPY_SOURCE not in str(request)
        return SimpleNamespace(content=[SimpleNamespace(type='text',text='Objective and progress preserved.')])
    provider=SimpleNamespace(complete=AsyncMock(side_effect=complete))
    view=await context.get_messages_for_request(provider=provider)
    assert provider.complete.await_count>0 and ENVELOPE not in str(view)
    assert await context.get_messages()==rows
    # The same worker's unknown context is explicitly unavailable, even though
    # the qualified shared library remains importable in this runtime.
    class ThirdParty:
        async def get_messages_for_request(self):return []
    events=[];monkeypatch.setattr('amplifier_acp.native.runtime_worker.publish',events.append)
    mounted['context']=ThirdParty();worker.stage_original('unsupported',envelope('unsupported'))
    assert worker.public_originals.get('unsupported') is None
    assert events==[{'type':'public.message.unavailable','input_id':'unsupported',
        'reason':'shared-request-view-unavailable','originalAuthority':'host'}]


@pytest.mark.asyncio
async def test_route_identity_validation_precedes_revise_or_steering_effects():
    from amplifier_acp.steering import validate as validate_steer
    from amplifier_acp.history_edit import revise
    bad=envelope('different')
    with pytest.raises(ValueError):validate_steer({'inputId':'input','activeInputId':'active','text':'x','public_message':bad})
    # Empty worker deliberately has no controls/files/runtime: mismatch must
    # fail before those effects can be touched.
    with pytest.raises(ValueError):await revise(SimpleNamespace(),{
        'inputId':'input','operationId':'edit','targetMessageId':'native:target',
        'expectedHistoryRevision':'revision','text':'x','public_message':bad},None)


def test_fork_readback_failure_leaves_unknown_creation_without_refork(tmp_path,monkeypatch):
    from amplifier_acp.session_lifecycle import Lifecycle
    from amplifier_foundation.session.history import SessionHistoryStore
    server,identity,cwd=server_fixture(tmp_path,[row()])
    lifecycle=Lifecycle(server,identity,cwd);target=str(uuid.uuid4())
    args={'commandId':'fork','targetSessionId':target,'expectedHistoryRevision':history.inspect(server.directory(cwd,identity))['revision']}
    prepared=lifecycle.prepare_fork(args);original=SessionHistoryStore.save
    def omit_original(self,messages,metadata,**kwargs):
        altered=copy.deepcopy(messages)
        for item in altered:item.get('metadata',{}).pop(ENVELOPE,None)
        return original(self,altered,metadata,**kwargs)
    monkeypatch.setattr(SessionHistoryStore,'save',omit_original)
    with pytest.raises(ValueError,match='creation remains unknown'):lifecycle.fork(args,prepared)
    marker=json.loads((tmp_path/'app/sessions'/target/'history-fork.json').read_text())
    assert marker['state']=='reserved'
    with pytest.raises(ValueError,match='already exists'):lifecycle.prepare_fork(args)


def test_direct_native_capture_prepares_snapshot_and_acp_absence_keeps_legacy():
    from amplifier_acp.public_message import input_original
    data={'input_id':'input','text':' @literal mention\n', 'attachments':[
        {'id':'native-opaque-id','name':'literal file','mime':'image/png','size':2}]}
    original=input_original(data)
    data['text']='expanded prompt';data['attachments'][0]['name']='mutated'
    assert original['capture']=='native-input'
    assert original['blocks']==[{'type':'text','text':' @literal mention\n'},
        {'type':'attachment','reference':{'owner':'native-attachments','id':'native-opaque-id'},
         'name':'literal file','mimeType':'image/png','size':2}]
    assert input_original({**data,'public_input_transport':'acp'}) is None


@pytest.mark.asyncio
async def test_invalid_prompt_original_precedes_native_attachment_effects(tmp_path,monkeypatch):
    from amplifier_acp.server import Session
    server=SimpleNamespace(config={'appHome':str(tmp_path/'app')},extensions={'amplifier.dev/native'})
    session=Session(server,'session',str(tmp_path))
    def effect(*_):raise AssertionError('attachment conversion ran before validation')
    monkeypatch.setattr('amplifier_acp.content.image_attachment',effect)
    with pytest.raises(ValueError):await session.send([{'type':'image','mimeType':'image/png','data':'data'}],{
        'amplifier.dev/command':{'commandId':'input'},META:envelope('different')})
    assert not (tmp_path/'app').exists()


@pytest.mark.asyncio
async def test_source_rpc_storage_and_reserved_slot_without_worker(tmp_path,monkeypatch):
    from io import StringIO
    from amplifier_acp.server import Server,Session,RpcError,NATIVE
    from amplifier_acp.public_message import mounted_capability
    server=Server({'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app'),
        'workerCommand':['/must-not-start']},StringIO())
    init=await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{NATIVE:{'version':1}}}})
    capability=init['agentCapabilities']['_meta'][NATIVE]['publicMessagePreservation']
    assert capability==CAPABILITY and 'supported' not in capability
    cwd=tmp_path/'cwd';cwd.mkdir();sid=str(uuid.uuid4());directory=server.directory(str(cwd),sid)
    save(directory,[row()]);before=(directory/'transcript.jsonl').read_bytes()
    result=await server.dispatch('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'includePublicRows':True})
    exact=await server.dispatch('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),
        'includePublicRows':True,'rowLocator':result['publicRows'][0]['locator']})
    assert exact['publicRows']==result['publicRows'] and not server.sessions
    with pytest.raises(RpcError):await server.dispatch('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),
        'includePublicRows':True,'cursor':'invalid','rowLocator':result['publicRows'][0]['locator']})
    session=Session(server,sid,str(cwd));server.sessions[sid]=session;seen=[]
    async def settled(operation,args,**kwargs):seen.append((operation,args));return {'accepted':False}
    session.settled_input=settled
    async def steering(_,args):seen.append(('steer.submit',args));return {'accepted':False}
    monkeypatch.setattr('amplifier_acp.steering.submit',steering)
    for operation in ('history.revise','steer.submit'):
        params={'sessionId':sid,'operation':operation,'args':{'inputId':'input'},'_meta':{META:envelope()}}
        await server.dispatch('_amplifier/native',params)
        assert seen[-1]==(operation,{'inputId':'input','public_message':envelope()})
        with pytest.raises(RpcError):await server.dispatch('_amplifier/native',{
            **params,'args':{'inputId':'input','public_message':envelope()}})
    assert (directory/'transcript.jsonl').read_bytes()==before
    assert mounted_capability(object())=={'version':1,'supported':False,'originalAuthority':'host',
        'reason':'shared-request-view-unavailable'}


def test_hidden_original_row_has_no_public_projection(tmp_path):
    hidden=row();hidden['metadata']['ephemeral']=True
    assert public_row('session',hidden,0,None) is None
    assert history.content_updates(hidden,0)==([],[])
    directory=tmp_path/'session';save(directory,[hidden])
    assert history.page(directory,include_public_rows=True)['publicRows']==[]


def test_third_party_measured_override_is_not_qualified_by_inherited_ordinary_route():
    from amplifier_module_context_simple import SimpleContextManager
    class ThirdParty(SimpleContextManager):
        async def get_measured_request_view(self,**kwargs):return self.messages
    assert not supports_context(ThirdParty())
