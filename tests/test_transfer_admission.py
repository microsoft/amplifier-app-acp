"""Original disposition, restart and actual writer-lifetime admission cases."""
import asyncio,json,sqlite3,threading,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from client import Client
from amplifier_acp.admin_lifecycle import AdminFenced,AdminGate
from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
from amplifier_acp.transfer_admission import TransferAdmission,ATTEMPTS,ACTIVE,digest,encoded
from amplifier_acp.transfer_lifecycle import TransferLifecycle

CTX={'fenceId':'distribution-fence','commandId':'original-command','instanceId':'host-instance','dataScope':'owned-data','purpose':'distribution-update','ownerId':'native-transfer-peer'}
PROOF={**{k:v for k,v in CTX.items() if k!='ownerId'},'kind':'distribution-admission-abort','verified':True,'receiptId':'authenticated-abort'}

def owner(tmp_path):
    authority=tmp_path/'authority';authority.mkdir()
    with sqlite3.connect(authority/'owner.sqlite3') as db:
        db.execute('CREATE TABLE commands(scope TEXT,id TEXT,signature TEXT,body TEXT,PRIMARY KEY(scope,id))')
    config={'home':str(tmp_path/'native-home'),'transferAuthorityDirectory':str(authority)}
    return TransferLifecycle(SimpleNamespace(config=config)),config

def abort(peer,proof=PROOF,ctx=CTX):return peer.control('abortAdmission',{**ctx,'proof':proof})

def test_original_busy_refusal_survives_idle_and_replacement_without_acquisition(tmp_path):
    peer,config=owner(tmp_path)
    writer=Gate(config)
    try:refused=peer.control('acquire',CTX)
    finally:writer.release()
    assert refused['acquired'] is False and refused['executed'] is False and refused['intakeClosed'] is False
    replacement=TransferLifecycle(SimpleNamespace(config=config))
    assert replacement.control('acquire',CTX)==refused
    assert replacement.inspect()['intakeClosed'] is False
    result=abort(replacement)
    assert result['status']=='not-acquired' and result['ownerId']==CTX['ownerId']
    assert abort(TransferLifecycle(SimpleNamespace(config=config)))==result
    with pytest.raises(AdminFenced):replacement.control('acquire',CTX)

def test_lost_acquisition_reply_replacement_observes_original_and_aborts_under_admin_hold(tmp_path):
    peer,config=owner(tmp_path)
    original=peer.control('acquire',CTX)  # Original reply deliberately unconsumed.
    replacement=TransferLifecycle(SimpleNamespace(config=config))
    assert replacement.control('acquire',CTX)==original
    assert replacement.inspect()['fence']=={k:v for k,v in CTX.items() if k!='ownerId'}
    admin=AdminGate(config,exclusive=True)
    try:result=abort(replacement)
    finally:admin.release()
    assert result['status']=='released'
    assert set(result)=={'ownerId','status','receiptId','commandId','fenceId','instanceId','dataScope'}
    assert replacement.inspect()['intakeClosed'] is False
    # Lost abort response can be observed under a later writer without repeating
    # a lock acquisition or any intake transition.
    writer=Gate(config)
    try:assert abort(TransferLifecycle(SimpleNamespace(config=config)))==result
    finally:writer.release()
    with pytest.raises(AdminFenced):abort(replacement,{**PROOF,'receiptId':'changed-proof'})

@pytest.mark.parametrize('changed',[{'commandId':'other'},{'fenceId':'other'},{'instanceId':'other'},{'dataScope':'other'},{'ownerId':'other'}])
def test_changed_original_binding_cannot_settle_held_transfer(tmp_path,changed):
    peer,_=owner(tmp_path);peer.control('acquire',CTX)
    ctx={**CTX,**changed};p={**PROOF,**{k:v for k,v in changed.items() if k!='ownerId'}}
    with pytest.raises(AdminFenced):abort(peer,p,ctx)
    assert peer.inspect()['intakeClosed'] is True

