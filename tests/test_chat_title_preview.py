import json
import os
import pytest
from amplifier_acp.chat_title_preview import read

def write(directory, rows):
    (directory/'transcript.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))

def test_bounded_preview_ignores_observations_and_does_not_write(tmp_path):
    write(tmp_path,[{'role':'user','content':'internal','metadata':{'amplifier_input':{'kind':'observation'}}},
                   {'role':'assistant','content':'not user'}, {'role':'user','content':[{'type':'text','text':'First\n useful message'}]}])
    before=(tmp_path/'transcript.jsonl').read_bytes()
    assert read(tmp_path,{'title':'Conversation 12345678'})=={'title':'First useful message','source':'first-message'}
    assert (tmp_path/'transcript.jsonl').read_bytes()==before
    assert sorted(p.name for p in tmp_path.iterdir())==['transcript.jsonl']

def test_saved_names_are_never_replaced_and_empty_primary_wins(tmp_path):
    write(tmp_path,[])
    (tmp_path/'transcript.jsonl.backup').write_text('{"role":"user","content":"old"}\n')
    assert read(tmp_path,{'title':'Conversation 12345678','titleSource':'manual'})['source']=='saved'
    assert read(tmp_path,{'title':'Conversation 12345678'})['source']=='unnamed'
    (tmp_path/'transcript.jsonl').unlink()
    assert read(tmp_path,{'title':'Conversation 12345678'})['title']=='old'

@pytest.mark.parametrize('kind',['symlink','fifo','oversized','too-many-lines'])
def test_never_follows_special_files_or_exceeds_read_bounds(tmp_path,kind):
    path=tmp_path/'transcript.jsonl'
    if kind=='symlink':
        target=tmp_path/'target';target.write_text('{"role":"user","content":"secret"}\n');path.symlink_to(target)
    elif kind=='fifo':os.mkfifo(path)
    elif kind=='oversized':write(tmp_path,[{'role':'user','content':'x'*(256*1024)}])
    else:write(tmp_path,[{'role':'assistant','content':'skip'}]*128+[{'role':'user','content':'outside bound'}])
    assert read(tmp_path,{'title':'Conversation 12345678'})['source']=='unnamed'
