import base64,hashlib,io,json,sqlite3,tarfile
from pathlib import Path
import pytest
from client import Client
from test_admin_lifecycle import CONTEXT

PARTS=['native-import-records','native-preference-receipts']

async def launch(tmp_path):
    cwd=tmp_path/'workspace';cwd.mkdir(exist_ok=True);home=tmp_path/'home';home.mkdir(exist_ok=True)
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'adminHistoryImport':True,'adminVoicePreferences':True,'workerCommand':['/impossible/private-store-worker'],'bundle':'fixture'}
    path=tmp_path/'agent.json';path.write_text(json.dumps(config));client=await Client.launch(path)
    init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['archivePlans']['nativeStores']['parts']==PARTS+['native-maintenance-records','native-retained-archives']
    return client,cwd,config,path

async def call(client,cwd,operation,args=None,auth=None,okay=True):
    response=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':operation,'args':args or {},**({'_meta':{'amplifier.dev/admin':auth}} if auth else {})})
    if okay:assert 'result' in response,response;return response['result']
    assert 'error' in response,response;return response['error']

async def hold(client,cwd):
    result=await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':CONTEXT})
    assert result['result']['acquired'],result
    return {key:result['result']['lease'][key] for key in ('fenceId','commandId','leaseId')}

async def release(client,cwd,auth):
    result=await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'release','args':{**auth,'outcome':'unchanged','proof':{'verified':True,**CONTEXT,'outcome':'unchanged','receiptId':'fixture-settled'}}})
    assert result['result']['released'],result

async def prepare(client,cwd,auth,parts=PARTS,identity='one',okay=True):
    plan=await call(client,cwd,'maintenance.archive.create',{'commandId':identity+'-create','parts':parts,'privateContentReviewed':True},auth)
    return await call(client,cwd,'maintenance.archive.prepare',{'commandId':identity+'-prepare','planId':plan['planId'],'expectedRevision':0},auth,okay)

