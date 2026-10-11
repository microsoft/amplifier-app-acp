import json
from types import SimpleNamespace

import pytest
from amplifier_acp.history import page
from amplifier_acp.plan import accepted_plan
from amplifier_acp.server import Session
from amplifier_acp.native.execution_events import ExecutionEvents


def items(count=2):
    return [{'content': f'Step {n}', 'activeForm': f'Doing {n}', 'status': 'in_progress' if n == 0 else 'pending'} for n in range(count)]


def test_only_accepted_receipts_authorize_plan_and_bad_latest_is_explicit():
    args = {'action': 'update', 'todos': items()}
    assert accepted_plan(args, {'success': False}) is None
    assert accepted_plan(args, {'success': True, 'output': {'success': False}}) is None
    accepted = accepted_plan(args, {'success': True, 'output': {'status': 'updated'}})
    assert [e['content'] for e in accepted['entries']] == ['Step 0', 'Step 1']
    assert accepted_plan({}, json.dumps({'todos': []}))['entries'] == []
    for bad in [{'todos': items(201)}, {'todos': [{'content': 'x', 'status': 'invented'}]}, {'todos': [{'content': 'x'*4001, 'status': 'pending'}]}, {'todos': items(2000)}, 'not json', {'status': 'read'}]:
        result = accepted_plan(args, bad)
        assert result['entries'] == [] and result['_meta']['amplifier.dev/todo']['unavailable']


@pytest.mark.asyncio
async def test_live_plan_bypasses_truncated_preview_but_waits_for_root_success():
    wire = []
    async def notify(method, params): wire.append(params['update'])
    session = Session.__new__(Session)
    session.identity = 'root';session.tools = set();session.prompt = None
    session.server = SimpleNamespace(extensions=set(), notify=notify)
    session.coordination = SimpleNamespace(observe=lambda row: False)
    events = [];observer = ExecutionEvents('root', events.append)
    args = {'action': 'update', 'todos': items(100)}
    for sid, identity, success in [('root', 'good', True), ('child', 'child', True), ('root', 'failed', False)]:
        observer.hook(sid, 'tool:pre', {'tool_call_id': identity, 'tool_name': 'todo', 'tool_input': args})
        assert not any(e.get('event', {}).get('plan') for e in events[-1:])
        observer.hook(sid, 'tool:post', {'tool_call_id': identity, 'tool_name': 'todo', 'result': {'success': success, 'output': {'status': 'updated'}}})
    for event in events: await session.observe(event)
    plans = [row for row in wire if row['sessionUpdate'] == 'plan']
    assert len(plans) == 1 and len(plans[0]['entries']) == 100
    assert plans[0]['entries'][0]['_meta']['amplifier.dev/todo']['activeForm'] == 'Doing 0'
    assert not observer.plan_calls
    # The same snapshot remains stable after sending (no mutation of event evidence).
    assert next(e['event']['plan'] for e in events if e['event'].get('plan')) == plans[0]


def test_cold_history_pairs_results_without_worker_or_history_mutation(tmp_path):
    p = tmp_path/'native';p.mkdir()
    rows = [{'role': 'user', 'content': 'Make a plan', 'metadata': {'amplifier_input': {'id': 'original'}}},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'todo-1', 'tool': 'todo', 'arguments': {'action': 'update', 'todos': items()}}]},
            {'role': 'tool', 'tool_call_id': 'todo-1', 'content': json.dumps({'success': True, 'output': {'status': 'updated'}})},
            {'role': 'assistant', 'content': 'Ready'},
            {'role': 'user', 'content': 'Later', 'metadata': {'amplifier_input': {'id': 'later'}}},
            {'role': 'tool', 'tool_call_id': 'todo-1', 'content': json.dumps({'todos': []})}]
    file = p/'transcript.jsonl';file.write_text(''.join(json.dumps(row)+'\n' for row in rows));before = file.read_bytes()
    result = page(p)
    plans = [u for u in result['updates'] if u['sessionUpdate'] == 'plan']
    assert len(plans) == 1 and len(plans[0]['entries']) == 2
    assert plans[0]['_meta']['amplifier.dev/history']['turnId'] == 'original'
    assert not any(u['sessionUpdate'] == 'plan' for u in page(p, limit=1)['updates'])
    assert file.read_bytes() == before
