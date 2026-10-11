import json
from pathlib import Path
from client import Client


async def test_native_roots_are_launcher_owned_and_passive(tmp_path):
    cwd=tmp_path/'workspace';cwd.mkdir();home=tmp_path/'native';home.mkdir();app=tmp_path/'app'
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(cwd)],'workerCommand':['/must/not/start']}))
    client=await Client.launch(config)
    try:
        initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['storageRoots']=={'version':1,'passive':True,'launcherOwned':True}
        args={'cwd':str(cwd),'operation':'storage.roots','args':{}}
        result=await client.request('_amplifier/admin',args)
        assert 'result' in result, result
        assert result['result']['roots']==[
            {'id':'native-app','scope':'app','label':'Native configuration and saved data','path':str(app)},
            {'id':'history','scope':'history','label':'Shared CLI and Unified history (read only)','path':str(home/'projects')}]
        rejected=await client.request('_amplifier/admin',{**args,'args':{'home':str(tmp_path/'foreign')}})
        assert 'error' in rejected and not (tmp_path/'foreign').exists()
        assert not client.events and not (home/'projects').exists()
    finally:await client.close()
