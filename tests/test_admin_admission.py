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

def release_args(outcome='ready'):
    proof={key:CONTEXT[key] for key in ('fenceId','commandId','dataScope')}
    return {**CONTEXT,'outcome':outcome,'proof':{**proof,'verified':True,'outcome':outcome,'instanceId':'replacement-host' if outcome=='ready' else CONTEXT['instanceId'],'receiptId':'authenticated-ready-receipt'}}

def retained(tmp_path):
    original=life(tmp_path);held=original.perform('acquire',CONTEXT);original.close()
    return life(tmp_path),held

@pytest.mark.parametrize('outcome',['ready','unchanged'])
def test_release_reconciles_exact_original_without_reacquisition(tmp_path,outcome):
    replacement,held=retained(tmp_path);args=release_args(outcome)
    with pytest.raises(AdminFenced):replacement.perform('acquire',CONTEXT)
    result=replacement.perform('reconcileRelease',args)
    assert result['released'] and result['reconciled'] and result['workerStarted'] is False
    assert result['lease']==held['lease'] and result['adminInstanceId']!=held['adminInstanceId']
    assert not replacement.inspect()['intakeClosed']
    assert life(tmp_path).perform('reconcileRelease',args)==result # Lost reply and process replacement.
    ticket=replacement.enter('modules.save');replacement.leave(ticket)
    newer={**CONTEXT,'fenceId':'newer-fence','commandId':'newer-command'}
    assert replacement.perform('acquire',newer)['acquired']
    try:
        assert life(tmp_path).perform('reconcileRelease',args)==result
        assert replacement.admission.intake()['context']==newer
        with pytest.raises(AdminFenced):life(tmp_path).perform('reconcileRelease',{**args,'proof':{**args['proof'],'receiptId':'different'}})
    finally:replacement.close()
    assert not (tmp_path/'native'/'projects').exists()

@pytest.mark.parametrize('change',[
    {'verified':False},{'verified':1},{'receiptId':''},{'receiptId':'bad\nreceipt'},
    {'instanceId':CONTEXT['instanceId']},{'outcome':'unknown'},
    {'fenceId':'other'},{'commandId':'other'},{'dataScope':'other'},{'extra':True},
])
def test_release_wrong_proof_preserves_exact_durable_hold(tmp_path,change):
    replacement,_=retained(tmp_path);args=release_args();before=replacement.admission.path.read_bytes()
    with pytest.raises(AdminFenced):replacement.perform('reconcileRelease',{**args,'proof':{**args['proof'],**change}})
    assert replacement.admission.path.read_bytes()==before and replacement.inspect()['intakeClosed']

@pytest.mark.parametrize('state',['absent','pending','refused','aborted'])
def test_release_cannot_infer_original_retirement(tmp_path,state):
    original=life(tmp_path)
    if state=='pending':original.admission.begin(CONTEXT)
    elif state=='refused':original.perform('refuseAdmission',CONTEXT)
    elif state=='aborted':original.admission.begin(CONTEXT);abort(original)
    before=original.admission.path.read_bytes() if original.admission.path.exists() else None
    with pytest.raises((ValueError,AdminFenced)):original.perform('reconcileRelease',release_args())
    assert (original.admission.path.read_bytes() if original.admission.path.exists() else None)==before

@pytest.mark.parametrize('busy',['external','request','sign-in','maintenance','capture','closing','original-live'])
def test_release_requires_actual_writer_exclusion(tmp_path,busy):
    original=life(tmp_path);original.perform('acquire',CONTEXT)
    if busy!='original-live':original.close()
    replacement=life(tmp_path);external=None;ticket=None
    if busy=='external':external=AdminGate(original.owner.config)
    elif busy=='request':ticket=replacement.enter('voice.configuration')
    elif busy=='sign-in':replacement.owner.background_gates={SimpleNamespaceTask():None}
    elif busy=='maintenance':replacement.owner.maintenance=SimpleNamespace(lease_view=lambda:{'active':True})
    elif busy=='capture':replacement.owner.maintenance=SimpleNamespace(lease_view=lambda:{'active':False},capture_scope=SimpleNamespace(active=lambda:True))
    elif busy=='closing':replacement.closing=True
    try:
        with pytest.raises(AdminFenced):replacement.perform('reconcileRelease',release_args())
        assert replacement.inspect()['intakeClosed']
    finally:
        if external:external.release()
        if ticket:replacement.leave(ticket)
        original.close()

def test_release_transaction_failure_keeps_original_hold(tmp_path,monkeypatch):
    replacement,_=retained(tmp_path)
    put=replacement.admission.put
    def fail_after_write(db,key,row):
        put(db,key,row)
        if row['state']=='released':raise OSError('owned release transaction fault')
    monkeypatch.setattr(replacement.admission,'put',fail_after_write)
    with pytest.raises(OSError):replacement.perform('reconcileRelease',release_args())
    assert replacement.admission.receipt(CONTEXT)['state']=='acquired'
    assert replacement.inspect()['intakeClosed']
    monkeypatch.setattr(replacement.admission,'put',put)
    assert replacement.perform('reconcileRelease',release_args())['released']

def test_release_public_schema_is_closed_and_agrees_with_operation():
    import jsonschema
    schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/native-admin-lifecycle-request.v1.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator=jsonschema.Draft202012Validator(schema)
    request={'cwd':'/owned/workspace','operation':'reconcileRelease','args':release_args()}
    validator.validate(request)
    for change in [{'phase':'unknown'},{'purpose':'recovery'},{'outcome':'unknown'},{'proof':{**request['args']['proof'],'extra':True}}]:
        with pytest.raises(jsonschema.ValidationError):validator.validate({**request,'args':{**request['args'],**change}})
