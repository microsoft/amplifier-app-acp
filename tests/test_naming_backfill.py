import asyncio
import json
from types import SimpleNamespace
import pytest
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_acp.naming_commands import handle
from amplifier_acp.native.setup import SetupManager


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    directory=tmp_path/'history';directory.mkdir()
    metadata={'session_id':'12345678-test','name':'New chat','name_source':'fallback','name_revision':0,'name_policy_revision':0}
    (directory/'metadata.json').write_text(json.dumps(metadata))
    (directory/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'<system-reminder>Private machinery</system-reminder>Plan my garden'})+'\n')
    server=SimpleNamespace(config={'appHome':str(tmp_path/'app'),'home':str(tmp_path/'native')},sessions={},directory=lambda cwd,sid:directory)
    monkeypatch.setattr(SetupManager,'config',lambda *args:SimpleNamespace(settings={},providers=[{'module':'provider-fixture','config':{}}]))
    monkeypatch.setattr(SetupManager,'provider_rows',lambda *args:[{'id':'fixture','credentialsConfigured':True}])
    calls=[]
    async def probe(self,action,args,cwd):
        calls.append((action,args))
        return {'text':'{"action":"set","name":"Garden Plans","description":"A practical garden"}'}
    monkeypatch.setattr(SetupManager,'probe',probe)
    async def run(operation='backfill',args=None):return await handle(server,{'sessionId':'12345678-test','cwd':str(tmp_path),'operation':operation,'args':args or {}})
    return directory,server,calls,run


async def test_cold_backfill_names_once_without_history_replay(fixture):
    directory,server,calls,run=fixture;before=(directory/'transcript.jsonl').read_bytes()
    result=await run();assert result['applied'] and result['title']=='Garden Plans'
    assert len(calls)==1 and calls[0][0]=='naming.complete'
    assert 'Plan my garden' in calls[0][1]['prompt'] and 'Private machinery' not in calls[0][1]['prompt']
    assert (await run())['status']=='ineligible'
    assert (directory/'transcript.jsonl').read_bytes()==before and not server.sessions


@pytest.mark.parametrize('change',['manual','off','history','delete'])
async def test_late_name_cannot_replace_newer_user_choice(fixture,monkeypatch,change):
    directory,server,calls,run=fixture
    async def probe(*args):
        calls.append('once')
        if change=='manual':await run('rename',{'commandId':'human','title':'My chosen name'})
        elif change=='off':await run('policy',{'commandId':'off','automatic':False,'expectedNameRevision':0,'expectedPolicyRevision':0})
        elif change=='history':
            with (directory/'transcript.jsonl').open('a') as output:output.write('{"role":"user","content":"New subject"}\n')
        else:
            from amplifier_acp.session_lifecycle import lifecycle_state
            from amplifier_acp.configuration_clone import location
            path=location(server.config['appHome'],'12345678-test')/'lifecycle.json';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'sessionId':'12345678-test','historyCwd':str(directory.parent),'deleted':True}))
        return {'text':'{"action":"set","name":"Late result"}'}
    monkeypatch.setattr(SetupManager,'probe',probe)
    result=await run();assert not result['applied']
    assert SessionMetadataStore(directory).read()['name']==('My chosen name' if change=='manual' else 'New chat')
    assert len(calls)==1


async def test_unknown_provider_outcome_is_not_retried_after_reopen(fixture,monkeypatch):
    directory,server,calls,run=fixture
    async def lost(*args):calls.append('once');raise RuntimeError('lost response')
    monkeypatch.setattr(SetupManager,'probe',lost)
    with pytest.raises(RuntimeError,match='lost response'):await run()
    result=await run();assert result['status']=='unknown' and result['replayed'] is False
    assert len(calls)==1 and SessionMetadataStore(directory).read()['name']=='New chat'


async def test_missing_provider_does_not_consume_an_attempt(fixture,monkeypatch):
    directory,server,calls,run=fixture
    monkeypatch.setattr(SetupManager,'provider_rows',lambda *args:[{'id':'fixture','credentialsConfigured':False}])
    assert (await run())['status']=='provider-unavailable' and not calls
    monkeypatch.setattr(SetupManager,'provider_rows',lambda *args:[{'id':'fixture','credentialsConfigured':True}])
    assert (await run())['applied'] and len(calls)==1


