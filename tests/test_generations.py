import hashlib,json,os,sys
from pathlib import Path
import pytest
from amplifier_acp.generations import Generations,launcher
from amplifier_acp.native import runtime_environment as environments,runtime_qualification as qualification
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_diagnostic_attempts_survive_restart_and_replay_does_not_add_progress(tmp_path,monkeypatch):
    from amplifier_acp.native.update_diagnostics import CommandFailure
    owner=Generations(tmp_path,{})
    async def fail():
        raise CommandFailure({'exitCode':1,'stderrBytes':20,'probe':{'ok':False,'repositoryAccessFailure':True}})
    async def check():return await owner.diagnostics.run('source-fetch',fail)
    monkeypatch.setattr(owner,'check',check)
    args={'commandId':'first-check'}
    failed=await owner.perform(str(tmp_path),'generations.check',args)
    assert failed['state']=='failed'
    before=owner.inspect({})['diagnostics']
    assert before['attemptId']=='first-check'
    assert before['lastFailure']['probe']['repositoryAccessFailure'] is True
    assert all(row['attemptId']=='first-check' for row in before['events'])
    fetch=[row for row in before['events'] if row['phase']=='source-fetch']
    assert len(fetch)==2 and fetch[0]['commandId']==fetch[1]['commandId']
    assert len({row['id'] for row in before['events']})==4
    restarted=Generations(tmp_path,{})
    assert await restarted.perform(str(tmp_path),'generations.check',args)==failed
    assert restarted.inspect({})['diagnostics']==before
    async def succeed():return {'checkId':'second'}
    monkeypatch.setattr(restarted,'check',succeed)
    await restarted.perform(str(tmp_path),'generations.check',{'commandId':'second-check'})
    after=restarted.inspect({})['diagnostics']
    assert after['attemptId']=='second-check'
    assert after['lastFailure']==before['lastFailure']
    assert [row['status'] for row in after['events'] if row['attemptId']=='second-check']==['started','succeeded']

@pytest.mark.asyncio
async def test_generation_progress_is_advisory_coalesced_and_never_required_for_completion(tmp_path):
    import asyncio
    changes=[]
    def changed():
        changes.append(owner.inspect({}))
        raise RuntimeError('Observer disconnected')
    owner=Generations(tmp_path,{},changed)
    owner.diagnostics.state={'attemptId':'progress'}
    await owner.publish(attemptId='progress',phase='staging')
    owner.diagnostics.record('ecosystem-prepare','started',commandId='phase')
    await asyncio.sleep(0)
    assert len(changes)==1 and changes[0]['phase']=='staging'
    assert changes[0]['diagnostics']['events'][0]['commandId']=='phase'
    await owner.publish(phase='installed')
    await asyncio.sleep(0)
    assert len(changes)==2 and changes[-1]['phase']=='installed'

@pytest.mark.asyncio
async def test_admin_generation_passive_authority_and_no_worker(tmp_path):
    config,cwd=configuration(tmp_path,adminWorkspaceRoots=[str(tmp_path)],adminGenerations=True,workerCommand=['/missing/no-worker'])
    client=await Client.launch(config)
    try:
        init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['generations']['version']==1
        result=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'generations.inspect','args':{}})
        assert result['result']['scope']=='native-runtime' and result['result']['items']==[]
        assert not (tmp_path/'home/projects').exists()
        denied=await client.request('_amplifier/admin',{'cwd':'/','operation':'generations.check','args':{'commandId':'no'}})
        assert 'error' in denied and not (tmp_path/'app/updates/active.json').exists()
    finally:await client.close()

