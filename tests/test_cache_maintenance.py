import json,os,py_compile,sqlite3,subprocess,time
from pathlib import Path
import pytest
from client import Client
from test_private_store_snapshot import launch,call,hold
from amplifier_acp.cache_inspection import bytecode,digest


def repo(path):
    path.mkdir(parents=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=path,stderr=subprocess.DEVNULL,text=True).strip()
    git('init','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    source=path/'sample.py';source.write_text('VALUE = 42\n')
    git('add','sample.py');git('commit','-m','fixture')
    head=git('rev-parse','HEAD');git('remote','add','origin','https://example.invalid/fixture.git');git('update-ref','refs/remotes/origin/main',head);git('branch','--set-upstream-to','origin/main','main')
    (path/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://user:FIXTURE_SECRET@example.invalid/fixture.git?key=fixture','ref':'main','commit':head}))
    pyc=Path(py_compile.compile(str(source),doraise=True));return source,pyc,git

async def setup(tmp_path):
    client,cwd,config,path=await launch(tmp_path);await client.close();config['maintenanceExternalWriters']='stopped';path.write_text(json.dumps(config));client=await Client.launch(path)
    result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert result['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['cacheInventory']['sourceRetirement'] is False
    return client,cwd,config,path

async def scan(client,cwd,name='scan',auth=None):
    result=await call(client,cwd,'maintenance.cache.scan',{'commandId':name},auth)
    page=await call(client,cwd,'maintenance.cache.page',{'scanId':result['scanId'],'revision':result['revision']})
    return result,page

async def preview(client,cwd,index,ids,auth=None):
    return await call(client,cwd,'maintenance.cache.preview',{'scanId':index['scanId'],'revision':index['revision'],'ids':ids,'reviewed':True},auth)

@pytest.mark.asyncio
async def test_cold_protocol_inventory_and_exact_derived_clear_preserve_all_source_and_history(tmp_path):
    client,cwd,config,path=await setup(tmp_path)
    source,pyc,git=repo(Path(config['appHome'])/'foundation/cache/fixture');before=source.read_bytes();metadata=(source.parent/'.amplifier_cache_meta.json').read_bytes()
    history=Path(config['home'])/'projects/old/sessions/root';history.mkdir(parents=True);(history/'events.jsonl').write_text('untouched canonical logs')
    try:
        index,page=await scan(client,cwd)
        from jsonschema import Draft202012Validator
        Draft202012Validator(json.loads((Path(__file__).parents[1]/'schemas/native-cache-page.v1.json').read_text())).validate(page)
        assert 'FIXTURE_SECRET' not in json.dumps(page) and 'key=fixture' not in json.dumps(page)
        sources=[r for r in page['items'] if r['kind']=='cached-source'];assert len(sources)==1 and not sources[0]['clearable']
        bytecodes=[r for r in page['items'] if r['kind']=='python-bytecode'];assert len(bytecodes)==1 and bytecodes[0]['clearable'],page
        denied=await call(client,cwd,'maintenance.cache.preview',{'scanId':index['scanId'],'revision':index['revision'],'ids':[sources[0]['id']],'reviewed':True},okay=False);assert denied['data']['executed'] is False
        review=await preview(client,cwd,index,[bytecodes[0]['id']]);auth=await hold(client,cwd)
        # Passive indexed reads remain possible under the native admin fence.
        assert (await call(client,cwd,'maintenance.cache.page',{'scanId':index['scanId'],'revision':index['revision'],'limit':1}))['nextCursor']
        args={'commandId':'clear-once','previewHash':review['previewHash']}
        result=await call(client,cwd,'maintenance.cache.clear',args,auth);assert result['receipt']['state']=='succeeded' and not pyc.exists()
        assert result['sourceDirectoriesRemoved']==result['canonicalFilesChanged']==0
        assert source.read_bytes()==before and (source.parent/'.amplifier_cache_meta.json').read_bytes()==metadata and (history/'events.jsonl').read_text()=='untouched canonical logs'
        Path(py_compile.compile(str(source),doraise=True))
        same=await call(client,cwd,'maintenance.cache.clear',args,auth);assert same['replayed'] is False and pyc.exists()
        assert not client.events
    finally:await client.close()
    # A new process can inspect the same receipt without replaying or starting a worker.
    replacement=await Client.launch(path)
    try:
        await replacement.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        row=(await call(replacement,cwd,'maintenance.receipt',{'commandId':'clear-once'}))['receipt'];assert row['state']=='succeeded' and pyc.exists()
    finally:await replacement.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('protection',['dirty','untracked','unpushed','unknown-upstream','retained','external','runtime-reference','invalid-bytecode'])
async def test_local_and_unknown_reference_cases_are_never_clearable(tmp_path,protection):
    client,cwd,config,path=await setup(tmp_path);root=Path(config['appHome'])/'foundation/cache/fixture'
    if protection=='retained':root=Path(config['appHome'])/'updates/releases'/('a'*32)/'foundation/cache/fixture'
    if protection=='external':root=Path(config['home'])/'cache/fixture'
    source,pyc,git=repo(root)
    if protection=='dirty':source.write_text('VALUE = 43\n')
    if protection=='untracked':(root/'notes.txt').write_text('user work')
    if protection=='unpushed':(root/'new.txt').write_text('local commit');git('add','new.txt');git('commit','-m','unpushed')
    if protection=='unknown-upstream':git('branch','--unset-upstream')
    if protection=='runtime-reference':
        receipt=Path(config['appHome'])/'updates/baseline-runtime';receipt.mkdir(parents=True);(receipt/'runtime-installed.json').write_text(json.dumps({'path':str(root)}))
    if protection=='invalid-bytecode':pyc.write_bytes(pyc.read_bytes()+b'unknown extra content')
    try:
        _,page=await scan(client,cwd);assert not any(r['clearable'] for r in page['items']),page
        assert pyc.exists() and source.exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_stale_source_or_reference_review_refuses_before_effects_with_durable_receipt(tmp_path):
    client,cwd,config,path=await setup(tmp_path);source,pyc,_=repo(Path(config['appHome'])/'foundation/cache/fixture')
    try:
        index,page=await scan(client,cwd);candidate=next(r for r in page['items'] if r['clearable']);review=await preview(client,cwd,index,[candidate['id']]);auth=await hold(client,cwd)
        source.write_text('VALUE = 999\n')
        result=await call(client,cwd,'maintenance.cache.clear',{'commandId':'stale','previewHash':review['previewHash']},auth,False)
        assert result['data']['executed'] is False and pyc.exists()
        assert (await call(client,cwd,'maintenance.receipt',{'commandId':'stale'}))['receipt']['state']=='refused'
    finally:await client.close()

@pytest.mark.asyncio
async def test_writer_exclusion_and_expired_preview_prevent_cache_effects(tmp_path):
    client,cwd,config,path=await setup(tmp_path);source,pyc,_=repo(Path(config['appHome'])/'foundation/cache/fixture')
    from amplifier_acp.maintenance_gate import Gate
    try:
        index,page=await scan(client,cwd);review=await preview(client,cwd,index,[next(r['id'] for r in page['items'] if r['clearable'])]);auth=await hold(client,cwd)
        with Gate(config):
            denied=await call(client,cwd,'maintenance.cache.clear',{'commandId':'busy','previewHash':review['previewHash']},auth,False)
            assert denied['data']['executed'] is False and pyc.exists()
        # Exact expired preview created from owner storage: do not wait five minutes.
        db=sqlite3.connect(Path(config['appHome'])/'maintenance/receipts.sqlite3')
        body=json.loads(db.execute('SELECT body FROM cache_previews WHERE hash=?',(review['previewHash'],)).fetchone()[0]);body['expiresAt']=time.time()-1;expired=digest(body)
        db.execute('INSERT INTO cache_previews VALUES(?,?,?)',(expired,str(cwd),json.dumps(body)));db.commit();db.close()
        denied=await call(client,cwd,'maintenance.cache.clear',{'commandId':'expired','previewHash':expired},auth,False)
        assert denied['data']['executed'] is False and pyc.exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_index_pages_are_stable_cold_reads_and_unknown_receipts_never_replay(tmp_path):
    client,cwd,config,path=await setup(tmp_path);root=Path(config['appHome'])/'foundation/cache';root.mkdir(parents=True)
    for i in range(105):(root/f'unknown-{i:03}.json').write_text('{}')
    try:
        index,_=await scan(client,cwd);seen=[];cursor=None
        while True:
            page=await call(client,cwd,'maintenance.cache.page',{'scanId':index['scanId'],'revision':index['revision'],'limit':20,**({'cursor':cursor} if cursor else {})});seen.extend(row['id'] for row in page['items']);cursor=page['nextCursor']
            if not cursor:break
        assert len(seen)==106 and len(set(seen))==106  #105unknown files plus the configured launcher source
        # Source changes do not silently rescan or mutate a completed index page.
        (root/'unknown-000.json').unlink();assert (await call(client,cwd,'maintenance.cache.page',{'scanId':index['scanId'],'revision':index['revision'],'limit':20}))['items']
        assert not client.events
    finally:await client.close()


def test_bytecode_proof_is_content_exact_and_does_not_execute_source(tmp_path):
    source=tmp_path/'sideeffect.py';source.write_text("raise AssertionError('compile must not execute')\n")
    pyc=Path(py_compile.compile(str(source),doraise=True));assert bytecode(pyc)['sourceSha256']
    pyc.write_bytes(pyc.read_bytes()+b'x')
    with pytest.raises(ValueError,match='exactly reconstructible'):bytecode(pyc)

@pytest.mark.asyncio
async def test_partial_clear_is_unknown_and_exact_retry_never_removes_remaining_file(tmp_path,monkeypatch):
    from amplifier_acp.admin import Administration
    from test_admin_lifecycle import CONTEXT
    home=tmp_path/'home';home.mkdir();cwd=tmp_path/'workspace';cwd.mkdir()
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceExternalWriters':'stopped'}
    owner=Administration(config);source,first,git=repo(tmp_path/'app/foundation/cache/fixture')
    second_source=source.parent/'other.py';second_source.write_text('OTHER = 17\n');git('add','other.py');git('commit','-m','second');head=git('rev-parse','HEAD');git('update-ref','refs/remotes/origin/main',head)
    meta=source.parent/'.amplifier_cache_meta.json';value=json.loads(meta.read_text());value['commit']=head;meta.write_text(json.dumps(value));second=Path(py_compile.compile(str(second_source),doraise=True))
    try:
        scan=await owner.perform(str(cwd),'maintenance.cache.scan',{'commandId':'scan'})
        page=await owner.perform(str(cwd),'maintenance.cache.page',{'scanId':scan['scanId'],'revision':scan['revision']})
        review=await owner.perform(str(cwd),'maintenance.cache.preview',{'scanId':scan['scanId'],'revision':scan['revision'],'ids':[r['id'] for r in page['items'] if r['clearable']],'reviewed':True})
        held=owner.lifecycle.perform('acquire',CONTEXT);auth={k:held['lease'][k] for k in ('leaseId','commandId','fenceId')}
        original=Path.unlink;calls=[]
        def fail_after_one(path,*args,**kwargs):
            if path in {first,second}:
                calls.append(path)
                if len(calls)==2:raise OSError('fixture interruption after first effect')
            return original(path,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path,'unlink',fail_after_one)
            with pytest.raises(OSError):await owner.perform(str(cwd),'maintenance.cache.clear',{'commandId':'partial','previewHash':review['previewHash']},fence=auth)
        assert len(calls)==2 and sum(p.exists() for p in (first,second))==1
        receipt=await owner.perform(str(cwd),'maintenance.receipt',{'commandId':'partial'});assert receipt['receipt']['state']=='unknown'
        duplicate=await owner.perform(str(cwd),'maintenance.cache.clear',{'commandId':'partial','previewHash':review['previewHash']},fence=auth)
        assert duplicate['receipt']['state']=='unknown' and sum(p.exists() for p in (first,second))==1
    finally:await owner.close()

@pytest.mark.asyncio
async def test_cancelled_clear_joins_actual_file_thread_before_releasing_writer_gate(tmp_path,monkeypatch):
    import asyncio,threading
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
    from test_admin_lifecycle import CONTEXT
    home=tmp_path/'home';home.mkdir();cwd=tmp_path/'workspace';cwd.mkdir()
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceExternalWriters':'stopped'}
    owner=Administration(config);source,pyc,git=repo(tmp_path/'app/foundation/cache/fixture');entered=threading.Event();finish=threading.Event()
    try:
        scan=await owner.perform(str(cwd),'maintenance.cache.scan',{'commandId':'scan'})
        page=await owner.perform(str(cwd),'maintenance.cache.page',{'scanId':scan['scanId'],'revision':scan['revision']})
        review=await owner.perform(str(cwd),'maintenance.cache.preview',{'scanId':scan['scanId'],'revision':scan['revision'],'ids':[r['id'] for r in page['items'] if r['clearable']],'reviewed':True})
        held=owner.lifecycle.perform('acquire',CONTEXT);auth={k:held['lease'][k] for k in ('leaseId','commandId','fenceId')};original=Path.unlink
        def block(path,*args,**kwargs):
            if path==pyc:entered.set();assert finish.wait(10)
            return original(path,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path,'unlink',block)
            task=asyncio.create_task(owner.perform(str(cwd),'maintenance.cache.clear',{'commandId':'cancelled','previewHash':review['previewHash']},fence=auth))
            assert await asyncio.to_thread(entered.wait,10);task.cancel();await asyncio.sleep(.02)
            assert not task.done()
            with pytest.raises(MaintenanceBusy):Gate(config)
            finish.set()
            with pytest.raises(asyncio.CancelledError):await task
        # Cancellation of the transport/request does not cancel the joined
        # owner effect; its conclusive durable result remains inspectable.
        row=owner.maintenance.receipt(str(cwd),'cancelled')[1];assert row['state']=='succeeded' and not pyc.exists()
        with Gate(config):pass
    finally:finish.set();await owner.close()

@pytest.mark.asyncio
async def test_new_retained_generation_invalidates_exact_review_without_removing_bytes(tmp_path):
    client,cwd,config,path=await setup(tmp_path);source,pyc,_=repo(Path(config['appHome'])/'foundation/cache/fixture')
    try:
        index,page=await scan(client,cwd);review=await preview(client,cwd,index,[next(r['id'] for r in page['items'] if r['clearable'])]);auth=await hold(client,cwd)
        (Path(config['appHome'])/'updates/releases/new-retained-generation').mkdir(parents=True)
        result=await call(client,cwd,'maintenance.cache.clear',{'commandId':'new-reference','previewHash':review['previewHash']},auth,False)
        assert result['data']['executed'] is False and pyc.exists()
        assert (await call(client,cwd,'maintenance.receipt',{'commandId':'new-reference'}))['receipt']['state']=='refused'
    finally:await client.close()


def test_public_cache_schema_matches_strict_wire_and_rejects_path_selectors():
    from jsonschema import Draft202012Validator
    schema=json.loads((Path(__file__).parents[1]/'schemas/native-cache-maintenance.v1.json').read_text())
    valid={'cwd':'/authorized/workspace','operation':'maintenance.cache.clear','args':{'commandId':'clear','previewHash':'a'*64}}
    validator=Draft202012Validator(schema);validator.validate(valid)
    assert list(validator.iter_errors({**valid,'args':{**valid['args'],'path':'/caller/chosen'}}))
