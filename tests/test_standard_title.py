"""Standard ACP title projection; canonical metadata remains the only writer."""
import asyncio
import io
import json
from pathlib import Path

import pytest
from amplifier_acp.server import Session
from amplifier_foundation.session.metadata import SessionMetadataStore
from client import Client


def title_updates(rows):
    return [row for row in rows if row.get('method')=='session/update'
            and row['params']['update'].get('sessionUpdate')=='session_info_update']


def validate_update(row):
    import jsonschema
    schema=json.loads((Path(__file__).parent/'schema/acp-v1.json').read_text())
    jsonschema.Draft202012Validator({'$ref':'#/$defs/SessionNotification','$defs':schema['$defs']}).validate(row['params'])


@pytest.fixture
def selected(tmp_path):
    directory=tmp_path/'history';directory.mkdir()
    (directory/'metadata.json').write_text(json.dumps({'session_id':'one','name':'Generated','name_source':'generated','name_revision':1}))
    (directory/'transcript.jsonl').write_text('{"role":"user","content":"Original"}\n')
    from amplifier_acp.server import Server
    output=io.StringIO();server=Server({'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app')},output)
    server.directory=lambda *_:directory
    session=Session(server,'one',str(tmp_path));server.extensions=set()
    return session,directory,output


@pytest.mark.asyncio
async def test_late_event_reads_newer_manual_name_without_writing(selected):
    session,directory,output=selected
    session.prompt=asyncio.get_running_loop().create_future();session.prompt.set_result({'stopReason':'end_turn'})
    SessionMetadataStore(directory).set_name('Newer manual',source='manual')
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    await session.observe({'type':'session.naming','name':'Stale generated','nameRevision':1})
    rows=[json.loads(line) for line in output.getvalue().splitlines()]
    assert len(title_updates(rows))==1
    assert rows[0]['params']=={'sessionId':'one','update':{'sessionUpdate':'session_info_update','title':'Newer manual'}}
    validate_update(rows[0])
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before


@pytest.mark.asyncio
@pytest.mark.parametrize('event',[
    {'type':'session.naming.progress','completedInputs':['input']},
    {'type':'session.naming','name':'Ignored','nameRevision':1,'sessionId':'child'},
    {'type':'session.naming','name':'Ignored'},
    {'type':'session.naming','name':'Ignored','nameRevision':True},
    {'type':'session.naming','name':'Ignored','nameRevision':-1},
    {'type':'session.naming','name':None,'nameRevision':1},
    {'type':'session.naming','name':'','nameRevision':1},
    {'type':'session.naming','name':'x'*201,'nameRevision':1},
])
async def test_unrelated_or_malformed_event_does_not_publish_title(selected,event):
    session,_,output=selected
    await session.observe(event)
    assert not title_updates([json.loads(line) for line in output.getvalue().splitlines()])


@pytest.mark.asyncio
async def test_post_response_publication_failure_does_not_send_second_response(selected,monkeypatch):
    session,_,output=selected;server=session.server;server.sessions['one']=session
    async def opened(*_):return {'sessionId':'one'}
    async def unavailable():raise OSError('owned output failure')
    monkeypatch.setattr(server,'dispatch',opened)
    monkeypatch.setattr(session,'publish_title',unavailable)
    await server.handle({'jsonrpc':'2.0','id':1,'method':'session/new','params':{}})
    assert [json.loads(line) for line in output.getvalue().splitlines()]==[
        {'jsonrpc':'2.0','id':1,'result':{'sessionId':'one'}}]


class TraceClient(Client):
    def __init__(self,process):
        self.wire=[]
        super().__init__(process)
    async def read(self):
        while line:=await self.process.stdout.readline():
            row=json.loads(line);self.wire.append(row)
            if row.get('method'):
                self.events.append(row)
                assert 'id' not in row, 'No peer effects are expected in the inert title fixture'
            elif row.get('id') in self.pending:self.pending[row['id']].set_result(row)


async def eventually(predicate):
    async with asyncio.timeout(15):
        while not predicate():await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_actual_late_automatic_title_and_loaded_title_without_extensions(tmp_path):
    import shutil,yaml
    from native_setup import configuration
    config,cwd=configuration(tmp_path,runtimeImmutable=True,idleSeconds=60)
    declaration=Path(__file__).parent/'fixtures/provider';provider=tmp_path/'provider'
    shutil.copytree(declaration,provider)
    implementation=provider/'amplifier_module_provider_fixture/__init__.py'
    body=implementation.read_text();needle='    async def complete(self,request,**kwargs):\n'
    body=body.replace(needle,needle+'''        if self.config.get('standardTitleFixture'):
            import json
            audit=Path(self.config['titleAudit'])
            count=len(audit.read_text().splitlines()) if audit.exists() else 0
            with audit.open('a') as output:output.write(('user' if count==0 else 'automatic-name')+'\\n')
            if count==0:
                return ChatResponse(content=[TextBlock(text='Owned user completion')],finish_reason='stop',usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
            Path(self.config['titleEntered']).touch()
            while not Path(self.config['titleRelease']).exists():await asyncio.sleep(.01)
            return ChatResponse(content=[TextBlock(text=json.dumps({'action':'set','name':'Offline automatic title','description':'Owned title fixture'}))],finish_reason='stop',usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
''',1);implementation.write_text(body)
    settings=json.loads(config.read_text());bundle=Path(settings['bundle']);doc=yaml.safe_load(bundle.read_text())
    audit=tmp_path/'calls';entered=tmp_path/'entered';release=tmp_path/'release'
    doc['providers'][0].update(source=str(provider),config={'standardTitleFixture':True,'titleAudit':str(audit),'titleEntered':str(entered),'titleRelease':str(release)})
    bundle.write_text(yaml.safe_dump(doc))
    client=await TraceClient.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}})
        created=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]})
        assert 'result' in created,created
        sid=created['result']['sessionId']
        result=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'One explicit inert turn'}]})
        assert result['result']['stopReason']=='end_turn',result
        await eventually(entered.exists)
        terminal_index=client.wire.index(result)
        release.touch()
        await eventually(lambda:any(r['params']['update']['title']=='Offline automatic title' for r in title_updates(client.wire)))
        title=next(r for r in title_updates(client.wire) if r['params']['update']['title']=='Offline automatic title')
        assert client.wire.index(title)>terminal_index
        assert not any(r.get('method')=='_amplifier/event' for r in client.wire)
        validate_update(title)
        assert audit.read_text().splitlines()==['user','automatic-name']
    finally:
        release.touch();await client.close()
    from amplifier_acp.native.session_files import project_slug
    directory=Path(settings['home'])/'projects'/project_slug(str(cwd))/'sessions'/sid
    SessionMetadataStore(directory).set_name('Manual retained title',source='manual')
    before={name:(directory/name).read_bytes() for name in ['transcript.jsonl','metadata.json']}
    for method in ['session/load','session/resume']:
        client=await TraceClient.launch(config)
        try:
            await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}})
            loaded=await client.request(method,{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
            assert 'result' in loaded,loaded
            await eventually(lambda:bool(title_updates(client.wire)))
            titles=title_updates(client.wire)
            assert len(titles)==1 and titles[0]['params']['update']['title']=='Manual retained title'
            assert client.wire.index(titles[0])>client.wire.index(loaded)
            validate_update(titles[0])
        finally:await client.close()
        assert audit.read_text().splitlines()==['user','automatic-name']
        assert {name:(directory/name).read_bytes() for name in before}==before
