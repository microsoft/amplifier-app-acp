import asyncio,base64,hashlib,io,json,os,tarfile
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration


async def initialized(config):
    client=await Client.launch(config)
    result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    return client,result['result']


def selections(sid,cwd,**values):
    return {'sessions':[{'nativeSessionId':sid,'cwd':str(cwd)}],'parts':['session-history','session-state','native-settings'],'privateContentReviewed':True,**values}


async def seeded(tmp_path):
    config,cwd=configuration(tmp_path)
    settings=json.loads(config.read_text());settings.update(adminWorkspaceRoots=[str(cwd)],adminMaintenance=True,maintenanceExternalWriters='foundation-cooperative')
    config.write_text(json.dumps(settings));client,_=await initialized(config)
    sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
    await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'snapshot evidence'}]})
    return config,cwd,settings,client,sid


def admin(client,cwd):
    async def call(operation,args=None,*,okay=True):
        response=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':operation,'args':args or {}})
        if okay:assert 'result' in response,response;return response['result']
        assert 'error' in response,response;return response['error']
    return call


@pytest.mark.asyncio
async def test_actual_native_private_snapshot_busy_gate_and_chunk_export(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path)
    other=tmp_path/'admin.json';other.write_text(json.dumps({**settings,'workerCommand':['/impossible/no-worker']}))
    control,init=await initialized(other);call=admin(control,cwd)
    try:
        assert init['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['selectedOnly']
        preview=await call('maintenance.preview',selections(sid,cwd))
        busy=await call('maintenance.acquire',{'commandId':'while-resident','previewHash':preview['previewHash']},okay=False)
        assert busy['data']['executed'] is False
        assert (await call('maintenance.receipt',{'commandId':'while-resident'}))['receipt']['state']=='refused'
        await runtime.request('session/close',{'sessionId':sid})
        # Native close may make a final checkpoint, so inspect a new immutable base.
        preview=await call('maintenance.preview',selections(sid,cwd))
        lease=await call('maintenance.acquire',{'commandId':'snapshot-lease','previewHash':preview['previewHash'],'ttlSeconds':30})
        denied=await runtime.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert denied['error']['data']['reason']=='native-maintenance-busy'
        snapshot=await call('maintenance.snapshot',{'commandId':'snapshot','leaseId':lease['leaseId'],'previewHash':preview['previewHash']})
        assert snapshot['coverage']=='explicit-selected-native-only' and snapshot['manifest']['spec']['includeCredentials'] is False
        raw=bytearray();offset=0
        while True:
            page=await call('maintenance.artifact.read',{'artifactId':snapshot['artifactId'],'sha256':snapshot['sha256'],'offset':offset,'maxBytes':8192})
            part=base64.b64decode(page['data']);assert hashlib.sha256(part).hexdigest()==page['chunkSha256'];raw.extend(part)
            if page['nextOffset'] is None:break
            offset=page['nextOffset']
        assert hashlib.sha256(raw).hexdigest()==snapshot['sha256']
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files=archive.getnames();assert f'history/{sid}/transcript.jsonl' in files
            manifest=json.load(archive.extractfile('manifest.json'));assert manifest['previewHash']==preview['previewHash']
            for row in manifest['inventory']['files']:assert hashlib.sha256(archive.extractfile(row['path']).read()).hexdigest()==row['sha256']
        duplicate=await call('maintenance.snapshot',{'commandId':'snapshot','leaseId':lease['leaseId'],'previewHash':preview['previewHash']})
        assert duplicate['artifactId']==snapshot['artifactId'] and duplicate['replayed'] is False
        await call('maintenance.release',{'leaseId':lease['leaseId']})
        assert not control.events,'Passive admin snapshot never starts Core or sends conversation events'
        assert (await runtime.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})).get('result') is not None
    finally:await control.close();await runtime.close()


