import hashlib,io,json,os,sqlite3,tarfile
from pathlib import Path
import pytest
from test_full_native_archive import full_setup
from test_private_store_snapshot import call,hold,prepare
from test_maintenance_store_snapshot import capture
from test_maintenance import initialized

async def snapshot(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    settings=cwd/'.amplifier';settings.mkdir(exist_ok=True);(settings/'settings.local.yaml').write_text('fixture_backup: retained\n')
    auth=await hold(client,cwd);preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
    artifact,raw=await capture(client,cwd,auth,preview,'restore-source')
    args={'artifactId':artifact['artifactId'],'sha256':artifact['sha256'],'destination':{'rootId':'owned','name':'restored'},'privateContentReviewed':True}
    return client,cwd,config,path,sid,auth,artifact,args

@pytest.mark.asyncio
async def test_protocol_restores_new_home_preserves_native_identity_and_never_replays(tmp_path,monkeypatch):
    client,cwd,config,path,sid,auth,artifact,args=await snapshot(tmp_path)
    from amplifier_acp.native.session_files import project_slug
    original=Path(config['home'])/'projects'/project_slug(cwd)/'sessions'/sid
    before={p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file() and not p.name.endswith(('.lock','-shm'))}
    try:
        plan=await call(client,cwd,'maintenance.restore.preview',args,auth)
        assert plan['startsWorker'] is False and plan['overwritesExisting'] is False
        page=await call(client,cwd,'maintenance.restore.manifest',{'previewHash':plan['previewHash'],'limit':2})
        assert len(page['items'])==2 and page['nextCursor']
        request={'commandId':'restore-once','previewHash':plan['previewHash']}
        applied=await call(client,cwd,'maintenance.restore.apply',request,auth)
        assert applied['receipt']['state']=='succeeded' and not client.events
        same=await call(client,cwd,'maintenance.restore.apply',request,auth)
        assert same['directory']==applied['directory'] and same['replayed'] is False
        inspected=await call(client,cwd,'maintenance.restore.inspect',{'restoreCommandId':'restore-once'})
        assert inspected['finalized'] is True and inspected['pending'] is False and inspected['receipt']==applied['receipt']
        target=Path(applied['directory']);restored=target/'native-home/projects'/project_slug(cwd)/'sessions'/sid
        assert all((restored/p).read_bytes()==raw for p,raw in before.items())
        assert all((original/p).read_bytes()==raw for p,raw in before.items())
        assert not (target/'native-home/.amplifier-acp-restore-pending.json').exists()
        stored=target/'workspace-settings'/hashlib.sha256(str(cwd).encode()).hexdigest()/'.amplifier/settings.local.yaml'
        assert stored.read_bytes()==(cwd/'.amplifier/settings.local.yaml').read_bytes()
        # Restore never starts a worker. An explicit separately reviewed launch
        # uses the copied graph selectors and same session identity only once.
        new_config=json.loads(Path(applied['launcherPath']).read_text())
        environment=json.loads(Path(applied['environmentPath']).read_text())
        for key,value in environment.items():monkeypatch.setenv(key,value)
        launch=tmp_path/'restored-agent.json';launch.write_text(json.dumps(new_config))
        agent,_=await initialized(launch)
        try:
            response=await agent.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
            assert 'result' in response,response
            assert all((original/p).read_bytes()==raw for p,raw in before.items())
            response=await agent.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'new restored input'}]})
            assert response.get('result',{}).get('stopReason')=='end_turn',response
            rows=[json.loads(line) for line in (restored/'transcript.jsonl').read_text().splitlines()]
            assert sum(row.get('content')=='snapshot evidence' for row in rows)==1
            assert sum(row.get('content')=='new restored input' for row in rows)==1
        finally:await agent.close()
        assert all((original/p).read_bytes()==raw for p,raw in before.items())
    finally:await client.close()

