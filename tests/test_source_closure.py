"""Causal contract checks for trusted source closure without provider calls."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from amplifier_acp import source_closure as closure
from amplifier_acp import preparation_policy as preparation

URI = 'git+https://github.com/example/provider@main'

@pytest.fixture
def foundation(monkeypatch):
    import amplifier_foundation.sources as sources
    current = ContextVar('test_admission', default=None)
    @contextmanager
    def policy(callback):
        prior = current.get()
        if prior is not None and prior is not callback: raise RuntimeError('different nested policy')
        token = current.set(callback)
        try: yield
        finally: current.reset(token)
    monkeypatch.setattr(sources, 'source_resolution_policy', policy, raising=False)
    monkeypatch.setattr(sources, 'SourceResolutionDenied', RuntimeError, raising=False)
    monkeypatch.setattr(sources, 'SOURCE_RESOLUTION_POLICY_VERSION', 1, raising=False)
    monkeypatch.setenv(closure.ENV, '{}')
    monkeypatch.delenv(closure.ENV)
    monkeypatch.delenv(preparation.VARIABLE, raising=False)
    closure._callbacks.clear()
    return current


def manifest(tmp_path, *, packages=None, changes=None):
    root = tmp_path/'source'; root.mkdir()
    (root/'bundle.yaml').write_text('bundle:\n  name: test\n')
    package = root/'closure_fixture_provider'; package.mkdir()
    (package/'__init__.py').write_text('MOUNT = "never called"\n')
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    row = dict(requestedUri=URI, basePath=None, activePath=str(root), sourceRoot=str(root),
               resolvedCommit='a'*40, approval='reviewed-exception-1', files=files,
               admissionFiles=['bundle.yaml'], packages=packages or [])
    if changes: row.update(changes)
    path = tmp_path/'closure.json'; path.write_text(json.dumps({'version': 1, 'sources': [row]}))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, root


def test_unbound_source_denies_before_handler_and_manifest_is_readonly(tmp_path, foundation):
    reference, root = manifest(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    closure.configure({'sourceResolutionManifest': reference})
    with closure.scope():
        callback = foundation.get()
        assert callback(URI, base_path=tmp_path).active_path == root
        effects = []
        with pytest.raises(closure.SourceClosureDenied):
            result = callback(URI.replace('@main', '@unreviewed'), base_path=tmp_path)
            effects.append(result)
        assert effects == []
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert foundation.get() is None


@pytest.mark.asyncio
async def test_nested_prepare_and_async_child_share_one_callback(tmp_path, foundation):
    reference, root = manifest(tmp_path)
    closure.configure({'sourceResolutionManifest': reference})
    callbacks = []
    class Bundle:
        async def prepare(self, **kwargs):
            callbacks.append(foundation.get())
            assert kwargs['install_deps'] is True
            result = await asyncio.create_task(asyncio.to_thread(lambda: foundation.get()(URI, base_path=tmp_path)))
            assert result.source_root == root
            return SimpleNamespace(resolver=SimpleNamespace(prepared_sources=lambda: (('provider-test', URI, root),)))
    @preparation.governed
    async def run():
        callbacks.append(foundation.get())
        return await preparation.prepare(Bundle())
    await run()
    assert callbacks[0] is callbacks[1] and callbacks[0] is not None
    assert foundation.get() is None


def test_missing_foundation_support_refuses_before_launch_effects(tmp_path, monkeypatch):
    import amplifier_foundation.sources as sources
    monkeypatch.delenv(closure.ENV, raising=False)
    monkeypatch.delattr(sources, 'source_resolution_policy', raising=False)
    reference, _ = manifest(tmp_path)
    from amplifier_acp.server import Server
    with pytest.raises(RuntimeError, match='requires Foundation'):
        Server({'sourceResolutionManifest': reference, 'appHome': str(tmp_path/'app')}, None)
    assert not (tmp_path/'app').exists() and closure.ENV not in __import__('os').environ


def test_manifest_rebinding_and_digest_change_cannot_relax_policy(tmp_path, foundation):
    reference, _ = manifest(tmp_path)
    closure.configure({'sourceResolutionManifest': reference})
    with pytest.raises(closure.SourceClosureDenied):
        closure.configure({'sourceResolutionManifest': {**reference, 'sha256': 'b'*64}})
    assert closure.configure({}) == reference
    Path(reference['path']).write_text('{}')
    with pytest.raises(closure.SourceClosureDenied):
        with closure.scope(): pass


def test_changed_needed_file_refuses_and_unchanged_files_are_cached(tmp_path, foundation, monkeypatch):
    reference, root = manifest(tmp_path)
    closure.configure({'sourceResolutionManifest': reference})
    callback = closure.admission()
    reads = []
    original = closure._read
    def read(path, limit):
        reads.append(Path(path))
        return original(path, limit)
    monkeypatch.setattr(closure, '_read', read)
    for _ in range(20): callback(URI, base_path=tmp_path)
    assert reads.count(root/'bundle.yaml') == 1
    assert not any(path.name == '__init__.py' for path in reads)
    (root/'bundle.yaml').write_text('unexpected code or resources')
    with pytest.raises(closure.SourceClosureDenied): callback(URI, base_path=tmp_path)


def test_wrong_already_importable_package_refuses_before_execution(tmp_path, foundation, monkeypatch):
    reference, _ = manifest(tmp_path, packages=['closure_fixture_provider'])
    foreign = tmp_path/'foreign'; package = foreign/'closure_fixture_provider'; package.mkdir(parents=True)
    marker = tmp_path/'effect'
    (package/'__init__.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
    monkeypatch.syspath_prepend(str(foreign))
    with pytest.raises(closure.SourceClosureDenied): closure.configure({'sourceResolutionManifest': reference})
    assert not marker.exists() and 'closure_fixture_provider' not in sys.modules


def test_loaded_origin_bytes_are_attested_and_change_refuses(tmp_path, foundation, monkeypatch):
    reference, root = manifest(tmp_path, packages=['closure_fixture_provider'])
    monkeypatch.syspath_prepend(str(root))
    closure.configure({'sourceResolutionManifest': reference})
    with closure.scope():
        module = __import__('closure_fixture_provider')
        monkeypatch.setitem(sys.modules, 'closure_fixture_provider', module)
        report = closure.attest()
        assert report['origins']['closure_fixture_provider']['verified']
        (root/'closure_fixture_provider/__init__.py').write_text('changed after qualification')
        with pytest.raises(closure.SourceClosureDenied): closure.attest()
        # Restore exact bytes before leaving the enclosing scope.
        (root/'closure_fixture_provider/__init__.py').write_text('MOUNT = "never called"\n')
    sys.modules.pop('closure_fixture_provider', None)


def test_relative_uri_is_bound_to_exact_base(tmp_path, foundation):
    reference, _ = manifest(tmp_path, changes={'requestedUri': './bundle.yaml', 'basePath': str(tmp_path)})
    closure.configure({'sourceResolutionManifest': reference})
    callback = closure.admission()
    assert callback('./bundle.yaml', base_path=tmp_path)
    foreign = tmp_path/'foreign'; foreign.mkdir()
    with pytest.raises(closure.SourceClosureDenied): callback('./bundle.yaml', base_path=foreign)


def test_prepared_local_paths_require_declared_manifest_membership(tmp_path, foundation):
    reference, root = manifest(tmp_path)
    closure.configure({'sourceResolutionManifest': reference})
    callback = closure.admission()
    assert callback(str(root), base_path=tmp_path).active_path == root
    assert callback((root/'bundle.yaml').as_uri(), base_path=tmp_path).source_root == root
    outside = tmp_path/'unqualified'; outside.mkdir()
    bad = SimpleNamespace(resolver=SimpleNamespace(prepared_sources=lambda: (('provider-test', URI, outside),)))
    with pytest.raises(closure.SourceClosureDenied): closure.attest_prepared(bad)
    with pytest.raises(RuntimeError, match='prepared_sources'): closure.attest_prepared(SimpleNamespace())


def test_shared_installed_root_local_member_is_independent_of_row_order(tmp_path, foundation):
    reference, root = manifest(tmp_path)
    path = Path(reference['path']); doc = json.loads(path.read_text())
    row = doc['sources'][0]
    second = {**row, 'requestedUri': URI.replace('provider', 'provider-two'),
              'files': {'closure_fixture_provider/__init__.py': row['files']['closure_fixture_provider/__init__.py']},
              'admissionFiles': ['closure_fixture_provider/__init__.py']}
    row['files'] = {'bundle.yaml': row['files']['bundle.yaml']}
    doc['sources'].append(second); path.write_text(json.dumps(doc))
    reference['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    closure.configure({'sourceResolutionManifest': reference})
    assert closure.admission()(str(root/'closure_fixture_provider/__init__.py'), base_path=tmp_path).source_root == root


def test_shared_root_contradictory_file_digest_refuses(tmp_path, foundation):
    reference, _ = manifest(tmp_path)
    path = Path(reference['path']); doc = json.loads(path.read_text())
    row = doc['sources'][0]
    doc['sources'].append({**row, 'requestedUri': URI+'-other', 'files': {**row['files'], 'bundle.yaml': 'b'*64}})
    path.write_text(json.dumps(doc)); reference['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(closure.SourceClosureDenied): closure.configure({'sourceResolutionManifest': reference})


@pytest.mark.asyncio
@pytest.mark.parametrize('change, accepted', [
    ({}, True), (None, False), ({'version': True}, False), ({'version': 1.0}, False),
    ({'version': 2}, False), ({'manifestSha256': 'b'*64}, False),
    ({'manifestSha256': None}, False), ({'sourceResolution': 'unrestricted'}, False),
    ({'sourceResolution': True}, False), ({'extra': True}, False),
    ({'omit': 'manifestSha256'}, False),
])
async def test_worker_must_confirm_exact_manifest_before_admitting_work(tmp_path, foundation, change, accepted):
    from amplifier_acp.worker import NativeWorker, NativeError
    from unittest.mock import AsyncMock
    import uuid
    reference, _ = manifest(tmp_path)
    closure.configure({'sourceResolutionManifest': reference})
    # Positive uses the current production ready-report projection. Negative
    # children acknowledge dependency policy but ignore/misstate source closure.
    marker = closure.ready_report({'sourceClosure': closure.attest()})
    report = {'runtimePreparation': {'version': 1, 'immutable': True,
              'dependencyInstallation': False, 'policy': 'foundation-dependency-installation'}}
    if change is not None:
        report['sourceClosure'] = {**marker, **change}
        if 'omit' in report['sourceClosure']:
            report['sourceClosure'].pop(report['sourceClosure'].pop('omit'))
    script = tmp_path/'worker.py'
    script.write_text("import sys,json\njson.loads(sys.stdin.readline())\nprint("+
                     repr(json.dumps({'type':'runtime.ready','report':report}))+",flush=True)\njson.loads(sys.stdin.readline())\n")
    worker = NativeWorker(str(uuid.uuid4()),str(tmp_path),
                          {'runtimeImmutable':True,'sourceResolutionManifest':reference,
                           'home':str(tmp_path/'home'),'appHome':str(tmp_path/'app'),
                           'workerCommand':[sys.executable,'-I','-B',str(script)]},AsyncMock(),AsyncMock())
    if accepted:
        assert await worker.start() == report
        await worker.close()
    else:
        with pytest.raises(NativeError,match='exact launcher source closure'): await worker.start()
    assert worker.closed and worker.process.returncode == 0
    assert not (tmp_path/'home').exists() and not (tmp_path/'app').exists()
