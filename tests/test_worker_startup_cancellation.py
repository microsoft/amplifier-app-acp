"""A real child stays owned across startup and repeated cancellation."""
import asyncio
from pathlib import Path
import sys

import pytest

from amplifier_acp.worker import NativeWorker, NativeError
from amplifier_acp.native import generation_leases


CHILD = r'''
import json,sys,time
from pathlib import Path
root=Path(sys.argv[1]);(root/'spawned').write_text('ready')
for line in sys.stdin:
    op=json.loads(line)['op']
    with (root/'controls').open('a') as output:output.write(op+'\n')
    if op=='stop':
        (root/'stopping').write_text('ready')
        while not (root/'release').exists():time.sleep(.005)
        break
'''


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():await asyncio.sleep(.005)


@pytest.mark.parametrize('boundary',['spawn','readiness','lease-error'])
async def test_cancelled_startup_joins_spawn_and_repeatedly_cancelled_close(tmp_path,monkeypatch,boundary):
    async def observe(_):pass
    async def bridge(*_):raise AssertionError('No delegated work')
    worker=NativeWorker('cancel-fixture',str(tmp_path),{
        'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app'),
        'runtimeGeneration':'a'*32,'startupTimeout':30,
        'workerCommand':[sys.executable,'-u','-c',CHILD,str(tmp_path)]},observe,bridge)
    created=[];spawn_release=asyncio.Event();real_spawn=asyncio.create_subprocess_exec
    async def spawn(*args,**kwargs):
        process=await real_spawn(*args,**kwargs);created.append(process)
        if boundary=='spawn':await spawn_release.wait()
        return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    if boundary=='lease-error':
        def fail(*args):raise OSError('Fixture lease acquisition failed')
        monkeypatch.setattr(generation_leases,'acquire',fail)
    task=asyncio.create_task(worker.start())
    try:
        await until(lambda:(tmp_path/'spawned').exists())
        if boundary=='spawn':
            task.cancel();await asyncio.sleep(.01);task.cancel()
            assert not task.done() and not worker._spawn_task.cancelled()
            spawn_release.set()
        elif boundary=='readiness':
            await until(lambda:(tmp_path/'controls').exists())
            task.cancel()
        await until(lambda:(tmp_path/'stopping').exists())
        if boundary!='lease-error':
            task.cancel();await asyncio.sleep(.01)
            assert not task.done() and created[0].returncode is None
        leases=list((tmp_path/'app/updates/worker-leases').glob('*.json'))
        assert len(leases)==(1 if boundary=='readiness' else 0)
        (tmp_path/'release').write_text('exit')
        with pytest.raises(NativeError if boundary=='lease-error' else asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task),5)
        assert len(created)==1 and created[0].returncode==0
        assert worker.closed and worker._close_task.done()
        assert not list((tmp_path/'app/updates/worker-leases').glob('*.json'))
        controls=(tmp_path/'controls').read_text().splitlines()
        assert controls==(['start','stop'] if boundary=='readiness' else ['stop'])
        assert not worker.pending and not worker.tasks
        await worker.close()
    finally:
        spawn_release.set();(tmp_path/'release').write_text('exit')
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await worker.close()
        for process in created:
            if process.returncode is None:process.kill()
            await process.wait()


async def test_concurrent_close_waits_for_exit_even_if_first_waiter_is_cancelled(tmp_path):
    async def observe(_):pass
    async def bridge(*_):raise AssertionError('No delegated work')
    worker=NativeWorker('close-fixture',str(tmp_path),{},observe,bridge)
    worker.process=await asyncio.create_subprocess_exec(sys.executable,'-u','-c',CHILD,str(tmp_path),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
    first=asyncio.create_task(worker.close())
    second=None
    try:
        await until(lambda:(tmp_path/'stopping').exists())
        first.cancel();second=asyncio.create_task(worker.close());await asyncio.sleep(.02)
        assert not first.done() and not second.done()
        (tmp_path/'release').write_text('exit')
        with pytest.raises(asyncio.CancelledError):await first
        await second
        assert worker.process.returncode==0
        assert (tmp_path/'controls').read_text()=='stop\n'
    finally:
        (tmp_path/'release').write_text('exit')
        await asyncio.gather(first,*([second] if second else []),return_exceptions=True)
        await worker.close()
