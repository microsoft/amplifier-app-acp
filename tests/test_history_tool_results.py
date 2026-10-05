"""Selected canonical call/result lifecycle across Native ACP and actual Host reduction."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from amplifier_acp.history import page
from amplifier_acp.native.session_files import project_slug


@pytest.mark.parametrize('shape', ['function', 'flat', 'tool_use', 'tool_call'])
@pytest.mark.parametrize('missing', [False, True])
def test_canonical_tool_result_reaches_actual_host_reducer(tmp_path, shape, missing):
    host = os.environ.get('HISTORY_HOST_MODULE')
    sdk = os.environ.get('HISTORY_AHP_SDK')
    if not host or not sdk:
        pytest.skip('Cross-component control requires the explicitly qualified Host and AHP SDK')
    cwd = tmp_path/'workspace';cwd.mkdir()
    home = tmp_path/'native-home';app = tmp_path/'native-app';app.mkdir()
    sid = 'history-tool-fixture'
    directory = home/'projects'/project_slug(str(cwd))/'sessions'/sid;directory.mkdir(parents=True)
    arguments = {'path': 'owned.txt'}
    if shape == 'function':
        call = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'exact-call', 'function': {'name': 'read_file', 'arguments': json.dumps(arguments)}}]}
    elif shape == 'flat':
        call = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'exact-call', 'name': 'read_file', 'arguments': arguments}]}
    else:
        call = {'role': 'assistant', 'content': [{'type': shape, 'id': 'exact-call', 'name': 'read_file', 'input': arguments}]}
    rows = [{'role': 'user', 'content': 'Read owned file', 'metadata': {'amplifier_input': {'id': 'saved-input'}}}, call]
    if not missing:
        rows.append({'role': 'tool', 'tool_call_id': 'exact-call', 'content': [{'type': 'text', 'text': 'Full owned result'}]})
    rows.append({'role': 'assistant', 'content': 'Recorded answer'})
    transcript = directory/'transcript.jsonl';transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    before = transcript.read_bytes()
    config = tmp_path/'native-config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(app),'workerCommand':['/must-not-start-for-passive-history']}))
    # Presence of the actual canonical role:tool result is explicit, not inferred from an output dict.
    assert sum(row['role']=='tool' for row in rows) == (0 if missing else 1)
    result = subprocess.run(['node',str(Path(__file__).parent/'fixtures/history_host_projection.mjs'),str(cwd),str(config),sys.executable,str(missing).lower()],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    assert transcript.read_bytes() == before
    assert page(directory,limit=1)['turnGroups'][0]['id'] == 'saved-input'
