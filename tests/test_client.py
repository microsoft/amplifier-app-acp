import asyncio
import base64
import sys

import pytest

from client import Client


@pytest.mark.asyncio
async def test_client_reads_base64_archive_chunk_larger_than_default_line_limit(monkeypatch):
    launch = asyncio.create_subprocess_exec
    payload = bytes(range(256)) * 256
    peer = """
import base64, json, sys
for line in sys.stdin:
    request = json.loads(line)
    result = {'data': base64.b64encode(bytes(range(256)) * 256).decode()} if request['method'] == 'archive' else {'available': True}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
"""

    async def fixture_peer(*args, **kwargs):
        # Exercise the real pipe reader with Client.launch's transport options.
        return await launch(sys.executable, '-I', '-B', '-c', peer, **kwargs)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fixture_peer)
    client = await Client.launch('unused-fixture-config')
    try:
        response = await client.request('archive', {}, timeout=3)
        assert len(response['result']['data']) > 65536
        assert base64.b64decode(response['result']['data']) == payload
        assert (await client.request('status', {}, timeout=3))['result'] == {'available': True}
    finally:
        await client.close()
