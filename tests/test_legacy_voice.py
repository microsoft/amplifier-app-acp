"""Legacy application snapshots become passive, independently resumable history."""
import asyncio
import hashlib
import json
import sqlite3
import uuid

import pytest

from amplifier_acp.legacy_voice import prepare, materialize, sha
from amplifier_acp import history


def seed(tmp_path, *, bundle='work', cold=False):
    original = tmp_path/'original'; original.mkdir()
    home = original/'native'; home.mkdir()
    database = original/'app.sqlite3'
    workspace = tmp_path/'workspace'; workspace.mkdir(exist_ok=True)
    sid = str(uuid.uuid4())
    session = {'id': sid, 'title': 'My spoken plan', 'titleSource': 'manual', 'status': 'idle',
        'workspace': str(workspace), 'bundle': bundle, 'createdAt': 1700000000, 'recentActivityAt': 1700000001,
        'messages': [{'id': 'old-user', 'role': 'user', 'text': 'Remember the Harbor design.', 'via': 'call', 'voiceId': 'old-call', 'voiceItemId': 'old-item-1', 'createdAt': 1700000000},
                     {'id': 'old-assistant', 'role': 'assistant', 'text': 'The Harbor design is blue.', 'via': 'call', 'voiceId': 'old-call', 'voiceItemId': 'old-item-2', 'createdAt': 1700000001}]}
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE state_records (kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))')
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY,value TEXT)')
        if cold:
            rows = session.pop('messages'); digest = sha(rows)
            session['_coldFields'] = {'messages': {'$resource': digest}}
            (original/'artifacts').mkdir()
            (original/'artifacts'/(digest+'.json')).write_text(json.dumps(rows))
            db.execute('INSERT INTO state_resources VALUES (?,?)', (digest, json.dumps({'$blob': digest})))
        db.execute('INSERT INTO state VALUES (1,?)', (json.dumps({'sessions': [session], 'revision': 1}),))
    return database, home, sid, session


@pytest.mark.parametrize('cold', [False, True])
def test_exact_voice_history_identity_catalog_and_originals(tmp_path, cold):
    db, home, sid, session = seed(tmp_path, cold=cold)
    original = {p: p.read_bytes() for p in db.parent.rglob('*') if p.is_file()}
    plan, _ = prepare(db, home)
    assert plan['counts'] == {'ready': 1}
    assert 'Harbor' not in json.dumps(plan), 'Plan does not expose private conversation text'
    output = tmp_path/'staged'
    receipt = materialize(db, home, output, plan['planHash'])
    assert receipt['status'] == 'completed' and not receipt['executionStarted']
    directory = next((output/'native/projects').glob('*/sessions/'+sid))
    rows = [json.loads(line) for line in (directory/'transcript.jsonl').read_text().splitlines()]
    assert [row['content'] for row in rows] == ['Remember the Harbor design.', 'The Harbor design is blue.']
    assert all(row['metadata']['amplifier_voice']['recordedOnly'] for row in rows)
    assert all(history.provenance(row)['authorization'] == 'unverified-native-history' for row in rows)
    assert all('amplifier_input' not in row['metadata'] for row in rows)
    assert json.loads((directory/'metadata.json').read_text())['name'] == 'My spoken plan'
    from amplifier_session_catalog import Catalog
    from amplifier_session_catalog.discovery import Discovery
    catalog = Catalog(tmp_path/'catalog.sqlite')
    result = Discovery(catalog, [output/'native']).hint(sessionDirectory=str(directory))
    assert 'uri' in result, result
    record = catalog.get(result['uri'])
    assert record['title'] == 'My spoken plan'
    with pytest.raises(FileExistsError):
        materialize(db, home, output, plan['planHash'])
    assert {p: p.read_bytes() for p in original} == original


def test_scoped_records_replace_delete_and_cold_resource_integrity(tmp_path):
    db, home, sid, session = seed(tmp_path, cold=True)
    with sqlite3.connect(db) as con:
        changed = {**session, 'title': 'Latest scoped title'}
        con.execute('INSERT INTO state_records VALUES (?,?,?)', ('session', sid, json.dumps(changed)))
    plan, rows = prepare(db, home)
    assert rows[sid]['metadata']['name'] == 'Latest scoped title'
    source = next((db.parent/'artifacts').glob('*.json'))
    source.write_text('[{"text":"Corrupt saved body"}]')
    assert prepare(db, home)[0]['items'][0]['status'] == 'review-required'
    with pytest.raises(ValueError, match='changed'):
        materialize(db, home, tmp_path/'staged', plan['planHash'])
    assert not (tmp_path/'staged').exists()
    with sqlite3.connect(db) as con:
        con.execute('UPDATE state_records SET value=?', ('null',))
    assert prepare(db, home)[0]['items'] == []


