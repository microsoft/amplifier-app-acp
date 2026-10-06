"""Pre-worker failures retain private diagnostics and never submit input."""
import asyncio
import json
from pathlib import Path
import stat

import pytest

from amplifier_acp.worker import NativeWorker, NativeError
from amplifier_acp.server import Server
from amplifier_acp.lifecycle_failure import read, save_startup
from amplifier_acp.native.worker_diagnostics import setup_failure_detail


@pytest.mark.asyncio
async def test_spawn_failure_is_private_and_can_be_inspected(tmp_path, monkeypatch):
    secret = 'startup-credential-value-12345'
    monkeypatch.setenv('PRIVATE_API_KEY', secret)
    inherited = tmp_path/'wrong-app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(inherited))
    config = {'appHome': str(tmp_path/'app'), 'home': str(tmp_path/'home'),
              'runtimeGeneration': None, 'workerCommand': ['/not/a/worker']}
    observed = []
    async def observe(row): observed.append(row)
    async def bridge(*args): raise AssertionError('No host work')
    async def fail(*args, **kwargs): raise FileNotFoundError('Missing binary '+secret)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fail)
    worker = NativeWorker('startup-test', str(tmp_path), config, observe, bridge)
    with pytest.raises(NativeError) as caught:
        await worker.start()
    error = caught.value
    assert worker.closed and worker.process is None
    assert error.data['inputSubmitted'] is False and error.data['replayed'] is False
    assert error.data['category'] == 'worker_startup'
    assert secret not in str(error) + json.dumps(error.data) + json.dumps(observed)
    assert len(observed) == 1 and observed[0]['type'] == 'runtime.error'
    receipt = tmp_path/'app/logs/workers'/error.data['diagnosticReceipt']
    assert receipt.is_file() and stat.S_IMODE(receipt.stat().st_mode) == 0o600
    detail = receipt.read_text()
    assert 'Missing binary' in detail and secret not in detail and '[REDACTED]' in detail
    assert not inherited.exists()
    server = Server(config, None)
    save_startup(server, 'startup-test', observed[0])
    saved = read(server, 'startup-test')
    assert saved['source'] == 'recorded-native-startup'
    assert saved['diagnosticReceipt'] == receipt.name
    assert saved['inputSubmitted'] is False
    assert 'Missing binary' not in json.dumps(saved)


@pytest.mark.asyncio
async def test_failed_qualification_preserves_probe_guidance_without_spawning(tmp_path, monkeypatch):
    from amplifier_acp.native import runtime_profiles
    error = RuntimeError('private resolver payload')
    error.diagnostic_facts = {'probe': {'ok': False, 'stage': 'prepare',
        'errorType': 'BundleNotFoundError', 'untrusted': 'never public'}}
    async def ensure(*args, **kwargs): raise error
    monkeypatch.setattr(runtime_profiles, 'ensure', ensure)
    async def spawn(*args, **kwargs): raise AssertionError('Must not spawn')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    observed = []
    async def observe(row): observed.append(row)
    async def bridge(*args): raise AssertionError('No host work')
    config = {'appHome': str(tmp_path/'app'), 'runtimeGeneration': 'a'*32,
              'managedGenerations': True}
    worker = NativeWorker('qualified-start', str(tmp_path), config, observe, bridge)
    with pytest.raises(NativeError) as caught: await worker.start()
    assert caught.value.data['category'] == 'bundle_configuration'
    assert caught.value.data['errorType'] == 'BundleNotFoundError'
    assert 'Choose a registered bundle ID' in str(caught.value)
    assert 'private resolver payload' not in str(caught.value)
    assert 'never public' not in json.dumps(observed)
    assert worker.closed and worker.process is None


@pytest.mark.asyncio
async def test_diagnostic_write_failure_does_not_mask_startup_failure(tmp_path, monkeypatch):
    from amplifier_acp.native import worker_diagnostics
    monkeypatch.setattr(worker_diagnostics, 'save_startup_failure', lambda *a, **k: None)
    async def observe(row): raise OSError('Disconnected client')
    async def bridge(*args): raise AssertionError('No host work')
    worker = NativeWorker('missing-log', str(tmp_path), {
        'appHome': str(tmp_path/'app'), 'runtimeGeneration': None,
        'workerCommand': ['/not/a/worker']}, observe, bridge)
    with pytest.raises(NativeError, match='could not start') as caught:
        await worker.start()
    assert 'diagnosticReceipt' not in caught.value.data
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert worker.closed


def test_unformattable_exception_does_not_hide_failure():
    class BrokenError(Exception):
        def __str__(self): raise ValueError('unformattable')
    assert 'Exception text could not be formatted' in setup_failure_detail(BrokenError())


@pytest.mark.asyncio
async def test_same_native_handle_can_recover_after_failed_spawn_without_replay(tmp_path):
    from amplifier_acp.server import Session
    from native_setup import configuration
    path, cwd = configuration(tmp_path)
    config = json.loads(path.read_text())
    server = Server(config, None)
    async def notify(*args): pass
    server.notify = notify
    session = Session(server, 'startup-recovery', str(cwd))
    config['workerCommand'] = ['/not/a/worker']
    for _ in range(2):
        with pytest.raises(NativeError, match='could not start'):
            await session.start()
        assert session.worker.closed and session.worker.process is None
    config.pop('workerCommand')
    try:
        await session.start()
        assert session.report['standalone']
        assert session.worker.process.returncode is None
        assert read(server, session.identity)['current'] is False
        directory = server.directory(str(cwd), session.identity)
        transcript = directory/'transcript.jsonl'
        rows = [json.loads(line) for line in transcript.read_text().splitlines()] if transcript.exists() else []
        assert not any(row.get('role') in {'user', 'assistant'} for row in rows)
    finally:
        if session.idle_task: session.idle_task.cancel()
        await session.worker.close()


@pytest.mark.asyncio
async def test_cancelled_preparation_does_not_report_failed_input(tmp_path, monkeypatch):
    from amplifier_acp.native import runtime_profiles
    entered = asyncio.Event()
    async def ensure(*args, **kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(runtime_profiles, 'ensure', ensure)
    observed = []
    async def observe(row): observed.append(row)
    async def bridge(*args): raise AssertionError('No host work')
    worker = NativeWorker('cancelled-preparation', str(tmp_path), {
        'appHome': str(tmp_path/'app'), 'runtimeGeneration': 'a'*32,
        'managedGenerations': True}, observe, bridge)
    task = asyncio.create_task(worker.start())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert worker.closed and worker.process is None and observed == []
    assert not (tmp_path/'app/logs').exists()