@pytest.mark.asyncio
async def test_selected_reset_preserves_history_authority_and_restores_exact_originals(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path)
    await runtime.request('session/close',{'sessionId':sid});await runtime.close()
    app=Path(settings['appHome'])/'sessions'/sid
    state=json.loads((app/'control-state.json').read_text());state.update(selection={'instance':'fixture','model':'custom'},task={'id':'never-reset','revision':3},taskReceipts={'command':'retained'},capacityReceipts={'cost':'retained'})
    (app/'control-state.json').write_text(json.dumps(state));(app/'configuration.json').write_text('{"fixture":"private override"}')
    original={name:(app/name).read_bytes() for name in ('configuration.json','effective-configuration.json','control-state.json')}
    from amplifier_acp.native.session_files import project_slug
    history=Path(settings['home'])/'projects'/project_slug(cwd)/'sessions'/sid
    canonical={p.relative_to(history):p.read_bytes() for p in history.rglob('*') if p.is_file()}
    control,_=await initialized(config);call=admin(control,cwd)
    async def lease(name):
        preview=await call('maintenance.reset.preview',{'nativeSessionId':sid,'cwd':str(cwd),'scope':'session-configuration','privateContentReviewed':True})
        held=await call('maintenance.acquire',{'commandId':name,'previewHash':preview['previewHash']})
        return preview,held
    try:
        preview,held=await lease('reset-lease')
        result=await call('maintenance.reset.apply',{'commandId':'reset','leaseId':held['leaseId'],'previewHash':preview['previewHash']})
        assert result['canonicalFilesChanged']==0 and not (app/'configuration.json').exists()
        current=json.loads((app/'control-state.json').read_text());assert 'selection' not in current and current['task']==state['task'] and current['capacityReceipts']==state['capacityReceipts']
        assert all((history/p).read_bytes()==raw for p,raw in canonical.items())
        await call('maintenance.release',{'leaseId':held['leaseId']})
        preview,held=await lease('restore-lease')
        restored=await call('maintenance.reset.restore',{'commandId':'restore','leaseId':held['leaseId'],'previewHash':preview['previewHash'],'resetCommandId':'reset','expectedPostResetHash':result['postResetHash']})
        assert restored['restored'] and all((app/name).read_bytes()==raw for name,raw in original.items())
        assert all((history/p).read_bytes()==raw for p,raw in canonical.items())
        await call('maintenance.release',{'leaseId':held['leaseId']})
    finally:await control.close()


@pytest.mark.asyncio
async def test_expiry_and_process_death_release_exclusive_ownership(tmp_path):
    config,cwd,settings,runtime,sid=await seeded(tmp_path)
    await runtime.close();control,_=await initialized(config);call=admin(control,cwd)
    preview=await call('maintenance.preview',selections(sid,cwd))
    first=await call('maintenance.acquire',{'commandId':'short','previewHash':preview['previewHash'],'ttlSeconds':1})
    try:
        await asyncio.sleep(1.1)
        assert (await call('maintenance.lease'))['active'] is False
        same=await call('maintenance.acquire',{'commandId':'short','previewHash':preview['previewHash'],'ttlSeconds':1})
        assert same['active'] is False and same['replayed'] is False
        second=await call('maintenance.acquire',{'commandId':'second','previewHash':preview['previewHash'],'ttlSeconds':30})
        assert second['leaseId']!=first['leaseId']
        control.process.kill();await control.process.wait();await control.reader
        replacement,_=await initialized(config)
        try:
            recovered=await admin(replacement,cwd)('maintenance.acquire',{'commandId':'after-death','previewHash':preview['previewHash']})
            assert recovered['active']
            assert (await admin(replacement,cwd)('maintenance.receipt',{'commandId':'second'}))['receipt']['state']=='succeeded'
        finally:await replacement.close()
    finally:
        if control.process.returncode is None:await control.close()


@pytest.mark.asyncio
async def test_foundation_external_writer_refuses_acquisition_and_credentials_require_review(tmp_path,monkeypatch):
    config,cwd,settings,runtime,sid=await seeded(tmp_path);await runtime.close()
    monkeypatch.setenv('AMPLIFIER_HOME',settings['home'])
    from amplifier_foundation.session.shared_state import SharedSessionStore
    external=SharedSessionStore(cwd,sid).acquire(app='cooperating-cli-fixture')
    control,_=await initialized(config);call=admin(control,cwd)
    try:
        denied=await call('maintenance.preview',selections(sid,cwd,includeCredentials=True),okay=False)
        assert 'credential review' in denied['message']
        preview=await call('maintenance.preview',selections(sid,cwd))
        denied=await call('maintenance.acquire',{'commandId':'external-busy','previewHash':preview['previewHash']},okay=False)
        assert denied and not (await call('maintenance.lease'))['active']
        external.release()
        (Path(settings['home'])/'keys.env').write_text('SYNTHETIC_PRIVATE_KEY=not-an-account\n')
        preview=await call('maintenance.preview',selections(sid,cwd,includeCredentials=True,credentialsReviewed=True))
        assert 'not-an-account' not in json.dumps(preview)
        assert any(r['path'].endswith('keys.env') for r in preview['inventory']['files'])
        # A stale review refuses; it never quietly creates a different snapshot.
        (Path(settings['home'])/'settings.yaml').write_text('changed: true\n')
        denied=await call('maintenance.acquire',{'commandId':'stale','previewHash':preview['previewHash']},okay=False)
        assert 'changed after review' in denied['message']
    finally:external.release();await control.close()