@pytest.mark.parametrize('change', [
    {'status': 'working'}, {'selection': {'model': 'other-model'}}, {'activeInputId': 'uncertain'},
    {'messages': [{'role': 'user', 'text': 'Pending typed work', 'via': 'chat'}]},
    {'_coldFields': {'configuration': {'$resource': '0'*64}}}, {'location': {'kind': 'managed'}},
])
def test_other_owners_and_uncertain_work_are_not_invented(tmp_path, change):
    db, home, sid, session = seed(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute('INSERT INTO state_records VALUES (?,?,?)', ('session', sid, json.dumps({**session, **change})))
    report, payloads = prepare(db, home)
    assert report['counts'] == {'review-required': 1} and not payloads


def test_existing_native_backup_wins_and_outputs_cannot_touch_sources(tmp_path):
    db, home, sid, _ = seed(tmp_path)
    original = home/'projects/old/sessions'/sid; original.mkdir(parents=True)
    (original/'transcript.jsonl.backup').write_text('exact original')
    plan, payloads = prepare(db, home)
    assert plan['counts'] == {'native-history-owned': 1} and not payloads
    with pytest.raises(ValueError, match='separate'):
        materialize(db, home, home/'new', plan['planHash'])
    assert (original/'transcript.jsonl.backup').read_text() == 'exact original'


@pytest.mark.asyncio
async def test_installed_native_cold_resume_and_fork_do_not_replay_voice(tmp_path, monkeypatch):
    from client import Client
    from native_setup import configuration
    from test_session_lifecycle import initialize, action, prompt
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))
    config, cwd = configuration(tmp_path)
    db, home, sid, _ = seed(tmp_path, bundle=str(tmp_path/'fixture.yaml'), cold=True)
    before_source = hashlib.sha256(db.read_bytes()).hexdigest()
    plan, _ = prepare(db, home)
    output = tmp_path/'converted'; materialize(db, home, output, plan['planHash'])
    # Retain existing settings and credential bytes in the owned staging home.
    settings = b'bundle:\n  app: []\nvoice:\n  voice: marin\n  interruptions: false\n'
    credentials = b'MIGRATION_INERT_KEY=not-a-live-credential\n'
    (output/'native/settings.yaml').write_bytes(settings)
    (output/'native/keys.env').write_bytes(credentials)
    value = json.loads(config.read_text()); value['home'] = str(output/'native'); config.write_text(json.dumps(value))
    directory = next((output/'native/projects').glob('*/sessions/'+sid))
    saved = {name: (directory/name).read_bytes() for name in ('transcript.jsonl', 'metadata.json')}
    client = await Client.launch(config)
    try:
        await initialize(client)
        inspected = (await action(client, sid, cwd, 'inspect'))['result']
        fork_id = str(uuid.uuid4())
        fork = await action(client, sid, cwd, 'fork', {'commandId': 'legacy-fork-once', 'targetSessionId': fork_id, 'expectedHistoryRevision': inspected['historyRevision']})
        assert fork.get('result', {}).get('creationConfirmed'), fork
        resumed = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        assert not any(e.get('method') == 'session/update' and e['params']['update']['sessionUpdate'] == 'agent_message_chunk' for e in client.events)
        assert {name: (directory/name).read_bytes() for name in saved} == saved
        result = await prompt(client, sid, 'Explicit new work after migration', 'after-migration-one')
        assert result.get('result', {}).get('stopReason') == 'end_turn', result
        rows = [json.loads(line) for line in (directory/'transcript.jsonl').read_text().splitlines()]
        assert len([r for r in rows if r.get('metadata', {}).get('amplifier_voice')]) == 2
        assert len([r for r in rows if r.get('content') == 'Explicit new work after migration']) == 1
        fork_rows = [json.loads(line) for line in (directory.parent/fork_id/'transcript.jsonl').read_text().splitlines()]
        assert [r['content'] for r in fork_rows if r.get('metadata', {}).get('amplifier_voice')] == ['Remember the Harbor design.', 'The Harbor design is blue.']
        assert 'Explicit new work after migration' not in json.dumps(fork_rows)
        assert (output/'native/settings.yaml').read_bytes() == settings
        assert (output/'native/keys.env').read_bytes() == credentials
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before_source
    finally:
        await client.close()
