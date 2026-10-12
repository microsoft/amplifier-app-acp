import json
from pathlib import Path

import pytest

from amplifier_acp.history import HistoryError, exact_message, message_id
from amplifier_acp.saved_messages import CAPABILITY, recent


def write(path, rows):
    path.mkdir(exist_ok=True)
    data = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode()
    (path / 'transcript.jsonl').write_bytes(data)
    (path / 'metadata.json').write_text('{}')
    return data


def test_saved_reference_discovery_uses_only_persisted_visible_assistant_text(tmp_path):
    rows = [{'role': 'user', 'content': 'user'},
            {'role': 'assistant', 'content': 'commentary'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'x'}]},
            {'role': 'tool', 'content': 'result', 'tool_call_id': 'x'},
            {'role': 'assistant', 'content': 'private', 'metadata': {'ephemeral': True}},
            {'role': 'system', 'content': 'private instructions'},
            {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'private reasoning'}]},
            {'role': 'assistant', 'content': '🎬' * 600}]
    data = write(tmp_path, rows)
    value = recent(tmp_path)
    assert [row['text'] for row in value['messages']] == ['commentary', '🎬' * 512]
    assert value['messages'][-1]['truncated'] and not value['execution']
    assert value['mutationAuthority'] == 'none' and value['hasOlder'] is False
    for row in value['messages']:
        proof = exact_message(tmp_path, row['messageId'], value['revision'], media_reference_only=True)
        assert proof['message']['id'] == row['messageId'] and proof['message']['role'] == 'assistant'
    assert (tmp_path / 'transcript.jsonl').read_bytes() == data


def test_recent_tail_is_bounded_but_keeps_exact_offsets_after_large_tool_records(tmp_path):
    rows = [{'role': 'tool', 'content': 'x' * (CAPABILITY['maxScanBytes'] + 100), 'tool_call_id': 'large'}]
    rows += [{'role': 'assistant', 'content': str(index)} for index in range(20)]
    write(tmp_path, rows)
    value = recent(tmp_path)
    assert [row['text'] for row in value['messages']] == list(map(str, range(8, 20)))
    assert value['hasOlder']
    for row in value['messages']:
        assert exact_message(tmp_path, row['messageId'], value['revision'], media_reference_only=True)['complete']
    write(tmp_path, [rows[0]])
    assert recent(tmp_path)['messages'] == [] and recent(tmp_path)['hasOlder']


def test_saved_discovery_refuses_backup_partial_malformed_symlink_and_changed_source(tmp_path, monkeypatch):
    file = tmp_path / 'transcript.jsonl'
    write(tmp_path, [{'role': 'assistant', 'content': 'saved'}])
    file.rename(tmp_path / 'transcript.jsonl.backup')
    with pytest.raises(HistoryError, match='backup'): recent(tmp_path)
    file.write_text('{"role":"assistant","content":"unfinished"}')
    with pytest.raises(HistoryError, match='still being written'): recent(tmp_path)
    file.write_text('bad record\n')
    with pytest.raises(HistoryError, match='Malformed'): recent(tmp_path)
    file.unlink();file.symlink_to(tmp_path / 'transcript.jsonl.backup')
    with pytest.raises(HistoryError): recent(tmp_path)
    file.unlink();write(tmp_path, [{'role': 'assistant', 'content': 'saved'}])
    from amplifier_acp import saved_messages
    original = saved_messages.content_updates
    def change(row, position):
        file.write_text('{"role":"assistant","content":"changed"}\n')
        return original(row, position)
    monkeypatch.setattr(saved_messages, 'content_updates', change)
    with pytest.raises(HistoryError, match='changed'): recent(tmp_path)


@pytest.mark.asyncio
async def test_saved_message_protocol_is_passive_and_exactly_scoped(tmp_path):
    from client import Client
    from amplifier_acp.native.session_files import project_slug
    home = tmp_path / 'home';cwd = tmp_path / 'workspace';cwd.mkdir()
    sid = '33333333-3333-4333-8333-333333333333'
    directory = home / 'projects' / project_slug(str(cwd)) / 'sessions' / sid
    directory.mkdir(parents=True)
    write(directory, [{'role': 'assistant', 'content': 'Saved active commentary'}])
    (directory / 'metadata.json').write_text(json.dumps({'session_id': sid, 'working_dir': str(cwd)}))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'home': str(home), 'appHome': str(tmp_path / 'app'), 'workerCommand': ['/must-not-start']}))
    client = await Client.launch(config)
    try:
        init = await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['history']['savedMessages'] == CAPABILITY
        scope = {'sessionId': sid, 'cwd': str(cwd)}
        response = await client.request('_amplifier/history/saved-messages', scope)
        assert response['result']['messages'][0]['text'] == 'Saved active commentary'
        assert not client.requests and not client.events
        assert 'error' in await client.request('_amplifier/history/saved-messages', {**scope, 'limit': 500})
        other = tmp_path / 'other';other.mkdir()
        assert 'error' in await client.request('_amplifier/history/saved-messages', {**scope, 'cwd': str(other)})
    finally:
        await client.close()
