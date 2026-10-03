import base64,hashlib,io,json,sqlite3,tarfile
from pathlib import Path
import pytest
from test_private_store_snapshot import launch,hold,call,prepare
from test_maintenance import seeded,initialized

RECORDS='native-maintenance-records';BODIES='native-retained-archives'

async def capture(client,cwd,auth,preview,prefix):
    lease=await call(client,cwd,'maintenance.acquire',{'commandId':prefix+'-acquire','previewHash':preview['previewHash']},auth)
    artifact=await call(client,cwd,'maintenance.snapshot',{'commandId':prefix+'-capture','leaseId':lease['leaseId'],'previewHash':preview['previewHash']},auth)
    await call(client,cwd,'maintenance.release',{'leaseId':lease['leaseId']},auth)
    content=bytearray();offset=0
    while True:
        page=await call(client,cwd,'maintenance.artifact.read',{'artifactId':artifact['artifactId'],'sha256':artifact['sha256'],'offset':offset,'maxBytes':16384})
        chunk=base64.b64decode(page['data']);assert hashlib.sha256(chunk).hexdigest()==page['chunkSha256'];content.extend(chunk)
        if page['nextOffset'] is None:break
        offset=page['nextOffset']
    assert hashlib.sha256(content).hexdigest()==artifact['sha256']
    return artifact,bytes(content)

