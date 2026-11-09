"""Trusted owner mode keeps standard transport and default native projection."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_acp.server import Server, RpcError
from amplifier_acp.event_projection import project


def owner():
    return SimpleNamespace(create=AsyncMock(return_value="owned-session"),
        attach=AsyncMock(side_effect=lambda identity, cwd: identity),
        prompt=AsyncMock(return_value={"outcome": "completed", "text": "finished"}),
        cancel=AsyncMock(), release=AsyncMock(), close=AsyncMock())


async def test_owner_mode_has_no_native_extensions_or_unsupported_fallback(tmp_path):
    backend = owner()
    server = Server({}, io.StringIO(), execution_owner=backend)
    initialized = await server.dispatch("initialize", {"protocolVersion": 1,
        "clientCapabilities": {"_meta": {"amplifier.dev/host": {"version": 1}, "amplifier.dev/native": {"version": 1}}}})
    assert initialized["agentCapabilities"]["loadSession"] is False
    assert initialized["agentCapabilities"]["sessionCapabilities"] == {"resume": {}}
    assert "_meta" not in initialized["agentCapabilities"]
    for method in ("session/load", "session/set_mode", "_amplifier/native"):
        with pytest.raises(RpcError) as error:
            await server.dispatch(method, {"sessionId": "missing", "cwd": str(tmp_path)})
        assert error.value.code == -32601
    backend.create.assert_not_called()
    result = await server.dispatch("session/new", {"cwd": str(tmp_path), "mcpServers": []})
    assert result == {"sessionId": "owned-session"}
    backend.create.assert_awaited_once_with(str(tmp_path))
    await server.sessions["owned-session"].close()
    backend.release.assert_awaited_once_with("owned-session")
    backend.cancel.assert_not_called()


async def test_resume_preserves_identity_without_replay_or_creation(tmp_path):
    backend, wire = owner(), io.StringIO()
    server = Server({}, wire, execution_owner=backend)
    await server.dispatch("initialize", {})
    assert await server.dispatch("session/resume", {
        "sessionId": "saved", "cwd": str(tmp_path), "mcpServers": []}) == {}
    backend.attach.assert_awaited_once_with("saved", str(tmp_path))
    backend.create.assert_not_called()
    backend.prompt.assert_not_called()
    assert wire.getvalue() == ""
    assert await server.dispatch("session/prompt", {"sessionId": "saved",
        "prompt": [{"type": "text", "text": "New input"}]}) == {"stopReason": "end_turn"}
    assert backend.prompt.call_args.args[0] == "saved"


async def test_older_owner_does_not_advertise_or_fall_back_from_resume(tmp_path):
    backend = owner()
    del backend.attach
    server = Server({}, io.StringIO(), execution_owner=backend)
    initialized = await server.dispatch("initialize", {})
    assert "resume" not in initialized["agentCapabilities"]["sessionCapabilities"]
    with pytest.raises(RpcError) as error:
        await server.dispatch("session/resume", {"sessionId": "saved", "cwd": str(tmp_path)})
    assert error.value.code == -32601
    backend.create.assert_not_called()
    assert not server.sessions
    assert await server.dispatch("session/new", {"cwd": str(tmp_path)}) == {"sessionId": "owned-session"}


async def test_failed_resume_has_no_creation_fallback(tmp_path):
    backend = owner()
    backend.attach.side_effect = ValueError("Saved conversation is unavailable")
    server = Server({}, io.StringIO(), execution_owner=backend)
    await server.dispatch("initialize", {})
    with pytest.raises(ValueError, match="unavailable"):
        await server.dispatch("session/resume", {"sessionId": "missing", "cwd": str(tmp_path)})
    assert not server.sessions
    backend.create.assert_not_called()


async def test_new_and_resume_share_explicit_connection_limit(tmp_path):
    backend = owner()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def attach(identity, cwd):
        entered.set()
        await finish.wait()
        return identity
    backend.attach.side_effect = attach
    server = Server({"maxSessions": 1}, io.StringIO(), execution_owner=backend)
    await server.dispatch("initialize", {})
    first = asyncio.create_task(server.dispatch("session/resume", {
        "sessionId": "saved", "cwd": str(tmp_path)}))
    await entered.wait()
    second = asyncio.create_task(server.dispatch("session/new", {"cwd": str(tmp_path)}))
    finish.set()
    assert await first == {}
    with pytest.raises(RpcError, match="capacity"):
        await second
    backend.create.assert_not_called()


async def test_cancel_acceptance_does_not_override_completed_race(tmp_path):
    backend = owner()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def prompt(*args):
        entered.set()
        await finish.wait()
        return {"outcome": "completed", "text": "Won the race"}
    backend.prompt.side_effect = prompt
    server = Server({}, io.StringIO(), execution_owner=backend)
    await server.dispatch("initialize", {})
    await server.dispatch("session/new", {"cwd": str(tmp_path)})
    pending = asyncio.create_task(server.dispatch("session/prompt", {"sessionId": "owned-session",
        "prompt": [{"type": "text", "text": "test"}], "_meta": {"mode": "full"}}))
    await entered.wait()
    await server.dispatch("session/cancel", {"sessionId": "owned-session"})
    assert not pending.done()
    finish.set()
    assert await pending == {"stopReason": "end_turn"}
    assert backend.prompt.call_args.args[2] == "test"
    assert len(backend.prompt.call_args.args) == 4  # No peer-supplied authority.


async def test_concurrent_new_requests_share_the_connection_bound(tmp_path):
    backend = owner()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def create(cwd):
        entered.set()
        await finish.wait()
        return "owned-session"
    backend.create.side_effect = create
    server = Server({"maxSessions": 1}, io.StringIO(), execution_owner=backend)
    await server.dispatch("initialize", {})
    first = asyncio.create_task(server.dispatch("session/new", {"cwd": str(tmp_path)}))
    await entered.wait()
    second = asyncio.create_task(server.dispatch("session/new", {"cwd": str(tmp_path)}))
    finish.set()
    assert (await first)["sessionId"] == "owned-session"
    with pytest.raises(RpcError, match="capacity"):
        await second
    backend.create.assert_awaited_once()


async def test_default_owner_connections_have_no_session_count_ceiling(tmp_path):
    backend = owner()
    backend.create.side_effect = [f"owned-{index}" for index in range(40)]
    server = Server({}, io.StringIO(), execution_owner=backend)
    await server.dispatch("initialize", {})
    results = await asyncio.gather(*(server.dispatch("session/new", {"cwd": str(tmp_path)})
                                    for _ in range(40)))
    assert len({result["sessionId"] for result in results}) == 40
    assert backend.create.await_count == 40


async def test_owner_tool_projection_retains_only_current_input_and_unique_wire_ids(tmp_path):
    backend = owner()
    wire = io.StringIO()
    server = Server({}, wire, execution_owner=backend)
    await server.dispatch("initialize", {})
    await server.dispatch("session/new", {"cwd": str(tmp_path)})
    session = server.sessions["owned-session"]
    async def prompt(identity, input_id, text, observe):
        assert session.tools == set()
        for phase in ("running", "completed"):
            await observe({"type": "execution.event", "event": {
                "kind": "tool", "toolCallId": "reused-provider-id", "phase": phase}})
        assert len(session.tools) == 1
        return {"outcome": "completed"}
    backend.prompt.side_effect = prompt
    for _ in range(2):
        await server.dispatch("session/prompt", {"sessionId": "owned-session",
            "prompt": [{"type": "text", "text": "next"}]})
    import json
    updates = [json.loads(line)["params"]["update"] for line in wire.getvalue().splitlines()]
    assert [row["sessionUpdate"] for row in updates] == ["tool_call", "tool_call_update"] * 2
    assert updates[0]["toolCallId"] == updates[1]["toolCallId"]
    assert updates[2]["toolCallId"] == updates[3]["toolCallId"]
    assert updates[0]["toolCallId"] != updates[2]["toolCallId"]


async def test_shared_projection_keeps_text_tool_scope_and_final_deduplication():
    session = SimpleNamespace(identity="root", streamed=False, had_deltas=False,
        last_model_id=None, tools=set(), update=AsyncMock())
    await project(session, {"type": "assistant.delta", "text": "partial"})
    await project(session, {"type": "assistant.message", "text": "partial"})
    assert session.update.await_count == 1
    for child in ("child-a", "child-b"):
        await project(session, {"type": "execution.event", "event": {"kind": "tool",
            "id": "same-call", "sessionId": child, "phase": "running", "label": "write_file"}})
    assert session.tools == {"child-a:same-call", "child-b:same-call"}
    await project(session, {"type": "execution.event", "event": {"kind": "llm", "id": "next",
        "sessionId": "root", "phase": "running"}})
    await project(session, {"type": "assistant.message", "text": "final"})
    assert session.update.call_args.args == ("agent_message_chunk",)
    assert session.update.call_args.kwargs["content"]["text"] == "final"
