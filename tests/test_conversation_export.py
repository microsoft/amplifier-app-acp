import copy
import json
import pytest
from amplifier_acp.conversation_export import render, contribution


def fixtures():
    messages=[{'id':'native:one','role':'user','text':'Literal **markdown** and id user-authored',
               'provenance':{'createdAt':'2026-10-11T10:00:00Z','inputOrigin':'user','activeInputId':'turn'},
               'publicMessage':{'version':1,'inputId':'turn','capture':'host-standard','blocks':[
                   {'type':'text','text':'Literal **markdown** and id user-authored'},
                   {'type':'attachment','reference':{'owner':'resources','id':'private-attachment'},'name':'notes.txt','mimeType':'text/plain'}]},
               'attachments':[{'type':'attachment','reference':{'owner':'resources','id':'private-attachment'},'name':'notes.txt','mimeType':'text/plain','omittedPayload':True}]},
              {'id':'native:two','role':'assistant','text':'Result','provenance':{}}]
    context={'version':1,'source':'host-retained-observations','work':[
        {'anchor':'turn','status':'completed','toolCalls':2,'modelCalls':3,'durationSeconds':1.2345},
        {'anchor':'unmatched','status':'unknown','toolCalls':1,'modelCalls':0}],
        'artifacts':[{'id':'private-artifact','title':'Report [with](link)','kind':'markdown','messageId':'turn'},
                     {'id':'outside','title':'Other','kind':'text'}],'omissions':[]}
    return messages,context


def test_metadata_controls_preserve_text_and_do_not_leak_generated_references():
    messages,context=fixtures();before=copy.deepcopy([messages,context])
    body,summary=render(messages,{'format':'json','includeExecution':True,'includeTimestamps':True,
                                 'includeConversationId':False,'includeReferenceIds':False,'exportContext':context},'private-session','private-revision',{'turn':'native:one'})
    result=json.loads(body)
    assert result['messages'][0]['timestamp']=='2026-10-11T10:00:00+00:00'
    assert 'timestamp' not in result['messages'][1]
    assert result['messages'][0]['workSummaries'][0]['modelCalls']==3
    assert len(result['otherWorkSummaries'])==1 and summary['executionSummaryCount']==2
    assert result['messages'][0]['text']==messages[0]['text']
    assert not any(word in body.decode() for word in ['private-attachment','private-artifact','private-session','private-revision','native:one','activeInputId'])
    assert [messages,context]==before
    assert {'code':'some_message_timestamps_not_recorded'} in summary['omissions']


def test_range_uses_canonical_aliases_excludes_unmatched_work_and_defaults_hide_timestamps():
    messages,context=fixtures()
    args={'format':'markdown','scope':'range','minimal':True,'includeExecution':True,'exportContext':context}
    body,summary=render(messages[:1],args,'private-session','revision',{'turn':'native:one'})
    text=body.decode()
    assert '2 tool calls · 3 model calls · 1.234 seconds' in text
    assert 'Other retained work' not in text and 'unknown' not in text and 'Other (' not in text
    assert '2026-10-11' not in text and 'private-session' not in text
    assert 'private-attachment' in text and 'private-artifact' in text
    assert 'Report \\[with\\](link)' in text
    assert summary['artifactCount']==1 and summary['executionSummaryCount']==1
    body,_=render(messages,{'minimal':True},'private-session','revision',{})
    assert 'sessionId' not in json.loads(body) and all('provenance' not in row for row in json.loads(body)['messages'])


def test_untrusted_contribution_is_structural_bounded_data_only():
    messages,context=fixtures()
    for bad in [{'includeExecution':'yes'},{'includeReferenceIds':0},{'includeConversationId':None}]:
        with pytest.raises(ValueError):render(messages,bad,'s','r',{})
    for change in [{'output':'SECRET'}, {'reasoning':'SECRET'}, {'status':'finished-ish'}, {'durationSeconds':float('nan')}, {'toolCalls':True}]:
        bad=copy.deepcopy(context);bad['work'][0].update(change)
        with pytest.raises(ValueError):contribution(bad)
    bad=copy.deepcopy(context);bad['artifacts'][0]['path']='/private/file'
    with pytest.raises(ValueError):contribution(bad)
    bad=copy.deepcopy(context);bad['work']*=501
    with pytest.raises(ValueError):contribution(bad)