@pytest.mark.parametrize('p',[{'kind':'admission-refused'}, {**PROOF,'kind':'release'}, {**PROOF,'verified':False}, {**PROOF,'extra':'selector'}])
def test_old_or_extra_release_assertion_is_not_abort_authority(tmp_path,p):
    peer,_=owner(tmp_path);peer.control('acquire',CTX)
    with pytest.raises(AdminFenced):abort(peer,p)
    assert peer.inspect()['intakeClosed'] is True

def test_absent_authority_or_wrong_schema_cannot_be_created_or_interpreted_as_refusal(tmp_path):
    config={'home':str(tmp_path/'home'),'transferAuthorityDirectory':str(tmp_path/'missing')}
    peer=TransferLifecycle(SimpleNamespace(config=config))
    for op,args in [('acquire',CTX),('abortAdmission',{**CTX,'proof':PROOF})]:
        with pytest.raises(AdminFenced):peer.control(op,args)
    assert not Path(config['transferAuthorityDirectory']).exists()
    peer,config=owner(tmp_path)
    with sqlite3.connect(Path(config['transferAuthorityDirectory'])/'owner.sqlite3') as db:
        db.execute('DROP TABLE commands');db.execute('CREATE TABLE commands(id TEXT,body TEXT)')
    with pytest.raises(AdminFenced):peer.control('acquire',CTX)

@pytest.mark.parametrize('phase',['intent','unknown','acquired'])
def test_orphan_original_disposition_keeps_intake_unknown_without_pointer(tmp_path,phase):
    peer,config=owner(tmp_path);peer.control('acquire',CTX)
    with sqlite3.connect(Path(config['transferAuthorityDirectory'])/'owner.sqlite3') as db:
        raw=db.execute('SELECT body FROM commands WHERE scope=?',(ATTEMPTS,)).fetchone()[0]
        row=json.loads(raw);row['phase']=phase
        db.execute('UPDATE commands SET body=? WHERE scope=?',(encoded(row),ATTEMPTS))
        db.execute('DELETE FROM commands WHERE scope=?',(ACTIVE,))
    replacement=TransferLifecycle(SimpleNamespace(config=config))
    with pytest.raises(AdminFenced):replacement.inspect()
    with pytest.raises(AdminFenced):abort(replacement)
    with pytest.raises(AdminFenced):replacement.control('acquire',CTX)
    with pytest.raises(AdminFenced):replacement.durable().assert_open()

def test_each_original_peer_needs_its_own_receipt_and_other_hold_remains(tmp_path):
    peer,config=owner(tmp_path);other={**CTX,'ownerId':'second-native-transfer-peer'}
    peer.control('acquire',CTX);peer.control('acquire',other)
    assert abort(peer)['status']=='released'
    assert peer.inspect()['intakeClosed'] is True
    result=abort(TransferLifecycle(SimpleNamespace(config=config)),ctx=other)
    assert result['ownerId']==other['ownerId'] and result['status']=='released'
    assert peer.inspect()['intakeClosed'] is False

def test_normal_release_identifies_selected_peer_while_other_peer_keeps_global_intake_closed(tmp_path):
    peer,config=owner(tmp_path);other={**CTX,'ownerId':'second-native-transfer-peer'}
    peer.control('acquire',CTX);peer.control('acquire',other)
    p={'verified':True,**{k:v for k,v in CTX.items() if k!='ownerId'},'outcome':'unchanged','receiptId':'original-release'}
    request={**CTX,'outcome':'unchanged','proof':p};result=peer.control('release',request)
    assert result['ownerId']==CTX['ownerId'] and result['ownerIntakeClosed'] is False and result['intakeClosed'] is True
    assert TransferLifecycle(SimpleNamespace(config=config)).control('release',request)==result
    assert abort(peer)['status']=='released' and peer.inspect()['intakeClosed'] is True
    assert abort(peer,ctx=other)['status']=='released' and peer.inspect()['intakeClosed'] is False

