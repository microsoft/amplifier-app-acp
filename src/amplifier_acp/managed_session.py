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

def options(config):
    choices=config.get('forkBundles',{})
    if not isinstance(choices,dict) or len(choices)>64 or any(not isinstance(k,str) or not 1<=len(k)<=200 or not isinstance(v,str) or not 1<=len(v)<=4096 for k,v in choices.items()):raise Refused('Configured managed bundle choices are invalid')
    return {'version':1,'bundles':[{'id':key,'name':key,'revision':digest([key,value])} for key,value in choices.items()],'defaultBundle':{'kind':'engine-default'},'configurationScope':'global-and-session','runtimeStarted':False}

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

def copy_target(server,source_id,source_cwd,target_id,value):
    """A copied managed session always needs a new independently owned allocation."""
    source=runtime_scope(source_cwd,source_id,server.config)
    if not source:
        if value is not None:raise Refused('Managed targets are reserved for a proved managed source')
        return None
    if not isinstance(value,dict) or set(value)!={'cwd','commandId','allocationHash'}:raise Refused('Managed copy requires a fresh exact target allocation')
    target=marker(value['cwd'],target_id,server.config)
    if value['commandId']!=target['creationReceipt'] or value['allocationHash']!=target['allocationHash']:raise Refused('Managed copy allocation receipt differs')
    if target_id==source_id or value['cwd']==source_cwd:raise Refused('Managed copy must use a new independent allocation')
    if proof_path(target_id,server.config).parent.exists() or server.directory(value['cwd'],target_id).exists():raise Refused('Managed copy identity already exists; inspect the original receipt')
    return target

def reserve_copy(server,target,source):
    path=proof_path(target['id'],server.config)
    request={'sessionId':target['id'],'cwd':target['workspace'],'commandId':target['creationReceipt'],'allocationHash':target['allocationHash'],'source':source}
    value={'commandId':target['creationReceipt'],'status':'unknown','signature':digest(request),'request':request,'marker':target,'directoryIdentity':directory_identity(target['workspace'])}
    write(path,value);return value

