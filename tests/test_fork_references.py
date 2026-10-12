import hashlib
import json
import pytest
from amplifier_acp.fork_references import prepare,publish
from amplifier_acp.history import message_id
from amplifier_acp.session_lifecycle import HistoryRows


def test_exact_retention_remaps_originals_and_omits_outside_boundary():
    first={'role':'user','content':'First','metadata':{'amplifier_input':{'id':'turn1'}}}
    answer={'role':'assistant','content':'Saved draft'}
    later={'role':'user','content':'Later','metadata':{'amplifier_input':{'id':'turn2'}}}
    rows=HistoryRows([(0,first),(100,answer),(200,later)],epoch='epoch')
    refs=[{'key':'draft','inputId':'turn1','role':'assistant','textSha256':hashlib.sha256(b'Saved draft').hexdigest()},
          {'key':'later','messageId':'turn2'}]
    plan=prepare(refs,rows,rows[:2],{0:0,1:1})
    target=HistoryRows([(0,first),(105,answer)])
    result=publish(plan,target,[first,answer],True)
    assert result=={'version':1,'complete':True,'execution':False,'items':[
        {'key':'draft','sourceMessageId':message_id(answer,100,'epoch'),'targetMessageId':message_id(answer,105),'disposition':'retained'},
        {'key':'later','sourceMessageId':message_id(later,200,'epoch'),'disposition':'omitted'}]}
    recovery=prepare(refs,rows,rows[:2],{0:0})
    assert publish(recovery,HistoryRows([(0,first)]),[first],True)['items'][0]['disposition']=='omitted'


def test_ambiguous_or_changed_admitted_message_refuses_copy():
    first={'role':'user','content':'First','metadata':{'amplifier_input':{'id':'turn1'}}}
    answer={'role':'assistant','content':'Same'}
    rows=HistoryRows([(0,first),(100,answer),(200,answer)])
    refs=[{'key':'draft','inputId':'turn1','role':'assistant','textSha256':hashlib.sha256(b'Same').hexdigest()}]
    with pytest.raises(ValueError,match='ambiguous'):prepare(refs,rows,rows,{0:0,1:1,2:2})
    refs[0]['inputId']='another-turn'
    with pytest.raises(ValueError,match='unavailable'):prepare(refs,rows,rows,{0:0,1:1,2:2})
    assert prepare(None,rows,rows,{}) is None


def test_main_identity_binds_native_session_ordinal_role_and_raw_text():
    rows=HistoryRows([(0,{'role':'user','content':'Hello'}),
        (80,{'role':'assistant','content':[{'type':'text','text':'Café'},{'type':'text','text':'Draft'}]})])
    legacy=hashlib.sha256(json.dumps(['source',1,'assistant','Café\nDraft'],ensure_ascii=False).encode()).hexdigest()[:32]
    refs=[{'key':'old-output','legacyMessageId':legacy}]
    result=prepare(refs,rows,rows,{0:0,1:1},source_session='source')
    assert result[0]['targetIndex']==1
    for wrong in ['another',None]:
        with pytest.raises(ValueError,match='unavailable'):
            prepare(refs,rows,rows,{0:0,1:1},source_session=wrong)
    with pytest.raises(ValueError,match='unavailable'):
        prepare(refs,HistoryRows(rows,epoch='cleared'),rows,{0:0,1:1},source_session='source')


def test_preserved_output_origins_are_verified_but_never_gain_current_copy_positions():
    old=HistoryRows([(0,{'role':'user','content':'Before','metadata':{'amplifier_input':{'id':'before'}}}),
        (80,{'role':'assistant','content':'Original report'})])
    current=HistoryRows([(0,{'role':'user','content':'After','metadata':{'amplifier_input':{'id':'after'}}})],epoch='cleared')
    legacy=hashlib.sha256(json.dumps(['source',1,'assistant','Original report'],ensure_ascii=False).encode()).hexdigest()[:32]
    refs=[{'key':'old','legacyMessageId':legacy},{'key':'current','messageId':'after'}]
    plan=prepare(refs,current,current,{0:0},source_session='source',preserved=[old])
    assert plan[0]['targetIndex'] is None
    assert plan[1]['targetIndex']==0
    with pytest.raises(ValueError,match='ambiguous'):
        prepare(refs,current,current,{0:0},source_session='source',preserved=[old,old])
