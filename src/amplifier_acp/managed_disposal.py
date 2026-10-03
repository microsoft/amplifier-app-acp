"""Reviewed removal of one proved host allocation; native historical authority stays."""
from __future__ import annotations
import hashlib,json,os,sqlite3,stat,time,uuid
from pathlib import Path
from contextlib import contextmanager
from filelock import FileLock
from . import history,managed_session as managed
from .maintenance_gate import checked
from .lifecycle_family import own_descendants

MAX_ENTRIES=10000
MAX_BYTES=256*1024*1024
MAX_MANIFEST=4*1024*1024
class Refused(ValueError):
    data={'executed':False,'replayed':False,'reason':'managed-files-refused'}

def stamp(info):return {'dev':info.st_dev,'ino':info.st_ino,'mode':info.st_mode,'bytes':info.st_size,'modified':info.st_mtime_ns,'changed':info.st_ctime_ns}

@contextmanager
def open_directory(path):
    """Open every component without following links, retaining descriptor authority."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise Refused('Absolute canonical directory required')
    fd=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            next_fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        yield fd
    finally:os.close(fd)

def file_hash(path,*,dir_fd=None,expected=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dir_fd)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:raise Refused('Only private regular files may be reviewed')
        if expected is not None and stamp(before)!=expected:raise Refused('Managed file changed during review')
        digest=hashlib.sha256();size=0
        while data:=os.read(fd,1024*1024):
            size+=len(data)
            if size>MAX_BYTES:raise Refused('Managed file exceeds disposal byte budget')
            digest.update(data)
        if stamp(before)!=stamp(os.fstat(fd)):raise Refused('Managed file changed during review')
        return digest.hexdigest()
    finally:os.close(fd)

def inventory(root):
    rows=[];total=0;deadline=time.monotonic()+30
    def visit(fd,relative,depth):
        nonlocal total
        if depth>64 or len(rows)>=MAX_ENTRIES or time.monotonic()>deadline:raise Refused('Managed tree exceeds explicit disposal scan budget')
        before=stamp(os.fstat(fd));rows.append({'path':relative,'kind':'directory','stamp':before})
        with os.scandir(fd) as iterator:
            names=[]
            for entry in iterator:
                if len(names)+len(rows)>=MAX_ENTRIES:raise Refused('Managed tree exceeds explicit disposal entry budget')
                names.append(entry.name)
        for name in sorted(names):
            if len(rows)>=MAX_ENTRIES or time.monotonic()>deadline:raise Refused('Managed tree exceeds explicit disposal scan budget')
            info=os.stat(name,dir_fd=fd,follow_symlinks=False);child=name if not relative else relative+'/'+name
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):raise Refused('Managed tree contains a link or unsupported file type; nothing removed')
            if name in {'events.jsonl','transcript.jsonl','metadata.json'}:raise Refused('History-shaped files inside allocation require separate review; nothing removed')
            if stat.S_ISDIR(info.st_mode):
                nested=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    if stamp(os.fstat(nested))!=stamp(info):raise Refused('Managed directory changed during review')
                    visit(nested,child,depth+1)
                finally:os.close(nested)
            else:
                total+=info.st_size
                if total>MAX_BYTES:raise Refused('Managed tree exceeds disposal byte budget')
                rows.append({'path':child,'kind':'file','stamp':stamp(info),'sha256':file_hash(name,dir_fd=fd,expected=stamp(info))})
        if stamp(os.fstat(fd))!=before:raise Refused('Managed directory changed during review')
    with open_directory(root) as fd:visit(fd,'',0)
    if len(managed.encoded(rows))>MAX_MANIFEST:raise Refused('Managed manifest exceeds its persisted bound')
    return {'entries':rows,'entryCount':len(rows),'bytes':total,'treeHash':managed.digest(rows)}

def remove_reviewed(target,entries):
    """No path traversal through a swapped ancestor; no recursive wildcard removal.

    This is not a freeze of arbitrary same-account external filesystem writers.
    Every observed mismatch stops with the remainder preserved as unknown.
    """
    rows={entry['path']:entry for entry in entries};children={}
    for entry in entries[1:]:children.setdefault(entry['path'].rsplit('/',1)[0] if '/' in entry['path'] else '',[]).append(entry)
    def remove(parent,name,relative):
        entry=rows[relative];info=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if entry['kind']=='file':
            if stamp(info)!=entry['stamp'] or file_hash(name,dir_fd=parent,expected=entry['stamp'])!=entry['sha256']:raise OSError('Managed staged file changed; remaining files preserved')
            os.unlink(name,dir_fd=parent);os.fsync(parent);return
        if not stat.S_ISDIR(info.st_mode) or (info.st_ino,info.st_dev)!=(entry['stamp']['ino'],entry['stamp']['dev']):raise OSError('Managed staged directory changed; remaining files preserved')
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            if (os.fstat(fd).st_ino,os.fstat(fd).st_dev)!=(info.st_ino,info.st_dev):raise OSError('Managed staged directory changed')
            for child in children.get(relative,[]):remove(fd,Path(child['path']).name,child['path'])
            os.fsync(fd)
            current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (current.st_ino,current.st_dev)!=(info.st_ino,info.st_dev):raise OSError('Managed staged directory moved; remaining files preserved')
            os.rmdir(name,dir_fd=parent);os.fsync(parent)
        finally:os.close(fd)
    with open_directory(target.parent) as parent:remove(parent,target.name,'')

def root_for(config,cwd,identity):
    path=Path(cwd)
    if not path.is_absolute() or str(path)!=str(path.resolve()) or path.name!='files' or path.parent.name!=identity or path.parent.parent.name!='chats':raise Refused('Exact original managed allocation required')
    root=checked(path.parent.parent.parent)
    if not any(checked(Path(p)).resolve()==root for p in config.get('managedSessionRoots',[])):raise Refused('Managed root is not configured')
    return root

def supported():
    from amplifier_foundation.session import SharedSessionStore
    return callable(getattr(SharedSessionStore,'identity',None)) and callable(getattr(SharedSessionStore,'from_identity',None))

def historical_store(config,identity,cwd,*,source_identity=None):
    """Reopen only identities retained by the exact native disposal authority."""
    from amplifier_foundation.session import SharedSessionStore
    source=source_identity or identity
    proof=historical_proof(config,source,cwd)
    if not proof or not supported():raise Refused('Foundation retained historical ownership is unavailable')
    fence=managed.read(managed.proof_path(source,config).with_name('managed-files-fence.json'))
    retained=fence.get('sharedIdentities',{}).get(identity)
    if retained!={'version':1,'workspace':cwd,'sessionId':identity}:raise Refused('Historical family identity was not retained by the disposal owner')
    return SharedSessionStore.from_identity(retained)

def historical_proof(config,identity,cwd):
    """The only exception for missing cwd: exact retired native creation authority."""
    root_for(config,cwd,identity)
    original=managed.read(managed.proof_path(identity,config));fence_path=managed.proof_path(identity,config).with_name('managed-files-fence.json')
    if not fence_path.exists():return None
    fence=managed.read(fence_path)
    marker=original.get('marker',{})
    if (set(marker)!={'version','id','workspace','creationReceipt','owner','allocationHash'} or marker.get('version')!=1 or marker.get('id')!=identity or marker.get('workspace')!=cwd or marker.get('owner')!='amplifier-unified' or marker.get('allocationHash')!=managed.digest({k:v for k,v in marker.items() if k!='allocationHash'})
        or original.get('status')!='completed' or original.get('request',{}).get('cwd')!=cwd or original.get('request',{}).get('sessionId')!=identity or original.get('result',{}).get('historyCwd')!=cwd or original.get('result',{}).get('sessionId')!=identity or original.get('result',{}).get('location')!=managed.location(marker)
        or fence.get('operation')!='dispose-owned-files' or fence.get('creationRecordHash')!=managed.digest(original) or fence.get('sessionId')!=identity or fence.get('historyCwd')!=cwd or fence.get('allocationHash')!=marker['allocationHash'] or fence.get('state') not in {'unknown','completed'}
        or fence.get('sharedIdentities',{}).get(identity)!={'version':1,'workspace':cwd,'sessionId':identity}):raise Refused('Disposed managed identity lacks its native authoritative proof')
    return original

@contextmanager
def db(owner):
    path=checked(owner.app/'managed-files.sqlite3')
    for item in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
        checked(item)
        if item.exists() and (not item.is_file() or item.stat().st_nlink!=1):raise Refused('Disposal journal must be a private regular file')
    connection=sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA synchronous=FULL')
    connection.executescript('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL);')
    try:
        with connection:yield connection
    finally:connection.close()

def receipt(owner,command):
    managed.bounded(command,'command ID',200)
    path=checked(owner.app/'managed-files.sqlite3')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_nlink!=1:raise Refused('Disposal receipt must be a private regular file')
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:row=connection.execute('SELECT body FROM commands WHERE id=?',(command,)).fetchone()
    finally:connection.close()
    if not row:return None
    value=json.loads(row[0]);fence=owner.app/'managed-files-fence.json'
    if value['status']=='unknown' and fence.exists():
        proof=managed.read(fence)
        if proof.get('state')=='completed' and proof.get('commandId')==command and proof.get('reviewHash')==value.get('reviewHash'):
            historical_proof(owner.server.config,owner.identity,owner.cwd)
            result=proof.get('result',{})
            if (result.get('commandId')!=command or result.get('sessionId')!=owner.identity or result.get('historyCwd')!=owner.cwd or result.get('reviewHash')!=proof['reviewHash'] or result.get('allocationHash')!=proof['allocationHash'] or result.get('preservesCanonical') is not True or result.get('canonicalFilesDeleted')!=0 or result.get('eventsDeleted')!=0 or result.get('directoryRemoved') is not True):raise Refused('Completed disposal evidence differs from the original identity')
            return {**value,'status':'completed','result':result}
    return value

def save(owner,command,signature,value):
    with db(owner) as connection:connection.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',(command,signature,json.dumps(value)))

def perform(owner,operation,args):
    if operation=='managedFiles.dispose':
        from .maintenance_gate import Gate,native_home
        # Whole native workers and ordinary ACP requests hold this same gate
        # shared. Do not upgrade a shared descriptor or kill an active owner.
        with Gate(owner.server.config,exclusive=True):
            if (native_home(owner.server.config)/'.amplifier-acp-restore-pending.json').exists():raise Refused('An unresolved native restore protects managed files')
            return _perform(owner,operation,args)
    return _perform(owner,operation,args)

def _perform(owner,operation,args):
    allowed={'managedFiles.preview':set(),'managedFiles.dispose':{'commandId','reviewId','reviewHash','expectedHistoryRevision','descendants'},'managedFiles.receipt':{'commandId'}}
    if operation not in allowed or not isinstance(args,dict) or set(args)-allowed[operation]:raise Refused('Exact managed-files operation required')
    if not supported():raise Refused('Foundation retained historical ownership is unavailable')
    owner.app.mkdir(parents=True,exist_ok=True,mode=0o700)
    if operation=='managedFiles.receipt':return {'receipt':receipt(owner,args.get('commandId'))}
    with FileLock(str(checked(owner.app/'lifecycle.lock')),timeout=0):
        if operation=='managedFiles.dispose':
            command=managed.bounded(args.get('commandId'),'command ID',200);signature=managed.digest(args)
            with db(owner) as connection:prior=connection.execute('SELECT signature FROM commands WHERE id=?',(command,)).fetchone()
            if prior:
                if prior[0]!=signature:raise Refused('Managed disposal command differs from its original request')
                saved=receipt(owner,command);return {**saved.get('result',{}),'receipt':saved,'replayed':False}
        with owner.own():
            owned=managed.proof(owner.identity,owner.cwd,owner.server.config)
            if not owned:raise Refused('Managed creation proof required; cwd never grants deletion authority')
            root=root_for(owner.server.config,owner.cwd,owner.identity);allocation=Path(owner.cwd).parent
            if owner.directory.is_relative_to(allocation) or owner.app.is_relative_to(allocation):raise Refused('Canonical native authority cannot be contained in disposed files')
            inspected=owner.inspect()
            if inspected['location']['blocked']:raise Refused('Managed execution ownership is unresolved')
            if operation=='managedFiles.preview':
                manifest=inventory(allocation);review_id=str(uuid.uuid4());expires=time.time()+600
                body={'reviewId':review_id,'expiresAt':expires,'sessionId':owner.identity,'historyCwd':owner.cwd,'historyRevision':inspected['historyRevision'],'allocationHash':owned['marker']['allocationHash'],**manifest}
                review_hash=managed.digest(body);body['reviewHash']=review_hash
                with db(owner) as connection:
                    connection.execute("DELETE FROM reviews WHERE json_extract(body,'$.expiresAt')<?",(time.time(),))
                    if connection.execute('SELECT count(*) FROM reviews').fetchone()[0]>=100:raise Refused('Managed review capacity reached')
                    connection.execute('INSERT INTO reviews VALUES(?,?)',(review_id,json.dumps(body)))
                return {k:body[k] for k in ('reviewId','reviewHash','expiresAt','sessionId','historyCwd','historyRevision','allocationHash','treeHash','entryCount','bytes')}|{'allocationId':owner.identity,'scope':'owned-allocation-files','preservesCanonical':True,'canonicalFilesDeleted':0,'eventsDeleted':0,'scanBudget':{'maxEntries':MAX_ENTRIES,'maxBytes':MAX_BYTES,'maxSeconds':30},'manifestComplete':True}
            with db(owner) as connection:row=connection.execute('SELECT body FROM reviews WHERE id=?',(args.get('reviewId'),)).fetchone()
            review=json.loads(row[0]) if row else None
            if not review or review['expiresAt']<=time.time() or review['reviewHash']!=args.get('reviewHash') or review['historyRevision']!=args.get('expectedHistoryRevision') or inspected['historyRevision']!=review['historyRevision'] or review['allocationHash']!=owned['marker']['allocationHash']:raise Refused('Managed files review expired or changed; nothing removed')
            from .session_lifecycle import guard_copy_source
            guard_copy_source(owner.server,owner.identity)
            from amplifier_foundation.session import SharedSessionStore
            retained={}
            def retain(sid,cwd):retained[sid]=SharedSessionStore(cwd,sid).identity()
            from .managed_family import verify
            try:family_evidence=verify(owner,args.get('descendants',[]))
            except (OSError,ValueError) as error:raise Refused(str(error)) from None
            with own_descendants(owner,args.get('descendants',[]),check=retain) as family:
                if family['count']!=len(args.get('descendants',[])):raise Refused('Native family differs from product-reviewed descendants; refresh the catalog before disposal')
                try:current_family=verify(owner,args.get('descendants',[]))
                except (OSError,ValueError) as error:raise Refused(str(error)) from None
                if current_family!=family_evidence:raise Refused('Canonical managed family changed while acquiring native ownership')
                family={**family,'selectedProject':family_evidence}
                current=inventory(allocation)
                if current['treeHash']!=review['treeHash']:raise Refused('Managed files changed since review; nothing removed')
                value={'commandId':command,'operation':'managedFiles.dispose','status':'unknown','reviewHash':review['reviewHash'],'preservesCanonical':True,'replayed':False}
                save(owner,command,signature,value)
                fence={'commandId':command,'state':'unknown','operation':'dispose-owned-files','creationRecordHash':managed.digest(owned),'sharedIdentities':retained,'sessionId':owner.identity,'historyCwd':owner.cwd,'allocationHash':review['allocationHash'],'reviewHash':review['reviewHash']}
                try:
                    managed.write(owner.app/'managed-files-fence.json',fence)
                    staging=root/'.disposals';staging.mkdir(mode=0o700,exist_ok=True);checked(staging)
                    if not staging.is_dir():raise RuntimeError('Managed disposal staging is not a directory')
                    target=staging/managed.digest([owner.identity,command])
                    if target.exists():raise RuntimeError('Managed disposal stage already exists; inspect without replay')
                    os.rename(allocation,target)
                    for parent in (allocation.parent,staging):
                        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
                        try:os.fsync(fd)
                        finally:os.close(fd)
                    remove_reviewed(target,review['entries'])
                    result={'sessionId':owner.identity,'historyCwd':owner.cwd,'commandId':command,'reviewId':review['reviewId'],'reviewHash':review['reviewHash'],'allocationId':owner.identity,'allocationHash':review['allocationHash'],'treeHash':review['treeHash'],'entryCount':review['entryCount'],'bytes':review['bytes'],'directoryRemoved':True,'preservesCanonical':True,'canonicalFilesDeleted':0,'eventsDeleted':0,'historyRevision':review['historyRevision'],'familyGuard':family}
                    managed.write(owner.app/'lifecycle.json',{'sessionId':owner.identity,'historyCwd':owner.cwd,'deleted':True,'commandId':command,'updatedAt':time.time(),'managedFilesDisposed':True})
                    managed.write(owner.app/'managed-files-fence.json',{**fence,'state':'completed','result':result})
                    value={**value,'status':'completed','result':result};save(owner,command,signature,value)
                    return {**result,'receipt':value}
                except Refused as error:raise RuntimeError('Managed file effect is uncertain; preserve receipt and remaining stage') from error

async def dispatch(owner,operation,args):
    import asyncio
    task=asyncio.create_task(asyncio.to_thread(perform,owner,operation,args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except BaseException:break
        raise
