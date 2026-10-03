"""Actual OS writer fencing survives short idle TTL and live file threads."""
import asyncio,json,os,sys,threading
from pathlib import Path
import pytest
from amplifier_acp.admin import Administration
from amplifier_acp.maintenance import Maintenance
from test_admin_lifecycle import CONTEXT

async def setup(tmp_path):
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'home';home.mkdir();(home/'settings.yaml').write_text('retained: fixture\n')
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceFullNative':True,'maintenanceExternalWriters':'stopped','maintenanceFullTimeoutSeconds':10,'maintenanceFullNativeRoots':{'checkpoint':os.environ['AMPLIFIER_SESSION_STATE_HOME'],'events':os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'],'sources':os.environ['AMPLIFIER_SOURCE_STORE']}}
    admin=Administration(config);assert admin.lifecycle.perform('acquire',CONTEXT)['acquired'];owner=Maintenance(admin);admin.maintenance=owner
    plan=await owner.perform(str(cwd),'maintenance.archive.create',{'commandId':'create','parts':['full-native-authority'],'privateContentReviewed':True})
    preview=await owner.perform(str(cwd),'maintenance.archive.prepare',{'commandId':'prepare','planId':plan['planId'],'expectedRevision':0})
    lease=await owner.perform(str(cwd),'maintenance.acquire',{'commandId':'acquire','previewHash':preview['previewHash'],'ttlSeconds':1})
    return config,admin,owner,str(cwd),preview,lease

async def writer_refused(config):
    script='''import json,sys
from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
try:
 with Gate(json.loads(sys.argv[1])):pass
except MaintenanceBusy:sys.exit(17)
'''
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',script,json.dumps(config),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    await child.communicate();return child.returncode==17

@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_full_snapshot_past_idle_expiry_keeps_real_process_writer_exclusion(tmp_path,monkeypatch,cancel):
    config,admin,owner,cwd,preview,lease=await setup(tmp_path)
    entered=threading.Event();finish=threading.Event();original=owner.snapshot
    def delayed(*args):
        entered.set();assert finish.wait(10)
        return original(*args)
    monkeypatch.setattr(owner,'snapshot',delayed)
    request=asyncio.create_task(owner.perform(cwd,'maintenance.snapshot',{'commandId':'once','leaseId':lease['leaseId'],'previewHash':preview['previewHash']}))
    closing=None
    try:
        while not entered.is_set():await asyncio.sleep(.001)
        await asyncio.sleep(1.1)
        assert owner.valid(lease['leaseId'])['expiresAt']>lease['expiresAt']
        assert await writer_refused(config),'Expiry must not release a still-running file thread'
        if cancel:
            request.cancel();closing=asyncio.create_task(owner.close());await asyncio.sleep(.01)
            assert not request.done() and not closing.done()
            assert await writer_refused(config),'Cancellation/close must join the actual effect'
        finish.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):await request
            await closing
            assert owner.receipt(cwd,'once')[1]['state']=='unknown'
        else:
            result=await request;assert result['receipt']['state']=='succeeded'
            assert result['format']=='amplifier-native-authority'
            await owner.close()
        assert not await writer_refused(config)
    finally:
        finish.set()
        if not request.done():await asyncio.gather(request,return_exceptions=True)
        await owner.close();admin.lifecycle.close()

@pytest.mark.asyncio
async def test_full_snapshot_cannot_revive_expired_idle_lease(tmp_path):
    config,admin,owner,cwd,preview,lease=await setup(tmp_path)
    try:
        await asyncio.sleep(1.1)
        with pytest.raises(ValueError,match='absent, expired or released'):
            await owner.perform(cwd,'maintenance.snapshot',{'commandId':'too-late','leaseId':lease['leaseId'],'previewHash':preview['previewHash']})
        assert not list((owner.root/'artifacts').glob('*')) if (owner.root/'artifacts').exists() else True
        assert not await writer_refused(config)
    finally:await owner.close();admin.lifecycle.close()
