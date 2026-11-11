"""Actual worker acquisition cannot leave a late OS writer behind on cancellation."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from amplifier_foundation.session.activation import ActivationGate
from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp

from amplifier_acp.native.runtime_worker import Worker
from amplifier_acp.native.session_files import sessions_dir


async def reached(event):
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(.001)


def wait(event):
    assert event.wait(8), 'Controlled acquisition barrier did not open'


@pytest.mark.skipif(os.name != 'posix', reason='Independent process assertion uses POSIX writer lock')
@pytest.mark.parametrize('entry', ['startup', 'parked'])
@pytest.mark.parametrize('phase', ['before-acquire', 'after-acquire'])
async def test_worker_cancellation_settles_late_writer_before_return(tmp_path, monkeypatch, entry, phase):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'shared'))
    monkeypatch.setattr('amplifier_acp.native.runtime_bootstrap.bootstrap_app_package', lambda: None)
    monkeypatch.setattr('amplifier_acp.native.runtime_worker.publish', lambda data: None)
    prepare, recover = AsyncMock(), Mock()
    monkeypatch.setattr('amplifier_acp.native.host.session.prepare_manager', prepare)
    monkeypatch.setattr('amplifier_acp.native.history_revision.recover_pending', recover)
    worker = Worker()
    worker.workspace, worker.home = tmp_path / 'workspace', tmp_path / 'app'
    worker.workspace.mkdir()
    identity = 'cancelled-native'
    store = SharedSessionStore(worker.workspace, identity, root=tmp_path / 'shared')
    worker.shared_store, worker.shared_store_stamp = store, file_stamp
    worker.activation_gate = ActivationGate()
    if entry == 'parked':
        worker.parked = True
        worker.runtime = SimpleNamespace(session_id=identity)
    native = sessions_dir(worker.workspace) / identity / 'transcript.jsonl'
    native.parent.mkdir(parents=True)
    before = b'{"role":"user","content":"preserved native history"}\n'
    native.write_bytes(before)
    entered, proceed, releasing, released = (threading.Event() for _ in range(4))
    finished = threading.Event()
    handles, calls = [], []
    acquire = store.acquire

    def delayed_acquire(**kwargs):
        calls.append(kwargs)
        if phase == 'before-acquire':
            entered.set()
            wait(proceed)
        handle = acquire(**kwargs)
        handles.append(handle)
        release = handle.release

        def held_release():
            releasing.set()
            wait(released)
            release()

        handle.release = held_release
        if phase == 'after-acquire':
            entered.set()
            wait(proceed)
        return handle

    def delayed(**kwargs):
        try:
            return delayed_acquire(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(store, 'acquire', delayed)
    task = asyncio.create_task(worker.start({'id': identity, 'workspace': str(worker.workspace)},
        recover_bundle=False) if entry == 'startup' else worker.acquire_for_mutation())
    try:
        await reached(entered)
        task.cancel('original stop')
        await asyncio.sleep(0)
        task.cancel('repeat while acquiring')
        await asyncio.sleep(0)
        assert not task.done(), 'The worker returned while an acquisition could still own the lock'
        proceed.set()
        await reached(releasing)
        task.cancel('repeat while releasing')
        await asyncio.sleep(0)
        assert not task.done(), 'Cancellation must wait for release settlement'
        released.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
        assert cancelled.value.args == ('original stop',)
        assert len(handles) == 1 and not handles[0].active
        assert calls == [{'app': 'amplifier-acp', 'pid': os.getpid()}]
        assert worker.shared_handle is None and worker.activation is None
        assert entry != 'parked' or worker.parked
        prepare.assert_not_awaited()
        recover.assert_not_called()
        assert native.read_bytes() == before
        probe = """import json,sys
from amplifier_foundation.session.shared_state import SharedSessionStore
store=SharedSessionStore(sys.argv[1],sys.argv[2],root=sys.argv[3])
held=store.acquire(app='independent-check')
try:print(json.dumps({'active':held.active}))
finally:held.release()
"""
        check = subprocess.run([sys.executable, '-c', probe, str(worker.workspace), identity,
                                str(tmp_path / 'shared')], capture_output=True, text=True, timeout=5)
        assert check.returncode == 0, check.stderr
        assert json.loads(check.stdout) == {'active': True}
    finally:
        proceed.set()
        released.set()
        await asyncio.gather(task, return_exceptions=True)
        # Keep the intentionally failing old implementation from leaking its
        # late test handle; this is test cleanup, not production cancellation.
        await reached(finished)
        for handle in handles:
            if handle.active:
                handle.release()
        if worker.maintenance_gate:
            worker.maintenance_gate.release()
