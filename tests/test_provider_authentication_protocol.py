"""Actual ACP/Core/Foundation proof with a separately owned ChatGPT provider.

Set CHATGPT_PROVIDER_SOURCE to the qualified provider checkout or ``installed``.
The worker uses that exact source without changing the caller's environment.
"""
import json
import importlib.util
import os
from pathlib import Path
import sys

import pytest

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expired,status', [('chatgpt_codex',False,'unauthenticated'), ('chatgpt_codex',True,'expired'), ('chatgpt_plan',False,'unauthenticated')])
async def test_actual_graph_preserves_unselected_unavailable_provider(tmp_path, monkeypatch, mode, expired, status):
    source = os.environ.get('CHATGPT_PROVIDER_SOURCE')
    if not source:
        pytest.skip('Set CHATGPT_PROVIDER_SOURCE to the independently qualified mount-correction source')
    if source == 'installed':
        source = str(Path(importlib.util.find_spec('amplifier_module_provider_openai_chatgpt').origin).parent.parent)
    source = str(Path(source).resolve())
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '1')
    config, cwd = configuration(tmp_path)
    values = json.loads(config.read_text())
    tokens = tmp_path / 'fixture-tokens.json'
    if expired:
        tokens.write_text(json.dumps({'access_token':'synthetic-not-a-credential', 'refresh_token':'synthetic-not-a-credential', 'expires_at':'2000-01-01T00:00:00+00:00'}))
    token_before = tokens.read_bytes() if tokens.exists() else None
    audit = tmp_path / 'forbidden-effects'
    fixture_audit = tmp_path / 'fixture-requests'
    bundle = Path(values['bundle'])
    bundle.write_text(bundle.read_text().replace('config: {}', 'config:\n      selectedModelAudit: ' + str(fixture_audit)) +
        '\n  - module: provider-openai-chatgpt\n    source: ' + source +
        '\n    instance_id: unavailable-chatgpt\n    config:\n      auth_mode: ' + mode +
        '\n      token_file_path: ' + str(tokens) + '\n      login_on_mount: false\n      priority: 999\n')
    openai_source = Path(importlib.util.find_spec('amplifier_module_provider_openai').origin).parent
    with bundle.open('a') as stream:
        stream.write('\n  - module: provider-openai\n    source: ' + str(openai_source) +
                     '\n    instance_id: actual-openai\n    config:\n      api_key: synthetic-not-a-credential\n      priority: 1000\n')
    guard = tmp_path / 'guarded-worker.py'
    guard.write_text('''import sys,asyncio,runpy
from pathlib import Path
sys.path.insert(0,''' + repr(source) + ''')
audit=Path(''' + repr(str(audit)) + ''')
def denied(*args,**kwargs):
 audit.write_text('forbidden authentication or provider request')
 raise AssertionError('Passive mount/status must not authenticate or query provider')
async def denied_async(*args,**kwargs):denied()
def observe(event,args):
 if event in {'socket.connect','socket.getaddrinfo'}:denied()
sys.addaudithook(observe)
import amplifier_module_provider_openai_chatgpt as module
from amplifier_module_provider_openai_chatgpt import provider,oauth,plan_auth
module.login=denied_async
oauth.login=denied_async;oauth.refresh_tokens=denied_async
provider.oauth_login=denied_async;provider.refresh_tokens=denied_async
plan_auth.ensure_tokens=denied_async;plan_auth.login=denied_async
provider.ChatGPTProvider.login=denied_async
provider.ChatGPTProvider.complete=denied_async
provider.ChatGPTProvider.list_models=denied_async
runpy.run_module('amplifier_acp.native.runtime_worker',run_name='__main__')
''')
    values['workerCommand'] = [sys.executable, str(guard)]
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion':1, 'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        created = await client.request('session/new', {'cwd':str(cwd), 'mcpServers':[]})
        assert 'result' in created, created
        sid = created['result']['sessionId']
        async def control(operation,args=None):
            response = await client.request('_amplifier/native', {'sessionId':sid, 'operation':operation, 'args':args or {}})
            assert 'result' in response, response
            return response['result']
        await control('provider.select', {'instance':'actual-openai', 'model':'gpt6-sol'})
        inspected = await control('configuration.providers')
        assert {row['id'] for row in inspected['providers']} == {'fixture','actual-openai','unavailable-chatgpt'}
        oauth_row = next(row for row in inspected['providers'] if row['id']=='unavailable-chatgpt')
        assert oauth_row['authentication'] == {'status':status, 'source':'provider.auth_status', 'observational':True}
        assert oauth_row['supports']['authStatus'] is True
        assert inspected['effective']['instance'] == 'actual-openai'
        assert not fixture_audit.exists() and not audit.exists()
        # Deliberately switch to the offline provider for a turn: the actual
        # OpenAI provider above was mounted/selected, never sent an API request.
        await control('provider.select', {'instance':'fixture', 'model':'fixture-model'})
        result = await client.request('session/prompt', {'sessionId':sid, 'prompt':[{'type':'text','text':'deliberate offline selected-provider turn'}]})
        assert result['result']['stopReason'] == 'end_turn', result
        # The retained automatic naming hook also invokes the offline fixture.
        assert fixture_audit.read_text().splitlines() == ['fixture-model', 'None']
        assert not audit.exists()
        assert (tokens.read_bytes() if tokens.exists() else None) == token_before
        after = await control('configuration.providers')
        assert after['effective']['instance'] == 'fixture'
        assert {row['id'] for row in after['providers']} == {'fixture','actual-openai','unavailable-chatgpt'}
    finally:
        await client.close()
