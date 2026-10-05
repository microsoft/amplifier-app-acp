"""Resolve draft choices in an isolated probe, without a conversation or inference."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import signal

async def resolve_defaults(home, workspace, bundle=None, app_bundle=None, *, global_only=False, model_instance=None, initial_selection=None, registry_home=None):
    from .bundle_selection import defaults
    chosen=bundle or defaults(home,workspace,app_bundle,global_only=global_only)['effective']
    from ..worker import _default_worker_command
    command=[*_default_worker_command()[:-1], 'amplifier_acp.native.draft_defaults_probe']
    cwd=Path(workspace).expanduser().resolve()
    while not cwd.is_dir() and cwd.parent!=cwd:cwd=cwd.parent
    process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,cwd=cwd,env={**os.environ,'AMPLIFIER_WEB_HOME':str(home),**({'AMPLIFIER_ACP_REGISTRY_HOME':str(registry_home)} if registry_home is not None else {})})
    try:
        output,_=await asyncio.wait_for(process.communicate(json.dumps({'home':str(home),'workspace':workspace,'bundle':chosen,'globalOnly':global_only,**({'initialSelection':initial_selection} if initial_selection is not None else {}),**({'modelInstance':model_instance} if model_instance is not None else {})}).encode()),45)
        result=json.loads(output)
        if process.returncode or result.get('error'):
            from .managed_models import CatalogUnavailable
            raise CatalogUnavailable(result.get('reason','provider-discovery-failed'))
        return {**result,'bundle':chosen,'globalOnly':global_only,**({'initialSelection':initial_selection} if initial_selection is not None else {}),**({'modelInstance':model_instance} if model_instance is not None else {})}
    finally:
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()
