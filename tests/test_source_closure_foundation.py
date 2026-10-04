"""Actual candidate Foundation integration; no public API substitutions."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest
from amplifier_acp import source_closure as closure
from amplifier_acp import preparation_policy as preparation
from amplifier_foundation import Bundle, BundleRegistry
from amplifier_foundation.sources import SOURCE_RESOLUTION_POLICY_VERSION, SourceResolutionDenied
from amplifier_foundation.sources.resolver import SimpleSourceResolver
from amplifier_foundation.modules import ModuleActivator
from amplifier_foundation.bundle._prepared import BundleModuleResolver

URI = 'git+https://github.com/example/qualified-test@main'

@pytest.fixture
def qualified(tmp_path, monkeypatch):
    monkeypatch.setenv(closure.ENV, '{}'); monkeypatch.delenv(closure.ENV)
    monkeypatch.delenv(preparation.VARIABLE, raising=False)
    closure._callbacks.clear()
    before_path = list(sys.path)
    root = tmp_path/'root'; root.mkdir()
    (root/'bundle.yaml').write_text('bundle:\n  name: qualified-test\n')
    (root/'pyproject.toml').write_text('[project]\nname="qualified-test"\nversion="0.0.0"\n')
    pkg = root/'amplifier_module_provider_closure_test'; pkg.mkdir()
    (pkg/'__init__.py').write_text('def mount(coordinator, config=None):\n    return None\n')
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    row = dict(requestedUri=URI, basePath=None, activePath=str(root), sourceRoot=str(root), resolvedCommit='a'*40,
               approval=None, files=files, admissionFiles=list(files), packages=['amplifier_module_provider_closure_test'])
    path = tmp_path/'manifest.json'; path.write_text(json.dumps(dict(version=1, sources=[row])))
    reference = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    closure.configure({'sourceResolutionManifest': reference})
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail('Qualified source resolution invoked a scheme handler')
    for module, name in [('file','FileSourceHandler'), ('git','GitSourceHandler'), ('http','HttpSourceHandler'), ('zip','ZipSourceHandler')]:
        imported = __import__('amplifier_foundation.sources.'+module, fromlist=[name])
        monkeypatch.setattr(getattr(imported, name), 'resolve', forbidden)
    yield root, calls
    sys.modules.pop('amplifier_module_provider_closure_test', None)
    sys.path[:] = before_path


@pytest.mark.asyncio
async def test_actual_registry_root_and_nested_task_use_admitted_source(qualified, tmp_path):
    root, calls = qualified
    assert SOURCE_RESOLUTION_POLICY_VERSION == 1
    @preparation.governed
    async def run():
        registry = BundleRegistry(tmp_path/'registry', strict=True, persist=False)
        bundle = await registry.load(URI)
        assert bundle.name == 'qualified-test'
        assert (await asyncio.create_task(SimpleSourceResolver().resolve(URI))).active_path == root
        with pytest.raises(closure.SourceClosureDenied): await registry.load(URI+'-unknown')
    await run()
    assert not calls and not (tmp_path/'cache').exists()


@pytest.mark.asyncio
async def test_actual_prepare_activates_then_attests_effective_package(qualified, tmp_path):
    root, calls = qualified
    # This is preparation/activation only, not a Core/provider mount or request.
    bundle = Bundle.from_dict({'bundle': {'name':'qualified-mount'}, 'providers': [{'module':'provider-closure-test','source':URI}]})
    bundle.base_path = root
    @preparation.governed
    async def run():
        prepared = await preparation.prepare(bundle, readonly=True, strict=True)
        assert prepared.resolver.get_module_source('provider-closure-test') == str(root)
        __import__('amplifier_module_provider_closure_test')
        report = closure.attest_prepared(prepared)
        assert report['loadedPackages'] == 1 and report['loadedOriginsVerified']
        assert report['origins']['amplifier_module_provider_closure_test']['root'] == str(root)
    await run()
    assert not calls


@pytest.mark.asyncio
async def test_actual_cached_prepared_mismatch_denies(qualified, tmp_path):
    root, calls = qualified
    outside = tmp_path/'outside'; outside.mkdir()
    resolver = BundleModuleResolver({}, source_paths={('provider-closure-test', URI):outside})
    with closure.scope():
        with pytest.raises(SourceResolutionDenied): resolver.resolve('provider-closure-test', URI)
        with pytest.raises(SourceResolutionDenied): await resolver.async_resolve('provider-closure-test', URI)
        with pytest.raises(closure.SourceClosureDenied):
            await ModuleActivator(cache_dir=tmp_path/'cache', install_deps=False).activate('provider-closure-test',URI+'-unknown')
    assert not calls and not (tmp_path/'cache').exists()
