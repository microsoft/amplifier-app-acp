"""Actual installed Native/loop-live child with the official filesystem tools."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_stopped_child_explicit_cold_resume_preserves_history_and_workspace_file(tmp_path):
    filesystem = importlib.util.find_spec('amplifier_module_tool_filesystem')
    delegate = importlib.util.find_spec('amplifier_module_tool_delegate')
    if filesystem is None or delegate is None:
        pytest.skip('Installed official filesystem and delegate modules required')
    config, cwd = configuration(tmp_path)
    value = json.loads(config.read_text())
    bundle = Path(value['bundle'])
    bundle.write_text(bundle.read_text().replace('config: {}', 'config:\n      workerRecoveryFixture: true')
        + 'tools:\n  - module: tool-filesystem\n    source: ' + str(Path(filesystem.origin).parent)
        + '\n  - module: tool-delegate\n    source: ' + str(Path(delegate.origin).parent)
        + '\nagents:\n  fixture-child:\n    description: File recovery fixture\n    instruction: Use the fixture provider.\n')
    async def initialize(client):
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
    async def page(client, **args):
        result = await client.request('_amplifier/workers', {'sessionId': sid, 'cwd': str(cwd), **args})
        assert 'result' in result, result
        return result['result']
    async def until(client, predicate):
        async with asyncio.timeout(30):
            while True:
                result = await page(client)
                found = next((row for row in result.get('items', []) if predicate(row)), None)
                if found:
                    return found
                await asyncio.sleep(.03)
    client = await Client.launch(config)
    await initialize(client)
    made = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
    assert 'result' in made, made
    sid = made['result']['sessionId']
    task = asyncio.create_task(client.request('session/prompt', {'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'spawn-file-worker'}]}, timeout=90))
    try:
        worker = await until(client, lambda row: row.get('persistent') and row.get('status') == 'idle')
        wid = worker['workerId']
        output = cwd / 'worker-created.txt'
        original = output.read_bytes()
        assert original == b'Preserved worker output before stop.\n'
        stamp = output.stat().st_mtime_ns
        stopped = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'worker.interrupt', 'args': {'workerId': wid, 'commandId': 'stop-file-worker'}})
        assert stopped['result']['accepted'], stopped
        await until(client, lambda row: row.get('workerId') == wid and row.get('status') in {'cancelled', 'interrupted', 'completed'})
        assert 'result' in await asyncio.wait_for(task, 30)
    finally:
        await client.close()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    histories = list(Path(value['home']).glob('projects/*/sessions/' + wid + '/transcript.jsonl'))
    assert len(histories) == 1
    history = histories[0]
    before = [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
    # A new parent process can inspect the stopped worker without execution.
    cold = await Client.launch(config)
    await initialize(cold)
    try:
        saved = await page(cold, workerId=wid)
        assert not saved['resident'] and not saved['item']['canFollowup']
        assert history.read_text().splitlines() and output.read_bytes() == original
        resumed = await cold.request('session/resume', {'sessionId': sid, 'cwd': str(cwd), 'mcpServers': []})
        assert 'result' in resumed, resumed
        assert [json.loads(line) for line in history.read_text().splitlines() if line.strip()] == before
        completed = await cold.request('session/prompt', {'sessionId': sid, 'prompt': [{'type': 'text', 'text': 'resume-file-worker ' + wid}]}, timeout=90)
        assert 'result' in completed, completed
        after = [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
        assert after[:len(before)] == before
        assert len(after) > len(before)
        user_tasks = [str(row.get('content')).rsplit('[YOUR TASK]\n', 1)[-1].strip()
                      for row in after if row.get('role') == 'user']
        assert user_tasks.count('child-create-file') == 1
        assert user_tasks.count('child-after-stop') == 1
        calls = [call for row in after for call in row.get('tool_calls', [])]
        names = [call['tool'] for call in calls]
        assert names.count('write_file') == names.count('read_file') == 1, calls
        assert any(row.get('role') == 'tool' and 'Preserved worker output before stop.' in json.dumps(row) for row in after[len(before):])
        assert output.read_bytes() == original and output.stat().st_mtime_ns == stamp
        worker = await until(cold, lambda row: row.get('workerId') == wid and row.get('status') == 'completed')
        assert worker['runId'] != saved['item']['runId']
    finally:
        await cold.close()
