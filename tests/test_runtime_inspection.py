import json,sys
from pathlib import Path
import pytest
from amplifier_acp.runtime_inspection import current,page,public_distribution
from amplifier_acp.generations import Generations
from client import Client
from native_setup import configuration
from test_generations import receipt


def test_generation_receipts_are_bounded_current_policy_is_separate_and_credentials_redacted(tmp_path,monkeypatch):
    from amplifier_acp.native import runtime_environment as env
    generation='a'*32;project,directory=receipt(tmp_path,generation)
    manifest=tmp_path/'manifest.toml';manifest.write_text('[project]\nname="policy"\nversion="1"\n')
    monkeypatch.setenv('AMPLIFIER_ACP_RUNTIME_MANIFEST',str(manifest));(directory/'runtime-base.toml').write_text('older policy')
    (tmp_path/'updates/active.json').write_text(json.dumps({'current':generation}))
    graph=[{'name':f'package-{i:04}','version':'1','directUrl':{'url':'https://user:secret@example.invalid/repo?token=private','vcs_info':{'vcs':'git','commit_id':'abc','requested_revision':'main'}}} for i in range(5000)]
    (directory/'runtime-installed.json').write_text(json.dumps(graph))
    monkeypatch.setattr(env,'installed_sources',lambda *_:pytest.fail('passive receipt read scanned live install'))
    first=current(tmp_path,{'limit':2},{'checkedGeneration':generation,'lastCheck':123})
    # A check-state change is part of the revision and requires a new page.
    assert first['policy']['matchesRecordedBaseline'] is False and first['verification']['installedGraph']=='not_checked'
    assert first['items'][0]['source']['url']=='https://example.invalid/repo' and 'secret' not in json.dumps(first)
    assert len(first['items'])==2 and first['total']==5000 and first['workerStarted'] is False
    with pytest.raises(ValueError,match='snapshot'):current(tmp_path,{'cursor':first['nextCursor'],'limit':2})
    same=current(tmp_path,{'cursor':first['nextCursor'],'limit':2},{'checkedGeneration':generation,'lastCheck':123});assert same['items'][0]['name']=='package-0002'
    manifest.write_text('changed')
    with pytest.raises(ValueError,match='revision'):current(tmp_path,{'expectedRevision':first['revision']})

@pytest.mark.asyncio
async def test_generation_current_no_runtime_or_account_required(tmp_path):
    config,cwd=configuration(tmp_path,adminWorkspaceRoots=[str(tmp_path)],adminGenerations=True,workerCommand=['/impossible-worker'])
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        reply=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'generations.current','args':{}})
        assert reply['result']['available'] is False and not (tmp_path/'home/projects').exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_actual_resident_runtime_inspection_pages_origins_and_refuses_cold_start(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config)
    async def init(c):return await c.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    try:
        initialized=await init(client);assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['runtimeInspection']['residentOnly']
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def inspect(args):return await client.request('_amplifier/native',{'sessionId':sid,'operation':'runtime.inspect','args':args})
        first=(await inspect({'limit':2,'refresh':True}))['result'];assert first['resident'] and first['python']['version'];assert len(first['items'])==2
        mounted=(await inspect({'surface':'mounted','expectedRevision':first['revision']}))['result']
        assert any(any(part['module']=='amplifier_module_provider_fixture' for part in row['implementations']) for row in mounted['items'])
        wrong_surface=await inspect({'surface':'mounted','cursor':first['nextCursor']});assert 'error' in wrong_surface
        assert first['coverage']['loadedCodeBytesVerified'] is False
        second=(await inspect({'limit':2,'cursor':first['nextCursor'],'expectedRevision':first['revision']}))['result'];assert first['items']!=second['items'] and first['instanceId']==second['instanceId']
        wrong=await inspect({'expectedRevision':'wrong'});assert 'error' in wrong
        # Retirement must not be undone by a report read.
        retired=await client.request('_amplifier/retire',{'sessionId':sid});assert retired['result']['retired'],retired
        assert (await inspect({}))['error']['code']==-32008
        await client.close()
        settings=json.loads(config.read_text());settings['workerCommand']=['/impossible-worker'];config.write_text(json.dumps(settings))
        client=await Client.launch(config);await init(client)
        cold=(await client.request('_amplifier/runtime',{'sessionId':sid,'cwd':str(cwd)}))['result'];assert cold['available'] is False and cold['workerStarted'] is False
        assert cold['coverage']=='this-acp-connection'
    finally:await client.close()
