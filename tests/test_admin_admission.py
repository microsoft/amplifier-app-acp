import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_acp.admin_lifecycle import AdminLifecycle,AdminFenced,AdminGate

CONTEXT={'fenceId':'original-fence','commandId':'original-command','purpose':'distribution-update','instanceId':'unchanged-instance','dataScope':'owned-data'}
PROOF={**CONTEXT,'kind':'distribution-admission-abort','verified':True,'receiptId':'authenticated-host-receipt'}

def owner(tmp_path):
    return SimpleNamespace(config={'home':str(tmp_path/'native'),'appHome':str(tmp_path/'app')},background_gates={},on_idle=None)
def life(tmp_path):return AdminLifecycle(owner(tmp_path))
def abort(value,proof=None,context=None):return value.perform('abortAdmission',{**(context or CONTEXT),'proof':PROOF if proof is None else proof})

def test_acquired_restart_retains_intake_and_same_abort_receipt(tmp_path):
    original=life(tmp_path);held=original.perform('acquire',CONTEXT);assert held['acquired']
    original.close() # Process exit drops OS ownership, not durable evidence.
    replacement=life(tmp_path)
    assert replacement.inspect()['intakeClosed'] and replacement.fence is None
    with pytest.raises(AdminFenced):replacement.enter('modules.save')
    with pytest.raises(AdminFenced):replacement.perform('acquire',CONTEXT)
    result=abort(replacement)
    assert result['status']=='released' and set(result)=={'ownerId','status','receiptId','commandId','fenceId','instanceId','dataScope'}
    assert not replacement.inspect()['intakeClosed']
    assert abort(life(tmp_path))==result # Lost abort reply, new process.
    with pytest.raises(AdminFenced):abort(life(tmp_path),{**PROOF,'receiptId':'different-proof'})
    assert not (tmp_path/'native'/'projects').exists()

def test_pending_before_os_gate_is_released_never_not_acquired(tmp_path):
    original=life(tmp_path);original.admission.begin(CONTEXT)
    assert abort(life(tmp_path))['status']=='released'

def test_explicit_refusal_remains_not_acquired_after_restart(tmp_path):
    original=life(tmp_path);ticket=original.enter('modules.save')
    refused=original.perform('acquire',CONTEXT);assert refused['executed'] is False
    with pytest.raises(AdminFenced):abort(original)
    original.leave(ticket);original.close()
    assert abort(life(tmp_path))['status']=='not-acquired'

def test_original_node_no_dispatch_refusal_and_no_synthetic_abort(tmp_path):
    original=life(tmp_path)
    with pytest.raises(ValueError):abort(original)
    assert not (tmp_path/'app'/'maintenance'/'receipts.sqlite3').exists()
    assert original.perform('refuseAdmission',CONTEXT)['reason']=='node-no-dispatch'
    assert abort(life(tmp_path))['status']=='not-acquired'

@pytest.mark.parametrize('proof',[
    {'kind':'admission-refused'}, {**PROOF,'verified':1}, {**PROOF,'kind':'service-lifecycle'},
    {**PROOF,'receiptId':''}, {**PROOF,'extra':True}, *[{**PROOF,key:'different'} for key in CONTEXT],
])
def test_wrong_proof_preserves_original_intake(tmp_path,proof):
    original=life(tmp_path);original.perform('acquire',CONTEXT)
    try:
        with pytest.raises(AdminFenced):abort(original,proof)
        assert original.inspect()['intakeClosed']
    finally:original.close()

def test_pending_effect_external_writer_and_other_fence_block(tmp_path):
    original=life(tmp_path);original.admission.begin(CONTEXT)
    external=AdminGate(original.owner.config)
    try:
        with pytest.raises(AdminFenced):abort(original)
    finally:external.release()
    other={**CONTEXT,'fenceId':'other'}
    with pytest.raises(ValueError):abort(original,{**PROOF,**other},other)
    assert original.inspect()['intakeClosed']
    assert abort(original)['status']=='released'

def test_lost_final_abort_transaction_retains_receipt_id_and_intake(tmp_path,monkeypatch):
    original=life(tmp_path);original.perform('acquire',CONTEXT)
    update=original.admission.update
    def lose(context,**changes):
        if changes.get('state')=='aborted':raise OSError('lost final commit')
        return update(context,**changes)
    monkeypatch.setattr(original.admission,'update',lose)
    with pytest.raises(OSError):abort(original)
    row=original.admission.receipt(CONTEXT);assert original.inspect()['intakeClosed']
    assert abort(life(tmp_path))['receiptId']==row['abortReceiptId']

def test_legacy_release_is_not_abort_authority(tmp_path):
    original=life(tmp_path);held=original.perform('acquire',CONTEXT)
    original.perform('release',{'fenceId':CONTEXT['fenceId'],'commandId':CONTEXT['commandId'],'leaseId':held['lease']['leaseId'],'outcome':'unchanged','proof':{'kind':'admission-refused'}})
    with pytest.raises(AdminFenced):abort(life(tmp_path),{'kind':'admission-refused'})
    assert abort(life(tmp_path))['status']=='released'

@pytest.mark.parametrize('kind',['sign-in','maintenance','capture'])
def test_all_native_lease_owners_block_abort(tmp_path,kind):
    original=life(tmp_path);original.perform('acquire',CONTEXT)
    if kind=='sign-in':original.owner.background_gates={SimpleNamespaceTask():None}
    elif kind=='maintenance':original.owner.maintenance=SimpleNamespace(lease_view=lambda:{'active':True})
    else:original.owner.maintenance=SimpleNamespace(lease_view=lambda:{'active':False},capture_scope=SimpleNamespace(active=lambda:True))
    try:
        with pytest.raises(AdminFenced):abort(original)
        assert original.inspect()['intakeClosed']
    finally:original.close()

class SimpleNamespaceTask:
    def done(self):return False

def test_live_acquired_abort_reopens_only_original_intake(tmp_path):
    original=life(tmp_path);assert original.perform('acquire',CONTEXT)['acquired']
    result=abort(original);assert result['status']=='released'
    ticket=original.enter('modules.save');original.leave(ticket)
    assert abort(original)==result
