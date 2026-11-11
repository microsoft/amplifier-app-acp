"""Native worker park/reacquire consumes Foundation's task-local write fence."""
import asyncio
from types import SimpleNamespace

import pytest
from amplifier_foundation.session.activation import ActivationGate
from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp

from amplifier_acp.native.runtime_worker import Worker
from amplifier_acp.native.session_files import sessions_dir


async def test_worker_park_reacquires_without_authorizing_old_callback(tmp_path, monkeypatch):
    worker = Worker()
    worker.workspace, worker.home = tmp_path / 'workspace', tmp_path / 'app'
    worker.workspace.mkdir()
    worker.home.mkdir()
    worker.runtime = SimpleNamespace(session_id='parked-native', queued_inputs=0,
                                     inbox=asyncio.Queue(), generation=None)
    worker.shared_store = SharedSessionStore(worker.workspace, worker.runtime.session_id,
                                             root=tmp_path / 'shared')
    worker.shared_store_stamp = file_stamp
    worker.shared_handle = worker.shared_store.acquire(app='amplifier-acp', fixture=True)
    first_handle = worker.shared_handle
    worker.activation_gate = ActivationGate()
    first = worker.activation = worker.activation_gate.activate()
    native = sessions_dir(worker.workspace) / worker.runtime.session_id / 'transcript.jsonl'
    native.parent.mkdir(parents=True)
    before = b'{"role":"user","content":"kept native conversation"}\n'
    native.write_bytes(before)
    checkpoints = []

    async def checkpoint(status):
        worker.activation_gate.check_current()
        worker.shared_handle.check()
        checkpoints.append(status)

    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda key: None,
        get_capability=lambda key: checkpoint if key == 'live.checkpoint' else None))
    ready, resume = asyncio.Event(), asyncio.Event()

    async def old_callback():
        ready.set()
        await resume.wait()
        assert worker.activation_gate.current() is first
        with pytest.raises(RuntimeError, match='released or superseded'):
            worker.activation_gate.check_current()

    stale = asyncio.create_task(old_callback())
    monkeypatch.setattr('amplifier_acp.native.runtime_worker.publish', lambda data: None)
    try:
        await ready.wait()
        await worker.park(activation=first)
        assert worker.parked and not first_handle.active and worker.shared_handle is None
        await worker.acquire_for_mutation()
        assert not worker.parked and worker.shared_handle.active
        assert worker.activation is not first
        worker.activation_gate.check_current()
        resume.set()
        await stale
        assert checkpoints == ['completed']
        assert native.read_bytes() == before
        assert worker.runtime.session_id == 'parked-native'
    finally:
        stale.cancel()
        await asyncio.gather(stale, return_exceptions=True)
        if worker.ownership.registration:
            await worker.ownership.registration.close()
        if worker.shared_handle:
            worker.shared_handle.release()