@pytest.mark.parametrize('legacy', ['name', 'off', 'view-off'])
async def test_legacy_names_and_opt_out_are_read_only_and_preserved(fixture, legacy):
    directory,server,calls,run=fixture
    metadata=directory/'metadata.json'
    metadata.write_text(json.dumps({'session_id':'12345678-test'}))
    if legacy=='view-off':
        (directory/'unified').mkdir()
        (directory/'unified'/'view.json').write_text('{"title":"New chat","autoName":false}')
    else:
        value={'name':'Chosen before migration','name_source':'manual'} if legacy=='name' else {'name_auto':False}
        (directory/'naming.json').write_text(json.dumps(value))
    before={str(p):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert (await run())['status']=='ineligible' and not calls
    assert before=={str(p):p.read_bytes() for p in directory.rglob('*') if p.is_file()}


async def test_local_provider_without_declared_key_can_name(fixture,monkeypatch):
    _,_,calls,run=fixture
    monkeypatch.setattr(SetupManager,'provider_rows',lambda *args:[{'id':'fixture','credentialsConfigured':False,'credential':{'supported':False}}])
    assert (await run())['applied'] and len(calls)==1


async def test_default_provider_is_never_replaced_by_connected_alternative(fixture,monkeypatch):
    _,_,calls,run=fixture
    monkeypatch.setattr(SetupManager,'config',lambda *args:SimpleNamespace(settings={},providers=[{'module':'provider-missing','config':{'priority':1}},{'module':'provider-fixture','config':{'priority':2}}]))
    monkeypatch.setattr(SetupManager,'provider_rows',lambda *args:[{'id':'missing','credentialsConfigured':False},{'id':'fixture','credentialsConfigured':True}])
    assert (await run())['status']=='provider-unavailable' and not calls


async def test_concurrent_request_reuses_the_durable_attempt(fixture,monkeypatch):
    directory,server,calls,run=fixture;entered=asyncio.Event();release=asyncio.Event()
    async def slow(*args):
        calls.append('once');entered.set();await release.wait()
        return {'text':'{"action":"set","name":"Only once"}'}
    monkeypatch.setattr(SetupManager,'probe',slow)
    task=asyncio.create_task(run());await entered.wait()
    try:assert (await run())['status']=='unknown'
    finally:release.set()
    assert (await task)['applied'] and len(calls)==1


async def test_children_and_busy_chats_are_skipped_before_model_request(fixture):
    directory,server,calls,run=fixture
    lock=asyncio.Lock();await lock.acquire();server.sessions['12345678-test']=SimpleNamespace(lock=lock)
    assert (await run())['status']=='ineligible';server.sessions.clear()
    path=directory/'metadata.json';value=json.loads(path.read_text());value['parent_id']='parent';path.write_text(json.dumps(value))
    assert (await run())['status']=='ineligible' and not calls


async def test_installed_cold_peer_uses_provider_probe_without_starting_conversation(tmp_path):
    from pathlib import Path
    import yaml
    from client import Client
    from native_setup import configuration
    from amplifier_acp.native.session_files import project_slug
    config,cwd=configuration(tmp_path,workerCommand=['/must/not/start'])
    settings=json.loads(config.read_text());app=Path(settings['appHome']);app.mkdir()
    audit=tmp_path/'naming-calls.jsonl'
    (Path(settings['home'])/'settings.yaml').write_text(yaml.safe_dump({'bundle':{'app':[]},'config':{'providers':[{'module':'provider-fixture','config':{'api_key':'offline-test-value','default_model':'fixture-model','namingResponse':'{"action":"set","name":"Cold Garden Plans"}','namingAudit':str(audit)}}]}}))
    directory=Path(settings['home'])/'projects'/project_slug(str(cwd))/'sessions'/'12345678-test';directory.mkdir(parents=True)
    (directory/'metadata.json').write_text(json.dumps({'session_id':'12345678-test','name_source':'fallback'}))
    transcript=directory/'transcript.jsonl';transcript.write_text('{"role":"user","content":"Plan my garden"}\n');before=transcript.read_bytes()
    async def invoke():
        peer=await Client.launch(config)
        try:
            init=await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
            assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['naming']['backfill']['cold']
            result=await peer.request('_amplifier/naming',{'sessionId':'12345678-test','cwd':str(cwd),'operation':'backfill','args':{}})
            assert 'result' in result,result
            assert not peer.requests and not any(row.get('method')=='session/update' for row in peer.events)
            return result['result']
        finally:await peer.close()
    first=await invoke();assert first.get('title')=='Cold Garden Plans',first
    assert (await invoke())['status']=='ineligible'
    calls=[json.loads(row) for row in audit.read_text().splitlines()];assert len(calls)==1
    assert not calls[0]['tools'] and calls[0]['max_output_tokens']==256 and calls[0]['extended_thinking'] is False
    assert len(calls[0]['messages'])==1 and 'Plan my garden' in calls[0]['messages'][0]['content']
    assert transcript.read_bytes()==before

