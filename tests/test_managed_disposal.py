import json,os
from pathlib import Path
import pytest
from test_managed_copy import source,target
from amplifier_acp.session_lifecycle import Lifecycle
from amplifier_acp.managed_session import runtime_scope

def prepared(tmp_path,monkeypatch):
    server,config,args,cwd,directory=source(tmp_path,monkeypatch)
    owner=Lifecycle(server,args['sessionId'],str(cwd));(directory/'events.jsonl').write_text('canonical retained event\n')
    preview=owner.perform('managedFiles.preview',{})
    request={'commandId':'dispose','reviewId':preview['reviewId'],'reviewHash':preview['reviewHash'],'expectedHistoryRevision':preview['historyRevision'],'descendants':[]}
    return owner,config,cwd,directory,preview,request

def test_exact_review_removes_only_owned_allocation_preserves_native_and_allows_cold_recovery(tmp_path,monkeypatch):
    owner,config,cwd,directory,review,args=prepared(tmp_path,monkeypatch);before={p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    result=owner.perform('managedFiles.dispose',args);assert result['directoryRemoved'] and result['canonicalFilesDeleted']==0 and result['eventsDeleted']==0
    assert not cwd.parent.exists() and all((directory/name).read_bytes()==body for name,body in before.items())
    reopened=Lifecycle(owner.server,owner.identity,str(cwd));assert reopened.inspect()['historyRevision']==review['historyRevision']
    assert reopened.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt']['status']=='completed'
    with pytest.raises(ValueError,match='fenced'):runtime_scope(str(cwd),owner.identity,owner.server.config)
    selected,allocation=target(tmp_path,config)
    copied=reopened.perform('fork',{'commandId':'recovery-after-disposal','targetSessionId':selected['sessionId'],'expectedHistoryRevision':review['historyRevision'],'mode':'recovery','managedTarget':allocation})
    assert copied['creationConfirmed'] and copied['preservedSource'];assert Path(allocation['cwd']).is_dir()

def test_changed_or_linked_or_history_shaped_files_refuse_without_effect(tmp_path,monkeypatch):
    owner,_,cwd,directory,review,args=prepared(tmp_path,monkeypatch);(cwd/'new.txt').write_text('new unreviewed output')
    with pytest.raises(ValueError,match='changed'):owner.perform('managedFiles.dispose',args)
    assert cwd.is_dir() and owner.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt'] is None
    external=tmp_path/'outside';external.write_text('untouched');(cwd/'link').symlink_to(external)
    with pytest.raises(ValueError,match='link'):owner.perform('managedFiles.preview',{})
    (cwd/'link').unlink();(cwd/'events.jsonl').write_text('possibly other canonical history')
    with pytest.raises(ValueError,match='History-shaped'):owner.perform('managedFiles.preview',{})
    assert external.read_text()=='untouched'

def test_interrupted_stage_remains_unknown_and_no_replay(tmp_path,monkeypatch):
    owner,_,cwd,directory,review,args=prepared(tmp_path,monkeypatch)
    import amplifier_acp.managed_disposal as module
    rename=module.os.rename
    def lost(*values):rename(*values);raise OSError('process lost after rename')
    monkeypatch.setattr(module.os,'rename',lost)
    with pytest.raises(OSError):owner.perform('managedFiles.dispose',args)
    monkeypatch.setattr(module.os,'rename',rename)
    reopened=Lifecycle(owner.server,owner.identity,str(cwd));assert reopened.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt']['status']=='unknown'
    assert reopened.perform('managedFiles.dispose',args)['receipt']['status']=='unknown'
    stages=list((cwd.parent.parent.parent/'.disposals').iterdir());assert len(stages)==1 and (stages[0]/'files/source.txt').exists()
    assert (directory/'transcript.jsonl').exists() and (directory/'events.jsonl').exists()

def test_actual_killed_writer_keeps_unknown_stage_and_recreated_cwd_never_reactivates(tmp_path,monkeypatch):
    import subprocess,sys
    owner,config,cwd,directory,review,args=prepared(tmp_path,monkeypatch)
    payload=tmp_path/'dispose-request.json';payload.write_text(json.dumps({'config':owner.server.config,'sessionId':owner.identity,'cwd':str(cwd),'args':args}))
    code="""import json,os,signal,sys
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle
import amplifier_acp.managed_disposal as disposal
p=json.load(open(sys.argv[1]));owner=Lifecycle(Server(p['config'],None),p['sessionId'],p['cwd']);rename=disposal.os.rename
def killed(*args,**kwargs):
 rename(*args,**kwargs);os.kill(os.getpid(),signal.SIGKILL)
disposal.os.rename=killed
owner.perform('managedFiles.dispose',p['args'])
"""
    child=subprocess.run([sys.executable,'-I','-c',code,str(payload)],capture_output=True,timeout=30)
    assert child.returncode<0 and not cwd.exists()
    reopened=Lifecycle(owner.server,owner.identity,str(cwd));receipt=reopened.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt'];assert receipt['status']=='unknown'
    assert reopened.perform('managedFiles.dispose',args)['receipt']==receipt
    stage=next((cwd.parent.parent.parent/'.disposals').iterdir());assert (stage/'files/source.txt').exists()
    cwd.mkdir(parents=True)
    with pytest.raises(ValueError,match='fenced'):runtime_scope(str(cwd),owner.identity,owner.server.config)
    assert reopened.inspect()['location']['executionAvailable'] is False
    assert (directory/'events.jsonl').read_text()=='canonical retained event\n'

def test_review_expiry_native_proof_and_live_descendant_are_independent_guards(tmp_path,monkeypatch):
    import subprocess,sys,uuid
    import amplifier_acp.managed_disposal as module
    from amplifier_foundation.session.history import SessionHistoryStore
    owner,_,cwd,directory,review,args=prepared(tmp_path,monkeypatch)
    child_id=str(uuid.uuid4());child_dir=owner.server.directory(str(cwd),child_id)
    SessionHistoryStore(child_dir,session_id=child_id).save([{'role':'user','content':'child retained'}],{'session_id':child_id,'parent_id':owner.identity,'status':'idle'})
    args['descendants']=[{'sessionId':child_id,'cwd':str(cwd),'parentSessionId':owner.identity}]
    code="from amplifier_foundation.session import SharedSessionStore;import sys;h=SharedSessionStore(sys.argv[1],sys.argv[2]).acquire(app='independent-child');print('owned',flush=True);sys.stdin.read();h.release()"
    process=subprocess.Popen([sys.executable,'-I','-c',code,str(cwd),child_id],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='owned'
        with pytest.raises(ValueError,match='descendant'):owner.perform('managedFiles.dispose',args)
        assert owner.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt'] is None
    finally:process.communicate(timeout=10)
    with monkeypatch.context() as change:
        change.setattr(module.time,'time',lambda:review['expiresAt']+1)
        with pytest.raises(ValueError,match='expired'):owner.perform('managedFiles.dispose',args)
    completed=owner.perform('managedFiles.dispose',args);assert completed['familyGuard']['count']==1
    # Both original and child checkpoint identities survive missing cwd, under the same locks.
    with owner.own():
        from amplifier_acp.lifecycle_family import own_descendants
        with own_descendants(owner,args['descendants']) as family:assert family['ownersHeld']
    proof=owner.app/'managed-creation.json';body=json.loads(proof.read_text());body['marker']['owner']='copied-fake-owner';proof.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='authoritative proof'):Lifecycle(owner.server,owner.identity,str(cwd))

def test_swapped_staged_ancestor_cannot_traverse_outside_and_unreviewed_files_stay(tmp_path,monkeypatch):
    import amplifier_acp.managed_disposal as module
    owner,_,cwd,_,_,_=prepared(tmp_path,monkeypatch)
    nested=cwd/'nested';nested.mkdir();(nested/'keep.txt').write_text('owned')
    external=tmp_path/'external';external.mkdir();(external/'keep.txt').write_text('external remains')
    review=owner.perform('managedFiles.preview',{});args={'commandId':'swapped','reviewId':review['reviewId'],'reviewHash':review['reviewHash'],'expectedHistoryRevision':review['historyRevision'],'descendants':[]}
    original=module.remove_reviewed
    def swap(target,entries):
        (target/'files/nested').rename(target/'files/moved');(target/'files/nested').symlink_to(external,target_is_directory=True)
        return original(target,entries)
    monkeypatch.setattr(module,'remove_reviewed',swap)
    with pytest.raises(OSError):owner.perform('managedFiles.dispose',args)
    assert (external/'keep.txt').read_text()=='external remains'
    assert owner.perform('managedFiles.receipt',{'commandId':'swapped'})['receipt']['status']=='unknown'
    stage=next((cwd.parent.parent.parent/'.disposals').iterdir());assert (stage/'files/moved/keep.txt').exists()

@pytest.mark.asyncio
async def test_cancelled_rpc_joins_actual_disposal_before_releasing_ownership(tmp_path,monkeypatch):
    import asyncio,threading
    import amplifier_acp.managed_disposal as module
    owner,_,cwd,_,_,args=prepared(tmp_path,monkeypatch);entered=threading.Event();finish=threading.Event();original=module.remove_reviewed
    def wait(target,entries):entered.set();assert finish.wait(10);return original(target,entries)
    monkeypatch.setattr(module,'remove_reviewed',wait)
    task=asyncio.create_task(module.dispatch(owner,'managedFiles.dispose',args))
    assert await asyncio.to_thread(entered.wait,5);task.cancel();await asyncio.sleep(.02);assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not cwd.exists() and owner.perform('managedFiles.receipt',{'commandId':'dispose'})['receipt']['status']=='completed'

def test_managed_disposal_request_schema_and_capability_are_explicit(tmp_path,monkeypatch):
    from jsonschema import Draft202012Validator
    owner,_,cwd,_,_,args=prepared(tmp_path,monkeypatch)
    schema=json.loads((Path(__file__).parents[1]/'schemas/native-managed-files.v1.json').read_text());Draft202012Validator.check_schema(schema);validator=Draft202012Validator(schema)
    request={'sessionId':owner.identity,'cwd':str(cwd),'operation':'managedFiles.dispose','args':args};validator.validate(request)
    assert list(validator.iter_errors({**request,'args':{**args,'path':'/arbitrary'}}))
    assert list(validator.iter_errors({**request,'args':{**args,'descendants':[{'sessionId':'x','cwd':'/somewhere','parentSessionId':owner.identity}]*101}}))
