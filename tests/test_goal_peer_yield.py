import copy
from types import SimpleNamespace
import pytest
from amplifier_acp.native.task_continuity import TaskController
from amplifier_acp.native.runtime_worker import Worker
from amplifier_acp.native.shared_state import ActivationGate


def fixture():
    hooks={}; caps={}; state={'goal': {'condition':'Keep working', 'turns_used':2, 'cap':8, 'continuations':1, 'future':{'kept':True}}, 'task': {'id':'task','revision':3,'appliedRevision':3,'status':'active'}}
    coordinator=SimpleNamespace(session_state=state, register_capability=caps.__setitem__, get_capability=caps.get, hooks=SimpleNamespace(register=lambda event,callback,**kw:hooks.__setitem__(event,callback)), cancellation=SimpleNamespace(is_cancelled=False))
    saves=[]
    controls=SimpleNamespace(coordinator=coordinator,session=SimpleNamespace(session_id='root'),capacity=SimpleNamespace(policy={'revision':2},last_denial=None),persist=lambda **kw:saves.append(copy.deepcopy(state)))
    controller=TaskController(controls);controls.tasks=controller
    return coordinator,controls,caps,hooks,saves


def event(): return {'state':'continuing','session_id':'root','condition':'Keep working','turn':2}

@pytest.mark.asyncio
async def test_eligible_exact_goal_yields_with_every_progress_field_and_no_goal_clear():
    co,ctrl,caps,hooks,saves=fixture()
    async def ready(): return True
    caps['web.peer_continuation_ready']=ready
    previous=co.session_state['goal']; original=copy.deepcopy(previous)
    await hooks['orchestrator:goal_progress']('orchestrator:goal_progress',event())
    assert co.session_state['goal'] == original and co.session_state['goal'] is not previous
    assert co.session_state['task']['revision'] == 3 and len(saves)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['not_ready','foreign','pause','cap','capacity','goal_race','task_race','policy_race','error'])
async def test_yield_refuses_changed_or_unqualified_boundary(change):
    co,ctrl,caps,hooks,saves=fixture(); data=event(); previous=co.session_state['goal']
    async def ready():
        if change=='goal_race': co.session_state['goal']=None
        if change=='task_race': co.session_state['task']['revision']+=1
        if change=='policy_race': ctrl.capacity.policy['revision']+=1
        if change=='error': raise OSError('unavailable')
        return change!='not_ready'
    caps['web.peer_continuation_ready']=ready
    if change=='foreign': data['session_id']='child'
    if change=='pause': co.session_state['task']['status']='paused'
    if change=='cap': previous['cap']=2
    if change=='capacity': ctrl.capacity.last_denial={'budgetRevision':2}
    await hooks['orchestrator:goal_progress']('orchestrator:goal_progress',data)
    assert not saves
    assert co.session_state['goal'] is (None if change=='goal_race' else previous)

@pytest.mark.asyncio
async def test_persist_failure_restores_original_goal_identity():
    co,ctrl,caps,hooks,saves=fixture()
    async def ready(): return True
    def fail(**kw): raise OSError('disk')
    caps['web.peer_continuation_ready']=ready;ctrl.persist=fail; previous=co.session_state['goal']
    with pytest.raises(OSError): await hooks['orchestrator:goal_progress']('orchestrator:goal_progress',event())
    assert co.session_state['goal'] is previous

@pytest.mark.asyncio
@pytest.mark.parametrize('race',['none','stop','generation','gate','cancel','retire','transport'])
async def test_root_bridge_rechecks_native_generation_after_wait(race):
    co,ctrl,caps,hooks,saves=fixture(); worker=Worker()
    worker.session=SimpleNamespace(coordinator=co);worker.controls=ctrl
    worker.runtime=SimpleNamespace(generation={'id':'g'},closed=False,stop_requested=False)
    worker.activation_gate=ActivationGate();worker.activation_gate.activate()
    worker.start_config={'hostInfo':{'features':{'peerContinuation':True}}}
    calls=[]
    async def bridge(operation,args):
        calls.append((operation,args))
        if race=='stop': worker.peer_stop_epoch+=1
        if race=='generation': worker.runtime.generation={'id':'g2'}
        if race=='gate': worker.activation_gate.release(worker.activation_gate.current())
        if race=='cancel': co.cancellation.is_cancelled=True
        if race=='retire': worker.retiring=True
        if race=='transport': worker.transport_closed=True
        return {'ready':True}
    worker.bridge=bridge;worker.app_access_bridge(co)
    assert await caps['web.peer_continuation_ready']() is (race=='none')
    assert len(calls)==1 and calls[0][0]=='coordination.continuation_ready'
    assert calls[0][1]['taskRevision']==3
    child_caps={}; child=SimpleNamespace(register_capability=child_caps.__setitem__)
    worker.app_access_bridge(child)
    assert 'web.peer_continuation_ready' not in child_caps
