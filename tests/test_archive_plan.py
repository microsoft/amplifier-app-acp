import base64,hashlib,json,tarfile
from pathlib import Path
import pytest
from test_maintenance import seeded,initialized,admin

async def plan(call,*,sessions=(),workspaces=(),parts=('session-history',),credentials=False):
    created=await call('maintenance.archive.create',{'commandId':'create-plan','parts':list(parts),'privateContentReviewed':True,'includeCredentials':credentials,'credentialsReviewed':credentials})
    if sessions or workspaces:
        added=await call('maintenance.archive.add',{'commandId':'select-plan','planId':created['planId'],'expectedRevision':0,'sessions':list(sessions),'workspaces':list(workspaces)})
    else:added=created
    return await call('maintenance.archive.prepare',{'commandId':'prepare-plan','planId':created['planId'],'expectedRevision':added['revision']})

@pytest.mark.asyncio
async def test_actual_native_archive_keeps_large_manifest_paged_and_configuration_scoped(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    settings.update(maintenanceExternalWriters='stopped',workerCommand=['/impossible/archive-worker']);config.write_text(json.dumps(settings))
    home=Path(settings['home']);routing=home/'routing';routing.mkdir()
    for index in range(2200):(routing/f'row-{index:04}.yaml').write_text(f'fixture: {index}\n')
    (home/'keys.env').write_text('SYNTHETIC_PRIVATE_KEY=reviewed-fixture-no-account\n')
    local=cwd/'.amplifier';local.mkdir(exist_ok=True);(local/'settings.local.yaml').write_text('fixture: local\n');(cwd/'unselected.txt').write_text('preserve workspace')
    foreign=home/'projects/unselected/sessions/foreign';foreign.mkdir(parents=True);(foreign/'events.jsonl').write_text('UNSELECTED')
    client,init=await initialized(config);call=admin(client,cwd)
    try:
        assert init['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['archivePlans']['pagedManifest']
        preview=await plan(call,sessions=[{'nativeSessionId':sid,'cwd':str(cwd)}],workspaces=[str(cwd)],parts=['session-history','session-state','shared-configuration','workspace-configuration'],credentials=True)
        assert preview['inventory']['files']>2200 and len(json.dumps(preview))<8192
        assert 'reviewed-fixture-no-account' not in json.dumps(preview)
        page=await call('maintenance.archive.manifest',{'previewHash':preview['previewHash'],'limit':7})
        assert len(page['items'])==7 and page['nextCursor']
        assert not any('source' in row or 'stamp' in row for row in page['items'])
        assert str(home) not in json.dumps(page)
        held=await call('maintenance.acquire',{'commandId':'archive-lease','previewHash':preview['previewHash'],'ttlSeconds':120})
        captured=await call('maintenance.snapshot',{'commandId':'archive-snapshot','leaseId':held['leaseId'],'previewHash':preview['previewHash']})
        destination=tmp_path/'download.tar';offset=0;digest=hashlib.sha256()
        with destination.open('wb') as stream:
            while True:
                chunk=await call('maintenance.artifact.read',{'artifactId':captured['artifactId'],'sha256':captured['sha256'],'offset':offset,'maxBytes':32768})
                raw=base64.b64decode(chunk['data']);assert hashlib.sha256(raw).hexdigest()==chunk['chunkSha256'];stream.write(raw);digest.update(raw)
                if chunk['nextOffset'] is None:break
                offset=chunk['nextOffset']
        assert digest.hexdigest()==captured['sha256']
        with tarfile.open(destination) as archive:
            manifest=json.load(archive.extractfile('manifest.json'));assert manifest['summary']['previewHash']==preview['previewHash']
            names=archive.getnames();assert f'history/{sid}/transcript.jsonl' in names and 'shared-configuration/keys.env' in names
            assert not any('unselected' in name for name in names)
            for row in manifest['entries']:
                if row['status']=='included':assert hashlib.sha256(archive.extractfile(row['path']).read()).hexdigest()==row['sha256']
        repeat=await call('maintenance.snapshot',{'commandId':'archive-snapshot','leaseId':held['leaseId'],'previewHash':preview['previewHash']})
        assert repeat['artifactId']==captured['artifactId'] and repeat['replayed'] is False
        await call('maintenance.release',{'leaseId':held['leaseId']})
        assert (foreign/'events.jsonl').read_text()=='UNSELECTED' and (cwd/'unselected.txt').read_text()=='preserve workspace'
        assert not client.events
    finally:await client.close()
    replacement,_=await initialized(config)
    try:
        resumed=await admin(replacement,cwd)('maintenance.archive.manifest',{'previewHash':preview['previewHash'],'cursor':page['nextCursor'],'limit':7})
        assert len(resumed['items'])==7 and resumed['items'][0]['path']>page['nextCursor']
    finally:await replacement.close()

@pytest.mark.asyncio
async def test_archive_changed_source_and_selection_are_refused_without_replay(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    settings.update(maintenanceExternalWriters='stopped');config.write_text(json.dumps(settings));client,_=await initialized(config);call=admin(client,cwd)
    try:
        preview=await plan(call,workspaces=[str(cwd)],parts=['workspace-configuration'])
        local=cwd/'.amplifier';local.mkdir(exist_ok=True);(local/'settings.local.yaml').write_text('new: state\n')
        denied=await call('maintenance.acquire',{'commandId':'stale-lease','previewHash':preview['previewHash']},okay=False)
        assert denied['data']['executed'] is False
        receipt=await call('maintenance.receipt',{'commandId':'stale-lease'});assert receipt['receipt']['state']=='refused'
        sealed=await call('maintenance.archive.add',{'commandId':'cannot-append','planId':preview['archivePlanId'],'expectedRevision':1,'workspaces':[str(cwd)]},okay=False)
        assert 'sealed' in sealed['message']
        assert (local/'settings.local.yaml').read_text()=='new: state\n'
    finally:await client.close()

@pytest.mark.asyncio
async def test_archive_configuration_requires_stopped_external_authority_and_explicit_credentials(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close();client,_=await initialized(config);call=admin(client,cwd)
    try:
        denied=await call('maintenance.archive.create',{'commandId':'denied-key','parts':['shared-configuration'],'privateContentReviewed':True,'includeCredentials':True},okay=False)
        assert 'credential review' in denied['message']
        created=await call('maintenance.archive.create',{'commandId':'configuration-plan','parts':['shared-configuration'],'privateContentReviewed':True})
        denied=await call('maintenance.archive.prepare',{'commandId':'unproven-source','planId':created['planId'],'expectedRevision':0},okay=False)
        assert 'stopped external writers' in denied['message']
        assert not (await call('maintenance.lease'))['active']
    finally:await client.close()

@pytest.mark.asyncio
async def test_archive_batches_revision_and_seal_detect_tampering_without_source_scan(tmp_path):
    import sqlite3
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    settings.update(maintenanceExternalWriters='stopped');config.write_text(json.dumps(settings));client,_=await initialized(config);call=admin(client,cwd)
    try:
        created=await call('maintenance.archive.create',{'commandId':'create-batches','parts':['session-history'],'privateContentReviewed':True})
        args={'commandId':'batch-1','planId':created['planId'],'expectedRevision':0,'sessions':[{'nativeSessionId':sid,'cwd':str(cwd)}]}
        added=await call('maintenance.archive.add',args);assert added['revision']==1
        repeated=await call('maintenance.archive.add',args);assert repeated['revision']==1 and repeated['replayed'] is False
        stale=await call('maintenance.archive.add',{**args,'commandId':'stale-batch'},okay=False);assert stale['data']['settled'] is True and stale['data']['canonicalFilesChanged']==0
        second=await call('maintenance.archive.add',{**args,'commandId':'batch-2','expectedRevision':1});assert second['sessions']==1 and second['revision']==2
        preview=await call('maintenance.archive.prepare',{'commandId':'seal-batches','planId':created['planId'],'expectedRevision':2})
        dbpath=Path(settings['appHome'])/'maintenance/plans'/created['planId']/'manifest.sqlite3'
        with sqlite3.connect(dbpath) as db:
            query=db.execute('EXPLAIN QUERY PLAN SELECT path,body FROM entries WHERE path>? ORDER BY path LIMIT ?',('',8)).fetchall()
            assert any('INDEX' in row[3] and 'path>?' in row[3] for row in query)
        # Paging immutable inventory does not enumerate or read sources again.
        from amplifier_acp.native.session_files import project_slug
        metadata=Path(settings['home'])/'projects'/project_slug(str(cwd))/'sessions'/sid/'metadata.json'
        original=metadata.read_bytes();metadata.write_bytes(original+b' ')
        assert (await call('maintenance.archive.manifest',{'previewHash':preview['previewHash'],'limit':2}))['items']
        metadata.write_bytes(original)
        with sqlite3.connect(dbpath) as db:db.execute('DELETE FROM entries WHERE path=(SELECT min(path) FROM entries)')
        corrupted=await call('maintenance.archive.manifest',{'previewHash':preview['previewHash']},okay=False);assert 'manifest file changed' in corrupted['message']
    finally:await client.close()

@pytest.mark.asyncio
async def test_archive_rejects_untrusted_workspace_and_special_sources(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    settings.update(maintenanceExternalWriters='stopped');config.write_text(json.dumps(settings));client,_=await initialized(config);call=admin(client,cwd)
    try:
        created=await call('maintenance.archive.create',{'commandId':'create-unsafe','parts':['workspace-configuration'],'privateContentReviewed':True})
        outsider=tmp_path/'outside';outsider.mkdir()
        denied=await call('maintenance.archive.add',{'commandId':'denied-workspace','planId':created['planId'],'expectedRevision':0,'workspaces':[str(outsider)]},okay=False)
        assert denied['data']['canonicalFilesChanged']==0
        added=await call('maintenance.archive.add',{'commandId':'select-safe-workspace','planId':created['planId'],'expectedRevision':0,'workspaces':[str(cwd)]})
        local=cwd/'.amplifier';local.mkdir(exist_ok=True);private=outsider/'private.yaml';private.write_text('never read this');(local/'settings.local.yaml').symlink_to(private)
        denied=await call('maintenance.archive.prepare',{'commandId':'reject-symlink','planId':created['planId'],'expectedRevision':added['revision']},okay=False)
        assert denied['data']['settled'] is True and denied['data']['canonicalFilesChanged']==0
        assert (await call('maintenance.receipt',{'commandId':'reject-symlink'}))['receipt']['state']=='failed'
        assert private.read_text()=='never read this' and not (await call('maintenance.lease'))['active']
    finally:await client.close()

@pytest.mark.asyncio
async def test_independent_admin_processes_cannot_duplicate_commands_or_selection_revision(tmp_path):
    import asyncio,sqlite3
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    settings.update(maintenanceExternalWriters='stopped');config.write_text(json.dumps(settings))
    first,_=await initialized(config);second,_=await initialized(config);a,b=admin(first,cwd),admin(second,cwd)
    try:
        await a('maintenance.receipt',{'commandId':'warm-first'});await b('maintenance.receipt',{'commandId':'warm-second'})
        request={'commandId':'same-create','parts':['session-history'],'privateContentReviewed':True}
        results=await asyncio.gather(a('maintenance.archive.create',request),b('maintenance.archive.create',request))
        saved=await a('maintenance.receipt',{'commandId':'same-create'});assert saved['receipt']['state']=='succeeded'
        plan=saved['receipt']['result']['planId'];assert all(row.get('planId',plan)==plan for row in results)
        directory=Path(settings['appHome'])/'maintenance/plans';assert len(list(directory.iterdir()))==1
        requests=[{'commandId':command,'planId':plan,'expectedRevision':0,'sessions':[{'nativeSessionId':sid,'cwd':str(cwd)}]} for command in ['client-a','client-b']]
        replies=await asyncio.gather(*(client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'maintenance.archive.add','args':args}) for client,args in zip([first,second],requests)))
        assert sum(row.get('result',{}).get('revision')==1 for row in replies)==1
        with sqlite3.connect(directory/plan/'manifest.sqlite3') as db:
            assert json.loads(db.execute('SELECT body FROM meta').fetchone()[0])['revision']==1
            assert db.execute('SELECT count(*) FROM selections').fetchone()[0]==1
    finally:await first.close();await second.close()
