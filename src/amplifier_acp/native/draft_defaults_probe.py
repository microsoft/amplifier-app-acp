"""Inspect composed providers, without mounting hooks, tools, or a session."""
from __future__ import annotations
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()

async def query(request):
    from amplifier_acp.native.host.config import load_config
    from amplifier_acp.native.host.session import load_root_bundle, is_snapshot, module_source
    from amplifier_acp.native.provider_environment import close_provider, construct_provider, construct_schema_provider, config_schema, materialize_provider_config, provider_class
    from amplifier_acp.native.provider_probe import public
    workspace=Path(request['workspace']).expanduser().resolve()
    existing=workspace
    while not existing.is_dir() and existing.parent!=existing:existing=existing.parent
    config=load_config(existing,home=request['home'],global_only=bool(request.get('globalOnly')))
    config.workspace=workspace
    _,bundle,_=await load_root_bundle(config,request['bundle'],execution_workspace=workspace)
    # Compose with normal application policy, then prepare only enabled root
    # provider sources. Metadata must support the same local/community sources
    # as a native mount without constructing a conversation, hooks or tools.
    from amplifier_foundation import Bundle
    providers=[dict(row) for row in bundle.providers if row.get('enabled') is not False]
    instance=request.get('modelInstance')
    if instance is not None:
        providers=[row for row in providers if (row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-'))==instance]
        if len(providers)!=1:
            from .managed_models import CatalogUnavailable
            raise CatalogUnavailable('provider-instance-unavailable')
    probe=Bundle(name='draft-provider-defaults',providers=providers,
                 base_path=bundle.base_path,source_base_paths=bundle.source_base_paths)
    snapshot=is_snapshot(bundle)
    await probe.prepare(strict=True,install_deps=os.environ.get('AMPLIFIER_RUNTIME_IMMUTABLE')!='1',
        cache_dir=config.registry_home/'cache',
        source_resolver=lambda module,source:module_source(config,snapshot,module,source,getattr(bundle,'_host_components',None)))
    rows=[]
    for row in providers:
        if row.get('enabled') is False:continue
        cls=provider_class(row['module'])
        schema_provider=construct_schema_provider(cls,row.get('config',{}))
        try:schema=await config_schema(schema_provider)
        finally:await close_provider(schema_provider)
        values=materialize_provider_config(row.get('config',{}),schema)
        provider=construct_provider(cls,values)
        try:
            if instance is not None:
                from .managed_models import discover
                return await discover(provider,instance,row['module'])
            info=provider.get_info()
            if inspect.isawaitable(info):info=await info
            info=public(info)
            info={key:info[key] for key in ('id','display_name','defaults') if key in info}
            schema={'fields':[field for field in schema.get('fields',[]) if field.get('id')=='reasoning_effort']}
            identity=row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
            priority=getattr(provider,'priority',getattr(provider,'config',{}).get('priority',100))
            rows.append({'id':identity,'module':row['module'],'effort':values.get('reasoning_effort'),'info':info,'configSchema':public(schema),'priority':priority})
        finally:await close_provider(provider)
    rows.sort(key=lambda row:row['priority'])
    first=rows[0] if rows else {}
    defaults=first.get('info',{}).get('defaults',{})
    return {'providers':rows,'effective':{'instance':first.get('id'),'model':defaults.get('model') or defaults.get('default_model'),'effort':defaults.get('reasoning_effort') or first.get('effort')}}

def main():
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    try:result=asyncio.run(query(json.loads(sys.stdin.read())))
    except Exception as exc:
        from .managed_models import CatalogUnavailable
        result={'error':type(exc).__name__,'reason':exc.reason if isinstance(exc,CatalogUnavailable) else 'provider-discovery-failed'}
    output.write(json.dumps(result)+'\n');output.flush()
if __name__=='__main__':main()
