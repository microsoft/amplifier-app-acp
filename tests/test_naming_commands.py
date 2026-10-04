import json
from types import SimpleNamespace
import pytest
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_acp.naming_commands import Commands,NamingUnknown,handle,mutate,projection

@pytest.fixture
def owner(tmp_path):
    directory=tmp_path/'history';directory.mkdir()
    (directory/'metadata.json').write_text(json.dumps({'session_id':'named','name':'Before','name_source':'generated','name_revision':2,'name_policy_revision':3,'name_auto':True,'name_auto_revision':2}))
    (directory/'transcript.jsonl').write_text('{"role":"user","content":"Keep history"}\n')
    server=SimpleNamespace(config={'appHome':str(tmp_path/'app'),'home':str(tmp_path/'native')},directory=lambda cwd,identity:directory)
    return server,directory,tmp_path

async def call(owner,operation,args):
    server,directory,cwd=owner
    return await handle(server,{'sessionId':'named','cwd':str(cwd),'operation':operation,'args':args})

async def test_passive_rename_policy_and_exact_retry_preserve_history(owner):
    _,directory,_=owner;before=(directory/'transcript.jsonl').read_bytes()
    first=await call(owner,'rename',{'commandId':'manual','title':'My choice'})
    assert first['applied'] and first['naming']=={'automatic':False,'nameRevision':3,'policyRevision':4}
    retry=await call(owner,'rename',{'commandId':'manual','title':'My choice'})
    assert retry==first
    with pytest.raises(ValueError,match='conflicts'):
        await call(owner,'rename',{'commandId':'manual','title':'Changed request'})
    policy=await call(owner,'policy',{'commandId':'auto','automatic':True,'expectedNameRevision':3,'expectedPolicyRevision':4})
    assert policy['naming']=={'automatic':True,'nameRevision':3,'policyRevision':5}
    assert (directory/'transcript.jsonl').read_bytes()==before

async def test_late_generated_candidate_and_stale_policy_cannot_replace_manual(owner):
    candidate={'name':'Late name','name_revision':2,'name_policy_revision':3}
    await call(owner,'rename',{'commandId':'manual','title':'Keep this'})
    late=await call(owner,'commit',{'commandId':'late','candidate':candidate})
    assert not late['applied'] and late['title']=='Keep this'
    stale=await call(owner,'policy',{'commandId':'stale','automatic':True,'expectedNameRevision':2,'expectedPolicyRevision':3})
    assert not stale['applied'] and not stale['naming']['automatic']
    assert stale['reason']=='naming-revision-conflict'

async def test_committed_candidate_and_original_receipt_survive_newer_edit(owner):
    accepted=await call(owner,'commit',{'commandId':'generated','candidate':{'name':'Generated','name_revision':2,'name_policy_revision':3}})
    assert accepted['applied'] and accepted['naming']['nameRevision']==3
    await call(owner,'rename',{'commandId':'manual','title':'Newer'})
    receipt=await call(owner,'receipt',{'commandId':'generated'})
    assert receipt['receipt']['result']==accepted
    assert (await call(owner,'read',{}))['title']=='Newer'

async def test_interrupted_generation_is_unknown_and_never_called_twice(owner):
    server,directory,_=owner;commands=Commands(server.config['appHome'],'named',directory);calls=[]
    async def lost():
        calls.append('one')
        raise RuntimeError('lost model reply')
    with pytest.raises(RuntimeError):await commands.perform('generate',{'commandId':'original'},lost)
    with pytest.raises(NamingUnknown):await commands.perform('generate',{'commandId':'original'},lost)
    assert calls==['one'] and commands.receipt('original')['status']=='unknown'

async def test_interrupted_after_metadata_write_preserves_unknown_without_replay(owner):
    server,directory,_=owner;commands=Commands(server.config['appHome'],'named',directory)
    async def written():return mutate(directory,'rename',{'title':'Written once'})
    original_finish=commands.finish
    commands.finish=lambda *args:(_ for _ in ()).throw(OSError('lost receipt write'))
    with pytest.raises(OSError):await commands.perform('rename',{'commandId':'lost','title':'Written once'},written)
    commands.finish=original_finish
    with pytest.raises(NamingUnknown):await commands.perform('rename',{'commandId':'lost','title':'Written once'},written)
    assert projection(SessionMetadataStore(directory).read())['naming']['nameRevision']==3

async def test_policy_does_not_invoke_generation_and_read_creates_no_receipt(owner):
    server,directory,_=owner
    assert (await call(owner,'read',{}))['title']=='Before'
    assert not (owner[2]/'app').exists()
    result=await call(owner,'policy',{'commandId':'off','automatic':False,'expectedNameRevision':2,'expectedPolicyRevision':3})
    assert not result['naming']['automatic'] and result['title']=='Before'

