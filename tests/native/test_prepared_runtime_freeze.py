"""Prepared worker identity and inherited update policy are separate inputs.

Use the real native ensure/freeze owners and offline uv with tiny local wheels.
Only Foundation's bundle probes are replaced; no model or live account is used.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest

from amplifier_acp.native import runtime_environment as environments
from amplifier_acp.native import runtime_profiles, runtime_qualification, updates
from amplifier_acp.native.host import bundle_paths, config as host_config


class Diagnostics:
    async def run(self, phase, function, *args, **kwargs):
        return await function(*args, **kwargs)

    def record(self, *args, **kwargs):
        pass


@pytest.fixture
async def prepared_profile(tmp_path, monkeypatch):
    uv = shutil.which('uv')
    assert uv, 'The prepared test environment must include uv'
    for key in ('UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_PROJECT_ENVIRONMENT'):
        monkeypatch.delenv(key, raising=False)
    for key, value in {'UV_OFFLINE': 'true', 'UV_NO_INDEX': 'true',
                       'UV_CACHE_DIR': str(tmp_path / 'uv-cache'),
                       'UV_PYTHON_DOWNLOADS': 'never', 'UV_LINK_MODE': 'copy'}.items():
        monkeypatch.setenv(key, value)
    home, shared, workspace = (tmp_path / name for name in ('app', 'shared', 'workspace'))
    shared.mkdir()
    workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))

    def wheel(name):
        path = tmp_path / f'{name}-1.0-py3-none-any.whl'
        metadata = f'{name}-1.0.dist-info'
        files = {
            f'{name}/__init__.py': b'VALUE = 1\n',
            f'{metadata}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n'.encode(),
            f'{metadata}/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        }
        files[f'{metadata}/RECORD'] = ''.join(f'{name},,\n' for name in files).encode()
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        return path

    wheels = {name: wheel(name) for name in ('fixture_old', 'fixture_new', 'fixture_lazy', 'fixture_unexpected')}

    def manifest(names):
        return ('[project]\nname="fixture-worker"\nversion="1"\nrequires-python=">=3.13"\n'
                'dependencies=' + json.dumps([
                    name.replace('_', '-') + ' @ ' + wheels[name].as_uri() for name in names
                ]) + '\n[tool.uv]\npackage=false\n').encode()

    baseline_a = manifest(['fixture_old'])
    baseline_b = manifest(['fixture_old', 'fixture_new'])
    offered = tmp_path / 'offered.toml'
    offered.write_bytes(baseline_a)
    monkeypatch.setattr(environments, 'manifest_path', lambda: offered)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    generation = 'a' * 32
    receipt = environments.receipt_directory(home, generation)
    (receipt / 'foundation').mkdir(parents=True)
    prepared = environments.prepare_project(home, generation, content=baseline_a)
    process = updates.process
    await process(uv, 'lock', '--project', str(prepared), '--python', '3.13')
    await process(uv, 'sync', '--locked', '--project', str(prepared), '--python', '3.13')
    parent = await runtime_qualification.freeze(manager, generation, prepared)
    (receipt / 'profiles-qualified.json').write_text(json.dumps({
        'profiles': ['offered'], 'configuration': runtime_profiles.configuration_key({}),
    }))
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    offered.write_bytes(baseline_b)
    config = SimpleNamespace(settings={}, config_home=shared, workspace=workspace,
                             active_bundle='work', module_sources={}, bundle_sources={}, home=home)
    monkeypatch.setattr(host_config, 'read_config', lambda *args, **kwargs: config)
    monkeypatch.setattr(bundle_paths, 'local_bundle_path', lambda *args: None)
    h = SimpleNamespace(home=home, generation=generation, receipt=receipt, parent=parent,
                        manager=manager, a=baseline_a, b=baseline_b, uv=uv, process=process,
                        session={'id': 'fixture', 'workspace': str(workspace), 'bundle': 'work'},
                        prepared_graphs=[], origins=[], commands=[], corruption=None, wheels=wheels)
    h.readonly_entered = asyncio.Event()
    h.hold_readonly = False

    async def controlled_process(*args, **kwargs):
        h.commands.append(args)
        if any(str(arg).endswith('/update_probe.py') for arg in args):
            project = Path(args[args.index('--project') + 1])
            if '--read-only' in args and h.hold_readonly:
                h.readonly_entered.set()
                await asyncio.Event().wait()
            if '--read-only' not in args:
                # A profile-only dependency prevents immutable parent reuse from
                # hiding which manifest was used for the newly frozen project.
                await process(uv, 'pip', 'install', '--python', str(project / '.venv/bin/python'),
                              '--no-deps', str(wheels['fixture_lazy']))
            h.prepared_graphs.append(runtime_qualification.installed_graph(project))
            result = await process(
                str(project / '.venv/bin/python'), '-I', '-B', '-c',
                'import fixture_old, json, pathlib, sys; '
                'assert pathlib.Path(fixture_old.__file__).is_relative_to(sys.prefix); '
                'print(json.dumps({"python":sys.executable,"origin":fixture_old.__file__}))')
            h.origins.append(json.loads(result))
            return ''
        result = await process(*args, **kwargs)
        if '--project' in args and args[1] == 'sync':
            project = Path(args[args.index('--project') + 1])
            if project.name.startswith('q-'):
                if h.corruption == 'addition':
                    await process(uv, 'pip', 'install', '--python', str(project / '.venv/bin/python'),
                                  '--no-deps', str(wheels['fixture_unexpected']))
                elif h.corruption == 'payload':
                    next(project.glob('.venv/lib/python*/site-packages/fixture_old/__init__.py')).write_text('VALUE = 2\n')
        return result

    monkeypatch.setattr(updates, 'process', controlled_process)

    def snapshot():
        return {str(path.relative_to(home)): ('link:' + os.readlink(path) if path.is_symlink()
                else hashlib.sha256(path.read_bytes()).hexdigest())
                for root in (parent, receipt) for path in root.rglob('*') if path.is_file()}

    h.snapshot = snapshot
    h.before = snapshot()
    return h


@pytest.mark.parametrize('legacy', [False, True])
async def test_lazy_profile_keeps_prepared_graph_and_inherited_baseline(prepared_profile, legacy):
    h = prepared_profile
    if legacy:
        (h.receipt / 'runtime-base.toml').unlink()
        h.before = h.snapshot()
    try:
        selected = await runtime_profiles.ensure(h.home, h.generation, h.session)
    except ValueError as error:
        graphs = [[r['name'] for r in runtime_qualification.installed_graph(p)]
                  for p in sorted((h.home / 'runtime').glob('q-*'))]
        assert h.snapshot() == h.before
        pytest.fail(f'Prepared graph {h.prepared_graphs[0]}; frozen graphs {graphs}; {error}', pytrace=False)
    final = environments.project_path(h.home, selected)
    receipt = environments.receipt_directory(h.home, selected)
    assert selected != h.generation and final != h.parent
    assert [r['name'] for r in runtime_qualification.installed_graph(final)] == ['fixture-lazy', 'fixture-old']
    assert h.prepared_graphs[0] == h.prepared_graphs[1]
    baseline = (h.parent / 'pyproject.toml').read_bytes() if legacy else h.a
    assert (receipt / 'runtime-base.toml').read_bytes() == baseline
    assert (receipt / 'runtime-base.toml').read_bytes() != h.b
    assert environments.project_path(h.home, h.generation) == h.parent
    assert h.origins[0]['origin'].startswith(str(h.home / 'runtime/prepare-'))
    assert h.origins[1]['origin'].startswith(str(final / '.venv'))
    assert h.origins[1]['python'] == str(final / '.venv/bin/python')
    assert sum(args[1] == 'sync' for args in h.commands) == 2
    assert sum(args[1] == 'lock' for args in h.commands) == 1
    assert h.snapshot() == h.before
    commands = list(h.commands)
    assert await runtime_profiles.ensure(h.home, h.generation, h.session) == selected
    assert h.commands == commands and h.snapshot() == h.before


async def test_cancelled_profile_cannot_publish_validated_or_index(prepared_profile):
    h = prepared_profile
    h.hold_readonly = True
    task = asyncio.create_task(runtime_profiles.ensure(h.home, h.generation, h.session))
    try:
        await asyncio.wait_for(h.readonly_entered.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    stages = [p for p in (h.home / 'updates/releases').iterdir() if p != h.receipt]
    assert len(stages) == 1
    stage = stages[0]
    # Freeze may retain a qualified candidate, but interrupted admission cannot
    # make it a validated profile or a reuse-index hit.
    assert (stage / 'runtime.lock').exists()
    assert not (stage / 'validated.json').exists()
    assert not (stage / 'profiles-qualified.json').exists()
    assert not list((h.home / 'updates/profiles').glob('*.json'))
    assert h.snapshot() == h.before


async def test_fresh_generation_still_adopts_offered_baseline(prepared_profile):
    h = prepared_profile
    generation = 'b' * 32
    receipt = environments.receipt_directory(h.home, generation)
    receipt.mkdir(parents=True)
    update = next(row for row in environments.inventory(h.home) if row['id'] == 'runtime:environment')
    project = await environments.stage(h.manager, generation, [update], finalize=False)
    await h.process(h.uv, 'sync', '--locked', '--project', str(project), '--python', '3.13')
    graph = runtime_qualification.installed_graph(project)
    assert [row['name'] for row in graph] == ['fixture-new', 'fixture-old']
    final = await runtime_qualification.freeze(h.manager, generation, project)
    assert runtime_qualification.installed_graph(final) == graph
    assert (receipt / 'runtime-base.toml').read_bytes() == h.b
    assert h.snapshot() == h.before


@pytest.mark.parametrize('corruption', ['addition', 'payload'])
async def test_freeze_keeps_strict_graph_and_payload_refusal(prepared_profile, corruption):
    h = prepared_profile
    h.corruption = corruption
    with pytest.raises(ValueError, match='differs from its prepared source graph'):
        await runtime_profiles.ensure(h.home, h.generation, h.session)
    stages = [p for p in (h.home / 'updates/releases').iterdir() if p != h.receipt]
    assert len(stages) == 1
    stage = stages[0]
    finals = [p for p in (h.home / 'runtime').glob('q-*') if p != h.parent]
    assert len(finals) == 1
    graph = runtime_qualification.installed_graph(finals[0])
    if corruption == 'addition':
        assert [r['name'] for r in graph] == ['fixture-lazy', 'fixture-old', 'fixture-unexpected']
    else:
        assert graph == h.prepared_graphs[0]
        prepared = next((h.home / 'runtime').glob('prepare-*'))
        assert runtime_qualification.package_content(finals[0]) != runtime_qualification.package_content(prepared)
    for name in ['validated.json', 'runtime-installed.json', 'runtime.lock', 'runtime-project.json']:
        assert not (stage / name).exists()
    assert not list((h.home / 'updates/profiles').glob('*.json'))
    assert h.snapshot() == h.before
