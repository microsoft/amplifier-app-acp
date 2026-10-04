"""Actual fresh Core/Foundation worker and isolated metadata probe, no accounts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_explicit_immutable_custom_worker_and_admin_probe_preserve_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))
    # Signed launcher's top-level true must dominate a supplied engine env0.
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '0')
    tools = tmp_path/'bin'; tools.mkdir(); audit = tmp_path/'installer-called'
    uv = tools/'uv'
    uv.write_text('#!/bin/sh\nprintf attempted > '+str(audit)+'\nexit 97\n'); uv.chmod(0o700)
    monkeypatch.setenv('PATH', str(tools)+os.pathsep+os.environ['PATH'])
    config, cwd = configuration(tmp_path, runtimeImmutable=True,
        workerCommand=[sys.executable, '-I', '-B', '-m', 'amplifier_acp.native.runtime_worker'],
        adminWorkspaceRoots=[str(tmp_path)])
    provider = str((Path(__file__).parent/'fixtures/provider').resolve())
    values = json.loads(config.read_text()); home = Path(values['home'])
    settings = home/'settings.yaml'
    settings.write_text(yaml.safe_dump({'bundle': {'app': []}, 'config': {'providers': [
        {'id': 'fixture', 'module': 'provider-fixture', 'source': provider, 'config': {}}]}}))
    bundle = Path(values['bundle']); plan = yaml.safe_load(bundle.read_text())
    plan['providers'][0]['config']['selectedModelAudit'] = str(tmp_path/'provider-calls')
    bundle.write_text(yaml.safe_dump(plan))
    before = settings.read_bytes()
    # Every file in this owned thin interpreter is exact before/after. Existing
    # qualified dependency trees are referenced read-only; no graph is copied.
    prefix = Path(sys.prefix)
    def inventory():
        return {str(p.relative_to(prefix)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in prefix.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}
    installed_before = inventory()
    client = await Client.launch(config)
    try:
        initial = await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        marker = initial['result']['agentCapabilities']['_meta']['amplifier.dev/native']['runtimePreparation']
        import jsonschema
        contract = json.loads((Path(__file__).parents[1]/'schemas/launcher-runtime-policy.v1.json').read_text())
        jsonschema.validate(marker, contract['$defs']['capability'])
        assert marker['immutable'] and marker['dependencyInstallation'] is False
        made = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        sid = made['result']['sessionId']
        mounted = await client.request('_amplifier/native', {'sessionId': sid, 'operation': 'configuration.providers', 'args': {}})
        assert 'result' in mounted, mounted
        actual = await client.request('_amplifier/runtime', {'sessionId': sid, 'cwd': str(cwd), 'surface': 'mounted', 'refresh': True})
        assert actual['result']['runtimePreparation'] == marker
        (tmp_path/'runtime-policy-proof.json').write_text(json.dumps({
            'initializeRuntimePreparation': marker, 'runtimeInspection': actual['result']}, indent=2)+'\n')
        assert any(row['kind'] == 'providers' and row['id'] == 'fixture' for row in actual['result']['items'])
        schema = await client.request('_amplifier/admin', {'cwd': str(cwd), 'operation': 'providers.schema', 'args': {'id': 'fixture', 'module': 'provider-fixture'}})
        assert 'result' in schema and 'error' not in schema['result'], schema
        defaults = await client.request('_amplifier/admin', {'cwd': str(cwd), 'operation': 'configuration.defaults', 'args': {'bundle': values['bundle']}})
        assert 'result' in defaults and 'error' not in defaults['result'], defaults
    finally: await client.close()
    assert settings.read_bytes() == before and inventory() == installed_before and not audit.exists()
    assert not (tmp_path/'provider-calls').exists()


@pytest.mark.asyncio
async def test_missing_configured_dependency_refuses_without_installer_or_provider_fallback(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path/'writers'))
    tools = tmp_path/'bin'; tools.mkdir(); audit = tmp_path/'installer-called'
    uv = tools/'uv'; uv.write_text('#!/bin/sh\nprintf attempted > '+str(audit)+'\nexit 97\n'); uv.chmod(0o700)
    monkeypatch.setenv('PATH', str(tools)+os.pathsep+os.environ['PATH'])
    source = tmp_path/'unprepared-provider'
    shutil.copytree(Path(__file__).parent/'fixtures/provider', source, ignore=shutil.ignore_patterns('__pycache__'))
    package = source/'amplifier_module_provider_fixture/__init__.py'
    package.write_text('import deliberately_absent_native_fixture_dependency\n'+package.read_text())
    config, cwd = configuration(tmp_path, runtimeImmutable=True,
        moduleSources={'provider-fixture': str(source)}, registryHome=str(tmp_path/'registry'))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        made = await client.request('session/new', {'cwd': str(cwd), 'mcpServers': []})
        assert 'error' in made, made
    finally: await client.close()
    assert not audit.exists() and not list((tmp_path/'home').glob('projects/*/sessions/*/transcript.jsonl'))


@pytest.mark.asyncio
async def test_isolated_module_validation_uses_immutable_foundation_without_installer(tmp_path, monkeypatch):
    source = str((Path(__file__).parent/'fixtures/provider').resolve())
    env = {**os.environ, 'AMPLIFIER_RUNTIME_IMMUTABLE': '1', 'AMPLIFIER_WEB_HOME': str(tmp_path/'app'),
           'AMPLIFIER_SESSION_STATE_HOME': str(tmp_path/'writers'), 'PYTHONDONTWRITEBYTECODE': '1'}
    tools = tmp_path/'bin'; tools.mkdir(); audit = tmp_path/'installer-called'
    uv = tools/'uv'; uv.write_text('#!/bin/sh\nprintf attempted > '+str(audit)+'\nexit 97\n'); uv.chmod(0o700)
    env['PATH'] = str(tools)+os.pathsep+env['PATH']
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', '-m', 'amplifier_acp.native.module_validate',
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(
        {'id': 'fixture-validation', 'section': 'providers', 'module': 'provider-fixture', 'source': source}).encode()+b'\n'), 30)
    result = json.loads(stdout)
    assert process.returncode == 0 and result['passed'], (result, stderr.decode()[-2000:])
    assert not audit.exists()


@pytest.mark.asyncio
async def test_authentication_preparation_keeps_immutable_policy_in_isolated_process(tmp_path):
    # Synthetic provider-owned login, not any actual account or OAuth request.
    source = tmp_path/'source'; package = source/'amplifier_module_provider_openai_chatgpt'; package.mkdir(parents=True)
    (source/'pyproject.toml').write_text('[project]\nname="native-immutable-auth-fixture"\nversion="0.0.0"\n')
    (package/'__init__.py').write_text('')
    (package/'oauth.py').write_text('''import json
from pathlib import Path
async def login(token_file_path, print_fn):
    value={'access_token':'synthetic-fixture-only'}
    Path(token_file_path).write_text(json.dumps(value))
    return value
''')
    token = tmp_path/'private/token.json'; tools = tmp_path/'bin'; tools.mkdir(); audit = tmp_path/'installer-called'
    uv = tools/'uv'; uv.write_text('#!/bin/sh\nprintf attempted > '+str(audit)+'\nexit 97\n'); uv.chmod(0o700)
    env = {**os.environ, 'AMPLIFIER_RUNTIME_IMMUTABLE': '1', 'AMPLIFIER_WEB_HOME': str(tmp_path/'app'),
           'AMPLIFIER_ACP_MODULE_SOURCES': json.dumps({'provider-openai-chatgpt': str(source)}),
           'PYTHONDONTWRITEBYTECODE': '1', 'PATH': str(tools)+os.pathsep+os.environ['PATH']}
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', '-m', 'amplifier_acp.native.provider_auth',
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps({'module': 'provider-openai-chatgpt',
        'source': str(source), 'registryHome': str(tmp_path/'registry'), 'tokenFile': str(token)}).encode()+b'\n'), 30)
    assert process.returncode == 0 and json.loads(stdout)['status'] == 'completed', (stdout, stderr.decode()[-2000:])
    assert token.stat().st_mode & 0o777 == 0o600
    assert b'synthetic-fixture-only' not in stdout and not audit.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('change, accepted', [
    ({}, True),
    ({'version': True}, False),
    ({'version': 1.0}, False),
    ({'immutable': 1}, False),
    ({'dependencyInstallation': 0}, False),
    ({'extra': True}, False),
    ({'policy': 'unsupported'}, False),
    (None, False),
])
async def test_immutable_launcher_checks_exact_runtime_readiness_types(tmp_path, monkeypatch, change, accepted):
    from amplifier_acp.worker import NativeWorker, NativeError
    from unittest.mock import AsyncMock
    import uuid
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '0')
    script = tmp_path/'unqualified-worker.py'
    marker = {'version': 1, 'immutable': True, 'dependencyInstallation': False,
              'policy': 'foundation-dependency-installation'}
    report = {} if change is None else {'runtimePreparation': {**marker, **change}}
    script.write_text("import sys,json\njson.loads(sys.stdin.readline())\nprint("+
        repr(json.dumps({'type': 'runtime.ready', 'report': report}))+",flush=True)\njson.loads(sys.stdin.readline())\n")
    worker = NativeWorker(str(uuid.uuid4()), str(tmp_path), {'runtimeImmutable': True,
        'home': str(tmp_path/'home'), 'appHome': str(tmp_path/'app'),
        'workerCommand': [sys.executable, '-I', '-B', str(script)]}, AsyncMock(), AsyncMock())
    if accepted:
        assert await worker.start() == report
        await worker.close()
    else:
        with pytest.raises(NativeError, match='did not confirm'):
            await worker.start()
    assert worker.closed and worker.process.returncode == 0
