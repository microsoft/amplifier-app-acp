import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from amplifier_acp.repository_access import repository_environment


def contaminated(tmp_path):
    config = tmp_path / 'personal-git'
    config.write_text('[url "ssh://private.invalid/"]\n\tinsteadOf = https://github.com/\n[credential]\n\thelper = blocked-account\n[http]\n\textraHeader = Authorization: fixture\n')
    return {**os.environ, 'AMPLIFIER_GITHUB_ACCESS': 'anonymous',
            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'http.extraHeader', 'GIT_CONFIG_VALUE_0': 'Authorization: injected',
            'GIT_CONFIG_PARAMETERS': "'credential.helper=other-account'",
            'GH_TOKEN': 'fixture-token', 'GITHUB_TOKEN': 'fixture-token',
            'GH_ENTERPRISE_TOKEN': 'fixture-token', 'GITHUB_ENTERPRISE_TOKEN': 'fixture-token',
            'GIT_ASKPASS': '/must-not-run', 'SSH_ASKPASS': '/must-not-run',
            'HTTPS_PROXY': 'http://proxy.invalid:8080', 'SSL_CERT_FILE': '/fixture-ca'}


def test_real_git_ignores_personal_rewrites_and_account_injection_without_editing_them(tmp_path):
    original = contaminated(tmp_path)
    config = tmp_path / 'personal-git'
    before = config.read_bytes()
    env = repository_environment(original)
    url = 'https://github.com/example/public.git'
    assert subprocess.check_output(['git', 'ls-remote', '--get-url', url], cwd=tmp_path, env=env, text=True).strip() == url
    settings = subprocess.check_output(['git', 'config', '--list'], cwd=tmp_path, env=env, text=True)
    assert 'Authorization' not in settings and 'blocked-account' not in settings and 'other-account' not in settings
    assert 'credential.helper=\n' in settings
    assert all(key not in env for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GIT_ASKPASS', 'SSH_ASKPASS', 'GIT_CONFIG_PARAMETERS'))
    assert env['HTTPS_PROXY'] == original['HTTPS_PROXY'] and env['SSL_CERT_FILE'] == original['SSL_CERT_FILE']
    assert config.read_bytes() == before and original['GH_TOKEN'] == 'fixture-token'
    automatic = {**original, 'AMPLIFIER_GITHUB_ACCESS': 'auto'}
    assert repository_environment(automatic) == automatic
    assert repository_environment(env) == env


@pytest.mark.asyncio
async def test_update_child_and_nested_children_keep_explicit_download_policy(tmp_path):
    from amplifier_acp.native.updates import process
    env = contaminated(tmp_path)
    code = 'import os,json,subprocess,sys;print(subprocess.check_output([sys.executable,"-c","import os,json; print(json.dumps(dict(os.environ)))"],text=True))'
    observed = json.loads(await process(sys.executable, '-c', code, env=env))
    assert observed['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert observed['GIT_CONFIG_GLOBAL'] == os.devnull and 'GH_TOKEN' not in observed


@pytest.mark.asyncio
async def test_bundle_git_uses_anonymous_child_environment(tmp_path, monkeypatch):
    from amplifier_acp.native import bundles
    for key, value in contaminated(tmp_path).items(): monkeypatch.setenv(key, value)
    child = AsyncMock(returncode=0)
    child.communicate.return_value = (b'result', b'')
    create = AsyncMock(return_value=child)
    monkeypatch.setattr(bundles.asyncio, 'create_subprocess_exec', create)
    assert await bundles.git('ls-remote', 'https://github.com/example/public.git') == b'result'
    env = create.call_args.kwargs['env']
    assert env['GIT_CONFIG_GLOBAL'] == os.devnull and 'GH_TOKEN' not in env
    assert 'protocol.file.allow=never' in create.call_args.args


def test_launcher_and_separate_preparation_propagate_without_changing_parent_environment(tmp_path):
    from amplifier_acp.preparation_policy import configure, staging_environment
    env = contaminated(tmp_path)
    env.pop('AMPLIFIER_RUNTIME_IMMUTABLE', None)
    env.pop('AMPLIFIER_RUNTIME_SOURCE_CLOSURE', None)
    configure({}, env)
    assert 'GH_TOKEN' not in env and env['GIT_CONFIG_GLOBAL'] == os.devnull
    root = tmp_path / 'owned'; project = root / 'candidate'; project.mkdir(parents=True)
    parent = contaminated(tmp_path)
    staged = staging_environment(parent, project, root, prepared=False)
    assert staged['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous' and 'GIT_ASKPASS' not in staged
    assert parent['GIT_ASKPASS'] == '/must-not-run'
