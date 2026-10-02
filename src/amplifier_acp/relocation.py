"""Execution-directory handoff; native project history and identity never move."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
from .configuration_clone import location,read,fingerprint

def home(server):return Path(server.config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
def state(server,identity,history):
    history=str(Path(history).expanduser().resolve(strict=True));server.directory(history,identity)
    directory=location(home(server),identity);path=directory/'execution-location.json';pending=directory/'execution-relocation.json'
    value=read(path) if path.exists() else {'sessionId':identity,'historyCwd':history,'executionDirectory':history,'executionRevision':0}
    if value['sessionId']!=identity or value['historyCwd']!=history:raise ValueError('Native history location does not match saved execution ownership')
    journal=read(pending) if pending.exists() else None
    return {**value,'blocked':bool(journal and journal['status']!='applied'),**({'receipt':journal} if journal else {})}
def write(path,value):
    from .native.host.config import write_private
    write_private(path,json.dumps(value))
def git_identity(path):
    root=Path(subprocess.check_output(['git','-C',str(path),'rev-parse','--path-format=absolute','--git-common-dir'],stderr=subprocess.DEVNULL,text=True,timeout=10).strip()).resolve(strict=True)
    return str(root)
def validate_target(source,target):
    target=Path(target).expanduser()
    if not target.is_absolute() or not target.is_dir():raise ValueError('Target must be an existing absolute worktree directory')
    target=target.resolve(strict=True)
    try:
        if git_identity(source)!=git_identity(target):raise ValueError('Execution relocation requires worktrees from the same Git repository')
    except subprocess.SubprocessError as exc:raise ValueError('Execution relocation requires verified Git worktrees') from exc
    return str(target)
def passive(server,params,reconcile=False):
    identity=params.get('sessionId');history=params.get('historyCwd')
    if not isinstance(history,str) or not Path(history).is_absolute():raise ValueError('Absolute original historyCwd is required')
    value=state(server,identity,history);receipt=value.get('receipt')
    if reconcile:
        if not isinstance(params.get('commandId'),str):raise ValueError('Original relocation commandId is required')
        if not receipt or receipt['commandId']!=params['commandId']:raise ValueError('Relocation receipt does not match original command')
        if value.get('commandId')==receipt['commandId'] and value['executionDirectory']==receipt['target'] and value['executionRevision']==receipt['expectedExecutionRevision']+1:
            receipt={**receipt,'status':'applied'};write(location(home(server),identity)/'execution-relocation.json',receipt)
            value={**value,'blocked':False,'receipt':receipt,'reconciled':True}
        elif receipt['status']!='applied':return {**value,'reconciled':False,'reason':'Target remount has no committed proof; native execution remains blocked.'}
    return value

async def relocate(session,args):
    server=session.server;identity=session.identity;history=session.cwd
    command=args.get('commandId');expected=args.get('expectedExecutionRevision')
    if not isinstance(command,str) or not 1<=len(command)<=200 or type(expected) is not int or expected<0:raise ValueError('Stable commandId and native execution revision required')
    current=state(server,identity,history);prior=current.get('receipt');signature=fingerprint(args)
    if prior and prior['commandId']==command:
        if prior['signature']!=signature:raise ValueError('Relocation commandId has different arguments')
        if prior['status']=='applied':return {**current,'applied':True,'duplicate':True}
        raise ValueError('Relocation outcome is uncertain; inspect/reconcile original receipt. No replay.')
    if current['blocked']:raise ValueError('A prior relocation is unresolved; execution remains blocked')
    if expected!=current['executionRevision']:raise ValueError('Native execution revision changed')
    target=await asyncio.to_thread(validate_target,current['executionDirectory'],args.get('target',''))
    if session.lock.locked() or session.config_cancel is not None:raise ValueError('Finish the active turn or configuration change before relocating')
    async with session.lock:
        await session.start()
        parked=await session.worker.command('park')
        if not parked.get('parked'):raise ValueError('Runtime still owns work; relocation was not started')
        retired=await session.worker.command('retire')
        if not retired.get('retired'):raise ValueError('Runtime is not safely retirable; relocation was not started')
        await session.worker.process.wait();await session.worker.close()
        directory=location(home(server),identity)
        journal={'commandId':command,'signature':signature,'status':'remounting','historyCwd':history,'from':current['executionDirectory'],'target':target,'expectedExecutionRevision':expected}
        write(directory/'execution-relocation.json',journal)
        session.execution_directory=target;session.relocating=True
        try:
            await session.start()
            result={'sessionId':identity,'historyCwd':history,'executionDirectory':target,'executionRevision':expected+1,'commandId':command}
            write(directory/'execution-location.json',result)
            write(directory/'execution-relocation.json',{**journal,'status':'applied'})
            return {**result,'applied':True,'blocked':False}
        except BaseException:
            if session.worker:await session.worker.close()
            raise
        finally:session.relocating=False