@pytest.mark.asyncio
async def test_destination_existing_and_review_changes_are_known_no_effect(tmp_path):
    client,cwd,config,path,sid,auth,artifact,args=await snapshot(tmp_path)
    try:
        plan=await call(client,cwd,'maintenance.restore.preview',args,auth)
        target=tmp_path/'destinations/restored';target.mkdir();(target/'original').write_text('never overwrite')
        denied=await call(client,cwd,'maintenance.restore.apply',{'commandId':'restore-refused','previewHash':plan['previewHash']},auth,False)
        assert denied['data']['executed'] is False and (target/'original').read_text()=='never overwrite'
        receipt=await call(client,cwd,'maintenance.receipt',{'commandId':'restore-refused'})
        assert receipt['receipt']['state']=='refused'
        assert list(target.iterdir())==[target/'original']
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['traversal','duplicate','missing','link','version','digest','schema'])
async def test_restore_rejects_untrusted_archive_even_with_matching_outer_hash(tmp_path,kind):
    client,cwd,config,path,sid,auth,artifact,args=await snapshot(tmp_path)
    directory=Path(config['appHome'])/'maintenance/artifacts'/artifact['artifactId'];archive=directory/'snapshot.tar'
    original=archive.read_bytes()
    try:
        with tarfile.open(fileobj=io.BytesIO(original)) as src:
            members=[]
            for item in src:
                members.append((item,src.extractfile(item).read()))
        if kind=='schema':
            manifest=tmp_path/'untrusted.sqlite3';manifest.write_bytes(members[1][1])
            with sqlite3.connect(manifest) as db:db.execute('CREATE TABLE unrecognized(body TEXT)')
            body=manifest.read_bytes();members[1]=(members[1][0],body);members[1][0].size=len(body)
            header=json.loads(members[0][1]);header['manifest'].update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest());body=json.dumps(header).encode();members[0]=(members[0][0],body);members[0][0].size=len(body)
        altered=io.BytesIO()
        with tarfile.open(fileobj=altered,mode='w') as out:
            for i,(item,body) in enumerate(members):
                if kind=='missing' and i==2:continue
                if kind=='version' and i==0:
                    header=json.loads(body);header['version']=999;body=json.dumps(header).encode();item.size=len(body)
                if kind=='traversal' and i==2:item.name='native-home/../escape'
                if kind=='link' and i==2:item.type=tarfile.SYMTYPE;item.linkname='/tmp/escape';item.size=0;body=b''
                if kind=='digest' and i==2:body=b'x'*len(body)
                out.addfile(item,io.BytesIO(body))
                if kind=='duplicate' and i==2:out.addfile(item,io.BytesIO(body))
        raw=altered.getvalue();archive.write_bytes(raw)
        sha=hashlib.sha256(raw).hexdigest();description=json.loads((directory/'description.json').read_text());description.update(sha256=sha,bytes=len(raw));(directory/'description.json').write_text(json.dumps(description))
        denied=await call(client,cwd,'maintenance.restore.preview',{**args,'sha256':sha},auth,False)
        assert denied and not (tmp_path/'destinations/restored').exists()
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('loss',['during-write','after-receipt'])
async def test_process_death_preserves_unknown_or_exact_completed_receipt_without_replay(tmp_path,loss):
    import asyncio,sys
    client,cwd,config,path,sid,auth,artifact,args=await snapshot(tmp_path)
    plan=await call(client,cwd,'maintenance.restore.preview',args,auth);await client.close()
    # Kill a real separate owner process at the native write/finalize boundary.
    # The override exists only in this isolated fixture, never in production.
    script=r'''
import asyncio,json,os,sys
from amplifier_acp.admin import Administration
from amplifier_acp.maintenance import Maintenance
from amplifier_acp.native_restore import Restores
config=json.load(open(sys.argv[1]));admin=Administration(config)
context={'fenceId':'owned-crash-fence','commandId':'owned-crash-owner','purpose':'recovery','instanceId':'owned-fixture','dataScope':'owned-fixture'}
admin.lifecycle.perform('acquire',context)
owner=Maintenance(admin)
def crash(*args,**kwargs):os._exit(23)
if sys.argv[4]=='during-write':Restores.scan=crash
else:Restores.finalize=crash
asyncio.run(owner.perform(sys.argv[2],'maintenance.restore.apply',{'commandId':'crash-restore','previewHash':sys.argv[3]}))
'''
    process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,str(path),str(cwd),plan['previewHash'],loss,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    output,error=await asyncio.wait_for(process.communicate(),20);assert process.returncode==23,error.decode()
    marker=tmp_path/'destinations/restored/native-home/.amplifier-acp-restore-pending.json'
    assert marker.exists()
    from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
    with pytest.raises(MaintenanceBusy):Gate({'home':str(marker.parent)})
    replacement,_=await initialized(path)
    try:
        receipt=(await call(replacement,cwd,'maintenance.receipt',{'commandId':'crash-restore'}))['receipt']
        assert receipt['state']==('unknown' if loss=='during-write' else 'succeeded')
        inspected=await call(replacement,cwd,'maintenance.restore.inspect',{'restoreCommandId':'crash-restore'})
        assert inspected['finalized'] is False
        auth=await hold(replacement,cwd)
        before={p:p.read_bytes() for p in marker.parent.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
        duplicate=await call(replacement,cwd,'maintenance.restore.apply',{'commandId':'crash-restore','previewHash':plan['previewHash']},auth)
        assert duplicate['replayed'] is False and all(p.read_bytes()==raw for p,raw in before.items())
        if loss=='during-write':
            refused=await call(replacement,cwd,'maintenance.restore.reconcile',{'commandId':'inspect-only','restoreCommandId':'crash-restore'},auth,False)
            assert refused['data']['executed'] is False and marker.exists()
        else:
            settled=await call(replacement,cwd,'maintenance.restore.reconcile',{'commandId':'finish-proof','restoreCommandId':'crash-restore'},auth)
            assert settled['replayed'] is False and not marker.exists()
            with Gate({'home':str(marker.parent)}):pass
            inspected=await call(replacement,cwd,'maintenance.restore.inspect',{'restoreCommandId':'crash-restore'})
            assert inspected['finalized'] is True and inspected['pending'] is False
            again=await call(replacement,cwd,'maintenance.restore.reconcile',{'commandId':'finish-proof','restoreCommandId':'crash-restore'},auth)
            assert again['receipt']==settled['receipt']
        assert not replacement.events
    finally:await replacement.close()
