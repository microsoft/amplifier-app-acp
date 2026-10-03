"""Bounded source evidence: recorded generations and an actual resident interpreter.

No command launches a worker, resolves a Git branch, imports a provider, or infers
that filesystem metadata proves the bytes already executing in another process.
"""
from __future__ import annotations
import base64,hashlib,importlib.metadata,json,os,platform,sys,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

MAX_RECORDS=5000
MAX_RECEIPT=8*1024*1024

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def safe_url(value):
    if not isinstance(value,str):return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https','ssh','git','file'}:return None
        host=parsed.hostname or ''
        if parsed.port:host+=':'+str(parsed.port)
        return urlunsplit((parsed.scheme,host,parsed.path,'',''))[:4096]
    except ValueError:return None

def public_distribution(row):
    value={'name':str(row.get('name',''))[:200],'version':str(row.get('version',''))[:200]}
    direct=row.get('directUrl') or {};vcs=direct.get('vcs_info') or {};cached=row.get('cacheSource') or {}
    source={'kind':'git' if vcs.get('vcs')=='git' or cached else 'local' if direct.get('url','').startswith('file:') else 'registry','evidence':'installed-distribution-metadata'}
    url=safe_url(cached.get('url') or direct.get('url'))
    if url:source['url']=url
    for key,raw in [('revision',cached.get('revision') or vcs.get('commit_id')),('requestedRef',cached.get('ref') or vcs.get('requested_revision')),('subdirectory',cached.get('subdirectory') or direct.get('subdirectory'))]:
        if isinstance(raw,str):source[key]=raw[:512]
    if cached:source['dirty']=bool(cached.get('dirty'))
    if 'dir_info' in direct:source['editable']=bool(direct['dir_info'].get('editable'))
    return {**value,'source':source}

def validate_args(args):
    if not isinstance(args,dict):raise ValueError('Runtime inspection arguments must be an object')
    allowed={'cursor','limit','expectedRevision','refresh','surface'}
    if set(args)-allowed:raise ValueError('Unexpected runtime inspection argument')
    limit=args.get('limit',50)
    if type(limit)is not int or not 1<=limit<=100:raise ValueError('Inspection page must contain1–100 rows')
    if 'refresh' in args and type(args['refresh'])is not bool:raise ValueError('refresh must be boolean')
    surface=args.get('surface','distributions')
    if surface not in {'distributions','mounted'}:raise ValueError('Select installed distributions or mounted implementations')
    cursor=args.get('cursor')
    if cursor is not None and (not isinstance(cursor,str) or len(cursor)>2048):raise ValueError('Invalid inspection cursor')
    if args.get('expectedRevision') is not None and not isinstance(args['expectedRevision'],str):raise ValueError('Inspection revision must be a string')

def page(snapshot,args):
    validate_args(args)
    limit=args.get('limit',50);surface=args.get('surface','distributions')
    revision=snapshot['revision'];offset=0;cursor=args.get('cursor')
    if args.get('expectedRevision') not in {None,revision}:raise ValueError('Inspection revision changed; request a new first page')
    if cursor is not None:
        if not isinstance(cursor,str) or len(cursor)>2048:raise ValueError('Invalid inspection cursor')
        try:version,recorded,recorded_surface,offset=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
        except (ValueError,TypeError):raise ValueError('Invalid inspection cursor') from None
        if version!=1 or recorded!=revision or recorded_surface!=surface or type(offset)is not int or offset<0:raise ValueError('Inspection cursor belongs to another snapshot')
    items=snapshot.get('mounts',[]) if surface=='mounted' else snapshot['items'];next_offset=offset+limit
    next_cursor=base64.urlsafe_b64encode(json.dumps([1,revision,surface,next_offset],separators=(',',':')).encode()).decode().rstrip('=') if next_offset<len(items) else None
    result={**{k:v for k,v in snapshot.items() if k not in {'items','mounts'}},'surface':surface,'items':items[offset:next_offset],'nextCursor':next_cursor,'total':len(items)}
    if len(json.dumps(result).encode())>700*1024:raise ValueError('Inspection page exceeds700KiB; reduce its limit')
    return result

def read_receipt(path,default=None):
    path=Path(path)
    if any(item.is_symlink() for item in (path,*path.parents)):raise ValueError('Inspection receipt cannot traverse symbolic links')
    if not path.exists():return default
    if path.stat().st_size>MAX_RECEIPT:raise ValueError('Inspection receipt exceeds8MiB')
    return json.loads(path.read_text())