def complete_copy(server,value,*,title,bundle):
    target=value['marker'];cwd=target['workspace']
    # Completion never trusts a marker detached from the reserved inode identities.
    if marker(cwd,target['id'],server.config)!=target or directory_identity(cwd)!=value['directoryIdentity']:raise Refused('Managed target changed during copy; original evidence retained')
    result={'sessionId':target['id'],'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0,'creationConfirmed':True,'location':location(target),'title':title,'bundle':bundle,'configurationScope':'global-and-session','runtimeStarted':False,'workReplayed':False,'source':value['request']['source']}
    write(proof_path(target['id'],server.config),{**value,'status':'completed','result':result});return result

def copied_configuration(value,source_cwd,target_cwd):
    """Move only workspace-relative file policy; never rewrite prompt/history content."""
    keys={'allowed_write_paths','allowed_read_paths','denied_write_paths','denied_read_paths'}
    def visit(node):
        if isinstance(node,list):return [visit(x) for x in node]
        if not isinstance(node,dict):return node
        result={}
        for key,item in node.items():
            if key in keys and isinstance(item,list):
                def relocate(path):
                    if not isinstance(path,str) or not Path(path).is_absolute():return path
                    try:relative=Path(path).relative_to(source_cwd)
                    except ValueError:return path
                    return str(Path(target_cwd)/relative)
                result[key]=[relocate(path) for path in item]
            else:result[key]=visit(item)
        return result
    return visit(value)

def clone_source(server,reference):
    from .configuration_clone import capture,read as read_config,location as config_location,fingerprint
    if not isinstance(reference,dict) or set(reference)-{'version','sourceSessionId','sourceCwd','configurationHash','executionDirectory','locationRevision'} or reference.get('version')!=1:raise Refused('Exact managed configuration reference required')
    source_id=bounded(reference.get('sourceSessionId'),'source identity',36);source_cwd=bounded(reference.get('sourceCwd'),'source working directory',4096)
    if not runtime_scope(source_cwd,source_id,server.config):raise Refused('Managed configuration copy requires a proved managed source')
    hash_value=bounded(reference.get('configurationHash'),'configuration hash',64)
    if len(hash_value)!=64 or any(c not in '0123456789abcdef' for c in hash_value):raise Refused('Invalid configuration hash')
    saved=read_config(config_location(app_home(server.config),source_id)/'configuration-snapshots'/(hash_value+'.json'))
    if fingerprint(saved)!=hash_value or fingerprint(capture(app_home(server.config),source_id,source_cwd,saved['bundle']))!=hash_value:raise Refused('Managed source configuration changed; inspect a fresh reference')
    if saved['executionDirectory']!=source_cwd or reference.get('executionDirectory')!=source_cwd or reference.get('locationRevision')!=saved['locationRevision']:raise Refused('Managed source execution location changed')
    return saved

def configuration_snapshot(server,params):
    if set(params)!={'operation','sessionId','cwd'}:raise Refused('Exact selected managed snapshot identity required')
    identity=bounded(params.get('sessionId'),'session identity',36);cwd=bounded(params.get('cwd'),'working directory',4096)
    owned=runtime_scope(cwd,identity,server.config)
    if not owned:raise Refused('Managed source creation proof required')
    from amplifier_foundation.session.shared_state import SharedSessionStore
    from .session_lifecycle import guard_copy_source,record
    from .configuration_clone import snapshot
    handle=SharedSessionStore(cwd,identity).acquire(app='amplifier-acp-managed-snapshot',pid=os.getpid())
    try:
        guard_copy_source(server,identity);metadata=record(server.directory(cwd,identity)/'metadata.json')
        if metadata.get('session_id')!=identity or metadata.get('location')!=owned['result']['location']:raise Refused('Managed source metadata identity differs')
        return snapshot(app_home(server.config),identity,cwd,metadata.get('bundle') or metadata.get('bundle_name'))
    finally:handle.release()

def _perform(server,params):
    if params=={'operation':'options'}:return options(server.config)
    if params.get('operation')=='configuration.snapshot':return configuration_snapshot(server,params)
    if set(params)-{'operation','sessionId','cwd','commandId','allocationHash','title','bundle','bundleRevision','selection','source'}:raise Refused('Unknown managed creation fields')
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
    cloning=params.get('operation')=='clone'
    if params.get('operation') not in {'create','clone'}:raise Refused('Unknown managed operation')
    if cloning and any(key in params for key in ('bundle','bundleRevision','selection')) or not cloning and 'source' in params:raise Refused('Managed copy takes only its exact source configuration')
    title=params.get('title','New chat')
    if not isinstance(title,str) or len(title)>200:raise Refused('Title exceeds 200 characters')
    request={k:v for k,v in params.items() if k!='operation'};signature=digest(request)
    if path.exists():
        prior=read(path)
        if prior.get('signature')!=signature:raise Refused('Managed creation command conflicts with its original request')
        if prior.get('status')=='completed':proof(identity,cwd,server.config)
        return {k:prior[k] for k in ('commandId','status','result') if k in prior}|{'replayed':False}
    saved=clone_source(server,params.get('source')) if cloning else None
    selected_bundle=saved['bundle'] if saved else bundle(server.config,params);selected_model=selection(params.get('selection'))
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
        if saved:
            write(path.parent/'configuration.json',copied_configuration(saved['plan'],params['source']['sourceCwd'],cwd))
            write(path.parent/'control-state.json',copied_configuration(saved['controls'],params['source']['sourceCwd'],cwd))
        else:write(path.parent/'control-state.json',{'selection':selected_model} if selected_model is not None else {})
        write(path.parent/'execution-location.json',{'sessionId':identity,'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0})
        result={'sessionId':identity,'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0,'creationConfirmed':True,'location':location(selected),'title':title,'bundle':selected_bundle,'configurationScope':'global-and-session','runtimeStarted':False,'workReplayed':False}
        if saved:result['source']={'operation':'configuration-clone','sessionId':params['source']['sourceSessionId'],'configurationHash':params['source']['configurationHash']}
        write(path,{**value,'status':'completed','result':result});return {'commandId':command,'status':'completed','result':result,'replayed':False}
    finally:lease.release()

def perform(server,params):
    if params.get('operation')!='clone':return _perform(server,params)
    reference=params.get('source')
    if not isinstance(reference,dict):raise Refused('Exact managed source reference required')
    identity=bounded(reference.get('sourceSessionId'),'source identity',36);cwd=bounded(reference.get('sourceCwd'),'source directory',4096)
    if not runtime_scope(cwd,identity,server.config):raise Refused('Managed source creation proof required')
    from amplifier_foundation.session.shared_state import SharedSessionStore
    handle=SharedSessionStore(cwd,identity).acquire(app='amplifier-acp-managed-clone',pid=os.getpid())
    try:
        from .session_lifecycle import guard_copy_source
        guard_copy_source(server,identity)
        return _perform(server,params)
    finally:handle.release()

async def dispatch(server,params):
    if params.get('operation')=='models':
        from .native.managed_models import resolve
        return await resolve(server.config,params)
    if params.get('operation')=='defaults':
        if set(params)-{'operation','bundle','bundleRevision'}:raise Refused('Managed defaults accepts only a configured alias and revision')
        selected=bundle(server.config,params)
        from .native.draft_defaults import resolve_defaults
        result=await resolve_defaults(app_home(server.config),str(Path(server.config['home']).resolve()),selected,global_only=True)
        result.pop('bundle',None)
        if len(encoded(result))>262144 or len(result.get('providers',[]))>64:raise Refused('Managed provider metadata exceeds its bound')
        return {**result,'bundleSelection':({'kind':'configured','id':params['bundle'],'revision':params['bundleRevision']} if 'bundle' in params else {'kind':'engine-default'}),'configurationScope':'global-and-session','runtimeStarted':False}
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
