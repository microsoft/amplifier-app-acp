"""Passive creation in an explicitly owned allocation. A path never grants ownership."""
from __future__ import annotations
import hashlib,json,os,stat,time,uuid
from pathlib import Path
from .maintenance_gate import checked

class Refused(ValueError):
    data={'executed':False,'replayed':False,'reason':'managed-creation-refused'}

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def app_home(config=None):return Path((config or {}).get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
def bounded(value,name,limit):
    if not isinstance(value,str) or not value or len(value)>limit:raise Refused(f'Bounded {name} required')
    return value

def read(path,maximum=65536):
    path=checked(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>maximum:raise Refused('Managed ownership record is not a bounded private file')
        raw=os.read(fd,maximum+1);after=os.fstat(fd)
        if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Refused('Managed ownership record changed during inspection')
        return json.loads(raw)
    finally:os.close(fd)

def write(path,value):
    from .native.host.config import write_private
    checked(path);write_private(path,json.dumps(value,ensure_ascii=False))

def marker(cwd,identity,config=None):
    cwd=Path(cwd)
    if not cwd.is_absolute() or str(cwd)!=str(cwd.resolve()) or not cwd.is_dir():raise Refused('Managed working directory must be exact and existing')
    checked(cwd)
    if str(uuid.UUID(identity))!=identity or cwd.name!='files' or cwd.parent.name!=identity or cwd.parent.parent.name!='chats':raise Refused('Managed allocation layout does not match its reserved identity')
    root=cwd.parent.parent.parent
    if config is not None:
        roots=config.get('managedSessionRoots',[])
        if not isinstance(roots,list) or not 1<=len(roots)<=16 or any(not isinstance(r,str) or not Path(r).is_absolute() for r in roots):raise Refused('Managed creation is not launcher-enabled')
        if not any(checked(r).resolve()==root for r in roots):raise Refused('Managed allocation root is not authorized by the launcher')
    value=read(cwd.parent/'managed-chat.json',4096)
    expected={'version','id','workspace','creationReceipt','owner','allocationHash'}
    if set(value)!=expected or (type(value.get('version')) is not int or value.get('version')!=1) or value.get('id')!=identity or value.get('workspace')!=str(cwd) or value.get('owner')!='amplifier-unified':raise Refused('Managed allocation marker identity differs')
    bounded(value.get('creationReceipt'),'creation receipt',200)
    if value.get('allocationHash')!=digest({k:v for k,v in value.items() if k!='allocationHash'}):raise Refused('Managed allocation marker hash differs')
    return value

def directory_identity(cwd):
    path=Path(cwd);return {str(p):{'dev':p.stat().st_dev,'ino':p.stat().st_ino} for p in (path,path.parent,path.parent.parent,path.parent.parent.parent)}

def location(value):return {'version':1,'kind':'managed','allocationId':value['id'],'creationReceipt':value['creationReceipt'],'allocationHash':value['allocationHash'],'owner':'amplifier-unified'}

def proof_path(identity,config=None):return checked(app_home(config)/'sessions'/identity/'managed-creation.json')
def proof(identity,cwd,config=None):
    path=proof_path(identity,config)
    if not path.exists():return None
    value=read(path)
    if value.get('status')!='completed':raise Refused('Managed creation is unresolved; inspect its original receipt, never replay')
    selected=marker(cwd,identity,config)
    if value.get('directoryIdentity')!=directory_identity(cwd):raise Refused('Managed allocation directory identity changed')
    if value.get('marker')!=selected or value.get('request',{}).get('cwd')!=str(cwd) or value.get('result',{}).get('location')!=location(selected):raise Refused('Managed creation differs from its native authoritative record')
    return value

def runtime_scope(cwd,identity=None,config=None):
    cwd=Path(cwd).resolve()
    candidate=identity or (cwd.parent.name if cwd.name=='files' and cwd.parent.parent.name=='chats' else None)
    under_managed=config is not None and any(cwd.is_relative_to(Path(r).resolve()) for r in config.get('managedSessionRoots',[]))
    if not candidate:
        if under_managed:raise Refused('Private managed root is not a general workspace grant')
        return None
    try:
        if str(uuid.UUID(candidate))!=candidate:raise ValueError('Noncanonical identity')
    except (ValueError,TypeError):
        if under_managed:raise Refused('Private managed root requires an exact allocation identity') from None
        return None
    value=proof(candidate,str(cwd),config)
    if value:return value
    if under_managed:
        raise Refused('Managed directory has no native creation authority; ordinary session creation cannot adopt it')
    return None

def bundle(config,args):
    if 'bundle' not in args:
        if 'bundleRevision' in args:raise Refused('Bundle revision requires a configured alias')
        return bounded(config.get('bundle'),'configured default bundle',4096)
    choices=config.get('forkBundles',{})
    alias=bounded(args['bundle'],'bundle alias',200)
    selected=choices.get(alias) if isinstance(choices,dict) else None
    if not isinstance(selected,str) or args.get('bundleRevision')!=digest([alias,selected]):raise Refused('Configured bundle alias or revision changed')
    return selected

def selection(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'instance','model','effort'}:raise Refused('Initial selection must be a typed provider/model object')
    bounded(value.get('instance'),'provider instance',200);bounded(value.get('model'),'model',500)
    if 'effort' in value:bounded(value['effort'],'reasoning effort',100)
    return value

def perform(server,params):
    if set(params)-{'operation','sessionId','cwd','commandId','allocationHash','title','bundle','bundleRevision','selection'}:raise Refused('Unknown managed creation fields')
    identity=bounded(params.get('sessionId'),'session ID',36);cwd=bounded(params.get('cwd'),'working directory',4096);command=bounded(params.get('commandId'),'command ID',200)
    selected=marker(cwd,identity,server.config)
    if command!=selected['creationReceipt'] or params.get('allocationHash')!=selected['allocationHash']:raise Refused('Host allocation receipt does not match managed creation')
    path=proof_path(identity,server.config)
    if params.get('operation')=='receipt':
        if set(params)-{'operation','sessionId','cwd','commandId','allocationHash'}:raise Refused('Receipt accepts exact identity only')
        if not path.exists():return None
        value=read(path)
        if value.get('commandId')!=command or value.get('marker')!=selected:raise Refused('Native managed receipt identity differs')
        if value.get('status')=='completed':proof(identity,cwd,server.config)
        return {k:value[k] for k in ('commandId','status','result') if k in value}|{'replayed':False}
    if params.get('operation')!='create':raise Refused('Unknown managed operation')
    title=params.get('title','New chat')
    if not isinstance(title,str) or len(title)>200:raise Refused('Title exceeds 200 characters')
    selected_bundle=bundle(server.config,params);selected_model=selection(params.get('selection'))
    request={k:v for k,v in params.items() if k!='operation'};signature=digest(request)
    if path.exists():
        prior=read(path)
        if prior.get('signature')!=signature:raise Refused('Managed creation command conflicts with its original request')
        if prior.get('status')=='completed':proof(identity,cwd,server.config)
        return {k:prior[k] for k in ('commandId','status','result') if k in prior}|{'replayed':False}
    directory=server.directory(cwd,identity)
    if directory.exists() or identity in server.sessions:raise Refused('Native identity already exists; no managed allocation was adopted')
    # Exclusive directory reservation precedes every native mutation. A partial
    # reservation is never resumed by repeating create under a new process.
    path.parent.mkdir(parents=True,exist_ok=False,mode=0o700)
    value={'commandId':command,'status':'unknown','signature':signature,'request':request,'marker':selected,'directoryIdentity':directory_identity(cwd)}
    write(path,value)
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.shared_state import SharedSessionStore
    lease=SharedSessionStore(cwd,identity).acquire(app='amplifier-acp-managed-create',pid=os.getpid())
    try:
        if directory.exists():raise Refused('Native identity was created by another writer')
        directory.mkdir(parents=True,exist_ok=False,mode=0o700)
        metadata={'session_id':identity,'parent_id':None,'created_at':time.time(),'status':'created','name':title,'name_source':'user','working_dir':cwd,'bundle':selected_bundle,'bundle_name':selected_bundle,'location':location(selected)}
        SessionHistoryStore(directory,session_id=identity).save([],metadata,preserve_system=True)
        write(path.parent/'control-state.json',{'selection':selected_model} if selected_model is not None else {})
        write(path.parent/'execution-location.json',{'sessionId':identity,'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0})
        result={'sessionId':identity,'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0,'creationConfirmed':True,'location':location(selected),'title':title,'bundle':selected_bundle,'configurationScope':'global-and-session','runtimeStarted':False,'workReplayed':False}
        write(path,{**value,'status':'completed','result':result});return {'commandId':command,'status':'completed','result':result,'replayed':False}
    finally:lease.release()

async def dispatch(server,params):
    # Keep the native shared writer gate until off-thread creation actually
    # settles, even when transport shutdown cancels its awaiting request.
    import asyncio
    task=asyncio.create_task(asyncio.to_thread(perform,server,params))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except BaseException:break
        raise
