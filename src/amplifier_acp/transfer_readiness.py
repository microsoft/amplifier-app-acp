"""Destination-owned, exactly-once bounded provider readiness probes."""
import asyncio,copy,json,os,sys,time
from pathlib import Path
from .native.portability_policy import completion_receipt,effective_effort,readiness_policy,validate_policy,policy_digest
from .session_lifecycle import record
from .relocation import home,write

def configured(owner):
    from .native.setup import SetupManager
    manager=SetupManager(home(owner.server));payload=owner.package();intent=payload['intent'];selection=intent['selection']
    config=manager.config(Path(owner.cwd));provider=selection.get('instance') or selection.get('provider')
    if config.active_bundle!=intent['bundle']:raise ValueError('Configure the source bundle identity on the destination before transfer')
    row=next((row for row in config.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==provider),None)
    if row is None or row.get('enabled') is False:raise ValueError('Configure and enable the selected provider instance on destination')
    return payload,provider,row,config

def policy(owner):
    owner.expect('incoming',{'staging','ready','activating'})
    payload,provider,row,_=configured(owner);selection=payload['intent']['selection']
    value=readiness_policy(row['module'],provider,selection['model'],effective_effort(selection,row.get('config',{})))
    return {'policy':value,'policyHash':policy_digest(value)}

async def check(owner):
    owner.expect('incoming',{'staging','activating'})
    selected=validate_policy(owner.row.get('readinessPolicy'),owner.row.get('readinessPolicyHash'))
    if policy(owner)['policy']!=selected:raise ValueError('Destination-owned configuration changed after readiness admission')
    path=owner.root/('probe-'+owner.row['phase']+'.json')
    if path.exists():
        old=record(path)
        if old.get('status')=='completed':return old['result']
        raise ValueError('Destination provider probe outcome is uncertain; no paid request replayed')
    payload,provider,row,config=configured(owner)
    from .native.setup import environment_credential
    raw=copy.deepcopy(row.get('config',{}));credential=environment_credential(row['module'],raw)
    if not raw.get(credential['field']) and credential['available']:raw[credential['field']]='${'+credential['envVar']+'}'
    request={'module':row['module'],'config':raw,'model':selected['model'],'readinessPolicy':selected,'source':config.module_sources.get(row['module']) or row.get('source'),'registryHome':str(config.registry_home)}
    source=request['source']
    if source and not source.startswith('git+https://'):
        candidate=Path(source.removeprefix('file://')).expanduser()
        if not candidate.is_absolute() or candidate.resolve().is_relative_to(Path(owner.cwd)):raise ValueError('Readiness requires installed code or independent destination-owned source')
    command=owner.server.config.get('transferProbeCommand')
    worker=owner.server.config.get('workerCommand')
    if command is None:
        if worker and worker!=[sys.executable,'-m','amplifier_acp.native.runtime_worker']:raise ValueError('Custom native worker requires explicit matching transferProbeCommand')
        command=[sys.executable,'-m','amplifier_acp.native.portability_probe']
    directory=owner.node.directory/'probe';directory.mkdir(exist_ok=True,mode=0o700)
    write(path,{'status':'running','policyHash':policy_digest(selected),'inputsReplayed':False})
    process=None
    try:
        environment=dict(os.environ);environment.pop('VIRTUAL_ENV',None)
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,cwd=directory,env=environment,start_new_session=True)
        output,_=await process.communicate(json.dumps(request).encode())
        if len(output)>65536:raise ValueError('Destination readiness response exceeds bound')
        result=json.loads(output)
        if process.returncode or result.get('error') or result.get('accountVerified') is not True or result.get('runtimeTransferFence') is not True or result.get('readinessPolicyHash')!=policy_digest(selected):raise ValueError('Destination readiness failed: '+str(result.get('code','invalid_receipt')))
        from amplifier_worktrees.git import digest
        checked={'runtimeVerified':True,'accountVerified':True,'nativeFenceVerified':True,'credentialsOrigin':'destination','method':result['method'],'accountVerificationScope':result['accountVerificationScope'],'provider':provider,'model':selected['model'],'reasoningEffort':selected['reasoningEffort'],'readinessPolicy':selected,'readinessPolicyHash':policy_digest(selected),'completionReceipt':completion_receipt(result.get('completionReceipt'),selected),'intentHash':digest(payload['intent']),'checkedAt':time.time()}
        write(path,{'status':'completed','result':checked,'inputsReplayed':False});return checked
    except BaseException:
        write(path,{'status':'unknown','policyHash':policy_digest(selected),'inputsReplayed':False});raise
    finally:
        if process and process.returncode is None:
            import signal
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()
