"""Check a pin against the exact candidate's public model catalog, in isolation."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import signal
import sys

MAX_REPLY = 4096
MAX_REQUEST = 1048576
TIMEOUT = 45


async def inspect_selection(config, bundle, plan, selection, workspace):
    if not selection:
        return {'status': 'unpinned'}
    instance = selection.get('instance') or selection.get('provider')
    rows = [row for row in plan.get('providers', []) if row.get('enabled', True)
            and (row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')) == instance]
    if len(rows) != 1:
        return {'status': 'provider-missing'}
    from .host.session import is_snapshot, module_source
    row = rows[0]
    request = {'action': 'providers.compatibility', 'module': row['module'],
               'config': copy.deepcopy(row.get('config', {})), 'model': selection.get('model'),
               'source': module_source(config, is_snapshot(bundle), row['module'], row.get('source'),
                                       getattr(bundle, '_host_components', None)),
               'registryHome': str(config.registry_home),
               'basePath': str(bundle.base_path) if bundle.base_path is not None else None,
               'sourceBasePaths': {key: str(value) for key, value in (bundle.source_base_paths or {}).items()}}
    payload = json.dumps(request).encode()
    if len(payload) > MAX_REQUEST:
        return {'status': 'unverified'}
    # Do not import the candidate into the old mounted worker: a source change
    # can use the same module name. This probe mounts no session and never calls
    # complete/login. Runtime dependency policy and configured credentials remain
    # inherited; provider metadata may contact its advertised discovery service.
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-I', '-B', '-m', 'amplifier_acp.native.provider_probe',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True, cwd=workspace)
    try:
        async with asyncio.timeout(TIMEOUT):
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = b''
            while chunk := await process.stdout.read(MAX_REPLY + 1 - len(output)):
                output += chunk
                if len(output) > MAX_REPLY:
                    return {'status': 'unverified'}
            await process.wait()
            result = json.loads(output)
            if (process.returncode == 0 and isinstance(result, dict) and set(result) == {'status'}
                    and isinstance(result['status'], str)
                    and result['status'] in {'advertised', 'not-advertised', 'unverified'}):
                return result
    except (TimeoutError, ValueError, OSError):
        pass
    finally:
        # The leader can already have exited while an SDK child still owns its
        # stdout pipe. This isolated process group belongs to this one probe;
        # its lifetime ends on success as well as timeout/cancellation.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    # An unavailable/non-exhaustive catalog never proves that a pin is invalid,
    # but it also cannot authorize carrying that pin into a different bundle.
    return {'status': 'unverified'}
