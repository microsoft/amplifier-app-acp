import hashlib,json,os,sys
from pathlib import Path
import pytest
from amplifier_acp.generations import Generations,launcher
from amplifier_acp.native import runtime_environment as environments,runtime_qualification as qualification
from client import Client
from native_setup import configuration

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
