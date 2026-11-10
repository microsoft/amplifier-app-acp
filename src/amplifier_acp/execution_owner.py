"""Opt-in standard ACP attachment to a trusted, already owned execution service.

The launcher supplies the owner object; ACP input cannot select its principal,
implementation, configuration or standing authority. Native default mode is
unchanged. Saved conversations can resume without replaying their history.
"""
import asyncio
from pathlib import Path
import uuid

from .event_projection import project


class OwnedSession:
    def __init__(self, server, identity):
        self.server, self.identity = server, identity
        self.active_requests = 0
        self.lock = asyncio.Lock()
        self.streamed = self.had_deltas = False
        self.last_model_id = None
        self.tools = set()

    async def update(self, session_update, **data):
        await self.server.notify("session/update", {"sessionId": self.identity,
            "update": {"sessionUpdate": session_update, **data}})

    async def publish_title(self):
        pass

    async def prompt(self, blocks):
        from .server import RpcError
        if (not isinstance(blocks, list) or not blocks or any(
                not isinstance(block, dict) or block.get("type") != "text"
                or not isinstance(block.get("text"), str) for block in blocks)):
            raise RpcError(-32602, "This execution owner accepts text prompt blocks only.")
        if self.lock.locked():
            raise RpcError(-32000, "A prompt is already active for this session.")
        async with self.lock:
            self.streamed = self.had_deltas = False
            self.last_model_id = None
            self.tools.clear()
            input_id = str(uuid.uuid4())
            async def observe(event):
                await project(self, event, tool_namespace=input_id)
            result = await self.server.execution_owner.prompt(self.identity, input_id,
                "\n".join(block["text"] for block in blocks), observe)
            outcome = result.get("outcome")
            if outcome not in {"completed", "cancelled"}:
                raise RpcError(-32000, "Execution outcome is unresolved; inspect owner state before retrying.")
            if result.get("text") and not self.streamed:
                await self.update("agent_message_chunk", content={"type": "text", "text": result["text"]})
            return {"stopReason": "end_turn" if outcome == "completed" else "cancelled"}

    async def close(self):
        # Attachment release is not execution Stop or conversation deletion.
        await self.server.execution_owner.release(self.identity)


async def dispatch(server, method, params):
    from .server import RpcError
    if method == "initialize":
        if server.initialized:
            raise RpcError(-32600, "Connection already initialized.")
        server.initialized = True
        return {"protocolVersion": 1, "agentInfo": {"name": "amplifier-owned-execution", "version": "0.1"},
            "agentCapabilities": {"loadSession": False, "sessionCapabilities": {
                **({"resume": {}} if callable(getattr(server.execution_owner, "attach", None)) else {})}, "promptCapabilities": {
                "image": False, "audio": False, "embeddedContext": False}}, "authMethods": []}
    if not server.initialized:
        raise RpcError(-32002, "Initialize the connection first.")
    if method == "session/resume" and not callable(getattr(server.execution_owner, "attach", None)):
        raise RpcError(-32601, "Resume is not supported by this execution owner.")
    if method in {"session/new", "session/resume"}:
        cwd = Path(params.get("cwd", "")).expanduser()
        if not cwd.is_absolute() or not cwd.is_dir():
            raise RpcError(-32602, "cwd must be an existing absolute directory.")
        if params.get("mcpServers") or params.get("additionalDirectories"):
            raise RpcError(-32602, "MCP servers and additional directories are not supported by this owner.")
        identity = params.get("sessionId") if method == "session/resume" else None
        if method == "session/resume" and (not isinstance(identity, str) or not identity):
            raise RpcError(-32602, "sessionId is required.")
        async with server._owner_creation_lock:
            if identity in server.sessions:
                raise RpcError(-32000, "This session is already attached to this connection.")
            limit = server.config.get("maxSessions")
            if limit is not None and len(server.sessions) >= limit:
                raise RpcError(-32000, "Connection session capacity reached.")
            if method == "session/new":
                identity = await server.execution_owner.create(str(cwd.resolve()))
            else:
                attached = await server.execution_owner.attach(identity, str(cwd.resolve()))
                if attached != identity:
                    raise RpcError(-32000, "Execution owner did not attach the requested identity.")
            if identity in server.sessions:
                raise RpcError(-32000, "Execution owner returned an existing attachment.")
            server.sessions[identity] = OwnedSession(server, identity)
            return {"sessionId": identity} if method == "session/new" else {}
    if method not in {"session/prompt", "session/cancel"}:
        raise RpcError(-32601, "Method is not supported by this execution owner.")
    session = server.sessions.get(params.get("sessionId"))
    if session is None:
        raise RpcError(-32602, "Session is not attached to this connection.")
    if method == "session/prompt":
        return await session.prompt(params.get("prompt"))
    await server.execution_owner.cancel(session.identity)
    return None