def current(home,args,state=None):
    from .generations import identity
    from .native import runtime_environment as env
    from .native.updates import active_release
    if set(args)-{'generation','cursor','limit','expectedRevision'}:raise ValueError('Unexpected generation inspection argument')
    pointer=active_release(home);generation=args.get('generation') or pointer.get('current')
    policy=env.manifest_path().read_bytes()
    if len(policy)>1024*1024:raise ValueError('Runtime policy exceeds1MiB')
    base={'version':1,'scope':'native-runtime','source':'qualified-generation-receipts','available':bool(generation),'generation':generation,'pointer':pointer,'networkContacted':False,'workerStarted':False,'verification':{'installedGraph':'not_checked','loadedCode':'not_checked','latestSources':'last_explicit_check_only'},'policy':{'manifestSha256':hashlib.sha256(policy).hexdigest()},'lastCheck':{k:(state or {}).get(k) for k in ('checkId','lastCheck','checkedGeneration','available')}}
    if not generation:return page({**base,'revision':digest(base),'items':[]},{k:v for k,v in args.items() if k!='generation'})
    identity(generation);directory=env.receipt_directory(home,generation)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Selected native generation is unavailable')
    graph=read_receipt(directory/'runtime-installed.json')
    if not isinstance(graph,list) or len(graph)>MAX_RECORDS:raise ValueError('Qualified generation has no bounded installed graph receipt')
    if any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in graph):raise ValueError('Invalid installed graph receipt')
    baseline=directory/'runtime-base.toml'
    if baseline.is_symlink():raise ValueError('Runtime policy receipt cannot be linked')
    baseline_hash=hashlib.sha256(baseline.read_bytes()).hexdigest() if baseline.is_file() and baseline.stat().st_size<=1024*1024 else None
    base['policy'].update(recordedBaselineSha256=baseline_hash,matchesRecordedBaseline=baseline_hash==base['policy']['manifestSha256'] if baseline_hash else None)
    validation=read_receipt(directory/'validated.json',{})
    base['qualification']={'recorded':validation.get('generationSchema')==1,'installedReceiptSha256':digest(graph),'profileReceiptPresent':(directory/'profiles-qualified.json').is_file()}
    items=sorted([public_distribution(row) for row in graph],key=lambda row:row['name'])
    return page({**base,'revision':digest({'base':base,'items':items}),'items':items},{k:v for k,v in args.items() if k!='generation'})

def mounted(coordinator):
    rows=[]
    def append(kind,name,value):
        chain=[];seen=set()
        for _ in range(6):
            if value is None or id(value) in seen:break
            seen.add(id(value));typ=type(value);module=sys.modules.get(typ.__module__)
            chain.append({'class':typ.__qualname__[:200],'module':typ.__module__[:300],'loadedFrom':str(getattr(module,'__file__',''))[:4096]})
            fields=vars(value) if hasattr(value,'__dict__') else {}
            value=next((fields[key] for key in ('previous','original','inner') if key in fields),None)
        rows.append({'kind':kind,'id':str(name)[:200],'implementations':chain})
    for kind in ('providers','tools'):
        values=coordinator.get(kind) or {}
        if not isinstance(values,dict):raise ValueError('Unrecognized mounted module registry')
        if len(values)>1000:raise ValueError('Mounted module registry exceeds inspection bound')
        for name,value in values.items():append(kind,name,value)
    for kind in ('context','orchestrator'):append(kind,kind,coordinator.get(kind))
    return rows

def interpreter(mounts,instance,module_source_overrides=None):
    rows=[]
    for dist in importlib.metadata.distributions():
        direct=dist.read_text('direct_url.json') or '{}'
        if len(direct.encode())>65536:raise ValueError('Installed source metadata exceeds64KiB')
        name=dist.metadata.get('Name') or ''
        rows.append(public_distribution({'name':name,'version':dist.version,'directUrl':json.loads(direct)}))
        if len(rows)>MAX_RECORDS:raise ValueError('Interpreter distributions exceed inspection bound')
    rows.sort(key=lambda row:row['name'])
    value={'version':1,'scope':'resident-native-worker','available':True,'resident':True,'instanceId':instance,'capturedAt':time.time(),'generation':os.environ.get('AMPLIFIER_UNIFIED_RELEASE') or None,'python':{'executable':sys.executable,'version':platform.python_version(),'prefix':sys.prefix},'mounts':mounts,'mountedCount':len(mounts),'coverage':{'installedDistributions':True,'selectedRootMounts':True,'hooksRegistration':'not_inspected','delegatedSessions':'not_inspected','loadedCodeBytesVerified':False},'items':rows}
    if len(json.dumps(value).encode())>MAX_RECEIPT:raise ValueError('Runtime inspection exceeds8MiB')
    value['moduleSourceOverrides'] = module_source_overrides or {}
    return {**value,'revision':digest(value)}

async def resident(session,args):
    validate_args(args)
    worker=session.worker if session else None
    if not worker or worker.closed or not worker.process or worker.process.returncode is not None or session.worker_retirement_pending or session.retiring or session.retired:
        return {'version':1,'available':False,'resident':False,'workerStarted':False,'coverage':'this-acp-connection','reason':'Original resident runtime unavailable on this connection'}
    return await worker.command('runtime.inspect',arguments=args)
