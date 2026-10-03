import json,os,shutil,time,uuid
from pathlib import Path
import pytest
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle
from amplifier_acp import retired_copy

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writer-leases'))
    cwd=tmp_path/'workspace';cwd.mkdir();root=tmp_path/'retired';root.mkdir();identity=str(uuid.uuid4())
    config={'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app'),'retiredSessionCopyRoots':[{'id':'old-app','directory':str(root),'format':'unified-session-store-v1','writerState':'retired'}]}
    server=Server(config,None);native=server.directory(str(cwd),identity);native.mkdir(parents=True)
    rows=[{'role':'user','content':'Retained request'},{'role':'assistant','content':'Retained answer'}]
    (native/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows));(native/'metadata.json').write_text(json.dumps({'session_id':identity,'updated_at':1,'name':'Preserved'}));(native/'events.jsonl').write_bytes(b'canonical events\n')
    copy=root/identity;copy.mkdir()
    for name in ('transcript.jsonl','metadata.json'):shutil.copyfile(native/name,copy/name)
    (copy/'events.jsonl').write_bytes(b'private events remain\n');(copy/'notes.txt').write_text('unique private note')
    return Lifecycle(server,identity,str(cwd)),copy,native,rows

def preview(owner):return owner.perform('legacyCopy.preview',{'rootId':'old-app'})
def request(view,command='remove-duplicates'):return {key:view[key] for key in ('previewId','previewHash')}|{'commandId':command,'expectedHistoryRevision':view['historyRevision']}

def test_exact_duplicates_removed_with_canonical_and_unique_private_files_preserved(fixture):
    owner,copy,native,rows=fixture;before={p.name:p.read_bytes() for p in native.iterdir()}
    inventory=owner.perform('legacyCopy.inventory',{});assert inventory['roots']==[{'id':'old-app','format':'unified-session-store-v1','writerState':'retired'}] and inventory['canonicalFilesRead'] is False
    view=preview(owner);assert len(view['files'])==2 and view['legacyWriterCoverage']=='launcher-attested-retired'
    assert str(copy.parent) not in json.dumps(view);assert {r['name'] for r in view['retained']}=={'events.jsonl','notes.txt'}
    result=owner.perform('legacyCopy.purge',request(view));assert len(result['removed'])==2 and result['canonicalFilesDeleted']==result['eventsDeleted']==0
    assert copy.is_dir() and (copy/'events.jsonl').read_bytes()==b'private events remain\n' and (copy/'notes.txt').read_text()=='unique private note'
    assert before=={p.name:p.read_bytes() for p in native.iterdir()}
    reopened=Lifecycle(owner.server,owner.identity,owner.cwd)
    assert reopened.perform('legacyCopy.purge',request(view))['receipt']==result['receipt']|{'signature':retired_copy.fingerprint({'operation':'legacyCopy.purge','args':request(view)})}
    with pytest.raises(ValueError,match='conflicts'):reopened.perform('legacyCopy.purge',request(view)|{'previewHash':'0'*64})

def test_checkpoint_requires_all_messages_and_metadata_preserved(fixture):
    owner,copy,native,rows=fixture
    checkpoint={'version':1,'messages':rows,'metadata':{'session_id':owner.identity,'name':'Preserved'}}
    (copy/'checkpoint.json').write_text(json.dumps(checkpoint));assert 'checkpoint.json' in [r['name'] for r in preview(owner)['files']]
    checkpoint['metadata']['private_app_value']='not saved in canonical authority';(copy/'checkpoint.json').write_text(json.dumps(checkpoint))
    view=preview(owner);assert 'checkpoint.json' not in [r['name'] for r in view['files']]
    assert {'name':'checkpoint.json','reason':'complete-canonical-copy-unproven'} in view['retained']
    owner.perform('legacyCopy.purge',request(view));assert (copy/'checkpoint.json').exists()

@pytest.mark.parametrize('change',['source','canonical','directory','expiry'])
def test_changed_or_expired_review_refuses_before_receipt_or_file_effect(fixture,change):
    owner,copy,native,rows=fixture;view=preview(owner)
    if change=='source':(copy/'transcript.jsonl').write_text('private newer content')
    if change=='canonical':(native/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'new canonical turn'})+'\n')
    if change=='directory':copy.rename(copy.with_name('original-copy'));copy.mkdir()
    if change=='expiry':
        path=owner.app/'retired-copy-reviews'/(view['previewId']+'.json');data=json.loads(path.read_text());data['expiresAt']=time.time()-1;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='changed|expired'):owner.perform('legacyCopy.purge',request(view))
    assert owner.saved('remove-duplicates') is None

