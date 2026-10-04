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
        async def operation(op,args):return await peer.request('_amplifier/naming',{'sessionId':'named','cwd':str(cwd),'operation':op,'args':args})
        read=await operation('read',{});assert read['result']['title']=='Before'
        changed=await operation('rename',{'commandId':'cold','title':'Saved cold'});assert changed['result']['applied']
        repeated=await operation('rename',{'commandId':'cold','title':'Saved cold'});assert repeated['result']==changed['result']
        receipt=await operation('receipt',{'commandId':'cold'});assert receipt['result']['receipt']['result']==changed['result']
        assert not peer.events and transcript.read_bytes()==before
    finally:await peer.close()
