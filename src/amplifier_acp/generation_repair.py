"""Rebuild a recorded native environment in a separate, qualified generation.

This is recovery of an exact installation, not update policy. Old projects,
source checkouts and histories are never rewritten or reclaimed here.
"""
from __future__ import annotations
import asyncio,hashlib,json,shutil,time
from pathlib import Path
from .native import runtime_environment as environments
from .native.host.config import write_private

FILES=('runtime.toml','runtime.lock','runtime-base.toml','runtime-installed.json',
       'runtime-install-overrides.txt','runtime-install-policy.json','runtime-sources.json',
       'profiles-qualified.json','validated.json')
MAX_FILE=8*1024*1024

def source(home,generation):
    from .generations import identity
    from .runtime_inspection import digest
    generation=identity(generation);directory=environments.receipt_directory(home,generation)
    if any(path.is_symlink() for path in (directory,*directory.parents)):
        raise ValueError('Repair receipts cannot traverse symbolic links')
    values={}
    for name in FILES:
        path=directory/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_FILE:
            raise ValueError('Repair needs complete bounded native qualification receipts')
        values[name]=path.read_bytes()
    if sum(map(len,values.values()))>32*1024*1024:raise ValueError('Repair receipts exceed32MiB')
    validation=json.loads(values['validated.json']);profiles=json.loads(values['profiles-qualified.json']).get('profiles')
    graph=json.loads(values['runtime-installed.json']);policy=json.loads(values['runtime-install-policy.json'])
    if validation.get('generationSchema')!=1:raise ValueError('Repair requires a native qualified generation')
    if not isinstance(profiles,list) or not 1<=len(profiles)<=100 or any(not isinstance(v,str) or not 1<=len(v)<=4096 for v in profiles):
        raise ValueError('Repair needs the exact bounded qualified profile list')
    if not isinstance(graph,list) or not 1<=len(graph)<=5000 or any(not isinstance(v,dict) or not isinstance(v.get('name'),str) for v in graph):
        raise ValueError('Repair needs the exact installed distribution graph')
    if policy.get('version')!=2 or policy.get('lockSha256')!=hashlib.sha256(values['runtime.lock']).hexdigest() or policy.get('overridesSha256')!=hashlib.sha256(values['runtime-install-overrides.txt']).hexdigest():
        raise ValueError('Recorded installation policy is missing, unsupported or changed')
    hashes={name:hashlib.sha256(value).hexdigest() for name,value in values.items()}
    public={'generation':generation,'sourceHash':digest(hashes),'profiles':profiles,'distributionCount':len(graph),
        'policy':'exact-recorded-lock','activation':'separate-generation-promotion','runningWorkersChanged':False,
        'sourceInputs':'retained-at-recorded-locations','sourceRepairSupported':False,
        'preserved':['original-environment','original-receipts','source-checkouts','native-history','events','workspaces']}
    return directory,values,public

def preview(home,args):
    from .native.updates import active_release
    if not isinstance(args,dict) or set(args)-{'generation'}:raise ValueError('Only a selected native generation can be inspected for repair')
    pointer=active_release(home);_,_,report=source(home,args.get('generation') or pointer.get('current'))
    return {**report,'expectedCurrent':pointer.get('current'),'workerStarted':False,'networkContacted':False}

async def rebuild(owner,args,generation):
    from .native.updates import active_release,process
    from .native.update_storage import copy_snapshot
    from .native.runtime_qualification import verify_recorded,package_content
    if set(args)-{'commandId','generation','expectedSourceHash','expectedCurrent'}:raise ValueError('Unexpected native repair argument')
    directory,values,report=await asyncio.to_thread(source,owner.home,args.get('generation'))
    if report['sourceHash']!=args.get('expectedSourceHash'):raise ValueError('Repair source receipts changed; inspect them again')
    if 'expectedCurrent' not in args or active_release(owner.home).get('current')!=args['expectedCurrent']:
        raise ValueError('Active native generation changed before repair')
    uv=shutil.which('uv')
    if not uv:raise RuntimeError('Install uv to reconstruct the native environment')
    stage=environments.receipt_directory(owner.home,generation);stage.mkdir(parents=True,exist_ok=False,mode=0o700)
    project=owner.home/'runtime'/('q-'+generation);project.mkdir(parents=True,exist_ok=False,mode=0o700)
    await owner.publish(phase='repairing',preparingRelease=generation,repairSource=args['generation'],error=None)
    # Preserve absolute source identities. A profile that requires new source
    # preparation is refused by the read-only qualification pass below.
    for name in ('foundation','config','routing','shared-config'):
        origin=directory/name
        if origin.is_symlink():raise ValueError('Repair configuration roots cannot be symbolic links')
        if origin.is_dir():await asyncio.to_thread(copy_snapshot,origin,stage/name)
        elif name in {'foundation','shared-config'}:(stage/name).mkdir(mode=0o700)
    for name,value in values.items():
        if name not in {'validated.json','profiles-qualified.json'}:write_private(stage/name,value.decode())
    write_private(stage/'runtime-project.json',json.dumps({'project':project.name}))
    write_private(project/'pyproject.toml',values['runtime.toml'].decode());write_private(project/'uv.lock',values['runtime.lock'].decode())
    write_private(stage/'repair-source.json',json.dumps({**report,'commandId':args['commandId'],'createdAt':time.time()}))
    # A damaged environment may share hard links with uv's extracted cache.
    # Rebuild from the locked inputs in a disposable cache, with independent
    # installed files; reusing that cache can reproduce the original damage.
    await owner.diagnostics.run('native-repair-install',process,uv,'sync','--frozen','--no-cache','--link-mode','copy','--project',str(project),'--python','3.13',timeout=900)
    if (project/'uv.lock').read_bytes()!=values['runtime.lock']:raise ValueError('Repair changed its recorded lock')
    await asyncio.to_thread(verify_recorded,project,stage)
    # Reuse the actual no-model profile probes and installation-policy checks.
    # This path cannot refresh dependencies or silently substitute new profiles.
    await owner.validate(stage,generation,profiles=report['profiles'])
    _,_,after=await asyncio.to_thread(source,owner.home,args['generation'])
    if after['sourceHash']!=report['sourceHash']:raise ValueError('Original repair receipts changed during reconstruction')
    if active_release(owner.home).get('current')!=args['expectedCurrent']:raise ValueError('Active generation changed during repair; candidate retained')
    payload=await asyncio.to_thread(package_content,project)
    from amplifier_acp import __version__
    write_private(stage/'validated.json',json.dumps({'generationSchema':1,'workerProtocol':1,'hostVersion':__version__,'createdAt':time.time(),
        'commandId':args['commandId'],'repairedFrom':args['generation'],'sourceHash':report['sourceHash'],'installedPayloadSha256':payload}))
    await owner.publish(phase='staged',pendingRelease=generation,preparingRelease=None)
    return {'generation':generation,'qualified':True,'repairedFrom':args['generation'],'sourceHash':report['sourceHash'],
        'runningWorkersChanged':False,'activation':'separate-generation-promotion','sourceRepairSupported':False}
