"""Provider editing shares the module-source contract and admin authority."""
import json
import importlib.util
import sys
from pathlib import Path

import pytest

from amplifier_acp.native.setup import SetupManager
from amplifier_acp.native.registry import source_uri
from client import Client
from native_setup import configuration


@pytest.mark.parametrize('scope', ['global', 'project', 'local'])
@pytest.mark.parametrize('form', ['absolute', 'relative', 'parent', 'file', 'home', 'symlink'])
async def test_save_local_provider_source_matches_module_contract(tmp_path, monkeypatch, scope, form):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    source = workspace / 'provider with spaces'
    source.mkdir()
    link = workspace / 'provider-link'
    link.symlink_to(source, target_is_directory=True)
    monkeypatch.setenv('HOME', str(tmp_path))
    submitted = {
        'absolute': str(source), 'relative': './provider with spaces',
        'parent': '../workspace/provider with spaces', 'file': 'file://' + str(source),
        'home': '~/workspace/provider with spaces', 'symlink': str(link),
    }[form]
    manager = SetupManager(tmp_path / 'app')
    try:
        result = await manager.perform('providers.save', {
            'workspace': str(workspace), 'scope': scope,
            'id': 'owned', 'module': 'provider-owned', 'source': submitted,
            'config': {'default_model': 'offline'},
        })
        assert result['takesEffect'] == 'new_sessions'
        saved = manager.store.read(workspace, scope)['config']['providers'][0]
        assert saved['source'] == source_uri(submitted, workspace) == str(source.resolve())
        # Editing a saved provider without resubmitting source preserves it.
        await manager.perform('providers.save', {
            'workspace': str(workspace), 'scope': scope, 'id': 'owned',
            'config': {'default_model': 'edited'},
        })
        edited = manager.store.read(workspace, scope)['config']['providers'][0]
        assert edited['source'] == saved['source']
        assert edited['config']['default_model'] == 'edited'
    finally:
        await manager.close()


@pytest.mark.parametrize('source', [
    'git+https://github.com/microsoft/amplifier-module-provider-openai@main',
    'https://github.com/microsoft/amplifier-module-provider-openai',
])
async def test_provider_https_source_preserved(tmp_path, source):
    manager = SetupManager(tmp_path / 'app')
    try:
        await manager.perform('providers.save', {'workspace': str(tmp_path),
            'id': 'owned', 'module': 'provider-owned', 'source': source})
        assert manager.store.read(tmp_path)['config']['providers'][0]['source'] == source_uri(source, tmp_path)
    finally:
        await manager.close()


@pytest.mark.parametrize('source', [
    'file:///missing-provider-source', './missing-provider', 'foundation',
    'git+ssh://example.invalid/provider',
    'https://user:secret-sentinel@github.com/a/b',
    'git+https://github.com/a/b@main?token=secret-sentinel',
    'git+https://github.com/a/b@main#subdirectory=../../escape',
    '/bad\x00path', '/bad\npath', ['not-a-string'], {'source': 'wrong-type'},
])
async def test_invalid_provider_source_preserves_settings_and_keys(tmp_path, source):
    manager = SetupManager(tmp_path / 'app')
    try:
        await manager.perform('providers.save', {'workspace': str(tmp_path),
            'id': 'owned', 'module': 'provider-owned', 'config': {'default_model': 'before'}})
        settings = manager.store.path(tmp_path, 'global')
        before = settings.read_bytes()
        keys = manager.store.shared_home / 'keys.env'
        with pytest.raises(ValueError) as failed:
            await manager.perform('providers.save', {'workspace': str(tmp_path),
                'id': 'owned', 'module': 'provider-owned', 'source': source,
                'apiKey': 'private-key-not-to-write', 'config': {'default_model': 'after'}})
        assert 'secret-sentinel' not in str(failed.value)
        assert settings.read_bytes() == before
        assert not keys.exists()
    finally:
        await manager.close()


@pytest.mark.parametrize('escape', ['outside', 'symlink', 'traversal'])
async def test_provider_save_refuses_unauthorized_workspace_before_effects(tmp_path, escape):
    from amplifier_acp.admin import Administration
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    home, app = tmp_path / 'home', tmp_path / 'app'
    owner = Administration({'home': str(home), 'appHome': str(app),
                            'adminWorkspaceRoots': [str(workspace)]})
    cwd = {'outside': outside, 'symlink': workspace / 'escape',
           'traversal': workspace / '..' / 'outside'}[escape]
    try:
        with pytest.raises(ValueError, match='outside launcher-authorized roots'):
            await owner.perform(str(cwd), 'providers.save', {
                'workspace': str(workspace), 'id': 'owned',
                'module': 'provider-owned', 'source': str(workspace),
                'apiKey': 'private-key-not-to-write',
            })
        assert owner.setup is None
        assert not home.exists() and not app.exists()
        assert not (outside / '.amplifier').exists()
    finally:
        await owner.close()


