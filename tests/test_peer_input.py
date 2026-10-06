import json
from types import SimpleNamespace
import pytest
from amplifier_acp.native.peer_input import admit


class Runtime:
    max_input_chars = 20000
    closed = False
    generation = None
    def __init__(self): self.inputs = []
    async def submit(self, value): self.inputs.append(value); return value.id


class Controls:
    def __init__(self, runtime):
        self.runtime = runtime; self.task = None; self.allowed = True; self.goal = {}; self.capability = None
        self.tasks = SimpleNamespace(record=lambda: self.task, continuation_allowed=self.continuation)
        self.coordinator = SimpleNamespace(session_state={'goal': self.goal}, get_capability=lambda name: self.capability if name == 'live.steering' else None)
    async def continuation(self): return self.allowed
    def require_idle(self):
        if self.runtime.generation: raise ValueError('Current generation is still active')


def args(): return {'inputId': 'peer-1', 'grantId': 'grant-1', 'taskId': None, 'taskRevision': None, 'text': 'CALLER TEXT MUST NOT BE USED'}
def permission(mode='queue'):
    return {'admitted': True, 'message': {'text': 'Saved ORBIT 572 request', 'peerEnvelope': {'requestId': 'peer-1', 'grantId': 'grant-1', 'mode': mode, 'senderSessionId': 'source', 'recipientSessionId': 'target'}}}


@pytest.mark.asyncio
async def test_saved_original_is_attributed_and_reserved_before_intake(tmp_path):
    runtime = Runtime(); controls = Controls(runtime); calls = []
    async def authorize(value): calls.append(value); return permission()
    value = await admit(controls, runtime, args(), None, authorize, provenance_path=tmp_path/'inputs.sqlite3')
    assert value['accepted'] and len(calls) == 2 and len(runtime.inputs) == 1
    assert 'Saved ORBIT 572' in runtime.inputs[0].text and 'CALLER TEXT' not in runtime.inputs[0].text
    from amplifier_acp.native.input_provenance import reserved
    assert reserved(tmp_path/'inputs.sqlite3', 'peer-1')
    with pytest.raises(ValueError, match='already reserved'):
        await admit(controls, runtime, args(), None, authorize, provenance_path=tmp_path/'inputs.sqlite3')
    assert len(runtime.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'task', 'budget', 'closed', 'revoked', 'busy', 'cap'])
async def test_boundary_changes_never_execute_or_reserve(tmp_path, change):
    runtime = Runtime(); controls = Controls(runtime); epoch = [0]; calls = []
    async def authorize(value):
        calls.append(value)
        if len(calls) == 2:
            if change == 'stop': epoch[0] += 1
            if change == 'task': controls.task = {'id': 'changed', 'revision': 1}
            if change == 'budget': controls.allowed = False
            if change == 'closed': runtime.closed = True
            if change == 'busy': runtime.generation = {'id': 'new-generation'}
            if change == 'cap': controls.goal.update(cap=3, turns_used=3)
            if change == 'revoked': return {'admitted': False, 'reason': 'revoked'}
        return permission()
    result = await admit(controls, runtime, args(), None, authorize, stop_epoch=lambda: epoch[0], provenance_path=tmp_path/'inputs.sqlite3')
    assert result['accepted'] is False and result['executed'] is False and runtime.inputs == []
    assert not (tmp_path/'inputs.sqlite3').exists()


@pytest.mark.asyncio
async def test_steering_is_anchored_without_queue_fallback_and_unknown_cannot_replay(tmp_path):
    runtime = Runtime(); runtime.generation = {'id': 'generation-1', 'initial_input_id': 'human-1'}; controls = Controls(runtime); seen = []
    async def submit(command, generation): seen.append((command, generation)); return {'accepted': True, 'disposition': 'queued'}
    controls.capability = {'version': 1, 'mode': 'request_boundary', 'submit': submit}
    async def authorize(value): return permission('steer')
    request = {**args(), 'targetGenerationId': 'generation-2', 'activeInputId': 'human-1'}
    assert not (await admit(controls, runtime, request, None, authorize, steering=True, provenance_path=tmp_path/'inputs.sqlite3'))['accepted']
    assert not seen and not runtime.inputs
    request['targetGenerationId'] = 'generation-1'
    result = await admit(controls, runtime, request, None, authorize, steering=True, provenance_path=tmp_path/'inputs.sqlite3')
    assert result['accepted'] and result['completed'] is False and len(seen) == 1 and not runtime.inputs
    assert seen[0][0].kind == 'steer' and seen[0][1] == 'generation-1'
    # Provenance must keep peer steering attached to its human turn on reload.
    import sqlite3
    with sqlite3.connect(tmp_path/'inputs.sqlite3') as db:
        saved = db.execute('SELECT body FROM provenance WHERE id=?', ('peer-1',)).fetchone()
    assert json.loads(saved[0])['activeInputId'] == 'human-1'
    with pytest.raises(ValueError, match='already reserved'):
        await admit(controls, runtime, request, None, authorize, steering=True, provenance_path=tmp_path/'inputs.sqlite3')


@pytest.mark.asyncio
async def test_forged_saved_envelope_is_refused_before_provenance(tmp_path):
    runtime = Runtime(); controls = Controls(runtime)
    async def authorize(value):
        saved = permission(); saved['message']['peerEnvelope']['requestId'] = 'different'; return saved
    with pytest.raises(ValueError, match='does not match'):
        await admit(controls, runtime, args(), None, authorize, provenance_path=tmp_path/'inputs.sqlite3')
    assert not runtime.inputs and not (tmp_path/'inputs.sqlite3').exists()
