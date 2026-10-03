"""Capture exclusion spans copying, streaming and an external product seal."""
import asyncio,json,os,threading
from pathlib import Path
import pytest
from amplifier_acp.admin import Administration
from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
from client import Client
from test_admin_lifecycle import CONTEXT
from test_full_native_archive import full_setup
from test_private_store_snapshot import call,hold,prepare
from test_maintenance_store_snapshot import capture as snapshot
from test_full_archive_lifetime import writer_refused

ACQUIRE={'commandId':'capture-scope','nonce':'n'*32,'instanceId':CONTEXT['instanceId'],'dataScope':CONTEXT['dataScope'],'stopReceiptSha256':'a'*64}

@pytest.mark.asyncio
async def test_actual_native_capture_exclusion_covers_prepare_snapshot_and_external_seal(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    try:
        auth=await hold(client,cwd)
        held=await call(client,cwd,'maintenance.capture.acquire',ACQUIRE,auth)
        selected={'captureId':held['captureId'],'nonce':held['nonce']}
        assert held['active'] and held['artifacts']==[] and await writer_refused(config)
        preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
        assert preview['fullNative']['capture']['captureId']==held['captureId']
        assert await writer_refused(config),'No writer gap after copy preparation'
        artifact,raw=await snapshot(client,cwd,auth,preview,'held')
        assert await writer_refused(config),'Snapshot release cannot release capture exclusion'
        attested=await call(client,cwd,'maintenance.artifact.inspect',{'artifactId':artifact['artifactId'],'sha256':artifact['sha256']})
        state=await call(client,cwd,'maintenance.capture.inspect',selected)
        assert state['active'] and state['artifacts']==[artifact['capture']['binding']]
        binding=state['artifacts'][0]
        assert binding['manifestDigest']==attested['manifest']['sha256']
        assert attested['capture']['context']==artifact['capture']['context']
        from jsonschema import validate
        validate(attested,json.loads((Path(__file__).parents[1]/'schemas/native-artifact-inspect-result.v1.json').read_text()))
        with pytest.raises(MaintenanceBusy):Gate(config)
        # Administration itself cannot be released during the wider capture.
        denied=await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'release','args':{**auth,'outcome':'unchanged','proof':{'kind':'admission-refused'}}})
        assert 'error' in denied
        release={'commandId':'sealed-release',**selected,'outcome':'sealed','artifactId':binding['artifactId'],'artifactSha256':binding['sha256'],'manifestDigest':binding['manifestDigest'],'productSha256':'b'*64}
        denied=await call(client,cwd,'maintenance.capture.release',{**release,'commandId':'bad-release','artifactSha256':'0'*64},auth,False)
        assert denied['data']['executed'] is False and await writer_refused(config)
        done=await call(client,cwd,'maintenance.capture.release',release,auth)
        assert done['released'] and not done['active'] and not await writer_refused(config)
        same=await call(client,cwd,'maintenance.capture.release',release,auth)
        assert same['receipt']==done['receipt'] and same['replayed'] is False
        reused=await call(client,cwd,'maintenance.capture.acquire',ACQUIRE,auth)
        assert not reused['active'] and not await writer_refused(config)
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_replacement_connection_cannot_recreate_capture_or_reuse_sealed_plan_authority(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    auth=await hold(client,cwd);held=await call(client,cwd,'maintenance.capture.acquire',ACQUIRE,auth)
    preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
    client.process.kill();await client.process.wait();await client.close();assert not await writer_refused(config)
    replacement=await Client.launch(path)
    try:
        await replacement.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        selected={'captureId':held['captureId'],'nonce':held['nonce']}
        state=await call(replacement,cwd,'maintenance.capture.inspect',selected)
        assert state['active'] is False and state['adminInstanceId']!=held['adminInstanceId']
        auth=await hold(replacement,cwd)
        prior=await call(replacement,cwd,'maintenance.capture.acquire',ACQUIRE,auth)
        assert prior['active'] is False and not await writer_refused(config)
        denied=await call(replacement,cwd,'maintenance.acquire',{'commandId':'reuse-plan','previewHash':preview['previewHash']},auth,False)
        assert denied['data']['executed'] is False
    finally:await replacement.close()

@pytest.mark.asyncio
async def test_capture_refuses_wrong_scope_and_absent_admin_before_writer_effects(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    try:
        denied=await call(client,cwd,'maintenance.capture.acquire',ACQUIRE,okay=False)
        assert denied['data']['executed'] is False and not await writer_refused(config)
        auth=await hold(client,cwd)
        denied=await call(client,cwd,'maintenance.capture.acquire',{**ACQUIRE,'commandId':'wrong','instanceId':'different'},auth,False)
        assert denied['data']['executed'] is False and not await writer_refused(config)
    finally:await client.close()

@pytest.mark.asyncio
async def test_cancel_and_close_join_preparation_while_capture_writer_is_still_excluded(tmp_path,monkeypatch):
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'home';home.mkdir();(home/'settings.yaml').write_text('fixture: retained\n')
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceFullNative':True,'maintenanceExternalWriters':'stopped','maintenanceFullNativeRoots':{'checkpoint':os.environ['AMPLIFIER_SESSION_STATE_HOME'],'events':os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'],'sources':os.environ['AMPLIFIER_SOURCE_STORE']}}
    admin=Administration(config);fence=admin.lifecycle.perform('acquire',CONTEXT);auth={k:fence['lease'][k] for k in ('fenceId','commandId','leaseId')}
    async def invoke(op,args):return await admin.perform(str(cwd),op,args,fence=auth)
    held=await invoke('maintenance.capture.acquire',ACQUIRE)
    plan=await invoke('maintenance.archive.create',{'commandId':'create','parts':['full-native-authority'],'privateContentReviewed':True})
    import amplifier_acp.full_native_archive as full
    entered=threading.Event();finish=threading.Event();original=full.prepare
    def delayed(*args):entered.set();assert finish.wait(10);return original(*args)
    monkeypatch.setattr(full,'prepare',delayed)
    request=asyncio.create_task(invoke('maintenance.archive.prepare',{'commandId':'prepare','planId':plan['planId'],'expectedRevision':0}))
    closing=None
    try:
        assert await asyncio.to_thread(entered.wait,10);request.cancel();closing=asyncio.create_task(admin.close());await asyncio.sleep(.02)
        assert not request.done() and not closing.done() and await writer_refused(config)
        finish.set()
        with pytest.raises(asyncio.CancelledError):await request
        await closing;assert not await writer_refused(config)
    finally:
        finish.set();await asyncio.gather(request,return_exceptions=True)
        if closing:await closing
        else:await admin.close()

