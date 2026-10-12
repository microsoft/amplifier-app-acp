import io
import pytest
from amplifier_acp.server import Server


@pytest.mark.parametrize('enabled',[False,True])
async def test_initialize_advertises_closed_scoped_signin_marker_only_for_admin(tmp_path,enabled):
    workspace=tmp_path/'workspace';workspace.mkdir()
    config={'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app'),'runtimeImmutable':True}
    if enabled:config['adminWorkspaceRoots']=[str(workspace)]
    server=Server(config,io.StringIO())
    result=await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    native=result['agentCapabilities']['_meta']['amplifier.dev/native']
    if enabled:
        marker=native['admin']['providerSignIn']
        assert marker=={'version':1,'workspaceBound':True,'scopedAttempts':True}
        assert native['admin']['imageSetup']=={'version':1,'catalog':'provider-image-models','preservesChat':True,'exclusive':True}
        assert type(marker['version']) is int and marker['workspaceBound'] is True and marker['scopedAttempts'] is True
    else:assert 'admin' not in native
    assert not server.sessions and not list((tmp_path/'home').glob('projects/*/sessions/*'))
