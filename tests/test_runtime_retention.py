"""Connection-owned timer negotiation; inert workers, no model or runtime mount."""
import asyncio
import io
from copy import deepcopy
from types import SimpleNamespace

import pytest
from amplifier_acp.server import Server, Session, RpcError, HOST, NATIVE

OFFER = {'version': 1, 'owner': 'host', 'lifetime': 'connection'}
ACK = {**OFFER, 'standaloneIdleSeconds': 60}


def server(tmp_path, **config):
    return Server({'home': str(tmp_path/'home'), 'appHome': str(tmp_path/'app'), **config}, io.StringIO())


async def initialize(peer, offer=None, host_version=1):
    meta = {} if offer is None else {HOST: {'version': host_version, 'runtimeRetention': offer}}
    return await peer.dispatch('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': meta}})


def lifecycle(reply):
    return reply['agentCapabilities']['_meta'][NATIVE]['lifecycle']


@pytest.mark.parametrize('offer,host_version', [
    (None, 1), ({}, 1), (False, 1), ('host', 1),
    ({**OFFER, 'version': 2}, 1), ({**OFFER, 'version': True}, 1),
    ({**OFFER, 'version': 1.0}, 1), ({**OFFER, 'owner': 'client'}, 1),
    ({**OFFER, 'lifetime': 'session'}, 1), ({'version': 1, 'owner': 'host'}, 1),
    ({**OFFER, 'heartbeat': True}, 1), (OFFER, 2), (OFFER, True), (OFFER, 1.0),
])
async def test_generic_unsupported_or_ambiguous_offer_keeps_standalone(tmp_path, offer, host_version):
    peer = server(tmp_path)
    reply = await initialize(peer, offer, host_version)
    assert 'runtimeRetention' not in lifecycle(reply)
    assert peer.host_runtime_retention is False
    assert peer.sessions == {} and not (tmp_path/'app').exists()


async def test_exact_offer_binds_before_sessions_and_cannot_be_renegotiated(tmp_path):
    peer = server(tmp_path)
    offer = deepcopy(OFFER)
    reply = await initialize(peer, offer)
    assert lifecycle(reply)['runtimeRetention'] == ACK
    assert peer.host_runtime_retention and peer.sessions == {}
    # Both request and returned metadata are caller-owned mutable dictionaries.
    offer['owner'] = 'other'
    lifecycle(reply)['runtimeRetention']['owner'] = 'other'
    peer.host_info['runtimeRetention']['lifetime'] = 'other'
    assert peer.host_runtime_retention
    with pytest.raises(RpcError, match='already initialized'):
        await initialize(peer)
    with pytest.raises(AttributeError): peer.host_runtime_retention = False
    assert peer.host_runtime_retention
    other = server(tmp_path/'other')
    assert 'runtimeRetention' not in lifecycle(await initialize(other))
    assert not other.host_runtime_retention


class Worker:
    def __init__(self, results=None):
        self.closed = False
        self.calls = []
        self.results = list(results or [{'retired': True}])
        self.process = SimpleNamespace(returncode=None, wait=self.wait)
        self.retirement_confirmed = False
        self.control_reply = asyncio.Event()
    async def command(self, operation):
        self.calls.append(operation)
        result = self.results.pop(0)
        if result.get('retired'):
            self.closed = True
            self.retirement_confirmed = True
        self.control_reply.set()
        return result
    async def wait(self):
        self.process.returncode = 0
        return 0
    async def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.delays = asyncio.Queue()
        self.ticks = asyncio.Queue()
    async def sleep(self, delay):
        await self.delays.put(delay)
        await self.ticks.get()
    async def next_delay(self):
        return await asyncio.wait_for(self.delays.get(), 2)


@pytest.mark.parametrize('offer', [None, {**OFFER, 'version': 2}])
@pytest.mark.parametrize('idle_seconds', [None, 7])
async def test_standalone_timer_default_and_config_remain_effective(tmp_path, monkeypatch, offer, idle_seconds):
    peer = server(tmp_path, **({} if idle_seconds is None else {'idleSeconds': idle_seconds}))
    await initialize(peer, offer)
    session = Session(peer, 'fixture', str(tmp_path)); session.worker = Worker()
    clock = Clock(); monkeypatch.setattr(asyncio, 'sleep', clock.sleep)
    session.schedule_retirement()
    try:
        assert await clock.next_delay() == (60 if idle_seconds is None else idle_seconds)
        clock.ticks.put_nowait(None)
        await asyncio.wait_for(session.idle_task, 2)
        assert session.worker.calls == ['retire'] and session.worker.process.returncode == 0
    finally: await session.close()


