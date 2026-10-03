import asyncio,json,threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from client import Client
from amplifier_acp.admin import Administration
from amplifier_acp.admin_lifecycle import AdminGate
from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
from amplifier_acp.transfer_lifecycle import TransferLifecycle
from test_admin_lifecycle import CONTEXT,start

@pytest.mark.asyncio
async def test_transfer_thread_cancellation_keeps_both_native_writer_gates_until_settled(tmp_path):
    workspace=tmp_path/'work';workspace.mkdir();config={'home':str(tmp_path/'home'),'adminWorkspaceRoots':[str(workspace)]}
    owner=TransferLifecycle(SimpleNamespace(config=config));entered=threading.Event();resume=threading.Event()
    def effect():entered.set();assert resume.wait(5);(tmp_path/'effect').write_text('once');return {'done':True}
    async def execute(params):return await asyncio.to_thread(effect)
    owner.execute=execute;task=asyncio.create_task(owner.perform({}))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        task.cancel();await asyncio.sleep(0)
        assert owner.control('acquire',CONTEXT)['acquired'] is False
        for cls in (AdminGate,Gate):
            with pytest.raises(MaintenanceBusy):cls(config,exclusive=True)
        resume.set();await asyncio.gather(task,return_exceptions=True)
        assert (tmp_path/'effect').read_text()=='once'
        for cls in (AdminGate,Gate):cls(config,exclusive=True).release()
        assert owner.control('acquire',CONTEXT)['acquired']
    finally:resume.set();await owner.close()

@pytest.mark.asyncio
async def test_transfer_peer_gate_and_home_admin_gate_have_noncompeting_acquisition_order(tmp_path):
    admin,cwd,values,config=await start(tmp_path)
    values.update(transferAuthorityDirectory=str(tmp_path/'transfer'),transferWorkspaceRoots=[str(cwd)])
    config.write_text(json.dumps(values));peer=await Client.launch(config)
    try:
        init=await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['transfer']['lifecycle']['nativeAdminWriter']
        held=await peer.request('_amplifier/transfer/lifecycle',{'operation':'acquire','args':CONTEXT});assert held['result']['acquired']
        home=await admin.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':CONTEXT});assert home['result']['acquired']
        denied=await peer.request('_amplifier/transfer',{'sessionId':'not-imported','cwd':str(cwd),'operation':'inspect','args':{}});assert denied['error']['data']['executed'] is False
        assert not Path(values['transferAuthorityDirectory']).exists(),'Held inspection must not initialize a transfer index'
        proof={'verified':True,**CONTEXT,'outcome':'unchanged','receiptId':'retained-owner-proof'}
        released=await peer.request('_amplifier/transfer/lifecycle',{'operation':'release','args':{**CONTEXT,'outcome':'unchanged','proof':proof}});assert released['result']['released']
        # The process fence released but the shared native-home writer gate still
        # prevents even a new transfer constructor from initializing private state.
        denied=await peer.request('_amplifier/transfer',{'sessionId':'not-imported','cwd':str(cwd),'operation':'inspect','args':{}});assert denied['error']['data']['executed'] is False
        assert not Path(values['transferAuthorityDirectory']).exists()
        lease=home['result']['lease'];await admin.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'release','args':{**{k:lease[k] for k in ('fenceId','commandId','leaseId')},'outcome':'unchanged','proof':proof}})
    finally:await peer.close();await admin.close()

@pytest.mark.asyncio
async def test_post_response_admin_idle_advisory_is_one_shot_after_refused_acquisition(tmp_path):
    workspace=tmp_path/'work';workspace.mkdir();events=[];finish=asyncio.Event()
    async def idle():events.append('idle')
    owner=Administration({'home':str(tmp_path/'home'),'adminWorkspaceRoots':[str(workspace)]},idle)
    class Setup:
        logins={}
        async def close(self):pass
    owner.setup=Setup()
    async def authorized(*args):owner.setup.logins['fixture']={'task':asyncio.create_task(finish.wait())};return {'phase':'waiting'}
    owner.authorized=authorized
    try:
        await owner.perform(str(workspace),'providers.login',{})
        assert not owner.lifecycle.perform('acquire',CONTEXT)['acquired']
        assert events==[];finish.set()
        for _ in range(5):await asyncio.sleep(0)
        assert events==['idle'];owner.lifecycle.maybe_idle();await asyncio.sleep(0);assert events==['idle']
    finally:finish.set();await owner.close()

@pytest.mark.asyncio
async def test_builtin_probe_child_retains_independent_gate_after_parent_death(tmp_path):
    import os,sys,subprocess
    config={'home':str(tmp_path/'home')};ready=tmp_path/'ready';release=tmp_path/'release';child_id=tmp_path/'pid'
    # The real entrypoint acquires the leases; replace only provider execution.
    child_code="""import asyncio,sys
from pathlib import Path
from amplifier_acp.native import portability_probe as p
async def fixture(request):
 Path(sys.argv[1]).write_text('ready')
 while not Path(sys.argv[2]).exists():await asyncio.sleep(.01)
 return {'fixture':True}
p.probe=fixture
p.main()
"""
    parent_code="""import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
p.stdin.write(b'{}');p.stdin.close();Path(sys.argv[4]).write_text(str(p.pid))
while True:time.sleep(1)
"""
    parent=subprocess.Popen([sys.executable,'-c',parent_code,child_code,str(ready),str(release),str(child_id)],env={**os.environ,'AMPLIFIER_HOME':config['home']})
    try:
        for _ in range(500):
            if ready.exists():break
            await asyncio.sleep(.01)
        assert ready.exists();parent.kill();await asyncio.to_thread(parent.wait)
        for cls in (AdminGate,Gate):
            with pytest.raises(MaintenanceBusy):cls(config,exclusive=True)
        release.touch()
        for _ in range(500):
            try:AdminGate(config,exclusive=True).release();break
            except MaintenanceBusy:await asyncio.sleep(.01)
        for cls in (AdminGate,Gate):cls(config,exclusive=True).release()
    finally:
        release.touch()
        if parent.poll() is None:parent.kill();await asyncio.to_thread(parent.wait)


def test_custom_probe_does_not_claim_unknown_subprocess_lifetime(tmp_path):
    owner=TransferLifecycle(SimpleNamespace(config={'home':str(tmp_path),'transferProbeCommand':['custom-probe']}))
    result=owner.control('acquire',CONTEXT)
    assert result['acquired'] is False and result['executed'] is False
    assert result['reason']=='custom-probe-lifetime-unproven'