@pytest.mark.asyncio
async def test_actual_native_maintenance_snapshot_retains_reset_originals_and_cutoff_archives(tmp_path):
    path,cwd,config,runtime,sid=await seeded(tmp_path);await runtime.close()
    config['workerCommand']=['/impossible/maintenance-snapshot-worker'];path.write_text(json.dumps(config))
    client,_=await initialized(path);root=Path(config['appHome'])/'maintenance'
    from amplifier_acp.native.session_files import project_slug
    canonical=Path(config['home'])/'projects'/project_slug(cwd)/'sessions'/sid
    before={p.relative_to(canonical):p.read_bytes() for p in canonical.rglob('*') if p.is_file()}
    try:
        auth=await hold(client,cwd)
        # Make a real retained configuration reset, without changing history.
        reset=await call(client,cwd,'maintenance.reset.preview',{'nativeSessionId':sid,'cwd':str(cwd),'scope':'session-configuration','privateContentReviewed':True},auth)
        held=await call(client,cwd,'maintenance.acquire',{'commandId':'reset-acquire','previewHash':reset['previewHash']},auth)
        changed=await call(client,cwd,'maintenance.reset.apply',{'commandId':'reset-original','leaseId':held['leaseId'],'previewHash':reset['previewHash']},auth)
        await call(client,cwd,'maintenance.release',{'leaseId':held['leaseId']},auth)
        originals={p.name:p.read_bytes() for p in (root/'retained'/changed['retainedId']).iterdir()}
        # Existing owner artifact, prepared images and open plans are preserved.
        first=await prepare(client,cwd,auth,parts=['native-import-records'],identity='prior')
        artifact,old_body=await capture(client,cwd,auth,first,'prior')
        prior_files={p.relative_to(root):p.read_bytes() for p in (root/'artifacts'/artifact['artifactId']).iterdir()}
        opened=await call(client,cwd,'maintenance.archive.create',{'commandId':'prior-open-plan','parts':['session-history'],'privateContentReviewed':True},auth)
        preview=await prepare(client,cwd,auth,parts=[RECORDS,BODIES],identity='backup')
        coverage=preview['ownerCoverage']
        import jsonschema
        schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/native-archive-owner-coverage.v1.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(coverage)
        assert all(row['owner']!='native-maintenance' for row in coverage['omitted'])
        assert all(row['cutoff']['excludedPlanId']==preview['archivePlanId'] for row in coverage['included'])
        assert not coverage['completeNativeBackup'] and not coverage['completeProductBackup']
        # Later bookkeeping is outside the declared cutoff, not a source of
        # false self-staleness or silently added members.
        later=await call(client,cwd,'maintenance.archive.create',{'commandId':'later-open-plan','parts':['session-history'],'privateContentReviewed':True},auth)
        snapshot,body=await capture(client,cwd,auth,preview,'final')
        with tarfile.open(fileobj=io.BytesIO(body)) as tar:
            names=tar.getnames();prefix='native-owned-stores/'+RECORDS+'/'
            assert not any(preview['archivePlanId'] in name or later['planId'] in name or snapshot['artifactId'] in name for name in names)
            assert prefix+'plans/'+opened['planId']+'/manifest.sqlite3' in names
            assert tar.extractfile('native-owned-stores/'+BODIES+'/artifacts/'+artifact['artifactId']+'/snapshot.tar').read()==old_body
            for name,raw in originals.items():assert tar.extractfile(prefix+'retained/'+changed['retainedId']+'/'+name).read()==raw
            image=tmp_path/'receipts-image.sqlite3';image.write_bytes(tar.extractfile(prefix+'receipts.sqlite3').read())
            with sqlite3.connect(image) as db:
                rows={identity:json.loads(value) for identity,value in db.execute('SELECT id,body FROM commands')}
                assert rows['reset-original']['state']=='succeeded'
                assert rows['backup-prepare']['state']=='running'
                assert 'final-capture' not in rows and 'later-open-plan' not in rows
            for name in names:
                if name.endswith('.sqlite3'):
                    check=tmp_path/'check.sqlite3';check.write_bytes(tar.extractfile(name).read())
                    with sqlite3.connect(check) as db:assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
            assert not any(name.endswith(('-wal','-shm','.lock')) for name in names)
        assert all((root/p).read_bytes()==raw for p,raw in prior_files.items())
        assert all((canonical/p).read_bytes()==raw for p,raw in before.items())
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_maintenance_records_exclude_unselected_archive_bodies_and_report_authority(tmp_path):
    client,cwd,config,_=await launch(tmp_path)
    try:
        auth=await hold(client,cwd)
        first=await prepare(client,cwd,auth,parts=['native-import-records'],identity='old')
        artifact,_=await capture(client,cwd,auth,first,'old')
        preview=await prepare(client,cwd,auth,parts=[RECORDS],identity='records')
        omitted=next(row for row in preview['ownerCoverage']['omitted'] if row['part']==BODIES)
        assert omitted['classification']=='authoritative'
        _,body=await capture(client,cwd,auth,preview,'records')
        with tarfile.open(fileobj=io.BytesIO(body)) as tar:
            names=tar.getnames()
            assert any(artifact['artifactId']+'/description.json' in name for name in names)
            assert not any(name.endswith('snapshot.tar') or '/native-stores/' in name for name in names)
        invalid=await call(client,cwd,'maintenance.archive.create',{'commandId':'bodies-without-records','parts':[BODIES],'privateContentReviewed':True},auth,False)
        assert 'require their maintenance records' in invalid['message']
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['unknown-root','symlink-retained','too-large'])
async def test_owner_snapshot_refuses_unknown_unsafe_or_oversized_content(tmp_path,bad):
    client,cwd,config,path=await launch(tmp_path);root=Path(config['appHome'])/'maintenance'
    try:
        await call(client,cwd,'maintenance.receipt',{'commandId':'create-store'})
        source=root/'unclassified-state.json'
        if bad=='unknown-root':source.write_bytes(b'preserve unknown owner record')
        elif bad=='symlink-retained':
            source=tmp_path/'private-original';source.write_bytes(b'preserve private');(root/'retained').mkdir();(root/'retained'/'alias').symlink_to(source)
        else:
            directory=root/'artifacts'/('a'*32);directory.mkdir(parents=True);source=directory/'snapshot.partial.tar';source.write_bytes(b'x'*65536)
            await client.close();config['maintenanceMaxBytes']=32768;path.write_text(json.dumps(config));client,_=await initialized(path)
        original=source.read_bytes();auth=await hold(client,cwd)
        failure=await prepare(client,cwd,auth,parts=[RECORDS,BODIES],identity='refuse',okay=False)
        assert failure['data']['settled'] and failure['data']['canonicalFilesChanged']==0
        assert source.read_bytes()==original
        receipt=await call(client,cwd,'maintenance.receipt',{'commandId':'refuse-prepare'})
        assert receipt['receipt']['state']=='failed'
        repeat=await call(client,cwd,'maintenance.archive.prepare',{'commandId':'refuse-prepare','planId':next((root/'plans').iterdir()).name,'expectedRevision':0},auth)
        assert repeat['receipt']==receipt['receipt'] and repeat['replayed'] is False
    finally:await client.close()