async def seed(client,cwd):
    raw=b'[{"role":"user","content":"private original evidence"}]'
    begin=await call(client,cwd,'history.import.begin',{'commandId':'import-begin','format':'json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    uid=begin['result']['uploadId']
    await call(client,cwd,'history.import.chunk',{'commandId':'import-chunk','uploadId':uid,'offset':0,'contentBase64':base64.b64encode(raw).decode()})
    view=await call(client,cwd,'voice.configuration')
    saved=await call(client,cwd,'voice.preferences.update',{'commandId':'voice-save','expectedRevision':view['preferencesRevision'],'patch':{'voice':'cedar'}})
    assert saved['status']=='succeeded'
    return uid,raw,saved

@pytest.mark.asyncio
async def test_protocol_owner_images_preserve_original_uploads_receipts_and_wal(tmp_path):
    client,cwd,config,path=await launch(tmp_path);peers=[]
    try:
        uid,raw,saved=await seed(client,cwd)
        # Retain real WAL state without an active write. Backup must incorporate
        # its committed contents, never ship or checkpoint the original WAL.
        paths=[Path(config['appHome'])/'history-imports/imports.sqlite3',Path(config['appHome'])/'voice-preferences/receipts.sqlite3']
        for source in paths:
            con=sqlite3.connect(source,isolation_level=None);con.execute('PRAGMA wal_autocheckpoint=0');con.execute('PRAGMA user_version=7');peers.append(con)
        originals={p:p.read_bytes() for source in paths for p in [source,Path(str(source)+'-wal')]}
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth)
        # A second configured process cannot mutate the fenced stores.
        peer=await Client.launch(path)
        try:
            await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
            denied=await call(peer,cwd,'history.import.begin',{'commandId':'blocked-import','format':'json','bytes':2,'sha256':hashlib.sha256(b'[]').hexdigest()},okay=False)
            assert denied['data']['executed'] is False
        finally:await peer.close()
        assert len(json.dumps(preview))<16000 and raw.decode() not in json.dumps(preview)
        coverage=preview['ownerCoverage']
        import jsonschema
        schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/native-archive-owner-coverage.v1.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(coverage)
        assert not coverage['completeNativeBackup'] and not coverage['completeProductBackup']
        assert {row['owner'] for row in coverage['omitted']}=={'native-maintenance'}
        assert {row['part'] for row in coverage['included']}==set(PARTS)
        page=await call(client,cwd,'maintenance.archive.manifest',{'previewHash':preview['previewHash'],'limit':1})
        assert page['nextCursor'] and len(page['items'])==1 and page['ownerCoverage']==coverage
        lease=await call(client,cwd,'maintenance.acquire',{'commandId':'capture-lease','previewHash':preview['previewHash']},auth)
        args={'commandId':'capture','leaseId':lease['leaseId'],'previewHash':preview['previewHash']}
        snapshot=await call(client,cwd,'maintenance.snapshot',args,auth)
        repeated=await call(client,cwd,'maintenance.snapshot',args,auth)
        assert repeated['artifactId']==snapshot['artifactId'] and repeated['replayed'] is False
        archive_bytes=bytearray();offset=0
        while True:
            chunk=await call(client,cwd,'maintenance.artifact.read',{'artifactId':snapshot['artifactId'],'sha256':snapshot['sha256'],'offset':offset,'maxBytes':4096})
            block=base64.b64decode(chunk['data']);assert len(block)<=4096 and hashlib.sha256(block).hexdigest()==chunk['chunkSha256'];archive_bytes.extend(block)
            if chunk['nextOffset'] is None:break
            offset=chunk['nextOffset']
        assert hashlib.sha256(archive_bytes).hexdigest()==snapshot['sha256']
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as tar:
            assert sorted(tar.getnames())==['manifest.json',*[f'native-owned-stores/{part}.sqlite3' for part in PARTS]]
            for part in PARTS:
                image=tmp_path/(part+'.sqlite3');image.write_bytes(tar.extractfile(f'native-owned-stores/{part}.sqlite3').read())
                with sqlite3.connect(image) as db:
                    assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
                    assert db.execute('PRAGMA journal_mode').fetchone()==('delete',)
                    if part=='native-import-records':assert db.execute('SELECT body FROM uploads WHERE id=?',(uid,)).fetchone()[0]==raw
                    else:assert json.loads(db.execute('SELECT body FROM commands WHERE id=?',('voice-save',)).fetchone()[0])==saved
        assert all(p.read_bytes()==content for p,content in originals.items())
        await call(client,cwd,'maintenance.release',{'leaseId':lease['leaseId']},auth);await release(client,cwd,auth)
        assert not client.events and not (Path(config['home'])/'projects').exists()
        replacement=await Client.launch(path)
        try:
            await replacement.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
            receipt=await call(replacement,cwd,'maintenance.receipt',{'commandId':'capture'})
            assert receipt['receipt']['result']['artifactId']==snapshot['artifactId']
            assert (await call(replacement,cwd,'maintenance.archive.manifest',{'previewHash':preview['previewHash']}))['ownerCoverage']==coverage
        finally:await replacement.close()
    finally:
        for con in peers:con.close()
        await client.close()

@pytest.mark.asyncio
async def test_explicit_absent_stores_are_not_created_and_no_unselected_stores_read(tmp_path):
    client,cwd,config,_=await launch(tmp_path)
    try:
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth,parts=[PARTS[0]])
        assert preview['ownerCoverage']['included'][0]['status']=='missing'
        assert {row['owner'] for row in preview['ownerCoverage']['omitted']}=={'native-voice-preferences','native-maintenance'}
        assert not (Path(config['appHome'])/'history-imports').exists() and not (Path(config['appHome'])/'voice-preferences').exists()
        assert not (Path(config['home'])/'projects').exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_private_store_prepare_requires_held_recovery_and_refuses_changed_review(tmp_path):
    client,cwd,config,_=await launch(tmp_path)
    try:
        await seed(client,cwd)
        denied=await prepare(client,cwd,None,identity='unheld',okay=False);assert 'held recovery' in denied['message']
        auth=await hold(client,cwd);preview=await prepare(client,cwd,auth);await release(client,cwd,auth)
        current=await call(client,cwd,'voice.configuration')
        await call(client,cwd,'voice.preferences.update',{'commandId':'new-save','expectedRevision':current['preferencesRevision'],'patch':{'voice':'marin'}})
        auth=await hold(client,cwd)
        denied=await call(client,cwd,'maintenance.acquire',{'commandId':'changed','previewHash':preview['previewHash']},auth,False)
        assert denied['data']['executed'] is False and 'changed after review' in denied['message']
        assert not (await call(client,cwd,'maintenance.lease'))['active']
    finally:await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('unsafe',['corrupt','symlink','oversized','foreign-schema','orphan-wal','rollback-journal'])
async def test_unsafe_private_stores_refuse_without_source_changes(tmp_path,unsafe):
    client,cwd,config,path=await launch(tmp_path)
    source=Path(config['appHome'])/'history-imports/imports.sqlite3';source.parent.mkdir(parents=True)
    external=tmp_path/'foreign';external.write_bytes(b'private preserve')
    if unsafe=='symlink':source.symlink_to(external)
    elif unsafe=='orphan-wal':Path(str(source)+'-wal').write_bytes(b'leftover')
    elif unsafe=='rollback-journal':
        with sqlite3.connect(source) as db:db.execute('CREATE TABLE commands(id)');db.execute('CREATE TABLE uploads(id)')
        Path(str(source)+'-journal').write_bytes(b'unresolved rollback evidence')
    elif unsafe=='foreign-schema':
        with sqlite3.connect(source) as db:db.execute('CREATE TABLE foreign_data(value)')
    elif unsafe=='oversized':
        source.write_bytes(b'x'*16384);await client.close();config['maintenanceMaxBytes']=8192;path.write_text(json.dumps(config));client=await Client.launch(path)
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    else:source.write_bytes(b'corrupt')
    before={p:p.read_bytes() for p in [source,Path(str(source)+'-wal'),Path(str(source)+'-journal'),external] if p.exists()}
    try:
        auth=await hold(client,cwd);denied=await prepare(client,cwd,auth,parts=[PARTS[0]],okay=False)
        assert denied['data']['settled'] and denied['data']['canonicalFilesChanged']==0
        assert all(p.read_bytes()==content for p,content in before.items())
        receipt=await call(client,cwd,'maintenance.receipt',{'commandId':'one-prepare'})
        assert receipt['receipt']['state']=='failed' and not (Path(config['home'])/'projects').exists()
    finally:await client.close()
