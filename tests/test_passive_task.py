import json
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_persisted_task_read_without_worker_or_history_loading(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config)
    async def init(c):return await c.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    try:
        await init(client);sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        made=await client.request('_amplifier/native',{'sessionId':sid,'operation':'task.create','args':{'objective':'Persist for passive authority','maxTurns':3,'commandId':'task1','expectedRevision':0}})
        task=made['result']['task']
    finally:await client.close()
    values=json.loads(config.read_text());values['workerCommand']=['/must/not/start'];config.write_text(json.dumps(values));cold=await Client.launch(config)
    try:
        await init(cold)
        params={'sessionId':sid,'cwd':str(cwd)}
        result=await cold.request('_amplifier/task/inspect',params);assert result['result']['task']['id']==task['id'] and result['result']['task']['revision']==task['revision']
        assert result['result']['source']=='persisted-native-controls'
        bad=await cold.request('_amplifier/task/inspect',{**params,'expectedRevision':'stale'});assert 'error' in bad
        other=tmp_path/'elsewhere';other.mkdir();denied=await cold.request('_amplifier/task/inspect',{**params,'cwd':str(other)});assert 'error' in denied
        assert not cold.events
    finally:await cold.close()
