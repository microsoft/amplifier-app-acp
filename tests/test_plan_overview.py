import json
import pytest
from amplifier_acp.plan_overview import overview


def snapshot(done, total=2, result=None):
    return [
        {'role': 'user', 'content': 'Plan'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'todo', 'tool': 'todo',
          'arguments': {'action': 'update', 'todos': [{'content': f'Step {i}', 'status': 'completed' if i < done else 'pending'} for i in range(total)]}}]},
        {'role': 'tool', 'tool_call_id': 'todo', 'content': json.dumps(result if result is not None else {'status': 'updated'})},
    ]


def write(path, rows):
    path.mkdir(exist_ok=True)
    file = path/'transcript.jsonl'
    file.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return file


def test_passive_overview_counts_and_actual_last_16_points(tmp_path):
    rows = sum((snapshot(i % 3) for i in range(20)), [])
    file = write(tmp_path, rows)
    before = file.read_bytes()
    result = overview(tmp_path)
    assert result['plan']['done'] == 1 and result['plan']['total'] == 2
    assert len(result['plan']['history']) == 16
    assert result['plan']['history'][0] == {'done': 1, 'total': 2}
    assert 'items' not in result['plan']
    assert overview(tmp_path, True)['plan']['items'][0]['content'] == 'Step 0'
    assert file.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['transcript.jsonl']


def test_latest_rejected_empty_or_unreadable_never_invents_progress(tmp_path):
    write(tmp_path, snapshot(1) + snapshot(2, result={'success': False}))
    assert overview(tmp_path)['plan']['done'] == 1
    write(tmp_path, snapshot(1) + snapshot(0, result={'status': 'read'}))
    assert overview(tmp_path)['plan']['unavailable']
    assert 'items' not in overview(tmp_path, True)['plan']
    write(tmp_path, snapshot(1) + snapshot(0, total=0))
    assert overview(tmp_path)['plan']['total'] == 0
    write(tmp_path, snapshot(1) + [{'role': 'user', 'content': 'Next'}, {'role': 'tool', 'tool_call_id': 'todo', 'content': '{"todos": []}'}])
    assert overview(tmp_path)['plan']['total'] == 2


def test_invalid_changed_and_oversized_history_refuses(tmp_path, monkeypatch):
    from amplifier_acp import history
    assert overview(tmp_path)['plan'] is None
    file = write(tmp_path, snapshot(1))
    with pytest.raises(ValueError, match='boolean'): overview(tmp_path, 1)
    file.write_text('not json\n')
    with pytest.raises(ValueError): overview(tmp_path)
    file.write_text(json.dumps({'role': 'user', 'content': 'x' * 1048576})+'\n')
    with pytest.raises(ValueError, match='bounded'): overview(tmp_path)
    write(tmp_path, snapshot(1))
    original = history.stamp
    calls = 0
    def changing(path):
        nonlocal calls
        calls += 1
        rev, size = original(path)
        return (rev if calls < 3 else 'changed', size)
    monkeypatch.setattr(history, 'stamp', changing)
    with pytest.raises(ValueError, match='changed'): overview(tmp_path)
