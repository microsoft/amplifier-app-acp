"""Bounded Native cleanup reply continuations; no model/network requests."""
import asyncio,json
from types import SimpleNamespace
import pytest
from amplifier_acp.native import runtime_worker as native

@pytest.fixture
async def harness(monkeypatch):
    rows=[];ready=asyncio.Event();wire={};closed=[]
    from amplifier_acp.native import history_hints
    monkeypatch.setattr(history_hints,'_observer',history_hints._observer)
    monkeypatch.setattr(native,'publish',lambda row:rows.append(dict(row)))
    worker=native.Worker()
    async def pipe(factory,stdin):
        protocol=factory();wire['reader']=protocol._stream_reader;ready.set()
        return SimpleNamespace(close=lambda:closed.append(True)),protocol
    monkeypatch.setattr(asyncio.get_running_loop(),'connect_read_pipe',pipe)
    monkeypatch.setattr(native,'publish',lambda row:rows.append(dict(row)))
    worker.ownership=SimpleNamespace(registration=None,yielding=False)
    worker.controls=SimpleNamespace(logins={},close=lambda:asyncio.sleep(0))
    worker.runtime=SimpleNamespace(queued_inputs=[],inbox=asyncio.Queue(),generation=None)
    worker.parked=True
    async def checkpoint():rows.append({'phase':'checkpoint','executionCancelled':bool(worker.execution and worker.execution.cancelled())})
    worker.checkpoint_terminal_outcome=checkpoint
    yield worker,rows,ready,wire,closed

async def wait_bridge(rows):
    async with asyncio.timeout(1):
        while not any(r.get('op')=='bridge' for r in rows):await asyncio.sleep(0)
    return next(r for r in rows if r.get('op')=='bridge')