async def test_saved_provider_source_mounts_in_fresh_native_worker(tmp_path, monkeypatch):
    """Real admin save, restart, Foundation preparation, Core mount and origin."""
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '1')
    monkeypatch.setenv('UV_OFFLINE', '1')
    config, workspace = configuration(tmp_path)
    values = json.loads(config.read_text())
    values['adminWorkspaceRoots'] = [str(workspace)]
    # Ordinary bundles inherit saved provider settings; a snapshot deliberately
    # freezes its mount plan. Keep the real required hook local and offline.
    hook = Path(importlib.util.find_spec('amplifier_module_hook_context_intelligence').origin).parent
    bundle = Path(values['bundle'])
    bundle.write_text(bundle.read_text().replace('1.0.0+amplifier-unified.snapshot.1', '1.0.0')
        + '\nhooks:\n  - module: hook-context-intelligence\n    source: '
        + str(hook) + '\n    config:\n      destinations: {}\n')
    # A sibling checkout is an existing explicit source-selection contract;
    # adminWorkspaceRoots controls the settings workspace, not module roots.
    source = tmp_path / 'provider source'
    package = source / 'amplifier_module_provider_saved_source'
    package.mkdir(parents=True)
    package.joinpath('__init__.py').write_text('''
from amplifier_core.models import ProviderInfo
class SavedSourceProvider:
    name = 'saved-source'
    def __init__(self, config=None): self.config = config or {}
    def get_info(self): return ProviderInfo(id='saved-source', display_name='Saved source fixture', defaults={'model':'saved-local-model'})
    async def list_models(self): return [{'id': 'saved-local-model'}]
    async def close(self): pass
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs): raise AssertionError('No inference in source qualification')
async def mount(coordinator, config=None):
    await coordinator.mount('providers', SavedSourceProvider(config), name='saved-source')
''')
    guard = tmp_path / 'offline-worker.py'
    forbidden = tmp_path / 'forbidden-network'
    guard.write_text('import sys,runpy\nfrom pathlib import Path\n'
        'def audit(event,args):\n'
        ' if event in {"socket.connect", "socket.getaddrinfo"}:\n'
        f'  Path({str(forbidden)!r}).write_text(event)\n'
        '  raise AssertionError("No network in fixture")\n'
        'sys.addaudithook(audit)\n'
        'runpy.run_module("amplifier_acp.native.runtime_worker",run_name="__main__")\n')
    values['workerCommand'] = [sys.executable, '-I', '-B', str(guard)]
    config.write_text(json.dumps(values))
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1,
            'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        saved = await client.request('_amplifier/admin', {'cwd': str(workspace),
            'operation': 'providers.save', 'args': {'id': 'saved-source',
                'module': 'provider-saved-source', 'source': str(source),
                'config': {'default_model': 'saved-local-model'}}})
        assert 'result' in saved, saved
        row = next(row for row in saved['result']['providers'] if row['id'] == 'saved-source')
        assert row['source'] == str(source)
        assert not (Path(values['home']) / 'projects').exists(), 'Save must not start a conversation'
    finally:
        (tmp_path / 'save-stderr.log').write_text(await client.close())
    client = await Client.launch(config)
    try:
        await client.request('initialize', {'protocolVersion': 1,
            'clientCapabilities': {'_meta': {'amplifier.dev/native': {'version': 1}}}})
        created = await client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}, timeout=120)
        assert 'result' in created, created
        sid = created['result']['sessionId']
        providers = await client.request('_amplifier/native', {'sessionId': sid,
            'operation': 'configuration.providers', 'args': {}})
        assert 'result' in providers, providers
        inspected = await client.request('_amplifier/runtime', {'sessionId': sid,
            'cwd': str(workspace), 'surface': 'mounted', 'refresh': True, 'limit': 100})
        assert 'result' in inspected, inspected
        matches = [row for row in inspected['result']['items']
                   if row['kind'] == 'providers' and row['id'] == 'saved-source']
        assert len(matches) == 1, inspected
        mounted = matches[0]
        loaded = Path(mounted['implementations'][0]['loadedFrom'])
        assert loaded == package / '__init__.py', mounted
        assert loaded.read_bytes() == package.joinpath('__init__.py').read_bytes()
        assert not forbidden.exists()
        (tmp_path / 'source-mount-proof.json').write_text(json.dumps({
            'source': str(source), 'saved': saved['result'], 'mounted': mounted,
            'networkAttempts': 0, 'inferenceRequests': 0}, indent=2))
    finally:
        (tmp_path / 'mount-stderr.log').write_text(await client.close())
