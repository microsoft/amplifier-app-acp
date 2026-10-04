import base64,hashlib,io,json,os,sqlite3,tarfile
from pathlib import Path
import pytest
from test_maintenance import seeded,initialized
from test_private_store_snapshot import call,hold,prepare
from test_maintenance_store_snapshot import capture

async def full_setup(tmp_path):
    path,cwd,config,runtime,sid=await seeded(tmp_path);await runtime.close()
    config.update(maintenanceFullNative=True,maintenanceExternalWriters='stopped',workerCommand=['/impossible/full-native-worker'],maintenanceFullNativeRoots={'checkpoint':os.environ['AMPLIFIER_SESSION_STATE_HOME'],'events':os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'],'sources':os.environ['AMPLIFIER_SOURCE_STORE'],'bundle':config['bundle']},maintenanceRestoreRoots={'owned':str(tmp_path/'destinations')})
    (tmp_path/'destinations').mkdir();path.write_text(json.dumps(config));client,init=await initialized(path)
    assert init['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['archivePlans']['fullNative']['version']==1
    return client,cwd,config,path,sid

@pytest.mark.asyncio
async def test_cold_full_native_captures_all_histories_generation_authority_and_checkpoint(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    home=Path(config['home']);app=Path(config['appHome'])
    # Historical projects with unavailable directories remain backup authority.
    old=home/'projects/-no-longer-existing/sessions/old-child';old.mkdir(parents=True)
    (old/'events.jsonl').write_bytes(b'{"retained":"old events"}\n');(old/'metadata.json').write_bytes(b'{"session_id":"old-child","working_dir":"/no-longer-existing"}')
    release=app/'updates/releases'/('a'*32);release.mkdir(parents=True)
    (release/'runtime.toml').write_text('[project]\nname="saved-policy"\nversion="0.1.0"\n')
    (release/'runtime.lock').write_text('version = 1\n');(release/'profiles.json').write_text('["original-profile"]')
    (app/'updates/worker-leases').mkdir();(app/'updates/worker-leases/stale.json').write_text('{"pid":1}')
    credentials=home/'keys.env';credentials.write_bytes(b'SYNTHETIC_NATIVE_BACKUP_KEY=fixture-only\n')
    before={p:p.read_bytes() for p in [old/'events.jsonl',old/'metadata.json',release/'runtime.lock',credentials]}
    from amplifier_acp.message_metadata import MessageMetadata,reserve_quote
    from amplifier_acp.history import message_id
    selected=tmp_path/'selected-message';selected.mkdir()
    row={'role':'assistant','content':'Retained quoted evidence'}
    (selected/'transcript.jsonl').write_text(json.dumps(row)+'\n')
    ref={'sessionId':sid,'messageId':message_id(row,0),'position':0,'historyEpoch':None}
    adjunct=MessageMetadata(app,sid,actor_id='archive-fixture-account')
    reacted=adjunct.reaction(selected,ref,emoji='👍',present=True,command_id='archived-reaction')
    quote=reserve_quote(adjunct,selected,ref,input_id='archived-unknown-quote',input_payload={'text':'explicit follow-up'})
    from amplifier_acp.bundle_commands import BundleCommands
    commands=BundleCommands(config,str(cwd))
    async def fixture_saved(effect):return {'saved':{'name':'retained-original'}}
    await commands.perform('bundle.save',{'commandId':'archived-command'},fixture_saved)
    try:
        from amplifier_foundation.session.shared_state import SharedSessionStore
        writer=SharedSessionStore(cwd,'legacy-root').acquire(app='owned-backup-fixture')
        try:writer.write([{'role':'user','content':'legacy-only canonical evidence'}],bundle=config['bundle'])
        finally:writer.release()
        auth=await hold(client,cwd)
        created=await call(client,cwd,'maintenance.archive.create',{'commandId':'full-create','parts':['full-native-authority'],'privateContentReviewed':True,'includeCredentials':True,'credentialsReviewed':True},auth)
        preview=await call(client,cwd,'maintenance.archive.prepare',{'commandId':'full-prepare','planId':created['planId'],'expectedRevision':0},auth)
        assert preview['ownerCoverage']['completeNativeBackup'] and not preview['ownerCoverage']['completeProductBackup']
        assert len(json.dumps(preview))<16384 and 'fixture-only' not in json.dumps(preview)
        page=await call(client,cwd,'maintenance.archive.manifest',{'previewHash':preview['previewHash'],'limit':3})
        assert len(page['items'])==3 and page['nextCursor']
        artifact,raw=await capture(client,cwd,auth,preview,'full')
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            assert tar.getnames()[:2]==['native-archive.json','native-manifest.sqlite3']
            header=json.load(tar.extractfile('native-archive.json'))
            attested=await call(client,cwd,'maintenance.artifact.inspect',{'artifactId':artifact['artifactId'],'sha256':artifact['sha256']})
            assert attested['manifest']['sha256']==header['manifest']['sha256']
            assert attested['manifest']['digestKind']=='sha256-exact-native-manifest-sqlite3-bytes'
            assert attested['authority']['completeNativeBackup'] and not attested['authority']['completeProductBackup']
            assert attested['authority']['externalWriters']['policy']=='operator-attested-stopped'
            assert attested['authority']['externalWriters']['cooperativeSessionLeasesHeld'] is False
            assert attested['authority']['externalWriters']['requiresOperatorVerification'] is True
            assert set(attested['authority']['declaredRootIds'])=={row['id'] for row in header['nativeAuthority']['roots']}
            assert str(tmp_path) not in json.dumps(attested) and 'fixture-only' not in json.dumps(attested)
            import jsonschema
            jsonschema.validate(attested,json.loads((Path(__file__).resolve().parents[1]/'schemas/native-artifact-inspect-result.v1.json').read_text()))
            import jsonschema
            schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/native-full-archive.v1.json').read_text());jsonschema.Draft202012Validator.check_schema(schema);jsonschema.Draft202012Validator(schema).validate(header)
            assert header['format']=='amplifier-native-authority' and header['version']==1
            assert tar.extractfile('native-home/projects/-no-longer-existing/sessions/old-child/events.jsonl').read()==before[old/'events.jsonl']
            assert tar.extractfile('app-home/updates/releases/'+('a'*32)+'/runtime.lock').read()==before[release/'runtime.lock']
            assert tar.extractfile('native-home/keys.env').read()==before[credentials]
            image=tmp_path/'bundle-command-image.sqlite3';image.write_bytes(tar.extractfile('app-home/bundles/command-receipts.sqlite3').read())
            with sqlite3.connect(image) as receipts:
                body=json.loads(receipts.execute('SELECT body FROM commands WHERE id=?',('archived-command',)).fetchone()[0])
                assert body==commands.receipt('archived-command')
                assert receipts.execute('PRAGMA quick_check').fetchone()==('ok',)
            image=tmp_path/'message-adjunct-image.sqlite3'
            image.write_bytes(tar.extractfile('app-home/sessions/'+sid+'/message-interactions.sqlite3').read())
            with sqlite3.connect(image) as db:
                assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
                assert db.execute('SELECT message,actor,emoji FROM annotations').fetchall()==[(ref['messageId'],'archive-fixture-account','👍')]
                assert json.loads(db.execute('SELECT value FROM receipts WHERE command=?',('archived-reaction',)).fetchone()[0])['result']==reacted
                reserved=json.loads(db.execute('SELECT value FROM quotes WHERE input=?',('archived-unknown-quote',)).fetchone()[0])
                assert reserved['quote']==quote and reserved['outcome']=='unknown' and reserved['replayed'] is False
            assert any(name.startswith('checkpoint/') and name.endswith('/checkpoint.json') for name in tar.getnames())
            assert not any('worker-leases' in name or created['planId'] in name or artifact['artifactId'] in name for name in tar.getnames()[2:])
            manifest=tmp_path/'manifest.sqlite3';manifest.write_bytes(tar.extractfile('native-manifest.sqlite3').read())
            assert hashlib.sha256(manifest.read_bytes()).hexdigest()==header['manifest']['sha256']
            with sqlite3.connect(manifest) as db:
                excluded=[json.loads(row[0]) for row in db.execute("SELECT body FROM entries WHERE json_extract(body,'$.status')='excluded'")]
                assert any(row['path']=='app-home/updates/worker-leases' and row['classification']=='transient' for row in excluded)
        assert all(p.read_bytes()==value for p,value in before.items())
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_full_native_excluded_credentials_make_coverage_explicitly_incomplete(tmp_path):
    client,cwd,config,_,_=await full_setup(tmp_path);(Path(config['home'])/'keys.env').write_text('SYNTHETIC_PRIVATE=fixture')
    try:
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
        assert not preview['ownerCoverage']['completeNativeBackup'] and preview['ownerCoverage']['authoritativeOmissions']==1
        _,raw=await capture(client,cwd,auth,preview,'excluded')
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:assert 'native-home/keys.env' not in tar.getnames()
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['unknown-home','unqualified-environment','missing-external-declaration','external-writers'])
async def test_full_native_refuses_unknown_or_unprotected_authority(tmp_path,bad):
    client,cwd,config,path,_=await full_setup(tmp_path)
    if bad=='unknown-home':(Path(config['home'])/'unknown-new-authority.json').write_text('{}')
    elif bad=='unqualified-environment':(Path(config['appHome'])/'runtime/unknown/.venv').mkdir(parents=True)
    else:
        await client.close()
        if bad=='missing-external-declaration':config['maintenanceFullNativeRoots'].pop('checkpoint')
        else:config['maintenanceExternalWriters']='foundation-cooperative'
        path.write_text(json.dumps(config));client,_=await initialized(path)
    try:
        auth=await hold(client,cwd)
        created=await call(client,cwd,'maintenance.archive.create',{'commandId':'bad-create','parts':['full-native-authority'],'privateContentReviewed':True},auth,okay=bad!='missing-external-declaration')
        if bad=='missing-external-declaration':assert 'launcher-declared external checkpoint' in created['message']
        else:
            denied=await call(client,cwd,'maintenance.archive.prepare',{'commandId':'bad-prepare','planId':created['planId'],'expectedRevision':0},auth,False)
            assert denied['data']['canonicalFilesChanged']==0
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_full_native_disk_inventory_pages_many_cold_histories_and_marks_unknown_workspace_authority(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path);home=Path(config['home'])
    for number in range(350):
        directory=home/'projects/-missing-project/sessions'/('saved-'+str(number));directory.mkdir(parents=True,exist_ok=True)
        (directory/'metadata.json').write_text(json.dumps({'session_id':'saved-'+str(number),'working_dir':'/missing-owned-fixture-project'}))
        (directory/'transcript.jsonl').write_text('{"role":"user","content":"retained offline input"}\n')
    outside=tmp_path/'other-workspace';outside.mkdir();(outside/'.amplifier').mkdir();(outside/'.amplifier/settings.yaml').write_text('must_not_read: ungranted')
    ungranted=home/'projects/-ungranted/sessions/ungranted';ungranted.mkdir(parents=True)
    (ungranted/'metadata.json').write_text(json.dumps({'session_id':'ungranted','working_dir':str(outside)}))
    try:
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
        assert preview['fullNative']['inventory']['knownNativeSessions']==352
        assert preview['ownerCoverage']['completeNativeBackup'] is False and preview['ownerCoverage']['authoritativeOmissions']==1
        assert len(json.dumps(preview))<16384
        names=[];cursor=None
        while True:
            page=await call(client,cwd,'maintenance.archive.manifest',{'previewHash':preview['previewHash'],'limit':97,**({'cursor':cursor} if cursor else {})})
            assert len(page['items'])<=97 and len(json.dumps(page))<512*1024
            names.extend(row['path'] for row in page['items']);cursor=page['nextCursor']
            if cursor is None:break
        assert names==sorted(set(names)) and len(names)>1000
        assert sum(name.endswith('/transcript.jsonl') for name in names)==351
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['artifact','manifest','old-evidence'])
async def test_passive_artifact_attestation_refuses_changed_bytes_and_missing_sealed_policy(tmp_path,changed):
    client,cwd,config,_,_=await full_setup(tmp_path)
    try:
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
        artifact,raw=await capture(client,cwd,auth,preview,'inspect')
        directory=Path(config['appHome'])/'maintenance/artifacts'/artifact['artifactId']
        if changed=='artifact':
            value=bytearray(raw);value[-1]^=1;(directory/'snapshot.tar').write_bytes(value)
        else:
            with tarfile.open(fileobj=io.BytesIO(raw)) as source:
                members=[(member,source.extractfile(member).read()) for member in source]
            if changed=='manifest':
                value=bytearray(members[1][1]);value[-1]^=1;members[1]=(members[1][0],bytes(value))
            else:
                header=json.loads(members[0][1]);header['nativeAuthority'].pop('writerProtection');value=json.dumps(header).encode();members[0][0].size=len(value);members[0]=(members[0][0],value)
            rebuilt=io.BytesIO()
            with tarfile.open(fileobj=rebuilt,mode='w') as target:
                for member,value in members:target.addfile(member,io.BytesIO(value))
            value=rebuilt.getvalue();(directory/'snapshot.tar').write_bytes(value)
            description=json.loads((directory/'description.json').read_text());description.update(sha256=hashlib.sha256(value).hexdigest(),bytes=len(value));(directory/'description.json').write_text(json.dumps(description));artifact=description
        denied=await call(client,cwd,'maintenance.artifact.inspect',{'artifactId':artifact['artifactId'],'sha256':artifact['sha256']},okay=False)
        assert ('hash changed' in denied['message']) if changed!='old-evidence' else ('no supported sealed writer-exclusion evidence' in denied['message'])
        assert not client.events
    finally:await client.close()
