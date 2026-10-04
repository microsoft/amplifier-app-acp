import json
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.native.session_files import project_slug
from amplifier_acp.history import message_id

@pytest.mark.asyncio
async def test_public_cold_reactions_and_receipts_no_worker(tmp_path):
    from amplifier_acp.server import Server, RpcError
    from io import StringIO
    native=tmp_path/'native';cwd=tmp_path/'cwd';cwd.mkdir();sid='existing'
    directory=native/'projects'/project_slug(cwd)/'sessions'/sid;directory.mkdir(parents=True)
    row={'role':'assistant','content':'Earlier answer'};transcript=directory/'transcript.jsonl';transcript.write_text(json.dumps(row)+'\n')
    before=transcript.read_bytes();output=StringIO()
    server=Server({'home':str(native),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start']},output,message_principal=lambda identity:'authenticated-user' if identity==sid else None)
    ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
    args={'sessionId':sid,'reference':ref,'emoji':'👍','present':True,'commandId':'once'}
    init=await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert init['agentCapabilities']['_meta']['amplifier.dev/native']['messageInteractions']['version']==1
    with pytest.raises(RpcError):await server.dispatch('_amplifier/message/reaction',args)
    await server.dispatch('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':1})
    read=await server.dispatch('_amplifier/message/read',{'sessionId':sid,'messageIds':[ref['messageId']]})
    assert read['items'][0]['revision']==0
    assert not (tmp_path/'app'/'sessions'/sid/'message-interactions.sqlite3').exists()
    for forged in ({**args,'actorId':'forged'},{**args,'cwd':str(cwd)}):
        with pytest.raises(ValueError):await server.dispatch('_amplifier/message/reaction',forged)
    reacted=await server.dispatch('_amplifier/message/reaction',args);assert reacted['mine']==['👍']
    assert await server.dispatch('_amplifier/message/reaction',args)==reacted
    receipt=await server.dispatch('_amplifier/message/receipt',{'sessionId':sid,'commandId':'once'});assert receipt['receipt']==reacted
    assert transcript.read_bytes()==before and not server.sessions
    events=[json.loads(line) for line in output.getvalue().splitlines()]
    assert len(events)==1 and events[0]['method']=='_amplifier/message/update'

@pytest.mark.asyncio
async def test_send_freezes_before_worker_and_unknown_does_not_replay(tmp_path):
    # Invalid reference refuses before the configured impossible worker is started.
    from amplifier_acp.server import Server,Session,RpcError,NATIVE
    from amplifier_acp.message_metadata import QuotedInputUnknown,input_quote
    from io import StringIO
    server=Server({'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start']},StringIO(),message_principal=lambda identity:'user')
    server.extensions={NATIVE};server.initialized=True
    cwd=tmp_path/'cwd';cwd.mkdir();sid='existing';directory=server.directory(str(cwd),sid);directory.mkdir(parents=True)
    row={'role':'assistant','content':'Prior answer'};(directory/'transcript.jsonl').write_text(json.dumps(row)+'\n')
    ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
    session=Session(server,sid,str(cwd));started=[]
    async def refuse_start():started.append(True);raise RuntimeError('pre-admission worker fixture refusal')
    session.start=refuse_start;server.sessions[sid]=session
    meta={'amplifier.dev/command':{'commandId':'quote-once'},'amplifier.dev/messageReply':ref}
    with pytest.raises(ValueError):await server.dispatch('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'new'}],'_meta':{**meta,'amplifier.dev/messageReply':{**ref,'messageId':'wrong'}}})
    assert started==[]
    with pytest.raises(RuntimeError):await server.dispatch('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'new'}],'_meta':meta})
    assert len(started)==1
    path=tmp_path/'app'/'sessions'/sid/'message-interactions.sqlite3'
    assert input_quote(path,'quote-once')['inputId']=='quote-once'
    with pytest.raises(QuotedInputUnknown):await server.dispatch('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'new'}],'_meta':meta})
    assert len(started)==1

@pytest.mark.asyncio
async def test_context_persists_exact_quote_in_actual_history(tmp_path):
    from amplifier_acp.message_metadata import MessageMetadata,reserve_quote
    from amplifier_acp.native.input_provenance import Context
    from amplifier_acp.history import page
    from types import SimpleNamespace
    sid='native';directory=tmp_path/'history';directory.mkdir();source={'role':'assistant','content':'Earlier answer'}
    path=directory/'transcript.jsonl';path.write_text(json.dumps(source)+'\n')
    ref={'sessionId':sid,'messageId':message_id(source,0),'position':0,'historyEpoch':None}
    store=MessageMetadata(tmp_path/'app',sid,actor_id='user');quote=reserve_quote(store,directory,ref,input_id='send',input_payload={'text':'new'})
    async def add(row):
        with path.open('a') as f:f.write(json.dumps(row)+'\n')
    context=Context(SimpleNamespace(add_message=add),store.path.with_name('input-provenance.sqlite3'))
    await context.add_message({'role':'user','content':'New explicit input','metadata':{'amplifier_input':{'id':'send','kind':'user','source':'user'}}})
    updates=page(directory)['updates'];saved=next(u for u in updates if u['sessionUpdate']=='user_message_chunk')
    assert saved['_meta']['amplifier.dev/history']['replyTo']==quote
    assert store.receipt('send')['receipt']['outcome']=='unknown'
    assert page(directory)['updates']==updates # passive restart/read retains quote
    from amplifier_acp.native.automatic_history import display_message
    canonical=json.loads(path.read_text().splitlines()[-1])
    copied=display_message(canonical,1,{'id':'copy','createdAt':0})
    assert copied['replyTo']==quote


@pytest.mark.asyncio
async def test_no_principal_no_advertisement(tmp_path):
    from amplifier_acp.server import Server,RpcError
    from io import StringIO
    server=Server({'home':str(tmp_path/'home')},StringIO())
    result=await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert 'messageInteractions' not in result['agentCapabilities']['_meta']['amplifier.dev/native']
    with pytest.raises(RpcError):await server.dispatch('_amplifier/message/read',{'sessionId':'x','messageIds':['native:x']})

@pytest.mark.asyncio
async def test_public_prompt_quote_admission_and_restart_receipt(tmp_path):
    from amplifier_acp.server import Server,Session,NATIVE
    from amplifier_acp.native.input_provenance import Context
    from amplifier_acp.message_metadata import MessageMetadata,QuotedInputUnknown
    from amplifier_acp.history import page
    from io import StringIO
    from types import SimpleNamespace
    server=Server({'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app')},StringIO(),message_principal=lambda identity:'account')
    server.initialized=True;server.extensions={NATIVE};cwd=tmp_path/'cwd';cwd.mkdir();sid='quoted'
    directory=server.directory(str(cwd),sid);directory.mkdir(parents=True)
    row={'role':'assistant','content':'Earlier answer'};path=directory/'transcript.jsonl';path.write_text(json.dumps(row)+'\n')
    ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
    session=Session(server,sid,str(cwd));server.sessions[sid]=session;sent=[]
    async def add(message):
        with path.open('a') as f:f.write(json.dumps(message)+'\n')
    context=Context(SimpleNamespace(add_message=add),tmp_path/'app'/'sessions'/sid/'input-provenance.sqlite3')
    async def command(operation,**args):
        if operation=='delivery':return {'delivery':'unknown'}
        sent.append(args)
        await context.add_message({'role':'user','content':args['text'],'metadata':{'amplifier_input':{'id':args['input_id'],'kind':'user','source':'user'}}})
        session.prompt.set_result({'stopReason':'end_turn'})
        return {'accepted':True,'inputId':args['input_id']}
    async def start():session.worker=SimpleNamespace(command=command)
    session.start=start;session.schedule_retirement=lambda:None
    request={'sessionId':sid,'prompt':[{'type':'text','text':'New explicit input'}],'_meta':{'amplifier.dev/command':{'commandId':'exact-send'},'amplifier.dev/messageReply':ref}}
    assert await server.dispatch('session/prompt',request)=={'stopReason':'end_turn'}
    quote=sent[0]['reply_context'];assert quote['inputId']=='exact-send'
    saved=next(u for u in page(directory)['updates'] if u['sessionUpdate']=='user_message_chunk')
    assert saved['_meta']['amplifier.dev/history']['replyTo']==quote
    cold=MessageMetadata(tmp_path/'app',sid,actor_id='account')
    receipt=cold.receipt('exact-send')['receipt'];assert receipt['status']=='accepted' and receipt['outcome']=='unobserved'
    with pytest.raises(QuotedInputUnknown) as error:await server.dispatch('session/prompt',request)
    assert error.value.data['outcome']=='already-admitted' and len(sent)==1

@pytest.mark.asyncio
async def test_cold_dispatch_receipt_without_history_or_scope(tmp_path):
    from amplifier_acp.server import Server
    from amplifier_acp.message_metadata import MessageMetadata,reserve_quote
    from io import StringIO
    directory=tmp_path/'history';directory.mkdir();row={'role':'assistant','content':'Original'}
    transcript=directory/'transcript.jsonl';transcript.write_text(json.dumps(row)+'\n');sid='native'
    ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
    store=MessageMetadata(tmp_path/'app',sid,actor_id='account')
    quote=reserve_quote(store,directory,ref,input_id='reserved',input_payload={'text':'new'})
    transcript.unlink()
    server=Server({'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app')},StringIO(),message_principal=lambda identity:'account' if identity==sid else None)
    await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    receipt=await server.dispatch('_amplifier/message/receipt',{'sessionId':sid,'commandId':'reserved'})
    assert receipt['receipt']['quote']==quote and receipt['receipt']['outcome']=='unknown'
    assert not server.sessions and not hasattr(server,'message_scopes')


def test_actual_native_fork_retains_frozen_quote(tmp_path,monkeypatch):
    from amplifier_acp.native.session_store import fork_session
    from amplifier_acp.message_metadata import MessageMetadata,reserve_quote
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    cwd=tmp_path/'cwd';cwd.mkdir();directory=tmp_path/'original';directory.mkdir()
    source_row={'role':'assistant','content':'Earlier answer'}
    (directory/'transcript.jsonl').write_text(json.dumps(source_row)+'\n')
    ref={'sessionId':'source','messageId':message_id(source_row,0),'position':0,'historyEpoch':None}
    store=MessageMetadata(tmp_path/'app','source',actor_id='account')
    quote=reserve_quote(store,directory,ref,input_id='input',input_payload={'text':'new'})
    reply={'role':'user','content':'New input','metadata':{'amplifier_input':{'id':'input','kind':'user','source':'user'},'replyTo':quote}}
    source={'id':'source','status':'idle','workspace':str(cwd),'messages':[]}
    fork=fork_session(tmp_path/'app',source,'target',live_messages=[source_row,reply])
    from amplifier_acp.native.host.storage import SessionStore
    saved=SessionStore.for_app(tmp_path/'app',cwd).load('target')
    assert saved[0][1]['metadata']['replyTo']==quote
    from amplifier_acp.history import page
    copied=page(SessionStore.for_app(tmp_path/'app',cwd).directory('target'))
    assert next(u for u in copied['updates'] if u['sessionUpdate']=='user_message_chunk')['_meta']['amplifier.dev/history']['replyTo']==quote

@pytest.mark.asyncio
async def test_actual_cli_principal_handshake_and_cold_message_methods(tmp_path,monkeypatch):
    import amplifier_acp
    # Launch the exact currently imported package (source or wheel consumer),
    # while retaining the immutable fixture interpreter's dependency paths.
    monkeypatch.setenv('PYTHONPATH',str(Path(amplifier_acp.__file__).resolve().parent.parent))
    native=tmp_path/'native';cwd=tmp_path/'cwd';cwd.mkdir();sid='existing'
    directory=native/'projects'/project_slug(cwd)/'sessions'/sid;directory.mkdir(parents=True)
    row={'role':'assistant','content':'Earlier answer'};path=directory/'transcript.jsonl';path.write_text(json.dumps(row)+'\n')
    before=path.read_bytes();config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(native),'appHome':str(tmp_path/'app'),'workerCommand':['/must-not-start']}))
    meta={'amplifier.dev/native':{'version':1},'amplifier.dev/messagePrincipal':{'version':1,'account':'server-protected-account'}}
    client=await Client.launch(config)
    try:
        init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':meta}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['messageInteractions']['version']==1
        assert 'error' in await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':meta}})
        await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':1})
        ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
        args={'sessionId':sid,'reference':ref,'emoji':'✅','present':True,'commandId':'exact'}
        assert 'error' in await client.request('_amplifier/message/reaction',{**args,'actorId':'forged'})
        result=(await client.request('_amplifier/message/reaction',args))['result']
        assert result['mine']==['✅'] and result['counts']=={'✅':1}
        assert (await client.request('_amplifier/message/reaction',args))['result']==result
        assert (await client.request('_amplifier/message/read',{'sessionId':sid,'messageIds':[ref['messageId']]}))['result']['items'][0]['mine']==['✅']
        assert (await client.request('_amplifier/message/receipt',{'sessionId':sid,'commandId':'exact'}))['result']['receipt']==result
        assert path.read_bytes()==before and len(client.events)==1
        assert client.events[0]['method']=='_amplifier/message/update'
    finally:await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('binding',[{'version':1,'account':'account','actorId':'forged'},{'version':True,'account':'account'},{'version':1,'account':''},{'version':1,'account':'x\ny'}])
async def test_actual_cli_malformed_principal_refuses(tmp_path,monkeypatch,binding):
    import amplifier_acp
    monkeypatch.setenv('PYTHONPATH',str(Path(amplifier_acp.__file__).resolve().parent.parent))
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(tmp_path/'native'),'workerCommand':['/must-not-start']}))
    client=await Client.launch(config)
    try:
        result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/messagePrincipal':binding}}})
        assert result['error']['code']==-32602 and not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_actual_cli_absent_binding_unadvertised(tmp_path,monkeypatch):
    import amplifier_acp
    monkeypatch.setenv('PYTHONPATH',str(Path(amplifier_acp.__file__).resolve().parent.parent))
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(tmp_path/'native'),'workerCommand':['/must-not-start']}))
    client=await Client.launch(config)
    try:
        result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert 'messageInteractions' not in result['result']['agentCapabilities']['_meta']['amplifier.dev/native']
        denied=await client.request('_amplifier/message/read',{'sessionId':'native','messageIds':['native:id']})
        assert denied['error']['code']==-32601 and not client.events
    finally:await client.close()
