import asyncio,json,sqlite3
from pathlib import Path
import pytest
from client import Client

CONTEXT={'fenceId':'app-reset-fence','commandId':'app-reset-hold','purpose':'recovery','instanceId':'test-instance','dataScope':'private-test'}
async def opened(tmp_path):
    home=tmp_path/'home';home.mkdir(exist_ok=True);app=tmp_path/'app';app.mkdir(exist_ok=True);cwd=tmp_path/'workspace';cwd.mkdir(exist_ok=True)
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True,'workerCommand':['/impossible/no-worker']}))
    client=await Client.launch(config);info=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert info['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['maintenance']['appReset']['parts']==['native.app-bundle-default']
    return client,cwd,home,app
async def hold(client,cwd):
    value=(await client.request('_amplifier/admin/lifecycle',{'cwd':str(cwd),'operation':'acquire','args':CONTEXT}))['result']['lease'];return {k:value[k] for k in ('fenceId','commandId','leaseId')}
async def call(client,cwd,op,args,auth=None):
    row=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'maintenance.appReset.'+op,'args':args,**({'_meta':{'amplifier.dev/admin':auth}} if auth else {})})
    return row
async def prepare(client,cwd,auth,command,**extra):
    result=await call(client,cwd,'prepare',{'commandId':command,'parts':['native.app-bundle-default'],'privateContentReviewed':True,**extra},auth)
    assert 'result' in result,result;return result['result']

@pytest.mark.asyncio
async def test_protocol_app_default_reset_exact_retention_undo_and_no_shared_changes(tmp_path):
    client,cwd,home,app=await opened(tmp_path);path=app/'admin-settings.yaml'
    raw=b'bundle:\n  active: fixture\n  other: retained\nprivate-other: never-present-in-reviews\n';path.write_bytes(raw)
    (home/'keys.env').write_text('fixture-secret-preserved');(home/'settings.yaml').write_text('shared: retained')
    try:
        denied=await call(client,cwd,'prepare',{'commandId':'unheld','parts':['native.app-bundle-default'],'privateContentReviewed':True});assert denied['error']['data']['executed'] is False
        auth=await hold(client,cwd);review=await prepare(client,cwd,auth,'prepare')
        assert 'never-present' not in json.dumps(review) and 'fixture-secret' not in json.dumps(review)
        args={'commandId':'apply','preparedId':review['preparedId'],'reviewHash':review['reviewHash']}
        changed=(await call(client,cwd,'apply',args,auth))['result'];assert changed['receipt']['state']=='succeeded' and 'active' not in path.read_text() and 'other: retained' in path.read_text()
        same=(await call(client,cwd,'apply',args,auth))['result'];assert same['postResetRevision']==changed['postResetRevision'] and same['replayed'] is False
        receipt=(await call(client,cwd,'inspect',{'commandId':'apply'}))['result']['receipt'];assert receipt['state']=='succeeded'
        restore=await prepare(client,cwd,auth,'restore-review',restoreCommandId='apply')
        undo=(await call(client,cwd,'restore',{'commandId':'undo','preparedId':restore['preparedId'],'reviewHash':restore['reviewHash'],'resetCommandId':'apply','expectedPostResetRevision':changed['postResetRevision']},auth))['result'];assert undo['restored'] and path.read_bytes()==raw
        assert (home/'keys.env').read_text()=='fixture-secret-preserved' and (home/'settings.yaml').read_text()=='shared: retained'
        assert not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_stale_review_expiry_changed_undo_and_cross_process_exact_receipts(tmp_path):
    client,cwd,home,app=await opened(tmp_path);path=app/'admin-settings.yaml';path.write_text('bundle:\n  active: first\n')
    try:
        auth=await hold(client,cwd);review=await prepare(client,cwd,auth,'review')
        path.write_text('bundle:\n  active: external-change\n')
        rejected=await call(client,cwd,'apply',{'commandId':'stale','preparedId':review['preparedId'],'reviewHash':review['reviewHash']},auth);assert rejected['error']['data']['executed'] is False
        review=await prepare(client,cwd,auth,'fresh');result=(await call(client,cwd,'apply',{'commandId':'apply','preparedId':review['preparedId'],'reviewHash':review['reviewHash']},auth))['result']
        undo=await prepare(client,cwd,auth,'undo-review',restoreCommandId='apply');path.write_text('newer: work\n')
        failed=await call(client,cwd,'restore',{'commandId':'undo','preparedId':undo['preparedId'],'reviewHash':undo['reviewHash'],'resetCommandId':'apply','expectedPostResetRevision':result['postResetRevision']},auth);assert failed['error']['data']['executed'] is False and path.read_text()=='newer: work\n'
    finally:await client.close()
    restarted,cwd,_,_=await opened(tmp_path)
    try:
        receipt=(await call(restarted,cwd,'inspect',{'commandId':'apply'}))['result']['receipt'];assert receipt['state']=='succeeded' and receipt['result']['postResetRevision']==result['postResetRevision']
        assert not restarted.events
    finally:await restarted.close()