async def test_actual_cold_peer_metadata_operations_do_not_start_worker(tmp_path):
    from client import Client
    from native_setup import configuration
    from amplifier_acp.native.session_files import project_slug
    config,cwd=configuration(tmp_path,workerCommand=['/must/not/start'])
    settings=json.loads(config.read_text());directory=__import__('pathlib').Path(settings['home'])/'projects'/project_slug(str(cwd))/'sessions'/'named'
    directory.mkdir(parents=True)
    (directory/'metadata.json').write_text(json.dumps({'session_id':'named','name':'Before','name_source':'fallback','name_revision':0}))
    transcript=directory/'transcript.jsonl';transcript.write_text('{"role":"user","content":"Keep history"}\n');before=transcript.read_bytes()
    peer=await Client.launch(config)
    try:
        initialized=await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['naming']['passiveMetadata']
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['lifecycle']['retirementStatus']['method']=='_amplifier/retire/status'
        async def operation(op,args):return await peer.request('_amplifier/naming',{'sessionId':'named','cwd':str(cwd),'operation':op,'args':args})
        read=await operation('read',{});assert read['result']['title']=='Before'
        changed=await operation('rename',{'commandId':'cold','title':'Saved cold'});assert changed['result']['applied']
        repeated=await operation('rename',{'commandId':'cold','title':'Saved cold'});assert repeated['result']==changed['result']
        receipt=await operation('receipt',{'commandId':'cold'});assert receipt['result']['receipt']['result']==changed['result']
        assert not peer.events and transcript.read_bytes()==before
    finally:await peer.close()

async def test_actual_explicit_generation_once_and_manual_race_through_native_dispatch(tmp_path):
    import asyncio,io,shutil
    from pathlib import Path
    from native_setup import configuration
    from amplifier_acp.server import Server
    config,cwd=configuration(tmp_path,idleSeconds=60)
    declaration=Path(__file__).parent/'fixtures/provider';provider=tmp_path/'provider'
    shutil.copytree(declaration,provider)
    implementation=provider/'amplifier_module_provider_fixture/__init__.py'
    body=implementation.read_text();needle='    async def complete(self,request,**kwargs):\n'
    body=body.replace(needle,needle+'''        if self.config.get('namingFixture'):
            import json
            with Path(self.config['namingAudit']).open('a') as output:output.write('one explicit offline completion\\n')
            if Path(self.config['namingArmed']).exists():
                Path(self.config['namingEntered']).touch()
                while not Path(self.config['namingRelease']).exists():await asyncio.sleep(.01)
            return ChatResponse(content=[TextBlock(text=json.dumps({'action':'set','name':'Offline generated name','description':'Owned explicit naming fixture'}))],finish_reason='stop',usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
''',1);implementation.write_text(body)
    settings=json.loads(config.read_text());bundle=Path(settings['bundle'])
    content=bundle.read_text().replace(str(declaration.resolve()),str(provider.resolve()))
    audit=tmp_path/'completions';armed=tmp_path/'armed';entered=tmp_path/'entered';release=tmp_path/'release'
    content=content.replace('    config: {}','    config: '+json.dumps({'namingFixture':True,'namingAudit':str(audit),'namingArmed':str(armed),'namingEntered':str(entered),'namingRelease':str(release)}))
    bundle.write_text(content)
    server=Server(settings,io.StringIO())
    await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    sid=(await server.dispatch('session/new',{'cwd':str(cwd),'mcpServers':[]}))['sessionId'];session=server.sessions[sid]
    async def metadata(op,args):return await server.dispatch('_amplifier/naming',{'sessionId':sid,'cwd':str(cwd),'operation':op,'args':args})
    async def generate(command):return await server.dispatch('_amplifier/native',{'sessionId':sid,'operation':'session.naming.generate','args':{'commandId':command}})
    pending=None
    try:
        await metadata('rename',{'commandId':'bootstrap-name','title':'Manual initial name'})
        await server.dispatch('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'One owned conversation turn'}]})
        assert len(audit.read_text().splitlines())==1
        transcript=server.directory(str(cwd),sid)/'transcript.jsonl';before=transcript.read_bytes()
        armed.touch();pending=asyncio.create_task(generate('explicit-race'))
        for _ in range(200):
            if entered.exists():break
            await asyncio.sleep(.01)
        assert entered.exists()
        refusal=await server.dispatch('_amplifier/retire',{'sessionId':sid})
        assert refusal=={'retired':False,'reason':'owned_work'}
        manual=await metadata('rename',{'commandId':'newer-manual','title':'Keep newer manual name'})
        assert manual['applied'] and not manual['naming']['automatic']
        release.touch();candidate=await pending
        assert candidate['name']=='Offline generated name' and len(audit.read_text().splitlines())==2
        repeated=await generate('explicit-race');assert repeated==candidate and len(audit.read_text().splitlines())==2
        stale=await metadata('commit',{'commandId':'stale-commit','candidate':candidate})
        assert not stale['applied'] and stale['title']=='Keep newer manual name'
        fresh=await generate('explicit-fresh');assert len(audit.read_text().splitlines())==3
        committed=await metadata('commit',{'commandId':'fresh-commit','candidate':fresh})
        assert committed['applied'] and committed['title']=='Offline generated name'
        assert not committed['naming']['automatic']
        assert await generate('explicit-fresh')==fresh and len(audit.read_text().splitlines())==3
        assert transcript.read_bytes()==before
    finally:
        release.touch()
        if pending:await asyncio.gather(pending,return_exceptions=True)
        await session.close()