def test_durable_unchanged_release_still_requires_new_distinct_abort_proof(tmp_path):
    peer,config=owner(tmp_path);peer.control('acquire',CTX)
    old={'verified':True,**{k:v for k,v in CTX.items() if k!='ownerId'},'outcome':'unchanged','receiptId':'original-release'}
    peer.control('release',{**CTX,'outcome':'unchanged','proof':old})
    replacement=TransferLifecycle(SimpleNamespace(config=config))
    with pytest.raises(AdminFenced):abort(replacement,old)
    assert abort(replacement)['status']=='released'

def test_ready_successor_release_cannot_be_relabelled_as_pre_retirement_abort(tmp_path):
    peer,config=owner(tmp_path);peer.control('acquire',CTX)
    p={'verified':True,**{k:v for k,v in CTX.items() if k!='ownerId'},'instanceId':'successor-instance','outcome':'ready','receiptId':'successor-ready'}
    peer.control('release',{**CTX,'outcome':'ready','proof':p})
    with pytest.raises(AdminFenced):abort(TransferLifecycle(SimpleNamespace(config=config)))

def test_actual_other_process_writer_blocks_abort_until_graceful_settlement(tmp_path):
    peer,config=owner(tmp_path);peer.control('acquire',CTX)
    source=str(Path(__import__('amplifier_acp').__file__).parent.parent)
    code="import sys,json;sys.path.insert(0,sys.argv[1]);from amplifier_acp.maintenance_gate import Gate;g=Gate(json.loads(sys.argv[2]));print('held',flush=True);sys.stdin.readline();g.release()"
    # A child holding a pre-existing writer lease represents original work
    # whose ACP parent/reply is gone. Completion is signalled, never replayed.
    child=subprocess.Popen([sys.executable,'-I','-B','-c',code,source,json.dumps(config)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='held'
        replacement=TransferLifecycle(SimpleNamespace(config=config))
        with pytest.raises(MaintenanceBusy):abort(replacement)
        assert replacement.inspect()['intakeClosed'] is True
        child.stdin.write('settled\n');child.stdin.flush();assert child.wait(timeout=5)==0
        result=abort(replacement);assert result['status']=='released'
        assert abort(TransferLifecycle(SimpleNamespace(config=config)))==result
    finally:
        if child.poll() is None:child.stdin.write('settled\n');child.stdin.flush();child.wait(timeout=5)
        child.stdin.close();child.stdout.close();child.stderr.close()

@pytest.mark.asyncio
async def test_foreign_cancelled_thread_retains_actual_writer_proof_and_refusal_is_immutable(tmp_path):
    peer,config=owner(tmp_path);other=TransferLifecycle(SimpleNamespace(config=config))
    entered=threading.Event();finish=threading.Event()
    def effect():entered.set();assert finish.wait(5);return {'effect':'settled-once'}
    async def execute(params):return await asyncio.to_thread(effect)
    peer.execute=execute;task=asyncio.create_task(peer.perform({}))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        task.cancel();await asyncio.sleep(0)
        refused=other.control('acquire',CTX);assert refused['acquired'] is False
        with pytest.raises(MaintenanceBusy):abort(other)
        for gate in (Gate,AdminGate):
            with pytest.raises(MaintenanceBusy):gate(config,exclusive=True)
        finish.set();await asyncio.gather(task,return_exceptions=True)
        assert TransferLifecycle(SimpleNamespace(config=config)).control('acquire',CTX)==refused
        assert abort(other)['status']=='not-acquired'
    finally:finish.set();await asyncio.gather(task,return_exceptions=True);await peer.close()

@pytest.mark.asyncio
async def test_replacement_transfer_refuses_before_constructor_or_effect_while_durably_held(tmp_path):
    peer,config=owner(tmp_path);peer.control('acquire',CTX)
    replacement=TransferLifecycle(SimpleNamespace(config=config));entered=[]
    async def execute(params):entered.append('constructor');return {}
    replacement.execute=execute
    with pytest.raises(AdminFenced):await replacement.perform({})
    assert entered==[]
    abort(replacement)
    assert await replacement.perform({})=={} and entered==['constructor']

@pytest.mark.asyncio
async def test_actual_stdio_dropped_original_and_abort_replies_survive_replacement(tmp_path):
    _,config=owner(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    config['transferWorkspaceRoots']=[str(workspace)];path=tmp_path/'config.json';path.write_text(json.dumps(config))
    clients=[]
    async def connect():
        peer=await Client.launch(path);clients.append(peer)
        init=await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['transfer']['lifecycle']['admissionAbort']=={'version':1}
        return peer
    async def original_phase(expected):
        for _ in range(300):
            with sqlite3.connect(Path(config['transferAuthorityDirectory'])/'owner.sqlite3') as db:
                row=db.execute('SELECT body FROM commands WHERE scope=?',(ATTEMPTS,)).fetchone()
            if row and json.loads(row[0])['phase']==expected:return
            await asyncio.sleep(.01)
        raise AssertionError('Original request did not persist its disposition')
    try:
        first=await connect()
        original=(await first.request('_amplifier/transfer/lifecycle',{'operation':'inspect','args':{}}))['result']['processId']
        # No pending future consumes this reply. The peer shuts down normally,
        # retaining original SQLite authority rather than retrying any effect.
        await first.send({'jsonrpc':'2.0','id':999,'method':'_amplifier/transfer/lifecycle','params':{'operation':'acquire','args':CTX}})
        await original_phase('acquired')
        await first.close();clients.remove(first)
        second=await connect()
        observed=(await second.request('_amplifier/transfer/lifecycle',{'operation':'acquire','args':CTX}))['result']
        assert observed['acquired'] is True and observed['processId']==original
        await second.send({'jsonrpc':'2.0','id':998,'method':'_amplifier/transfer/lifecycle','params':{'operation':'abortAdmission','args':{**CTX,'proof':PROOF}}})
        await original_phase('aborted')
        await second.close();clients.remove(second)
        with sqlite3.connect(Path(config['transferAuthorityDirectory'])/'owner.sqlite3') as db:
            body=json.loads(db.execute('SELECT body FROM commands WHERE scope=?',(ATTEMPTS,)).fetchone()[0])
        assert body['phase']=='aborted';original_abort=body['abortResult']
        third=await connect()
        result=await third.request('_amplifier/transfer/lifecycle',{'operation':'abortAdmission','args':{**CTX,'proof':PROOF}})
        assert result['result']==original_abort and original_abort['status']=='released'
        denied=await third.request('_amplifier/transfer/lifecycle',{'operation':'acquire','args':CTX});assert 'error' in denied
    finally:
        for peer in clients:await peer.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('operation,args',[
    ('acquire',{**CTX,'extra':'untrusted-selector'}),
    ('acquire',{**CTX,'purpose':'recovery'}),
    ('abortAdmission',{**CTX,'proof':{'kind':'admission-refused'}}),
    ('abortAdmission',{**{k:v for k,v in CTX.items() if k!='ownerId'},'proof':PROOF}),
])
async def test_stdio_malformed_new_admission_refuses_before_outer_gate_or_authority_write(tmp_path,operation,args):
    _,config=owner(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir();config['transferWorkspaceRoots']=[str(workspace)]
    path=tmp_path/'config.json';path.write_text(json.dumps(config));peer=await Client.launch(path)
    try:
        await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        response=await peer.request('_amplifier/transfer/lifecycle',{'operation':operation,'args':args})
        assert 'error' in response and not Path(config['home']).exists()
        with sqlite3.connect(Path(config['transferAuthorityDirectory'])/'owner.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]==0
    finally:await peer.close()
