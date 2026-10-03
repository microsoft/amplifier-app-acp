import json
from types import SimpleNamespace

import pytest

from amplifier_acp.native.provider_status import authentication_status
from amplifier_acp.native.runtime_controls import RuntimeControls


@pytest.mark.parametrize('status', ['authenticated', 'expired', 'unauthenticated'])
@pytest.mark.asyncio
async def test_status_projection_never_selects_logs_in_or_refreshes(tmp_path, monkeypatch, status):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    calls = []
    class Provider:
        def __init__(self, declared): self.declared = declared
        def get_info(self):
            return {'id':'named-account', 'defaults':{'model':'chosen-model'},
                    'capabilities':['auth:oauth_device_code'] if self.declared else []}
        def auth_status(self):
            assert self.declared, 'An undeclared status method must never run'
            calls.append('status')
            return status
        async def login(self): pytest.fail('A passive read cannot log in')
        async def list_models(self): pytest.fail('A passive read cannot query models')
        async def complete(self, request, **kwargs):
            calls.append('complete')
            return 'selected-provider-result'
    other, selected = Provider(True), Provider(False)
    providers = {'unselected-oauth':other, 'chosen':selected}
    loop = SimpleNamespace(root_provider=selected)
    loop._select_provider = lambda mounted: loop.root_provider
    class Coordinator:
        config = {'providers':[]}
        session_state = {}
        def get(self, name): return {'providers':providers, 'orchestrator':loop}.get(name)
        def get_capability(self, name): return None
        def register_capability(self, *args): pass
    controls = RuntimeControls(SimpleNamespace(session_id='selected', coordinator=Coordinator()), SimpleNamespace(generation=None, queued_inputs=0))
    try:
        result = await controls.perform('configuration.providers')
        oauth, chosen = result['providers']
        assert oauth['authentication'] == {'status':status, 'source':'provider.auth_status', 'observational':True}
        assert oauth['supports']['authStatus'] is True
        assert 'authentication' not in chosen and chosen['supports']['authStatus'] is False
        assert result['effective']['instance'] == 'chosen'
        assert providers == {'unselected-oauth':other, 'chosen':selected}
        assert loop.root_provider is selected and calls == ['status']
        assert not list(tmp_path.rglob('*')), 'Status observation must not persist settings or receipts'
        # Expired does not mean refresh cannot succeed on deliberate use.
        assert await other.complete(None) == 'selected-provider-result'
        assert calls == ['status', 'complete']
    finally:
        await controls.close()


@pytest.mark.parametrize('value', [{'access_token':'private'}, 'private' * 10000, None, True])
def test_unexpected_status_is_bounded_and_never_projected(value):
    observed, supported = authentication_status(SimpleNamespace(auth_status=lambda:value), {'capabilities':['auth:oauth_device_code']})
    assert supported and observed['status'] == 'unknown' and observed['reason'] == 'unsupported-status'
    assert 'private' not in json.dumps(observed) and len(json.dumps(observed)) < 180


def test_status_exception_is_redacted_and_undeclared_method_is_not_invoked():
    def failing(): raise RuntimeError('private account credential')
    provider = SimpleNamespace(auth_status=failing)
    assert authentication_status(provider, {'capabilities':[]}) == (None, False)
    result, supported = authentication_status(provider, {'capabilities':['auth:oauth_device_code']})
    assert supported and result['reason'] == 'status-read-failed'
    assert 'private' not in json.dumps(result)


def test_async_method_is_not_started_or_implicitly_adopted():
    async def forbidden(): pytest.fail('Async authentication is not the passive status contract')
    result, supported = authentication_status(SimpleNamespace(auth_status=forbidden), {'capabilities':['auth:oauth_device_code']})
    assert not supported and result['reason'] == 'async-status-unsupported'
