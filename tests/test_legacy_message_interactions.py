"""Old unmarked transcripts through installed ACP, without metadata backfill."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from client import Client
from native_setup import configuration
from amplifier_acp.native.session_files import project_slug


PUBLIC = "Earlier **public answer**, with `code` and café."
CONTENTS = [
    PUBLIC,
    [{"type": "text", "text": PUBLIC}],
    [{"type": "thinking", "thinking": "Private historical reasoning", "signature": "old-signature"},
     {"type": "text", "text": PUBLIC},
     {"type": "redacted_thinking", "data": "redacted-old-reasoning"}],
]


def seed(tmp_path, content, *, passive=False, boundary=False):
    config, cwd = configuration(tmp_path)
    settings = json.loads(config.read_text())
    if passive:
        settings["workerCommand"] = ["/must-not-start-legacy-interactions"]
        config.write_text(json.dumps(settings))
    else:
        bundle = tmp_path / "fixture.yaml"
        value = yaml.safe_load(bundle.read_text())
        if boundary:
            import amplifier_module_context_managed
            value["session"]["context"] = {
                "module": "context-managed", "source": str(Path(amplifier_module_context_managed.__file__).parent),
                "config": {"engine": "boundary", "token_meter": "actual", "summarize_trigger": 0.70, "summary_target_tokens": 1500}}
        value["providers"][0]["config"]["promptAuditFile"] = str(tmp_path / "provider-prompts.jsonl")
        bundle.write_text(yaml.safe_dump(value))
    sid = str(uuid4())
    directory = Path(settings["home"]) / "projects" / project_slug(cwd) / "sessions" / sid
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text(json.dumps({"session_id": sid, "working_dir": str(cwd), "name": "Saved legacy conversation"}))
    rows = [{"role": "user", "content": "Earlier café question"}, {"role": "assistant", "content": content}]
    transcript = directory / "transcript.jsonl"
    transcript.write_bytes("".join(json.dumps(row, separators=(',', ':')) + "\r\n" for row in rows).encode())
    return config, cwd, sid, directory


async def connect(config, account):
    client = await Client.launch(config)
    result = await client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {"_meta": {
        "amplifier.dev/native": {"version": 1},
        "amplifier.dev/messagePrincipal": {"version": 1, "account": account},
    }}})
    assert result["result"]["agentCapabilities"]["_meta"]["amplifier.dev/native"]["messageInteractions"]["version"] == 1
    return client


async def selected(client, sid, cwd):
    result = await client.request("_amplifier/history", {"sessionId": sid, "cwd": str(cwd), "limit": 50})
    assert "result" in result, result
    update = next(row for row in result["result"]["updates"] if row["sessionUpdate"] == "agent_message_chunk")
    assert update["content"]["text"] == PUBLIC
    reference = update["_meta"]["amplifier.dev/history"]["messageReference"]
    assert reference["sourceDigest"] == hashlib.sha256(PUBLIC.encode()).hexdigest()
    return reference


@pytest.mark.asyncio
@pytest.mark.parametrize("content", CONTENTS, ids=["plain", "text-blocks", "anthropic-thinking"])
async def test_old_history_two_trusted_principals_remove_only_own_reaction(tmp_path, content):
    config, cwd, sid, directory = seed(tmp_path, content, passive=True)
    before = {name: (directory / name).read_bytes() for name in ("metadata.json", "transcript.jsonl")}
    a, b = await connect(config, "account-one"), await connect(config, "account-two")
    try:
        ref = await selected(a, sid, cwd)
        assert await selected(b, sid, cwd) == ref
        async def react(client, identity, present=True, **extra):
            return await client.request("_amplifier/message/reaction", {
                "sessionId": sid, "reference": ref, "emoji": "❤️", "present": present, "commandId": identity, **extra})
        one = (await react(a, "first"))["result"]
        assert one["counts"] == {"❤️": 1} and one["sent"] is False
        assert (await react(a, "first"))["result"] == one
        two = (await react(b, "second"))["result"]
        assert two["counts"] == {"❤️": 2}
        assert "error" in await react(a, "forged", actorId="account-two")
        removed = (await react(a, "remove", False))["result"]
        assert removed["counts"] == {"❤️": 1} and removed["mine"] == []
        other = (await b.request("_amplifier/message/read", {"sessionId": sid, "messageIds": [ref["messageId"]]}))["result"]
        assert other["items"][0]["mine"] == ["❤️"]
        assert (await b.request("_amplifier/message/receipt", {"sessionId": sid, "commandId": "first"}))["result"]["receipt"] is None
        assert {name: (directory / name).read_bytes() for name in before} == before
        assert not any(row["method"] == "session/update" for row in a.events + b.events)
    finally:
        await a.close()
        await b.close()
    cold = await connect(config, "account-one")
    try:
        assert await selected(cold, sid, cwd) == ref
        restored = (await cold.request("_amplifier/message/read", {"sessionId": sid, "messageIds": [ref["messageId"]]}))["result"]["items"][0]
        assert restored["counts"] == {"❤️": 1} and restored["mine"] == []
        assert (await cold.request("_amplifier/message/receipt", {"sessionId": sid, "commandId": "remove"}))["result"]["receipt"] == removed
        assert {name: (directory / name).read_bytes() for name in before} == before
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", CONTENTS, ids=["plain", "text-blocks", "anthropic-thinking"])
@pytest.mark.parametrize("boundary", [False, True], ids=["simple", "managed"])
async def test_old_history_quote_and_passive_feedback_reach_real_worker_on_next_input(tmp_path, content, boundary):
    config, cwd, sid, directory = seed(tmp_path, content, boundary=boundary)
    before = (directory / "transcript.jsonl").read_bytes()
    client = await connect(config, "account-one")
    try:
        ref = await selected(client, sid, cwd)
        reaction = await client.request("_amplifier/message/reaction", {
            "sessionId": sid, "reference": ref, "emoji": "❤️", "present": True, "commandId": "old-heart"})
        assert reaction["result"]["sent"] is False
        assert (directory / "transcript.jsonl").read_bytes() == before
        assert not (tmp_path / "provider-prompts.jsonl").exists()
        resumed = await client.request("session/resume", {"sessionId": sid, "cwd": str(cwd), "mcpServers": []})
        assert "result" in resumed, resumed
        result = await client.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "Please explain that answer"}], "_meta": {
            "amplifier.dev/command": {"commandId": "legacy-reply"},
            "amplifier.dev/messageReply": ref}})
        assert result.get("result", {}).get("stopReason") == "end_turn", result
        requests = [json.loads(line) for line in (tmp_path / "provider-prompts.jsonl").read_text().splitlines()]
        assert len(requests) == 1
        users = [row["content"] for row in requests[0] if row["role"] == "user"]
        quoted = next(text for text in users if "Quoted earlier message" in str(text))
        assert PUBLIC in quoted and "Please explain that answer" in quoted
        assert "Private historical reasoning" not in quoted and "redacted-old-reasoning" not in quoted
        feedback = [text for text in users if "added ❤️ reaction" in str(text)]
        assert len(feedback) == 1 and PUBLIC in feedback[0]
        generations = [row for row in client.events if row.get("params", {}).get("event", {}).get("type") == "generation.started"]
        assert len(generations) == 1
        history = (await client.request("_amplifier/history", {"sessionId": sid, "cwd": str(cwd), "limit": 50}))["result"]
        saved = next(row for row in history["updates"] if row.get("_meta", {}).get("amplifier.dev/history", {}).get("replyTo"))
        quote = saved["_meta"]["amplifier.dev/history"]["replyTo"]
        assert quote["excerpt"] == PUBLIC and quote["sourceDigest"] == ref["sourceDigest"]
        assert await selected(client, sid, cwd) == ref
        assert (directory / "transcript.jsonl").read_bytes().startswith(before)
        visible = (await client.request("_amplifier/message/read", {"sessionId": sid, "messageIds": [ref["messageId"]]}))["result"]["items"][0]
        assert visible["mine"] == ["❤️"]
        old_rows = [json.loads(line) for line in (directory / "transcript.jsonl").read_text().splitlines()][:2]
        assert old_rows == [json.loads(line) for line in before.splitlines()]
    finally:
        await client.close()
    cold = await connect(config, "account-one")
    try:
        assert await selected(cold, sid, cwd) == ref
        restored = (await cold.request("_amplifier/message/read", {"sessionId": sid, "messageIds": [ref["messageId"]]}))["result"]["items"][0]
        assert restored["mine"] == ["❤️"]
        assert (directory / "transcript.jsonl").read_bytes().startswith(before)
    finally:
        await cold.close()

