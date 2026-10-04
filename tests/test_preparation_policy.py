"""Immutable installation policy at real native/Foundation process boundaries."""
import asyncio
import ast
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_acp import preparation_policy as policy


def test_launcher_boolean_is_strict_and_inherited_restriction_is_monotonic(monkeypatch):
    monkeypatch.setenv(policy.VARIABLE, '0')
    assert policy.configure({}) is False and policy.metadata() == {}
    for value in (1, 'true', None, [], {}):
        with pytest.raises(ValueError, match='boolean'): policy.configure({'runtimeImmutable': value})
    assert policy.configure({'runtimeImmutable': True})
    assert policy.configure({'runtimeImmutable': False})
    assert policy.metadata()['runtimePreparation']['dependencyInstallation'] is False
    child = dict(os.environ)
    assert policy.configure({'runtimeImmutable': False}, child) and child[policy.VARIABLE] == '1'


def test_missing_public_foundation_policy_refuses_before_launcher_effects(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.VARIABLE, raising=False)
    original = builtins.__import__
    def imported(name, *args, **kwargs):
        if name == 'amplifier_foundation.modules': raise ImportError('old peer')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', imported)
    from amplifier_acp.server import Server
    with pytest.raises(RuntimeError, match='requires Foundation'):
        Server({'runtimeImmutable': True, 'appHome': str(tmp_path/'app')}, None)
    assert not (tmp_path/'app').exists() and policy.VARIABLE not in os.environ


@pytest.mark.asyncio
async def test_whole_lifetime_and_deferred_public_activator_cannot_install(tmp_path, monkeypatch):
    from amplifier_foundation.modules import ModuleActivator, DependencyInstallationDenied
    source = tmp_path/'source'; source.mkdir()
    (source/'pyproject.toml').write_text('[project]\nname="native-policy-disposable-fixture"\nversion="0.0.0"\ndependencies=["must-never-resolve-native-policy"]\n')
    monkeypatch.setenv(policy.VARIABLE, '1')
    # Public activator created by a module during a native lifetime captures the
    # deny even when it requests the default mutable installation behavior.
    @policy.governed
    async def task():
        async def child():
            return await asyncio.to_thread(lambda: ModuleActivator(cache_dir=tmp_path/'cache', strict=True, refresh_dependencies=True))
        return await asyncio.create_task(child())
    activator = await task()
    monkeypatch.delenv(policy.VARIABLE)
    def forbidden(*args, **kwargs): pytest.fail('Immutable native activation reached a dependency installer')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    with pytest.raises(DependencyInstallationDenied):
        await activator.activate('tool-disposable-policy-fixture', str(source))


def test_only_explicit_separate_owned_candidate_can_drop_inherited_restriction(tmp_path):
    root = tmp_path/'runtime'; project = root/'prepare-test'; venv = project/'.venv'
    venv.mkdir(parents=True); (venv/'pyvenv.cfg').write_text('home = /fixture\n')
    env = {policy.VARIABLE: '1', 'KEEP': 'value', 'VIRTUAL_ENV': '/foreign', 'UV_PROJECT_ENVIRONMENT': '/foreign'}
    assert policy.staging_environment(env, project, root) == {'KEEP': 'value', 'UV_PROJECT_ENVIRONMENT': str(venv)}
    assert env[policy.VARIABLE] == '1'
    readonly = policy.staging_environment({'UV_PROJECT_ENVIRONMENT': '/old', policy.VARIABLE: '0'}, project, root, readonly=True)
    assert readonly[policy.VARIABLE] == '1' and readonly['UV_PROJECT_ENVIRONMENT'] == str(venv)
    (tmp_path/'different').mkdir()
    with pytest.raises(ValueError): policy.staging_environment(env, project, tmp_path/'different')
    (venv/'pyvenv.cfg').unlink(); venv.rmdir(); venv.symlink_to(sys.prefix, target_is_directory=True)
    with pytest.raises(ValueError, match='separate'): policy.staging_environment(env, project, root)


