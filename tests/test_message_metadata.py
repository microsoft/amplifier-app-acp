"""Installed-capable cold native message adjunct contract checks."""
import hashlib
import json
from pathlib import Path
import pytest
from amplifier_acp.history import message_id
from amplifier_acp.message_metadata import MessageError, MessageMetadata, freeze_quote, resolve_reference

SID = 'test-message-session'

@pytest.fixture
def selected(tmp_path):
    directory = tmp_path / 'canonical'; directory.mkdir()
    row = {'role': 'assistant', 'content': 'Earlier public answer ' * 250}
    transcript = directory / 'transcript.jsonl'; transcript.write_text(json.dumps(row) + '\n')
    (directory / 'events.jsonl').write_text('preserved events\n')
    reference = {'sessionId': SID, 'messageId': message_id(row, 0), 'position': 0, 'historyEpoch': None}
    return tmp_path, directory, transcript, reference


def store(selected, actor='user-one'):
    return MessageMetadata(selected[0] / 'app', SID, actor_id=actor)


def test_passive_untouched_has_no_storage_or_execution(selected):
    metadata = store(selected);before = selected[2].read_bytes()
    assert metadata.read([selected[3]['messageId']])['items'][0]['revision'] == 0
    assert metadata.receipt('missing') == {'receipt': None, 'replayed': False}
    assert metadata.export_metadata([selected[3]['messageId']])['executionAuthority'] == 'none'
    assert not (selected[0] / 'app').exists()
    assert selected[2].read_bytes() == before


def test_freeze_quote_is_native_bounded_and_input_bound(selected):
    _,directory,transcript,ref = selected
    before = transcript.read_bytes()
    quote = freeze_quote(directory, SID, ref, input_id='explicit-send-1')
    assert len(quote['excerpt']) == 4000 and quote['truncated']
    assert quote['inputId'] == 'explicit-send-1'
    assert quote['sourceDigest'] == hashlib.sha256(('Earlier public answer ' * 250).encode()).hexdigest()
    assert freeze_quote(directory,SID,ref,input_id='explicit-send-2')['id'] != quote['id']
    assert transcript.read_bytes() == before and not (selected[0] / 'app').exists()


@pytest.mark.parametrize('change', ['session','identity','position','epoch','digest','archive','excerpt'])
def test_forged_or_stale_reference_refuses_before_storage(selected, change):
    ref = dict(selected[3])
    if change=='session': ref['sessionId']='other'
    if change=='identity': ref['messageId']='native:wrong'
    if change=='position': ref['position']=1
    if change=='epoch': ref['historyEpoch']='old-context'
    if change=='digest': ref['sourceDigest']='0'*64
    if change=='archive': ref['messageId']='archive:old:'+ref['messageId']
    if change=='excerpt': ref['excerpt']='client-injected'
    with pytest.raises(MessageError): store(selected).reaction(selected[1],ref,emoji='👍',present=True,command_id='rejected')
    assert not (selected[0]/'app').exists()


def test_append_safe_rewrite_rejected(selected):
    _,directory,transcript,ref=selected
    with transcript.open('a') as f:f.write(json.dumps({'role':'user','content':'later'})+'\n')
    assert resolve_reference(directory,SID,ref)['messageId']==ref['messageId']
    transcript.write_text(json.dumps({'role':'assistant','content':'rewritten'})+'\n')
    with pytest.raises(MessageError): resolve_reference(directory,SID,ref)