@pytest.mark.asyncio
async def test_snapshot_idle_expiry_does_not_expire_wider_capture(tmp_path):
    client,cwd,config,path,sid=await full_setup(tmp_path)
    try:
        auth=await hold(client,cwd);held=await call(client,cwd,'maintenance.capture.acquire',ACQUIRE,auth)
        preview=await prepare(client,cwd,auth,parts=['full-native-authority'])
        lease=await call(client,cwd,'maintenance.acquire',{'commandId':'short-lease','previewHash':preview['previewHash'],'ttlSeconds':1},auth)
        await asyncio.sleep(1.1)
        assert (await call(client,cwd,'maintenance.lease'))['active'] is False
        state=await call(client,cwd,'maintenance.capture.inspect',{'captureId':held['captureId'],'nonce':held['nonce']})
        assert state['active'] and await writer_refused(config)
        artifact,_=await snapshot(client,cwd,auth,preview,'after-idle-expiry')
        assert artifact['capture']['context']['captureId']==held['captureId'] and await writer_refused(config)
        result=await call(client,cwd,'maintenance.capture.release',{'commandId':'abort','captureId':held['captureId'],'nonce':held['nonce'],'outcome':'aborted'},auth)
        assert result['released'] and not await writer_refused(config)
    finally:await client.close()
