"""Terminal failure is durable evidence, never no-effect or replay permission."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sqlite3
import uuid

import jsonschema
import pytest
import yaml

from amplifier_acp.input_outcome import CAPABILITY, KEY, Outcomes
from client import Client
from native_setup import configuration
from test_session_lifecycle import initialize, prompt


async def opened(tmp_path, **options):
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text()); bundle = Path(values['bundle'])
    plan = yaml.safe_load(bundle.read_text())
    plan['providers'][0]['config'] = {'inputOutcomeFailure': True, 'selectedModelAudit': str(tmp_path/'calls'), **options}
    bundle.write_text(yaml.safe_dump(plan))
    client = await Client.launch(config)
    initialized = await initialize(client)
    assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['inputOutcome'] == CAPABILITY
    made = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
    return client, made['result']['sessionId'], cwd, config


async def receipt(client, sid, cwd, identity):
    result = await client.request('_amplifier/input/receipt', {'sessionId': sid, 'cwd': str(cwd), 'inputId': identity})
    assert 'result' in result, result
    jsonschema.validate(result['result'], json.loads((Path(__file__).parents[1]/'schemas/input-outcome-receipt.v1.json').read_text()))
    return result['result']['receipt']


@pytest.fixture(autouse=True)
def isolated_writers(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))


@pytest.mark.asyncio
@pytest.mark.parametrize('after_tool', [False, True])
async def test_actual_core_failure_saved_before_error_and_new_corrected_input_is_independent(tmp_path, after_tool):
    client, sid, cwd, config = await opened(tmp_path, inputOutcomeAfterTool=after_tool)
    try:
        response = await prompt(client, sid, 'fail-outcome', 'original')
        proof = response['error']['data'][KEY]
        assert proof == await receipt(client, sid, cwd, 'original')
        assert proof['status'] == 'failed' and proof['inputPersisted'] and proof['terminal'] and proof['noReplay']
        assert proof['effectBoundary'] == 'unknown' and proof['error']['type'] == 'InvalidRequestError'
        assert 'private' not in json.dumps(proof) and 'executed' not in proof
        path = next((tmp_path/'home/projects').glob('*/sessions/'+sid+'/transcript.jsonl'))
        before = path.read_bytes(); rows = [json.loads(line) for line in before.splitlines()]
        assert any(row.get('metadata', {}).get('amplifier_input', {}).get('id') == 'original' for row in rows)
        assert any(row['role'] == 'tool' for row in rows) == after_tool
        count = (tmp_path/'calls').read_text().count('\n')
        duplicate = await prompt(client, sid, 'different text cannot replay original', 'original')
        assert duplicate['error']['data'][KEY] == proof
        assert (tmp_path/'calls').read_text().count('\n') == count and path.read_bytes() == before
        corrected = await prompt(client, sid, 'a new corrected instruction', 'new-original')
        assert corrected['result']['stopReason'] == 'end_turn', corrected
        assert await receipt(client, sid, cwd, 'original') == proof
        assert await receipt(client, sid, cwd, 'new-original') is None
    finally: await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_after_receipt', [False, True])
async def test_lost_reply_or_worker_exit_after_commit_recovers_exact_cold_receipt_without_worker(tmp_path, crash_after_receipt):
    client, sid, cwd, config = await opened(tmp_path, inputOutcomeCrashAfterReceipt=crash_after_receipt)
    await client.send({'jsonrpc': '2.0', 'id': 9000, 'method': 'session/prompt', 'params': {
        'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'fail-outcome'}],
        '_meta': {'amplifier.dev/command': {'commandId': 'lost-original'}}}})
    async with asyncio.timeout(15):
        while not (saved := await receipt(client, sid, cwd, 'lost-original')): await asyncio.sleep(.01)
    await client.close()
    calls = (tmp_path/'calls').read_bytes()
    path = tmp_path/'app/sessions'/sid/'input-provenance.sqlite3'; before = path.read_bytes()
    values = json.loads(config.read_text()); values['workerCommand'] = ['/impossible-worker']; config.write_text(json.dumps(values))
    client = await Client.launch(config); await initialize(client)
    try:
        assert await receipt(client, sid, cwd, 'lost-original') == saved
        other = tmp_path/'another-workspace'; other.mkdir()
        assert await receipt(client, sid, other, 'lost-original') is None
        assert await receipt(client, str(uuid.uuid4()), cwd, 'lost-original') is None
        assert await receipt(client, sid, cwd, 'not-original') is None
        # Passive exact recovery needs no session/resume (which legitimately
        # mounts the standard ACP session on this runtime).
        assert path.read_bytes() == before and (tmp_path/'calls').read_bytes() == calls
        assert not any(e.get('params', {}).get('event', {}).get('type') == 'runtime.ready' for e in client.events)
    finally: await client.close()
    values.pop('workerCommand'); config.write_text(json.dumps(values))
    client = await Client.launch(config); await initialize(client)
    try:
        resumed = await client.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        duplicate = await prompt(client, sid, 'changed text cannot reuse the old input', 'lost-original')
        assert duplicate['error']['data'][KEY] == saved
        assert (tmp_path/'calls').read_bytes() == calls
    finally: await client.close()


@pytest.mark.asyncio
async def test_failed_checkpoint_never_emits_terminal_proof(tmp_path):
    client, sid, cwd, _ = await opened(tmp_path, inputOutcomeCheckpoint='fail')
    try:
        response = await prompt(client, sid, 'fail-outcome', 'failed-checkpoint')
        assert KEY not in response['error'].get('data', {})
        assert await receipt(client, sid, cwd, 'failed-checkpoint') is None
        assert (tmp_path/'calls').read_text().count('\n') == 1
    finally: await client.close()


@pytest.mark.asyncio
async def test_checkpoint_join_blocks_early_proof_until_durable_input(tmp_path):
    client, sid, cwd, _ = await opened(tmp_path, inputOutcomeCheckpoint='gate', gateDirectory=str(tmp_path/'gate'))
    pending = asyncio.create_task(prompt(client, sid, 'fail-outcome', 'joined'))
    try:
        async with asyncio.timeout(15):
            while not (tmp_path/'gate/outcome-checkpoint.entered').exists(): await asyncio.sleep(.01)
        assert not pending.done() and await receipt(client, sid, cwd, 'joined') is None
        assert not any(e.get('params', {}).get('event', {}).get('type') == 'generation.failed' for e in client.events)
        (tmp_path/'gate/outcome-checkpoint.release').touch()
        response = await pending
        assert response['error']['data'][KEY] == await receipt(client, sid, cwd, 'joined')
    finally: await client.close()


@pytest.mark.asyncio
async def test_process_loss_before_terminal_checkpoint_stays_unknown_without_replay(tmp_path):
    client, sid, cwd, _ = await opened(tmp_path, inputOutcomeProviderGate=True, gateDirectory=str(tmp_path/'gate'))
    pending = asyncio.create_task(prompt(client, sid, 'fail-outcome', 'uncertain'))
    try:
        async with asyncio.timeout(15):
            while not (tmp_path/'gate/outcome-provider.entered').exists(): await asyncio.sleep(.01)
        owner = json.loads(next((tmp_path/'writers').glob('v1/*/'+sid+'/owner.json')).read_text())
        os.kill(owner['pid'], signal.SIGKILL)
        response = await pending
        assert KEY not in response['error'].get('data', {})
        assert await receipt(client, sid, cwd, 'uncertain') is None
        assert (tmp_path/'calls').read_text().count('\n') == 1
    finally: await client.close()


def test_lazy_scoped_indexed_reads_and_immutable_bounded_public_record(tmp_path):
    sid = str(uuid.uuid4()); owner = Outcomes(tmp_path/'app', sid, str(tmp_path))
    assert owner.read('missing') is None and not owner.path.exists()
    event = {'error_category': 'invalid_request', 'error_type': 'InvalidRequestError', 'error': {'secret': 'never copied'}}
    saved = owner.failed('original', event)
    assert set(saved) == {'version','sessionId','inputId','status','terminal','noReplay','inputPersisted','effectBoundary','error'}
    assert owner.failed('original', event) == saved
    with pytest.raises(ValueError, match='cannot be replaced'): owner.failed('original', {'error_category': 'authentication'})
    with sqlite3.connect(owner.path) as db:
        db.executemany('INSERT INTO outcomes VALUES(?,?,?)', [(f'old-{n}', str(tmp_path), '{}') for n in range(25000)])
        query = db.execute('EXPLAIN QUERY PLAN SELECT body FROM outcomes WHERE id=? AND cwd=?', ('original', str(tmp_path))).fetchall()
        assert any('SEARCH' in row[-1] and 'INDEX' in row[-1] for row in query)
    before = owner.path.read_bytes()
    assert owner.read('original') == saved and owner.path.read_bytes() == before
    assert Outcomes(tmp_path/'app', sid, str(tmp_path/'other')).read('original') is None
    with pytest.raises(ValueError): owner.read('x'*201)


@pytest.mark.asyncio
async def test_passive_receipt_negotiation_and_held_home_gate(tmp_path):
    from amplifier_acp.server import Server
    from amplifier_acp.maintenance_gate import Gate
    sid = str(uuid.uuid4()); config = {'home': str(tmp_path/'home'), 'appHome': str(tmp_path/'app')}
    server = Server(config, None)
    await server.dispatch('initialize', {'protocolVersion': 1})
    params = {'sessionId': sid, 'cwd': str(tmp_path), 'inputId': 'original'}
    with pytest.raises(Exception, match='not negotiated'): await server.dispatch('_amplifier/input/receipt', params)
    server.extensions.add('amplifier.dev/native')
    with Gate(config, exclusive=True):
        assert await server.dispatch('_amplifier/input/receipt', params) == {'receipt': None}
    assert not (tmp_path/'app/sessions').exists()
