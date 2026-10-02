"""Retained upstream qualification contracts, source fc9db3e."""
import json
from pathlib import Path
import pytest
from amplifier_acp.native import runtime_environment,runtime_qualification,updates

def test_recorded_graph_rejects_later_distribution_changes(tmp_path, monkeypatch):
    row = {'name': 'amplifier-fixture', 'version': '1.0', 'directUrl': {'url': 'https://example.invalid/fixture',
        'vcs_info': {'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'a' * 40}}}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([row]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [row])
    runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**row, 'version': '2.0'}])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)

def test_active_generation_uses_qualified_policy_without_overriding_user_choice(tmp_path, monkeypatch):
    release = '6' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    (tmp_path / 'updates/active.json').write_text(json.dumps({'current': release}))
    (receipt / 'runtime-installed.json').write_text('[]')
    target = receipt / 'runtime-install-overrides.txt'
    target.write_text('amplifier-core==1.6.1\n')
    (receipt / 'runtime.lock').write_text('version=1\n[[package]]\nname="amplifier-core"\nversion="1.6.1"\nsource={registry="https://pypi.org/simple"}\n')
    runtime_environment.prepare_project(tmp_path, release)
    calls = []
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', lambda project, directory, **policy: calls.append((project, directory)))
    assert runtime_qualification.active_install_overrides(tmp_path) == target
    assert runtime_qualification.active_install_overrides(tmp_path, str(target)) == target
    assert len(calls) == 2
    assert runtime_qualification.active_install_overrides(tmp_path, '/user/explicit-overrides.txt') is None
    assert len(calls) == 2
    target.write_text('amplifier-core==0.0.1\n')
    with pytest.raises(ValueError, match='policy changed'):
        runtime_qualification.active_install_overrides(tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match='policy is missing'):
        runtime_qualification.active_install_overrides(tmp_path)

def test_legacy_worker_generation_has_no_new_install_policy(tmp_path):
    assert runtime_qualification.active_install_overrides(tmp_path) is None
    assert not (tmp_path / 'updates').exists()

def test_active_additions_cannot_replace_any_qualified_dependency(tmp_path, monkeypatch):
    original = {'name': 'amplifier-original', 'version': '1.0'}
    added = {'name': 'amplifier-lazy', 'version': '2.0'}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([original]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [original, added])
    runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**original, 'version': '1.1'}, added])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)

def test_installer_overrides_preserve_editable_and_local_sources(tmp_path):
    (tmp_path / 'uv.lock').write_text('''version=1
[[package]]
name="amplifier-editable"
version="1.0"
source={editable="../cached-module"}
[[package]]
name="amplifier-local"
version="1.0"
source={directory="../local-module"}
''')
    output = runtime_qualification.lock_overrides(tmp_path, tmp_path / 'overrides.txt').read_text()
    assert '-e ' + (tmp_path.parent / 'cached-module').as_uri() in output
    assert 'amplifier-local @ ' + (tmp_path.parent / 'local-module').as_uri() in output
