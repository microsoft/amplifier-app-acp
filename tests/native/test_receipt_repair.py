"""Stopped-host receipt repair preserves history without cloning provider data."""
import copy
import json
import uuid
from io import StringIO

import pytest

from amplifier_acp.native.host.session import repair_interrupted_receipts


INTERRUPTED = {
    "status": "interrupted", "job_id": "job-1", "call_id": "call-1",
    "outcome": "unconfirmed", "effects": "not_rolled_back",
    "instruction": "This receipt was imported from a stopped host. Inspect actual state; do not automatically repeat the operation.",
}


@pytest.mark.parametrize("role,content,changes", [
    ("tool", '{"status":"queued","job_id":"job-1","call_id":"call-1"}', True),
    ("tool", '{"status":"queued","job_id":"job-1","call_id":"other"}', False),
    ("tool", '{"status":"queued","job_id":"","call_id":"call-1"}', False),
    ("tool", '{"status":"returned","job_id":"job-1","call_id":"call-1"}', False),
    ("assistant", '{"status":"queued","job_id":"job-1","call_id":"call-1"}', False),
    ("tool", '{malformed', False),
    ("tool", 'null', False),
    ("tool", '[]', False),
    ("tool", {"status": "queued", "job_id": "job-1", "call_id": "call-1"}, False),
])
def test_repair_preserves_matching_and_uncertain_outcome(role, content, changes):
    rows = [{"role": role, "tool_call_id": "call-1", "content": content,
             "metadata": {"retained": ["original"]}}]
    before = copy.deepcopy(rows)
    result = repair_interrupted_receipts(rows)
    expected = [{**before[0], "content": json.dumps(INTERRUPTED)}] if changes else before
    assert result == expected
    assert rows == before
    assert repair_interrupted_receipts(result) == result


def test_repair_does_not_clone_unrelated_nested_provider_history():
    class Continuation(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Unrelated provider state must not be cloned")

    continuation = Continuation(parts=[{"content": ["retained"]}])
    rows = [{"role": "assistant", "content": "saved", "metadata": {"provider": continuation}},
            {"role": "tool", "tool_call_id": "call-1", "metadata": {"provider": continuation},
             "content": '{"status":"queued","job_id":"job-1","call_id":"call-1"}'}]
    before = json.dumps(rows)
    result = repair_interrupted_receipts(rows)
    assert json.dumps(rows) == before
    assert result is not rows and result[0] is rows[0]
    assert result[1] is not rows[1]
    assert result[1]["metadata"] is rows[1]["metadata"]
    assert json.loads(result[1]["content"]) == INTERRUPTED


@pytest.mark.asyncio
async def test_passive_fork_repairs_receipt_and_detaches_public_provenance(tmp_path, monkeypatch):
    from amplifier_acp import session_lifecycle
    from amplifier_acp.history import page
    from amplifier_acp.public_message import COPY_SOURCE, ENVELOPE
    from amplifier_acp.server import NATIVE, Server

    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "writers"))
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    server = Server({"home": str(tmp_path / "native"), "appHome": str(tmp_path / "app"),
                     "workerCommand": ["/must-not-start"]}, StringIO())
    server.initialized = True
    server.extensions = {NATIVE}
    directory = server.directory(str(cwd), "source")
    directory.mkdir(parents=True)
    rows = [
        {"role": "user", "content": "Original", "metadata": {
            "amplifier_input": {"version": 1, "kind": "user", "id": "input-1"},
            ENVELOPE: {"version": 1, "inputId": "input-1", "capture": "host-standard",
                       "blocks": [{"type": "text", "text": "Original"}]}}},
        {"role": "assistant", "content": "Saved work", "tool_calls": [{"id": "call-1",
            "type": "function", "function": {"name": "fixture", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content":
            '{"status":"queued","job_id":"job-1","call_id":"call-1"}'},
    ]
    transcript = directory / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (directory / "metadata.json").write_text(json.dumps({"session_id": "source",
        "working_dir": str(cwd), "bundle": "fixture-bundle"}))
    original = transcript.read_bytes()
    observed = []
    canonical = session_lifecycle.canonical

    def capture(*args, **kwargs):
        result = canonical(*args, **kwargs)
        observed.append((result[0], copy.deepcopy(result[0])))
        return result

    monkeypatch.setattr(session_lifecycle, "canonical", capture)
    target = str(uuid.uuid4())
    args = {"commandId": "fork-1", "targetSessionId": target,
            "expectedHistoryRevision": page(directory)["revision"]}
    request = {"sessionId": "source", "cwd": str(cwd), "operation": "fork", "args": args}
    result = await server.dispatch("_amplifier/session", request)
    assert result["creationConfirmed"] and result["workReplayed"] is False
    assert observed and all(actual == saved for actual, saved in observed)
    assert not server.sessions and transcript.read_bytes() == original
    copied = [json.loads(line) for line in
              (server.directory(str(cwd), target) / "transcript.jsonl").read_text().splitlines()]
    assert copied[0]["metadata"][ENVELOPE] == rows[0]["metadata"][ENVELOPE]
    assert copied[0]["metadata"][COPY_SOURCE]["source"]["sessionId"] == "source"
    assert COPY_SOURCE not in rows[0]["metadata"]
    assert json.loads(copied[2]["content"]) == INTERRUPTED
    repeated = await server.dispatch("_amplifier/session", request)
    assert repeated["sessionId"] == target and repeated["replayed"] is False
    assert transcript.read_bytes() == original