@pytest.mark.asyncio
async def test_expired_review_and_unknown_after_file_commit_never_replay(tmp_path,monkeypatch):
    from amplifier_acp.admin import Administration
    from amplifier_acp.app_reset import digest
    # Real owner and OS gates, with a bounded injected disk-acknowledgement fault.
    home=tmp_path/'home';home.mkdir();app=tmp_path/'app';app.mkdir();cwd=tmp_path/'workspace';cwd.mkdir()
    owner=Administration({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'adminMaintenance':True})
    path=app/'admin-settings.yaml';path.write_text('bundle:\n  active: original\nother: retain\n')
    held=owner.lifecycle.perform('acquire',CONTEXT);auth={k:held['lease'][k] for k in ('fenceId','commandId','leaseId')}
    async def request(op,args):return await owner.perform(str(cwd),'maintenance.appReset.'+op,args,fence=auth)
    try:
        first=await request('prepare',{'commandId':'expired-review','parts':['native.app-bundle-default'],'privateContentReviewed':True})
        with owner.maintenance.db() as db:
            row=db.execute('SELECT body FROM app_reset_previews WHERE id=?',(first['preparedId'],)).fetchone();body=json.loads(row[0]);body['expiresAt']=0;body.pop('reviewHash');body['reviewHash']=digest(body);db.execute('UPDATE app_reset_previews SET body=? WHERE id=?',(json.dumps(body),first['preparedId']))
        with pytest.raises(ValueError) as expired:await request('apply',{'commandId':'expired','preparedId':first['preparedId'],'reviewHash':body['reviewHash']})
        assert expired.value.data['executed'] is False and 'active' in path.read_text()
        review=await request('prepare',{'commandId':'fresh','parts':['native.app-bundle-default'],'privateContentReviewed':True})
        from amplifier_acp.native import shared_settings
        original=shared_settings.atomic_write
        def lost(*args,**kwargs):original(*args,**kwargs);raise OSError('fixture acknowledgement loss after file replacement')
        monkeypatch.setattr(shared_settings,'atomic_write',lost)
        args={'commandId':'uncertain','preparedId':review['preparedId'],'reviewHash':review['reviewHash']}
        with pytest.raises(OSError):await request('apply',args)
        current=path.read_bytes();assert b'active' not in current
        inspected=await request('inspect',{'commandId':'uncertain'});assert inspected['receipt']['state']=='unknown'
        same=await request('apply',args);assert same['receipt']['state']=='unknown' and path.read_bytes()==current
        with pytest.raises(ValueError) as unresolved:await request('prepare',{'commandId':'no-new-review','parts':['native.app-bundle-default'],'privateContentReviewed':True})
        assert unresolved.value.data['executed'] is False
    finally:await owner.close()