def test_exact_receipts_and_two_actor_desired_state(selected):
    a,b=store(selected),store(selected,'agent-bound-user-two');directory,ref=selected[1],selected[3]
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    first=a.reaction(directory,ref,emoji='👍',present=True,command_id='one')
    assert first['revision']==1 and first['changed'] and first['mine']==['👍']
    assert a.reaction(directory,ref,emoji='👍',present=True,command_id='one')==first
    same=a.reaction(directory,ref,emoji='👍',present=True,command_id='two')
    assert same['revision']==1 and not same['changed']
    both=b.reaction(directory,ref,emoji='👍',present=True,command_id='three')
    assert both['counts']=={'👍':2} and both['revision']==2
    removed=a.reaction(directory,ref,emoji='👍',present=False,command_id='four')
    assert removed['counts']=={'👍':1} and removed['mine']==[]
    assert b.read([ref['messageId']])['items'][0]['mine']==['👍']
    assert a.receipt('one')['receipt']==first and b.receipt('one')['receipt'] is None
    with pytest.raises(MessageError): b.reaction(directory,ref,emoji='👍',present=True,command_id='one')
    with pytest.raises(MessageError): a.reaction(directory,ref,emoji='👍',present=False,command_id='one')
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before


def test_restart_receipt_survives_missing_history_and_export(selected):
    a=store(selected);ref=selected[3]
    result=a.reaction(selected[1],ref,emoji='❤️',present=True,command_id='committed')
    selected[2].unlink()
    fresh=store(selected)
    assert fresh.reaction(selected[1],ref,emoji='❤️',present=True,command_id='committed')==result
    exported=fresh.export_metadata([ref['messageId']])
    assert exported['items'][0]['reactions']==[{'actorId':'user-one','emoji':'❤️'}]
    assert 'committed' not in json.dumps(exported)
    exported['items'][0]['reactions'].clear()
    assert fresh.read([ref['messageId']])['items'][0]['reactions']==['❤️']


@pytest.mark.parametrize('row',[{'role':'system','content':'hidden'},{'role':'tool','content':'output'}, {'role':'assistant','content':'hidden','metadata':{'ephemeral':True}},{'role':'user','content':'service','metadata':{'amplifier_input':{'kind':'service','source':'watch'}}}])
def test_no_hidden_service_or_tool_quote(selected,row):
    selected[2].write_text(json.dumps(row)+'\n');ref={**selected[3],'messageId':message_id(row,0)}
    with pytest.raises(MessageError):freeze_quote(selected[1],SID,ref,input_id='input')


def test_selected_bounds_and_symlink_refusal(selected):
    a=store(selected)
    with pytest.raises(MessageError):a.read(['id']*51)
    with pytest.raises(MessageError):a.read(['id','id'])
    transcript=selected[2];transcript.rename(transcript.with_suffix('.saved'));transcript.symlink_to(transcript.with_suffix('.saved'))
    with pytest.raises(ValueError):a.reaction(selected[1],selected[3],emoji='👍',present=True,command_id='no')
    assert not a.path.exists()


def test_protocol_handler_refuses_forged_actor_and_scope(selected):
    from amplifier_acp.message_metadata import handle_message_metadata
    a=store(selected);args={'sessionId':SID,'reference':selected[3],'emoji':'🎉','present':True,'commandId':'native-command'}
    for forged in ({**args,'actorId':'someone-else'},{**args,'sessionId':'different'}):
        with pytest.raises(MessageError):handle_message_metadata(a,'_amplifier/message/reaction',forged,directory=selected[1])
    assert not a.path.exists()
    result=handle_message_metadata(a,'_amplifier/message/reaction',args,directory=selected[1])
    assert result['mine']==['🎉'] and result['sent'] is False
    assert handle_message_metadata(a,'_amplifier/message/receipt',{'sessionId':SID,'commandId':'native-command'})['receipt']==result


def test_concurrent_connections_preserve_actor_presence(selected):
    from concurrent.futures import ThreadPoolExecutor
    def react(actor):
        return store(selected,actor).reaction(selected[1],selected[3],emoji='✅',present=True,command_id=actor)
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(react,['user-a','user-b','user-c','user-d']))
    result=store(selected,'user-a').read([selected[3]['messageId']])['items'][0]
    assert result['counts']=={'✅':4} and result['revision']==4
    assert sorted(row['revision'] for row in results)==[1,2,3,4]