async def test_agreement_suppresses_only_autonomous_timer_and_preserves_explicit_refusal(tmp_path, monkeypatch):
    peer = server(tmp_path)
    await initialize(peer, deepcopy(OFFER))
    session = Session(peer, 'fixture', str(tmp_path)); session.worker = Worker([{'retired': False}, {'retired': True}])
    async def forbidden_sleep(delay): raise AssertionError('Host-owned connection must not schedule idle retirement')
    monkeypatch.setattr(asyncio, 'sleep', forbidden_sleep)
    session.schedule_retirement()
    assert session.idle_task is None and session.worker.calls == []
    assert await session.retire() == {'retired': False, 'reason': 'owned_work'}
    assert not session.retiring and not session.retired and not session.worker.closed
    assert session.idle_task is None and session.worker.calls == ['retire']
    assert await session.retire() == {'retired': True}
    assert session.worker.calls == ['retire', 'retire'] and session.retired
    await session.close()


@pytest.mark.parametrize('guard', ['active_requests', 'start_lock', 'config_applying', 'prompt'])
async def test_standalone_timer_preserves_active_and_owned_work_guards(tmp_path, monkeypatch, guard):
    peer = server(tmp_path); await initialize(peer)
    session = Session(peer, 'fixture', str(tmp_path)); session.worker = Worker([{'retired': False}, {'retired': True}])
    if guard == 'start_lock': await session.start_lock.acquire()
    elif guard == 'prompt': session.prompt = asyncio.get_running_loop().create_future()
    else: setattr(session, guard, True)
    clock = Clock(); monkeypatch.setattr(asyncio, 'sleep', clock.sleep)
    session.schedule_retirement()
    try:
        assert await clock.next_delay() == 60
        clock.ticks.put_nowait(None)
        assert await clock.next_delay() == 60 and session.worker.calls == []
        if guard == 'start_lock': session.start_lock.release()
        elif guard == 'prompt': session.prompt.set_result(None)
        else: setattr(session, guard, False)
        clock.ticks.put_nowait(None)
        assert await clock.next_delay() == 60 and session.worker.calls == ['retire']
        assert not session.worker.closed and session.worker_retirement_pending is None
        clock.ticks.put_nowait(None)
        await asyncio.wait_for(session.idle_task, 2)
        assert session.worker.calls == ['retire', 'retire']
    finally: await session.close()


@pytest.mark.parametrize('offer', [None, OFFER])
async def test_connection_eof_cancels_without_restart_or_replay(tmp_path, monkeypatch, offer):
    peer = server(tmp_path); await initialize(peer, deepcopy(offer))
    session = Session(peer, 'fixture', str(tmp_path)); session.worker = Worker()
    peer.sessions[session.identity] = session
    pending = asyncio.get_running_loop().create_future(); peer.pending['pending'] = pending
    peer.track(asyncio.Event().wait()); waiting = next(iter(peer.tasks))
    reader = None; closed = []
    async def pipe(factory, stdin):
        nonlocal reader
        protocol = factory(); reader = protocol._stream_reader
        reader.feed_eof()
        return SimpleNamespace(close=lambda: closed.append(True)), protocol
    monkeypatch.setattr(asyncio.get_running_loop(), 'connect_read_pipe', pipe)
    await peer.run()
    assert closed == [True] and session.cancelled and session.worker.closed
    assert session.worker.calls == [] and pending.cancelled() and waiting.cancelled()
    assert not (tmp_path/'app').exists()


@pytest.mark.parametrize('offer', [None, OFFER])
async def test_actual_cold_acp_connection_acknowledges_without_starting_worker(tmp_path, offer):
    import json
    from client import Client
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'home': str(tmp_path/'home'), 'appHome': str(tmp_path/'app'),
                                  'workerCommand': ['/must/not/start']}))
    peer = await Client.launch(config)
    try:
        meta = {} if offer is None else {HOST: {'version': 1, 'runtimeRetention': deepcopy(offer)}}
        reply = await peer.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': meta}})
        marker = lifecycle(reply['result'])
        if offer is None: assert 'runtimeRetention' not in marker
        else: assert marker['runtimeRetention'] == ACK
        repeated = await peer.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {}}})
        assert repeated['error']['code'] == -32600
        assert peer.events == [] and not (tmp_path/'app').exists()
    finally: await peer.close()
