import ast,json,os
from pathlib import Path
import pytest
from client import Client

def test_no_application_or_lite_engine_imports():
    from amplifier_acp import server
    root=Path(server.__file__).parent
    for path in root.rglob('*.py'):
        tree=ast.parse(path.read_text())
        assert not any(isinstance(n,ast.ClassDef) and n.name=='AppService' for n in ast.walk(tree))
        for node in ast.walk(tree):
            names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(name.split('.')[0] in {'amplifier_web','amplifier_agent','amplifier_app_cli'} for name in names),path

@pytest.mark.asyncio
async def test_history_browse_cannot_start_a_worker(tmp_path):
    home=tmp_path/'native';workspace=tmp_path/'workspace';workspace.mkdir()
    from amplifier_acp.native.session_files import project_slug
    directory=home/'projects'/project_slug(workspace)/'sessions'/'existing';directory.mkdir(parents=True)
    (directory/'transcript.jsonl').write_text('{"role":"user","content":"past","metadata":{"amplifier_input":{"id":"stable"}}}\n')
    (directory/'events.jsonl').symlink_to('/no-access')
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'workerCommand':['/a-worker-must-not-start']}))
    client=await Client.launch(config)
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        result=await client.request('_amplifier/history',{'sessionId':'existing','cwd':str(workspace),'limit':1})
        assert result['result']['turnGroups'][0]['id']=='stable'
        assert result['result']['turnGroups'][0]['state']=='unknown'
    finally:await client.close()
