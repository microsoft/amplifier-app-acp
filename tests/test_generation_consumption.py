import json
import pytest
from native_setup import configuration
from client import Client
from amplifier_acp.native.updates import selected_release,foundation_home

def test_worker_generation_is_stable_across_host_promotion(tmp_path,monkeypatch):
    (tmp_path/'updates').mkdir();path=tmp_path/'updates/active.json';path.write_text(json.dumps({'current':'a'*32}))
    monkeypatch.setenv('AMPLIFIER_UNIFIED_RELEASE','b'*32)
    assert selected_release(tmp_path)=='b'*32
    path.write_text(json.dumps({'current':'c'*32}))
    assert foundation_home(tmp_path)==tmp_path/'updates/releases'/('b'*32)/'foundation'
    monkeypatch.setenv('AMPLIFIER_UNIFIED_RELEASE','');assert selected_release(tmp_path) is None

@pytest.mark.asyncio
async def test_existing_qualified_generation_requires_exact_launcher_before_mount(tmp_path):
    config,cwd=configuration(tmp_path);(tmp_path/'app/updates').mkdir(parents=True);(tmp_path/'app/updates/active.json').write_text(json.dumps({'current':'a'*32}))
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1})
        response=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]})
        assert 'explicit workerCommand' in response.get('error',{}).get('message',''),response
        assert not (tmp_path/'home/projects').exists()
    finally:await client.close()
