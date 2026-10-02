import json
import pytest
from amplifier_acp.history import page,HistoryError

def history(path,count=7):
    path.mkdir();file=path/'transcript.jsonl'
    with file.open('w') as out:
        for i in range(count):
            for row in [{'role':'user','content':str(i),'metadata':{'amplifier_input':{'id':'turn-'+str(i)}}},
                        {'role':'assistant','content':'', 'tool_calls':[{'id':'t-'+str(i),'function':{'name':'read','arguments':'{}'}}]},
                        {'role':'tool','tool_call_id':'t-'+str(i),'content':'tool result'},
                        {'role':'assistant','content':'reply '+str(i)}]:out.write(json.dumps(row)+'\n')
    return file

def test_tail_pages_preserve_whole_turns_and_stable_ids(tmp_path):
    p=tmp_path/'session';history(p)
    a=page(p,limit=2);assert [r['id'] for r in a['turnGroups']]==['turn-5','turn-6']
    assert all(len(r['updates'])==4 for r in a['turnGroups'])
    b=page(p,cursor=a['nextCursor'],limit=2);assert [r['id'] for r in b['turnGroups']]==['turn-3','turn-4']
    c=page(p,cursor=b['nextCursor'],limit=2);d=page(p,cursor=c['nextCursor'],limit=2)
    assert [r['id'] for r in d['turnGroups']]==['turn-0'];assert d['nextCursor'] is None
    assert all(u['_meta']['amplifier.dev/history']['turnId']=='turn-5' for u in a['turnGroups'][0]['updates'])

def test_cursor_rejects_changed_history_and_traversal(tmp_path):
    p=tmp_path/'session';file=history(p);a=page(p,limit=1)
    with file.open('a') as out:out.write('{"role":"user","content":"later"}\n')
    with pytest.raises(HistoryError,match='changed'):page(p,cursor=a['nextCursor'])
    with pytest.raises(HistoryError,match='cursor'):page(p,cursor='../../anything')

def test_no_events_or_worker_needed_and_bytes_bounded(tmp_path):
    p=tmp_path/'session';history(p,2000);(p/'events.jsonl').symlink_to('/not/readable')
    a=page(p,limit=1,max_bytes=100_000);assert len(a['turnGroups'])==1
    (p/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'x'*2100000})+'\n')
    with pytest.raises(HistoryError,match='oversized'):page(p,limit=1)

def test_backup_and_malformed_records_are_explicit(tmp_path):
    p=tmp_path/'session';file=history(p);file.rename(p/'transcript.jsonl.backup')
    assert page(p)['diagnostics'][0]['code']=='backup_read'
    file.write_text('not-json\n')
    with pytest.raises(HistoryError,match='malformed'):page(p)
