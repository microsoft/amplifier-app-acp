"""Real child interpreters, with a small protocol peer instead of Core startup."""
import json
import os
from pathlib import Path
import subprocess
import sys

import amplifier_acp
import pytest


PEER = r'''
import json, os, sys
from pathlib import Path
try:
    import environment_probe
    environment_import = True
except ModuleNotFoundError:
    environment_import = False
# A normal import from an explicitly supplied directory makes the bytecode
# assertion observable even when -I intentionally ignores PYTHONPATH.
sys.path.insert(0, str(Path(__file__).parent / 'modules'))
import bytecode_probe
report = {
    'isolated': sys.flags.isolated,
    'ignore_environment': sys.flags.ignore_environment,
    'no_user_site': sys.flags.no_user_site,
    'safe_path': sys.flags.safe_path,
    'dont_write_bytecode': sys.dont_write_bytecode,
    'environment_import': environment_import,
    'bytecode': list(str(p) for p in (Path(__file__).parent / 'child-bytecode').rglob('bytecode_probe.*.pyc')),
}
if os.environ.get('AMPLIFIER_RUNTIME_IMMUTABLE') == '1':
    report['runtimePreparation'] = {'version': 1, 'immutable': True,
        'dependencyInstallation': False, 'policy': 'foundation-dependency-installation'}
for line in sys.stdin:
    row = json.loads(line)
    if row['op'] == 'start':
        print(json.dumps({'type': 'runtime.ready', 'report': report}), flush=True)
    elif row['op'] == 'stop':
        break
'''

PARENT = r'''
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from amplifier_acp.worker import NativeWorker

async def main():
    root = Path(sys.argv[2])
    explicit = sys.argv[3] == 'explicit'
    peer = str(root / 'peer.py')
    commands = []
    original = asyncio.create_subprocess_exec
    async def launch(*command, **kwargs):
        commands.append(list(command))
        if not explicit:
            assert command[-2:] == ('-m', 'amplifier_acp.native.runtime_worker')
            # Only replace the runtime entry point; execute the actual default
            # executable/flags through the real asyncio subprocess transport.
            # Redirect permitted bytecode into the owned fixture too, including
            # -I/-E cases which intentionally ignore PYTHONPYCACHEPREFIX.
            command = (*command[:-2], '-X', f'pycache_prefix={root / "child-bytecode"}', peer)
        return await original(*command, **kwargs)
    asyncio.create_subprocess_exec = launch
    async def observe(row):pass
    async def bridge(operation, args):raise AssertionError('No bridge calls expected')
    config = {'home': str(root / 'home'), 'appHome': str(root / 'app'),
              'runtimeGeneration': None, 'startupTimeout': 5}
    if sys.argv[4] == 'immutable':config['runtimeImmutable'] = True
    if explicit:
        config['workerCommand'] = [sys.executable, peer]
    worker = NativeWorker('interpreter-flags', str(root), config, observe, bridge)
    try:
        report = await worker.start()
    finally:
        await worker.close()
    print(json.dumps({'report': report, 'commands': commands,
                      'explicitCommand': config.get('workerCommand'),
                      'returncode': worker.process.returncode}))
asyncio.run(main())
'''


@pytest.mark.parametrize('flags,environment_no_bytecode,expected', [
    ([], False, (0, 0, 0, False, False, True)),
    (['-B'], False, (0, 0, 0, False, True, True)),
    (['-I'], False, (1, 1, 1, True, False, False)),
    (['-I', '-B'], False, (1, 1, 1, True, True, False)),
    (['-E', '-s', '-P', '-B'], False, (0, 1, 1, True, True, False)),
    (['-P'], False, (0, 0, 0, True, False, True)),
    ([], True, (0, 0, 0, False, True, True)),
])
def test_default_worker_inherits_interpreter_safety(tmp_path, flags, environment_no_bytecode, expected):
    result = run_parent(tmp_path, flags, environment_no_bytecode=environment_no_bytecode)
    report = result['report']
    assert tuple(report[key] for key in (
        'isolated', 'ignore_environment', 'no_user_site', 'safe_path',
        'dont_write_bytecode', 'environment_import')) == expected
    assert bool(report['bytecode']) is not expected[4]
    assert result['returncode'] == 0
    assert len(result['commands']) == 1


def test_explicit_worker_command_remains_launcher_owned(tmp_path):
    result = run_parent(tmp_path, ['-I', '-B'], explicit=True)
    assert result['commands'] == [result['explicitCommand']]
    assert result['report']['isolated'] == 0
    assert result['report']['dont_write_bytecode'] is False
    assert result['report']['environment_import'] is True
    assert result['report']['bytecode']
    assert result['returncode'] == 0


def test_immutable_default_child_keeps_isolation_and_no_bytecode(tmp_path):
    result = run_parent(tmp_path, ['-I', '-B'], immutable=True)
    assert result['report']['runtimePreparation']['immutable'] is True
    assert result['report']['isolated'] == 1
    assert result['report']['dont_write_bytecode'] is True
    assert result['report']['bytecode'] == []
    assert result['returncode'] == 0


def run_parent(root, flags, *, environment_no_bytecode=False, explicit=False, immutable=False):
    (root / 'modules').mkdir()
    (root / 'modules/bytecode_probe.py').write_text('VALUE = 1\n')
    (root / 'environment').mkdir()
    (root / 'environment/environment_probe.py').write_text('VALUE = 2\n')
    (root / 'peer.py').write_text(PEER)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PYTHON') and key not in {
               'AMPLIFIER_RUNTIME_IMMUTABLE', 'AMPLIFIER_ACP_SOURCE_RESOLUTION_MANIFEST',
               'AMPLIFIER_ACP_SOURCE_RESOLUTION_SHA256'}}
    env['PYTHONPATH'] = str(root / 'environment')
    env['PYTHONPYCACHEPREFIX'] = str(root / 'child-bytecode')
    env['HOME'] = str(root / 'home')
    if environment_no_bytecode:env['PYTHONDONTWRITEBYTECODE'] = '1'
    # amplifier_acp.__file__ selects the source or independently extracted
    # wheel under test; never mutate the dependency donor's installed package.
    source = str(Path(amplifier_acp.__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, *flags, '-X', f'pycache_prefix={root / "parent-bytecode"}', '-c', PARENT, source,
                             str(root), 'explicit' if explicit else 'default', 'immutable' if immutable else 'mutable'],
                            env=env, cwd=root, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout)