@pytest.mark.asyncio
async def test_denied_authority_unprotected_writers_symlinks_and_bounds(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir();home=tmp_path/'home';app=tmp_path/'app';config=tmp_path/'agent.json'
    values={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)],'workerCommand':['/impossible/worker']};config.write_text(json.dumps(values))
    control,_=await initialized(config)
    try:
        denied=await admin(control,workspace)('maintenance.preview',{'parts':['native-settings'],'privateContentReviewed':True},okay=False)
        assert 'launcher enablement' in denied['message'] and not app.exists() and not home.exists()
    finally:await control.close()
    values['adminMaintenance']=True;config.write_text(json.dumps(values));control,_=await initialized(config);call=admin(control,workspace)
    try:
        denied=await call('maintenance.preview',selections('missing',workspace),okay=False);assert 'unprotected' in denied['message']
        home.mkdir(exist_ok=True);(home/'settings.yaml').symlink_to(config)
        denied=await call('maintenance.preview',{'parts':['native-settings'],'privateContentReviewed':True},okay=False);assert 'symbolic links' in denied['message']
        assert 'error' in await control.request('_amplifier/admin',{'cwd':str(tmp_path),'operation':'maintenance.preview','args':{'parts':['native-settings'],'privateContentReviewed':True}})
        assert 'error' in await control.request('_amplifier/admin',{'cwd':str(workspace),'operation':'maintenance.artifact.read','args':{'artifactId':'../../outside','sha256':'a'*64}})
    finally:await control.close()


