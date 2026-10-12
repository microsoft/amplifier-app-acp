import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from amplifier_acp.admin import Administration,AdminPreflightRefusal
from amplifier_acp.configuration_target import CAPABILITY

GLOBAL={'kind':'global'}
WORKSPACE={'kind':'workspace','workspaceId':'workspace:one'}

@pytest.mark.asyncio
async def test_explicit_configuration_reads_and_writes_do_not_borrow_workspace(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();app=tmp_path/'app';workspace=tmp_path/'workspace';workspace.mkdir();other=tmp_path/'other';other.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME',str(home));monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(app))
    owner=Administration({'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace),str(other)]})
    async def call(op,args=None,cwd=workspace):return await owner.perform(str(cwd),op,args or {})
    try:
        await call('providers.save',{'id':'shared','module':'provider-fixture','config':{'default_model':'global'},'scope':'global','configurationTarget':GLOBAL})
        await call('providers.save',{'id':'shared','module':'provider-fixture','config':{'default_model':'workspace'},'scope':'project','configurationTarget':WORKSPACE})
        global_rows=await call('providers.list',{'configurationTarget':GLOBAL})
        workspace_rows=await call('providers.list',{'configurationTarget':WORKSPACE})
        other_rows=await call('providers.list',{'configurationTarget':{'kind':'workspace','workspaceId':'workspace:two'}},other)
        model=lambda result:next(p['config']['default_model'] for p in result['providers'] if p['id']=='shared')
        assert model(global_rows)=='global' and model(workspace_rows)=='workspace' and model(other_rows)=='global'
        assert global_rows['configurationTarget']==GLOBAL and workspace_rows['configurationTarget']==WORKSPACE
        await call('modules.save',{'section':'hooks','module':'hooks-one','config':{},'configurationTarget':WORKSPACE})
        assert (await call('modules.list',{'configurationTarget':WORKSPACE}))['modules'][0]['module']=='hooks-one'
        assert not any(row['module']=='hooks-one' for row in (await call('modules.list',{'configurationTarget':GLOBAL}))['modules'])
        assert not (home/'projects').exists()
        assert len(owner.configuration_managers)==3
    finally:await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('args',[
    {'configurationTarget':GLOBAL,'scope':'project'},
    {'configurationTarget':WORKSPACE,'scope':'global'},
    {'configurationTarget':GLOBAL,'workspace':'/ignored'},
    {'configurationTarget':GLOBAL,'nativeProject':'ignored'},
    {'configurationTarget':GLOBAL,'location':{'kind':'managed'}},
    {'configurationTarget':{'kind':'global','workspaceId':'foreign'}},
    {'configurationTarget':{'kind':'workspace','workspaceId':''}},
])
async def test_mixed_target_refuses_before_admission(tmp_path,args):
    owner=Administration({'adminWorkspaceRoots':[str(tmp_path)]});owner.lifecycle.enter=Mock(side_effect=AssertionError('no admission'))
    try:
        with pytest.raises(AdminPreflightRefusal) as exc:await owner.perform(str(tmp_path),'providers.list',args)
        assert exc.value.data['executed'] is False
        owner.lifecycle.enter.assert_not_called();assert owner.setup is None
    finally:await owner.close()

@pytest.mark.asyncio
async def test_actual_admin_peer_advertises_explicit_owners(tmp_path):
    from client import Client
    config=tmp_path/'native.json';config.write_text(json.dumps({'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(tmp_path)],'workerCommand':['/must/not/start']}))
    client=await Client.launch(config)
    try:
        result=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert result['result']['agentCapabilities']['_meta']['amplifier.dev/native']['admin']['configurationTargets']==CAPABILITY
        result=await client.request('_amplifier/admin',{'cwd':str(tmp_path),'operation':'providers.list','args':{'configurationTarget':GLOBAL}})
        assert result['result']['configurationTarget']==GLOBAL
        assert not client.events and not (tmp_path/'home/projects').exists()
    finally:await client.close()
