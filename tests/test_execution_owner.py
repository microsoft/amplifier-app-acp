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
        prompt=AsyncMock(return_value={"outcome": "completed", "text": "finished"}),
        cancel=AsyncMock(), release=AsyncMock(), close=AsyncMock())


async def test_owner_mode_has_no_native_extensions_or_unsupported_fallback(tmp_path):
    backend = owner()
    server = Server({}, io.StringIO(), execution_owner=backend)
    initialized = await server.dispatch("initialize", {"protocolVersion": 1,
        "clientCapabilities": {"_meta": {"amplifier.dev/host": {"version": 1}, "amplifier.dev/native": {"version": 1}}}})
    assert initialized["agentCapabilities"]["loadSession"] is False
    assert "_meta" not in initialized["agentCapabilities"]
    for method in ("session/load", "session/resume", "session/set_mode", "_amplifier/native"):
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
