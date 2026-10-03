import json,os,subprocess,sys,uuid
from pathlib import Path
import pytest
from amplifier_foundation.session.history import SessionHistoryStore
from test_managed_disposal import prepared


def child(owner,cwd,parent=None):
    identity=str(uuid.uuid4());directory=owner.server.directory(str(cwd),identity)
    SessionHistoryStore(directory,session_id=identity).save([{'role':'user','content':'Preserved child'}],{'session_id':identity,'parent_id':parent or owner.identity,'working_dir':str(cwd),'status':'idle'})
    return directory,{'sessionId':identity,'cwd':str(cwd),'parentSessionId':parent or owner.identity}


def test_empty_catalog_family_cannot_hide_canonical_child_and_complete_family_succeeds(tmp_path,monkeypatch):
    owner,_,cwd,directory,_,args=prepared(tmp_path,monkeypatch);one,hint=child(owner,cwd);two,second=child(owner,cwd,hint['sessionId'])
    original={path:path.read_bytes() for folder in [directory,one,two] for path in folder.iterdir() if path.is_file()}
    for hints in [[],[hint],[hint,{**second,'parentSessionId':owner.identity}]]:
        with pytest.raises(ValueError,match='family differs') as error:owner.perform('managedFiles.dispose',{**args,'descendants':hints})
        assert error.value.data['executed'] is False and cwd.is_dir()
        assert owner.perform('managedFiles.receipt',{'commandId':args['commandId']})['receipt'] is None
    result=owner.perform('managedFiles.dispose',{**args,'descendants':[second,hint]})
    assert result['familyGuard']['count']==2
    proof=result['familyGuard']['selectedProject'];assert proof['metadataRecords']==3 and proof['nativeHomeWritersExcluded'] and proof['externalWriterCoverage'] is False
    assert all(path.read_bytes()==value for path,value in original.items()) and not cwd.exists()


@pytest.mark.parametrize('damage',['missing','oversized','symbolic','foreign-cwd','wrong-id','independent','cycle'])
def test_unverifiable_selected_family_refuses_before_any_effect(tmp_path,monkeypatch,damage):
    owner,_,cwd,_,_,args=prepared(tmp_path,monkeypatch);directory,hint=child(owner,cwd);metadata=directory/'metadata.json';value=json.loads(metadata.read_text())
    if damage=='missing':metadata.unlink()
    elif damage=='oversized':metadata.write_text(' '*32769)
    elif damage=='symbolic':metadata.rename(directory/'original');metadata.symlink_to(directory/'original')
    else:
        if damage=='foreign-cwd':value['working_dir']=str(tmp_path)
        elif damage=='wrong-id':value['session_id']=str(uuid.uuid4())
        elif damage=='independent':value['parent_id']=None
        else:value['parent_id']=hint['sessionId'];hint['parentSessionId']=hint['sessionId']
        metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError) as error:owner.perform('managedFiles.dispose',{**args,'descendants':[hint]})
    assert error.value.data['executed'] is False and cwd.is_dir()
    assert owner.perform('managedFiles.receipt',{'commandId':args['commandId']})['receipt'] is None


def test_inventory_budget_and_changed_metadata_are_known_no_effect(tmp_path,monkeypatch):
    import amplifier_acp.managed_family as family
    owner,_,cwd,_,_,args=prepared(tmp_path,monkeypatch);directory,hint=child(owner,cwd)
    with monkeypatch.context() as bounded:
        bounded.setattr(family,'MAX_SESSIONS',1)
        with pytest.raises(ValueError,match='bound'):owner.perform('managedFiles.dispose',args)
    original=family.verify;calls=0
    def change(owner,hints):
        nonlocal calls
        result=original(owner,hints);calls+=1
        if calls==1:
            path=directory/'metadata.json';metadata=json.loads(path.read_text());metadata['name']='changed';path.write_text(json.dumps(metadata))
        return result
    monkeypatch.setattr(family,'verify',change)
    with pytest.raises(ValueError,match='changed'):owner.perform('managedFiles.dispose',{**args,'descendants':[hint]})
    assert cwd.is_dir() and owner.perform('managedFiles.receipt',{'commandId':args['commandId']})['receipt'] is None


def test_native_home_gate_excludes_new_workers_and_creation_through_entire_disposal(tmp_path,monkeypatch):
    import amplifier_acp.managed_family as family
    from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
    owner,_,cwd,_,_,args=prepared(tmp_path,monkeypatch)
    with Gate(owner.server.config):
        with pytest.raises(MaintenanceBusy):owner.perform('managedFiles.dispose',args)
    original=family.verify;attempts=[]
    def competing(owner,hints):
        code="import json,sys;from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy\ntry:\n with Gate(json.loads(sys.argv[1])):raise AssertionError('A concurrent native writer entered')\nexcept MaintenanceBusy:print('excluded')"
        result=subprocess.run([sys.executable,'-I','-c',code,json.dumps(owner.server.config)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0 and result.stdout.strip()=='excluded',result.stderr;attempts.append(result.stdout)
        return original(owner,hints)
    monkeypatch.setattr(family,'verify',competing)
    assert owner.perform('managedFiles.dispose',args)['directoryRemoved'] and len(attempts)==2
    with Gate(owner.server.config):pass
