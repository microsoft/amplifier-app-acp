import json,os,time,uuid
import pytest
from test_session_lifecycle import prepared,prompt
from amplifier_acp.server import Server
from amplifier_acp.session_lifecycle import Lifecycle

@pytest.fixture(autouse=True)
def private_writer(tmp_path,monkeypatch):monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))

def age(owner,seconds):
    metadata=json.loads((owner.directory/'metadata.json').read_text())
    for field in ('created','created_at','updated_at','modified_at'):
        if field in metadata:metadata[field]=seconds
    (owner.directory/'metadata.json').write_text(json.dumps(metadata))
    for name in ('metadata.json','transcript.jsonl','events.jsonl'):
        path=owner.directory/name
        if path.exists():os.utime(path,(seconds,seconds))

@pytest.mark.asyncio
async def test_retention_hide_rechecks_native_activity_and_preserves_all_canonical_bytes(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'preserved','one');await client.close()
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd));cutoff=time.time()-86400
    age(owner,cutoff-86400);original={name:(owner.directory/name).read_bytes() for name in ('metadata.json','transcript.jsonl','events.jsonl') if (owner.directory/name).exists()}
    revision=owner.inspect()['historyRevision'];args={'commandId':'hide-reviewed','expectedHistoryRevision':revision,'modifiedBefore':cutoff,'descendants':[]}
    # New background activity after the preview is enough to prevent hiding.
    events=owner.directory/'events.jsonl';events.write_text('new observation\n')
    with pytest.raises(ValueError,match='newer'):owner.perform('retention.hide',args)
    assert owner.saved(args['commandId']) is None and not owner.inspect()['state']['deleted']
    if 'events.jsonl' in original:events.write_bytes(original['events.jsonl'])
    else:events.unlink()
    age(owner,cutoff-86400);args['expectedHistoryRevision']=owner.inspect()['historyRevision'];before={p.name:p.read_bytes() for p in owner.directory.iterdir() if p.is_file()}
    result=owner.perform('retention.hide',args);assert result['deleted'] is True and result['retention']['canonicalFilesDeleted']==0 and result['familyGuard']['ownersHeld']
    assert owner.perform('retention.hide',args)['receipt']['result']==result['receipt']['result']
    assert before=={p.name:p.read_bytes() for p in owner.directory.iterdir() if p.is_file()}

@pytest.mark.asyncio
async def test_retention_refuses_unknown_canonical_journal_and_live_native_owner(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path);await prompt(client,sid,'preserved','one')
    server=Server(json.loads(config.read_text()),None);owner=Lifecycle(server,sid,str(cwd));cutoff=time.time()-86400;age(owner,cutoff-86400)
    args={'commandId':'still-active','expectedHistoryRevision':owner.inspect()['historyRevision'],'modifiedBefore':cutoff,'descendants':[]}
    with pytest.raises(Exception,match='owned|busy|owner'):owner.perform('retention.hide',args)
    assert owner.saved(args['commandId']) is None;await client.close()
    (owner.app/'history-edit-pending.json').write_text('{}')
    with pytest.raises(ValueError,match='unresolved'):owner.perform('retention.hide',{**args,'commandId':'unknown-edit'})
    assert owner.saved('unknown-edit') is None