@pytest.mark.asyncio
async def test_mutable_preparation_refuses_before_settings_or_source_resolution(tmp_path, monkeypatch):
    from amplifier_foundation.modules import DependencyInstallationDenied
    from amplifier_acp.native.host.session import prepare_dependencies
    monkeypatch.setenv(policy.VARIABLE, '1')
    with pytest.raises(DependencyInstallationDenied): await prepare_dependencies(tmp_path/'not-created')
    assert not list(tmp_path.iterdir())


def test_every_native_foundation_prepare_is_routed_through_policy():
    root = Path(__file__).parents[1]/'src/amplifier_acp'
    direct = []
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'prepare' and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {'bundle', 'loaded', 'probe'}):
                direct.append((path.relative_to(root).as_posix(), node.lineno))
    assert [name for name, _ in direct] == ['preparation_policy.py']


def test_initial_sync_is_bound_to_new_owned_prefix_and_foreign_venv_refuses(tmp_path):
    root = tmp_path/'runtime'; project = root/'new'; project.mkdir(parents=True)
    result = policy.staging_environment({policy.VARIABLE: '1', 'UV_PROJECT_ENVIRONMENT': sys.prefix}, project, root, prepared=False)
    assert policy.VARIABLE not in result and result['UV_PROJECT_ENVIRONMENT'] == str(project/'.venv')
    foreign = tmp_path/'foreign'; foreign.mkdir(); (foreign/'pyvenv.cfg').write_text('fixture')
    (project/'.venv').symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match='owned'):
        policy.staging_environment({}, project, root, prepared=False)


@pytest.mark.asyncio
async def test_actual_uv_candidate_and_frozen_probe_use_exact_owned_prefix(tmp_path, monkeypatch):
    import shutil
    uv = shutil.which('uv')
    assert uv, 'The native generation builder requires uv'
    root = tmp_path/'runtime'; project = root/'prepare-test'; project.mkdir(parents=True)
    (project/'pyproject.toml').write_text('[project]\nname="policy-candidate"\nversion="0.0.0"\nrequires-python=">=3.13"\ndependencies=[]\n[tool.uv]\npackage=false\n')
    original = {**os.environ, policy.VARIABLE: '1', 'UV_PROJECT_ENVIRONMENT': sys.prefix, 'UV_OFFLINE': '1'}
    env = policy.staging_environment(original, project, root, prepared=False)
    async def run(*args, env):
        child = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(child.communicate(), 30)
        assert child.returncode == 0, err.decode()[-2000:]
        return out
    await run(uv, 'sync', '--project', str(project), '--python', sys.executable, env=env)
    command = [uv, 'run', '--locked', '--no-sync', '--project', str(project), 'python', '-I', '-B', '-c',
        "import json,sys,os;print(json.dumps({'prefix':sys.prefix,'immutable':os.environ.get('AMPLIFIER_RUNTIME_IMMUTABLE')}))"]
    prepared = json.loads(await run(*command, env=policy.staging_environment(original, project, root)))
    assert Path(prepared['prefix']).resolve() == (project/'.venv').resolve() and prepared['immutable'] is None
    # A qualified candidate can use a different immutable project. Ensure the
    # old mutable project's uv selector cannot follow it across that boundary.
    final = root/'qualified'; final.mkdir()
    (final/'pyproject.toml').write_bytes((project/'pyproject.toml').read_bytes())
    await run(uv, 'sync', '--project', str(final), '--python', sys.executable,
              env=policy.staging_environment(original, final, root, prepared=False))
    command[command.index('--project')+1] = str(final)
    frozen = json.loads(await run(*command, env=policy.staging_environment(env, final, root, readonly=True)))
    assert Path(frozen['prefix']).resolve() == (final/'.venv').resolve() and frozen['immutable'] == '1'
    assert original[policy.VARIABLE] == '1' and original['UV_PROJECT_ENVIRONMENT'] == sys.prefix
