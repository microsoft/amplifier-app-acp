import pytest
from amplifier_acp.native import provider_probe


async def test_native_metadata_and_catalog_materialize_config_close_providers_and_hide_secrets(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key,config):
            if config:
                assert api_key=='private' and config=={'api_key':'private','base_url':''}
            else:
                assert api_key is None and config=={}
        def get_info(self):
            return {'config_fields':[
                {'id':'api_key','field_type':'secret','required':True},
                {'id':'base_url','field_type':'text','required':False},
                {'id':'reasoning_effort','field_type':'choice','required':False,'choices':['low','high']},
            ]}
        async def list_models(self):return [{'id':'model','api_key':'private'}]
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    monkeypatch.setenv('TEST_PROBE_KEY','private')
    monkeypatch.delenv('TEST_PROBE_BASE_URL',raising=False)
    result=await provider_probe.query({'module':'test','action':'providers.models','config':{'api_key':'${TEST_PROBE_KEY}','base_url':'${TEST_PROBE_BASE_URL}'}})
    assert result['models']==[{'id':'model'}] and closed==[True,True]
    assert result['configSchema']['fields'][2]['choices']==['low','high']
    assert 'private' not in str(result)


async def test_schema_probe_uses_empty_config_without_listing_models_and_closes_provider(monkeypatch):
    closed=[]
    metadata_calls=[]
    class Provider:
        def __init__(self,*,api_key,config):
            assert api_key is None and config=={}
        def get_info(self):
            metadata_calls.append(True)
            return {'config_fields':[]}
        async def list_models(self):raise AssertionError('schema discovery must not list models')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    result=await provider_probe.query({'module':'test','action':'providers.schema','config':{'ignored':'${MISSING}'}})
    assert result['configSchema']=={'fields':[]} and closed==[True]
    assert metadata_calls == [True]


async def test_failed_catalog_still_closes_provider(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key=None,config=None):pass
        def get_info(self):return {'config_fields':[]}
        async def list_models(self):raise ValueError('private exception')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    with pytest.raises(ValueError):await provider_probe.query({'module':'test','action':'providers.models'})
    assert closed==[True,True]


@pytest.mark.parametrize('action', ['providers.schema', 'providers.models', 'providers.test'])
async def test_endpoint_provider_uses_explicit_unmodified_endpoint_and_metadata_has_no_secrets(monkeypatch, action):
    endpoint = 'https://configured.test/v1/?route=%2F'
    calls, closed = [], []
    class Provider:
        def __init__(self, base_url=None, *, api_key=None, config=None):
            assert base_url == endpoint
            assert api_key == ('private' if config else None)
            self.config = config
            calls.append(('construct', bool(config)))
        def get_info(self):
            return {'config_fields': [
                {'id': 'base_url', 'field_type': 'text', 'required': True},
                {'id': 'api_key', 'field_type': 'secret', 'required': True},
            ]}
        async def list_models(self):
            assert self.config and action != 'providers.schema'
            calls.append(('models', True))
            return [{'id': 'configured-model'}]
        async def close(self):
            closed.append(True)
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setenv('EXACT_PROVIDER_ENDPOINT', endpoint)
    monkeypatch.setenv('EXACT_PROVIDER_KEY', 'private')
    result = await provider_probe.query({'module': 'provider-endpoint', 'action': action,
        'config': {'base_url': '${EXACT_PROVIDER_ENDPOINT}', 'api_key': '${EXACT_PROVIDER_KEY}'}})
    assert 'private' not in str(result)
    assert calls == ([('construct', False)] if action == 'providers.schema' else
                     [('construct', False), ('construct', True), ('models', True)])
    assert len(closed) == (1 if action == 'providers.schema' else 2)


@pytest.mark.parametrize('models', [[], [{'id':'static-or-cached-model'}]])
async def test_catalog_check_does_not_claim_connectivity_or_invoke_inference(monkeypatch, models):
    calls = []
    class Provider:
        def __init__(self, *, api_key=None, config=None): pass
        def get_info(self): return {'config_fields': []}
        def list_models(self):
            calls.append('catalog')
            return models
        async def complete(self, *args, **kwargs): pytest.fail('Catalog checks cannot send a message')
        async def login(self, *args, **kwargs): pytest.fail('Catalog checks cannot sign in')
        async def close(self): calls.append('close')
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    result = await provider_probe.query({'module':'fixture', 'action':'providers.test'})
    assert result['models'] == models
    assert result['test'] == {'catalogAvailable':True, 'reachable':None,
        'modelCount':len(models), 'method':'provider.list_models', 'inferenceVerified':False}
    assert calls == ['close', 'catalog', 'close']


async def test_failed_catalog_check_preserves_failure_and_closes(monkeypatch):
    closed = []
    class Provider:
        def __init__(self, *, api_key=None, config=None): pass
        def get_info(self): return {}
        async def list_models(self): raise ValueError('catalog unavailable')
        async def close(self): closed.append(True)
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    with pytest.raises(ValueError, match='catalog unavailable'):
        await provider_probe.query({'module':'fixture', 'action':'providers.test'})
    assert closed == [True, True]