@pytest.mark.asyncio
async def test_reset_unknown_retains_originals_fences_start_and_never_replays(tmp_path,monkeypatch):
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance import Maintenance
    from amplifier_acp.native.session_files import project_slug
    from amplifier_acp.session_lifecycle import guard_start
    from types import SimpleNamespace
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'home';app=tmp_path/'app';sid='selected'
    native=home/'projects'/project_slug(cwd)/'sessions'/sid;native.mkdir(parents=True);(native/'metadata.json').write_text(json.dumps({'session_id':sid}));(native/'transcript.jsonl').write_text('{}\n')
    local=app/'sessions'/sid;local.mkdir(parents=True);original=b'{"goal":"preserve","selection":{"model":"old"}}';(local/'control-state.json').write_bytes(original)
    monkeypatch.setenv('AMPLIFIER_HOME',str(home))
    config={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceExternalWriters':'foundation-cooperative'}
    owner=Maintenance(Administration(config));workspace=str(cwd)
    preview=await owner.perform(workspace,'maintenance.reset.preview',{'nativeSessionId':sid,'cwd':workspace,'scope':'session-configuration','privateContentReviewed':True})
    lease=await owner.perform(workspace,'maintenance.acquire',{'commandId':'lease','previewHash':preview['previewHash']})
    calls=[]
    def fail(*args):calls.append('effect');raise OSError('injected after retained originals and pending marker')
    monkeypatch.setattr(owner,'install',fail)
    args={'commandId':'uncertain','leaseId':lease['leaseId'],'previewHash':preview['previewHash']}
    try:
        with pytest.raises(OSError):await owner.perform(workspace,'maintenance.reset.apply',args)
        receipt=(await owner.perform(workspace,'maintenance.receipt',{'commandId':'uncertain'}))['receipt'];assert receipt['state']=='unknown'
        duplicate=await owner.perform(workspace,'maintenance.reset.apply',args);assert duplicate['receipt']['state']=='unknown' and calls==['effect']
        pending=json.loads((local/'maintenance-reset-pending.json').read_text());assert (owner.root/'retained'/pending['retainedId']/'control-state.json').read_bytes()==original
        with pytest.raises(ValueError,match='reset outcome is unresolved'):guard_start(SimpleNamespace(config=config),sid,workspace)
        assert (native/'transcript.jsonl').read_text()=='{}\n'
    finally:await owner.close()


@pytest.mark.asyncio
async def test_completed_reset_fence_reconciles_only_exact_durable_post_state(tmp_path,monkeypatch):
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance import Maintenance
    from amplifier_acp.native.session_files import project_slug
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'home';app=tmp_path/'app';sid='selected'
    native=home/'projects'/project_slug(cwd)/'sessions'/sid;native.mkdir(parents=True);(native/'metadata.json').write_text('{}')
    local=app/'sessions'/sid;local.mkdir(parents=True);(local/'control-state.json').write_text('{"goal":"kept","selection":{"model":"old"}}')
    monkeypatch.setenv('AMPLIFIER_HOME',str(home))
    config={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'maintenanceExternalWriters':'foundation-cooperative'}
    owner=Maintenance(Administration(config));workspace=str(cwd)
    preview=await owner.perform(workspace,'maintenance.reset.preview',{'nativeSessionId':sid,'cwd':workspace,'scope':'session-configuration','privateContentReviewed':True})
    lease=await owner.perform(workspace,'maintenance.acquire',{'commandId':'lease','previewHash':preview['previewHash']})
    original_finalize=owner.finalize_reset
    def lost_finalization(*_):raise OSError('after durable completed receipt')
    monkeypatch.setattr(owner,'finalize_reset',lost_finalization)
    try:
        with pytest.raises(OSError):await owner.perform(workspace,'maintenance.reset.apply',{'commandId':'reset','leaseId':lease['leaseId'],'previewHash':preview['previewHash']})
        assert (await owner.perform(workspace,'maintenance.receipt',{'commandId':'reset'}))['receipt']['state']=='succeeded'
        assert (local/'maintenance-reset-pending.json').exists()
        await owner.perform(workspace,'maintenance.release',{'leaseId':lease['leaseId']})
        preview=await owner.perform(workspace,'maintenance.preview',{'sessions':[{'nativeSessionId':sid,'cwd':workspace}],'parts':['session-state'],'privateContentReviewed':True})
        lease=await owner.perform(workspace,'maintenance.acquire',{'commandId':'proof-lease','previewHash':preview['previewHash']})
        monkeypatch.setattr(owner,'finalize_reset',original_finalize)
        result=await owner.perform(workspace,'maintenance.reset.reconcile',{'commandId':'proof','leaseId':lease['leaseId'],'previewHash':preview['previewHash'],'resetCommandId':'reset'})
        assert result['reconciled'] and not result['executedAgain'] and not (local/'maintenance-reset-pending.json').exists()
        assert json.loads((local/'control-state.json').read_text())=={'goal':'kept'}
    finally:await owner.close()


@pytest.mark.asyncio
async def test_cancelled_snapshot_thread_cannot_outlive_exclusive_gate(tmp_path,monkeypatch):
    import threading
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance import Maintenance
    from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'home';home.mkdir();app=tmp_path/'app'
    config={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True}
    owner=Maintenance(Administration(config));workspace=str(cwd)
    preview=await owner.perform(workspace,'maintenance.preview',{'parts':['native-settings'],'privateContentReviewed':True})
    lease=await owner.perform(workspace,'maintenance.acquire',{'commandId':'lease','previewHash':preview['previewHash']})
    entered=threading.Event();finish=threading.Event()
    def slow_capture(*_):
        entered.set();assert finish.wait(5);owner.valid(lease['leaseId'])
    monkeypatch.setattr(owner,'snapshot',slow_capture)
    task=asyncio.create_task(owner.perform(workspace,'maintenance.snapshot',{'commandId':'slow','leaseId':lease['leaseId'],'previewHash':preview['previewHash']}))
    try:
        while not entered.is_set():await asyncio.sleep(.001)
        task.cancel();await asyncio.sleep(.01)
        assert not task.done()
        with pytest.raises(MaintenanceBusy):Gate(config)
        finish.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert owner.receipt(workspace,'slow')[1]['state']=='unknown'
    finally:finish.set();await owner.close()
    with Gate(config):pass


@pytest.mark.asyncio
async def test_background_admin_signin_retains_writer_gate_after_request(tmp_path,monkeypatch):
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance_gate import Gate,MaintenanceBusy
    from types import SimpleNamespace
    workspace=tmp_path/'workspace';workspace.mkdir()
    config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(workspace)]}
    owner=Administration(config);finished=asyncio.Event();task=asyncio.create_task(finished.wait())
    owner.setup=SimpleNamespace(logins={'provider':{'task':task}})
    async def authorized(*_):return {'status':'waiting'}
    monkeypatch.setattr(owner,'authorized',authorized)
    try:
        result=await owner.perform(str(workspace),'providers.login',{})
        assert result['status']=='waiting'
        with pytest.raises(MaintenanceBusy):Gate(config,exclusive=True)
        finished.set();await task;await asyncio.sleep(0)
        with Gate(config,exclusive=True):pass
    finally:finished.set();await task


def test_advisory_release_failure_still_releases_native_home_gate(tmp_path):
    from amplifier_acp.maintenance import release_all
    from amplifier_acp.maintenance_gate import Gate
    config={'home':str(tmp_path/'home')};calls=[]
    class BrokenAdvisory:
        def release(self):calls.append('failed advisory');raise OSError('disk full while writing released metadata')
    class AnotherLease:
        def release(self):calls.append('another lease released')
    gate=Gate(config,exclusive=True)
    with pytest.raises(ValueError,match='attempting every release'):release_all([gate,AnotherLease(),BrokenAdvisory()])
    assert calls==['failed advisory','another lease released']
    with Gate(config,exclusive=True):pass