def receipt(tmp_path,generation):
    directory=environments.receipt_directory(tmp_path,generation);directory.mkdir(parents=True)
    project=tmp_path/'runtime'/('q-'+generation);(project/'.venv/bin').mkdir(parents=True)
    (project/'.venv/bin/python').symlink_to(sys.executable)
    lock=b'version = 1\n';(project/'uv.lock').write_bytes(lock);(project/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n')
    for name,body in {'runtime-project.json':{'project':project.name},'validated.json':{'generationSchema':1},'profiles-qualified.json':{'profiles':['fixture']},'runtime-installed.json':[],'runtime-install-policy.json':{'version':2,'lockSha256':hashlib.sha256(lock).hexdigest(),'overridesSha256':hashlib.sha256(b'\n').hexdigest()}}.items():(directory/name).write_text(json.dumps(body))
    (directory/'runtime.lock').write_bytes(lock);(directory/'runtime-install-overrides.txt').write_text('\n')
    return project,directory

@pytest.mark.asyncio
async def test_generation_promote_exact_receipt_unknown_no_replay_and_graph_tamper(tmp_path):
    owner=Generations(tmp_path,{});generation='a'*32;project,directory=receipt(tmp_path,generation)
    args={'commandId':'promote1','generation':generation,'expectedCurrent':None}
    result=await owner.perform(str(tmp_path),'generations.promote',args)
    assert result['state']=='succeeded' and result['result']['runningWorkersChanged'] is False
    exact=await owner.perform(str(tmp_path),'generations.promote',args);assert exact==result
    with pytest.raises(ValueError,match='conflicts'):await owner.perform(str(tmp_path),'generations.promote',{**args,'generation':'b'*32})
    bad=await owner.perform(str(tmp_path),'generations.promote',{**args,'commandId':'stale','expectedCurrent':None});assert bad['state']=='failed'
    from amplifier_acp.generations import digest
    unknownargs={'commandId':'unknown','generation':generation,'expectedCurrent':generation}
    owner.save({'commandId':'unknown','operation':'generations.promote','state':'running'},digest({'operation':'generations.promote','args':unknownargs,'workspace':str(tmp_path)}))
    previous=(tmp_path/'updates/active.json').read_bytes()
    assert (await owner.perform(str(tmp_path),'generations.promote',unknownargs))['state']=='unknown'
    assert (tmp_path/'updates/active.json').read_bytes()==previous
    (project/'uv.lock').write_text('changed')
    with pytest.raises(ValueError,match='lock'):await launcher(tmp_path,generation)

@pytest.mark.asyncio
async def test_generation_inventory_is_paged_and_read_does_not_scan(tmp_path,monkeypatch):
    owner=Generations(tmp_path,{})
    with owner.db() as db:
        db.executemany('INSERT INTO inventory VALUES (?,?)',[(f'{i:05}',json.dumps({'id':f'{i:05}','label':'component','kind':'runtime dependency'})) for i in range(5000)])
    monkeypatch.setattr(environments,'update_inventory',lambda *_:pytest.fail('passive read scanned runtime'))
    one=owner.inspect({'limit':10});two=owner.inspect({'limit':10,'cursor':one['nextCursor']})
    assert len(one['items'])==10 and two['items'][0]['id']=='00010'

@pytest.mark.asyncio
async def test_unused_broken_workspace_never_blocks_global_check_but_explicit_setup_refuses(tmp_path,monkeypatch):
    from yaml import YAMLError
    from amplifier_acp.native.runtime_profiles import ensure
    from amplifier_acp.native import update_sources
    home=tmp_path/'app';home.mkdir()
    workspace=tmp_path/'old-project';(workspace/'.amplifier').mkdir(parents=True)
    bad=workspace/'.amplifier/settings.yaml';bad.write_text('providers: [broken yaml')
    generation='c'*32
    owner=Generations(home,{})
    folder=environments.receipt_directory(home,generation);folder.mkdir(parents=True)
    (folder/'profiles-qualified.json').write_text('{}')
    (home/'updates/active.json').write_text(json.dumps({'current':generation}))
    async def inventory(*_):return []
    async def bindings(*_):return []
    monkeypatch.setattr(environments,'update_inventory',inventory)
    monkeypatch.setattr(update_sources,'bindings',bindings)
    result=await owner.check()
    assert result['checkId'] and owner.state()['phase'] in {'available','checked'}
    assert not any(row.get('status')=='check_failed' for row in owner.inspect({})['items'])
    with pytest.raises(YAMLError):
        await ensure(home,generation,{'id':'11111111-1111-4111-8111-111111111111','workspace':str(workspace)},shared_home=tmp_path/'shared')
    assert bad.read_text()=='providers: [broken yaml'

@pytest.mark.asyncio
async def test_independent_generation_sources_overlap_but_one_target_serializes(tmp_path,monkeypatch):
    import asyncio
    import amplifier_acp.generations as module
    owner=Generations(tmp_path,{});stage=tmp_path/'candidate';base=stage/'foundation';rows=[]
    for index in range(6):
        path=base/str(index);path.mkdir(parents=True);(path/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/source','ref':'main'}))
        rows.append({'path':str(index),'url':'https://example.invalid/source','ref':'main','current':'old','latest':'new','label':'fixture'})
    active=set();high=0
    async def command(*args,cwd=None,**kwargs):
        nonlocal high
        if 'rev-parse' in args:return 'old'
        if 'fetch' in args:
            assert cwd not in active;active.add(cwd);high=max(high,len(active));await asyncio.sleep(.02);active.remove(cwd)
        return ''
    async def changes(*_):return [],[]
    monkeypatch.setattr(module,'process',command);monkeypatch.setattr(module,'cache_changes',changes)
    await owner.stage_cache_sources(stage,[*rows,rows[0]])
    assert high==4 and not active
    assert all(json.loads((base/row['path']/'.amplifier_cache_meta.json').read_text())['commit']=='new' for row in rows)

@pytest.mark.asyncio
async def test_generation_failed_source_cancels_sibling_fetch_before_checkout(tmp_path,monkeypatch):
    import asyncio
    import amplifier_acp.generations as module
    owner=Generations(tmp_path,{});stage=tmp_path/'candidate';rows=[];checked=[];cancelled=[]
    for index in range(4):
        path=stage/'foundation'/str(index);path.mkdir(parents=True);(path/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/source','ref':'main'}))
        rows.append({'path':str(index),'url':'https://example.invalid/source','ref':'main','current':'old','latest':'new','label':'fixture'})
    async def command(*args,cwd=None,**kwargs):
        if 'rev-parse' in args:return 'old'
        if 'fetch' in args:
            if cwd.name=='0':await asyncio.sleep(.01);raise RuntimeError('fixture failure')
            try:await asyncio.sleep(10)
            except asyncio.CancelledError:cancelled.append(cwd.name);raise
        if 'checkout' in args:checked.append(cwd)
        return ''
    async def changes(*_):return [],[]
    monkeypatch.setattr(module,'process',command);monkeypatch.setattr(module,'cache_changes',changes)
    with pytest.raises(RuntimeError):await owner.stage_cache_sources(stage,rows)
    assert not checked and sorted(cancelled)==['1','2','3']