async def install_session(worker,tmp_path,mounted):
    if mounted:
        from amplifier_foundation import Bundle
        bundle=Bundle.from_dict({'bundle':{'name':'cleanup-only'},'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'providers':[]})
        prepared=await bundle.prepare(install_deps=False)
        session=await prepared.create_session(session_cwd=tmp_path)
        worker.session=session
        from amplifier_acp.native.app_guidance import install_app_access
        await install_app_access(session.coordinator,worker.app_access_bridge(session.coordinator),
                                 host_info={'name':'inert cleanup fixture','appControl':{'operations':['list_actions']}})
        return session.coordinator.register_cleanup
    callbacks=[]
    async def cleanup():
        for callback in callbacks:await callback()
    worker.session=SimpleNamespace(coordinator=SimpleNamespace(get=lambda name:None),cleanup=cleanup)
    return callbacks.append

@pytest.mark.parametrize('mounted',[False,True])
async def test_connected_cleanup_reply_settles_once_and_no_new_work_is_admitted(harness,tmp_path,mounted):
    worker,rows,ready,wire,closed=harness;register=await install_session(worker,tmp_path,mounted);effects=[]
    async def cleanup():
        effects.append('entered')
        if mounted:
            tool=worker.session.coordinator.get('tools')['app_control']
            result=await tool.execute({'operation':'list_actions','args':{}})
            assert result.success
            effects.append(result.output)
        else:effects.append(await worker.bridge('test.cleanup.owner',{'readOnly':True}))
    register(cleanup);run=asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(ready.wait(),1)
        await worker.command({'op':'retire','id':'original-retire'})
        request=await wait_bridge(rows)
        if mounted:assert request['operation']=='list_actions'
        assert not closed and not run.done()
        wire['reader'].feed_data((json.dumps({'op':'control','id':'new-effect','operation':'tool.invoke','arguments':{}})+'\n').encode())
        wire['reader'].feed_data((json.dumps({'op':'bridge.result','id':'unsolicited','result':'bad'})+'\n').encode())
        wire['reader'].feed_data((json.dumps({'op':'bridge.result','id':request['id'],'result':{'cleaned':True}})+'\n').encode())
        await asyncio.wait_for(run,2)
        assert effects==['entered',{'cleaned':True}] and closed==[True]
        assert any(r.get('id')=='new-effect' and r.get('executed') is False for r in rows)
        assert any(r.get('id')=='unsolicited' and r.get('executed') is False for r in rows)
        assert not worker.bridges and worker.draining
    finally:
        if not run.done():run.cancel()
        await asyncio.gather(run,return_exceptions=True)

@pytest.mark.parametrize('active',[False,True])
async def test_eof_joins_execution_checkpoints_and_fails_cleanup_bridge_promptly(harness,tmp_path,active):
    worker,rows,ready,wire,closed=harness;register=await install_session(worker,tmp_path,False)
    async def cleanup():await worker.bridge('test.cleanup.owner',{})
    register(cleanup)
    if active:
        async def execute():await asyncio.Event().wait()
        worker.execution=asyncio.create_task(execute())
    run=asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(ready.wait(),1);wire['reader'].feed_eof()
        with pytest.raises(RuntimeError,match='connection is closed'):
            await asyncio.wait_for(run,1)
        assert not any(r.get('op')=='bridge' for r in rows)
        assert any(r.get('phase')=='checkpoint' and r['executionCancelled']==active for r in rows)
        assert worker.transport_closed and not worker.bridges and closed==[True]
    finally:
        if not run.done():run.cancel()
        await asyncio.gather(run,return_exceptions=True)

async def test_buffered_reply_is_settled_before_following_eof(harness):
    worker,rows,ready,wire,closed=harness
    read=asyncio.create_task(worker.read());await asyncio.wait_for(ready.wait(),1)
    bridge=asyncio.create_task(worker.bridge('test.inert',{}));request=await wait_bridge(rows)
    wire['reader'].feed_data((json.dumps({'op':'bridge.result','id':request['id'],'result':{'seen':True}})+'\n').encode());wire['reader'].feed_eof()
    assert await asyncio.wait_for(bridge,1)=={'seen':True}
    await asyncio.wait_for(read,1);assert worker.transport_closed and not worker.bridges

@pytest.mark.parametrize('error',['explicit-error','disconnect'])
async def test_core_swallowing_cleanup_callback_failure_cannot_prove_clean_retirement(harness,tmp_path,error):
    worker,rows,ready,wire,closed=harness;register=await install_session(worker,tmp_path,True)
    async def cleanup():
        tool=worker.session.coordinator.get('tools')['app_control']
        await tool.execute({'operation':'list_actions','args':{}})
    register(cleanup);run=asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(ready.wait(),1);await worker.command({'op':'retire','id':'retire'})
        request=await wait_bridge(rows)
        if error=='disconnect':wire['reader'].feed_eof()
        else:wire['reader'].feed_data((json.dumps({'op':'bridge.result','id':request['id'],'error':'controlled inert refusal'})+'\n').encode())
        with pytest.raises(RuntimeError,match='cleanup bridge did not settle'):
            await asyncio.wait_for(run,2)
        assert worker.cleanup_bridge_failed and not worker.bridges
    finally:
        if not run.done():run.cancel()
        await asyncio.gather(run,return_exceptions=True)

async def test_retiring_accepts_only_exact_pending_reply_and_rejects_duplicate(harness):
    worker,rows,_,_,_=harness;worker.retiring=True
    bridge=asyncio.create_task(worker.bridge('test.inert',{}));request=await wait_bridge(rows)
    await worker.command({'op':'bridge.result','id':'wrong','result':{'bad':True}});assert not bridge.done()
    await worker.command({'op':'bridge.result','id':request['id'],'result':{'seen':True}})
    assert await asyncio.wait_for(bridge,1)=={'seen':True}
    await worker.command({'op':'bridge.result','id':request['id'],'result':{'bad':True}})
    assert rows[-1].get('executed') is False and not worker.bridges

async def test_connected_cancellation_preserves_join_checkpoint_and_cleanup_reply(harness,tmp_path):
    worker,rows,ready,wire,closed=harness;register=await install_session(worker,tmp_path,False);effects=[]
    async def execution():await asyncio.Event().wait()
    worker.execution=asyncio.create_task(execution())
    async def cleanup():effects.append(await worker.bridge('test.cleanup.owner',{}))
    register(cleanup);run=asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(ready.wait(),1);run.cancel()
        request=await wait_bridge(rows)
        assert worker.execution.cancelled() and not closed
        wire['reader'].feed_data((json.dumps({'op':'bridge.result','id':request['id'],'result':{'cleaned':True}})+'\n').encode())
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(run,1)
        assert effects==[{'cleaned':True}] and closed==[True]
        assert any(r.get('phase')=='checkpoint' and r['executionCancelled'] for r in rows)
    finally:
        if not run.done():run.cancel()
        await asyncio.gather(run,return_exceptions=True)

async def test_eof_fails_an_existing_bridge_future_without_replay(harness):
    worker,rows,ready,wire,closed=harness;read=asyncio.create_task(worker.read())
    await asyncio.wait_for(ready.wait(),1)
    bridge=asyncio.create_task(worker.bridge('test.inert',{}));await wait_bridge(rows)
    wire['reader'].feed_eof()
    with pytest.raises(RuntimeError,match='closed before bridge settlement'):
        await asyncio.wait_for(bridge,1)
    await asyncio.wait_for(read,1)
    assert worker.transport_closed and not worker.bridges and len([r for r in rows if r.get('op')=='bridge'])==1