@pytest.mark.parametrize('kind',['symlink','hardlink','unique','events','unknown'])
def test_nonduplicate_or_unrecognized_files_never_removed(fixture,kind):
    owner,copy,native,rows=fixture;path=copy/'transcript.jsonl'
    if kind=='symlink':path.unlink();path.symlink_to(native/'transcript.jsonl')
    if kind=='hardlink':path.unlink();os.link(native/'transcript.jsonl',path)
    if kind=='hardlink':
        with pytest.raises(ValueError,match='hard links'):preview(owner)
        assert path.exists() and owner.saved('remove-duplicates') is None;return
    if kind=='unique':path.write_text('unique private content')
    if kind=='events':(copy/'events.jsonl').write_bytes((native/'events.jsonl').read_bytes());path=copy/'events.jsonl'
    if kind=='unknown':path=copy/'new-owner.sqlite3';path.write_bytes(b'unrecognized authority')
    before=path.read_bytes();view=preview(owner);assert path.name not in [r['name'] for r in view['files']]
    owner.perform('legacyCopy.purge',request(view));assert path.read_bytes()==before

def test_no_recursive_partial_scan_or_configured_current_root(fixture):
    owner,copy,native,rows=fixture
    for count in range(257):(copy/('extra-'+str(count))).touch()
    with pytest.raises(ValueError,match='256'):preview(owner)
    declaration=owner.server.config['retiredSessionCopyRoots'][0]
    declaration['directory']=str(native.parent)
    with pytest.raises(ValueError,match='overlap'):owner.perform('legacyCopy.inventory',{})
    declaration['directory']=str(copy.parent);declaration['writerState']='unknown'
    with pytest.raises(ValueError,match='declaration'):preview(owner)

def test_unknown_after_staging_preserves_remaining_evidence_and_never_retries(fixture,monkeypatch):
    owner,copy,native,rows=fixture;view=preview(owner);before=(native/'transcript.jsonl').read_bytes();original=os.unlink
    def fail(path,*args,**kwargs):
        if path=='metadata.json':raise OSError('simulated loss after stage')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'unlink',fail)
    with pytest.raises(OSError):owner.perform('legacyCopy.purge',request(view))
    saved=owner.saved('remove-duplicates');assert saved['status']=='unknown'
    staged=next(copy.glob('.retired-copy-*'));assert (staged/'metadata.json').exists()
    monkeypatch.setattr(os,'unlink',original)
    assert owner.perform('legacyCopy.purge',request(view))['receipt']['status']=='unknown'
    assert staged.exists() and (native/'transcript.jsonl').read_bytes()==before
    with pytest.raises(ValueError,match='unresolved'):owner.perform('legacyCopy.purge',request(preview(owner),'new-command'))

def test_process_death_after_stage_retains_unknown_receipt_and_files(fixture):
    import subprocess,sys
    owner,copy,native,rows=fixture;view=preview(owner);args=request(view,'process-death')
    script="""import json,os,sys
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle
owner=Lifecycle(Server(json.loads(sys.argv[1]),None),sys.argv[2],sys.argv[3]);original=os.unlink
def crash(name,*args,**kwargs):
 if name=='metadata.json':os._exit(72)
 return original(name,*args,**kwargs)
os.unlink=crash
owner.perform('legacyCopy.purge',json.loads(sys.argv[4]))
"""
    result=subprocess.run([sys.executable,'-I','-c',script,json.dumps(owner.server.config),owner.identity,owner.cwd,json.dumps(args)],timeout=30)
    assert result.returncode==72 and owner.saved('process-death')['status']=='unknown'
    assert next(copy.glob('.retired-copy-*')).joinpath('metadata.json').exists()
    assert owner.perform('legacyCopy.purge',args)['receipt']['status']=='unknown'
    assert (native/'transcript.jsonl').exists() and (copy/'events.jsonl').exists()

@pytest.mark.asyncio
async def test_public_passive_wire_never_starts_native_worker(tmp_path):
    from test_session_lifecycle import prepared,prompt,action,initialize
    from client import Client
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'Preserved real native turn','turn1');await client.close()
    native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));root=tmp_path/'retired';copy=root/sid;copy.mkdir(parents=True)
    for name in ('metadata.json','transcript.jsonl'):shutil.copyfile(native/name,copy/name)
    value=json.loads(config.read_text());value['workerCommand']=['/impossible-worker'];value['retiredSessionCopyRoots']=[{'id':'old-app','directory':str(root),'format':'unified-session-store-v1','writerState':'retired'}];config.write_text(json.dumps(value))
    client=await Client.launch(config)
    try:
        init=await initialize(client);assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['sessionLifecycle']['retiredCopies']['version']==1
        view=(await action(client,sid,cwd,'legacyCopy.preview',{'rootId':'old-app'}))['result'];result=await action(client,sid,cwd,'legacyCopy.purge',request(view));assert result['result']['receipt']['status']=='completed',result
        receipt=await action(client,sid,cwd,'receipt',{'commandId':'remove-duplicates'});assert receipt['result']['receipt']['status']=='completed'
        assert native.is_dir() and (native/'transcript.jsonl').exists() and not (copy/'transcript.jsonl').exists()
    finally:await client.close()
