"""Remove only proven duplicate files from explicitly retired private app stores.

No discovery of native homes, recursive removal, source replay, or legacy writer
adoption. The launcher must attest that each old non-cooperating writer is retired.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,time,uuid
from contextlib import contextmanager
from pathlib import Path
from filelock import FileLock
from .configuration_clone import fingerprint
from .relocation import home,write
from .session_lifecycle import MAX_HISTORY,canonical,guard_copy_source,record,regular

FIELDS={'legacyCopy.receipt':{'commandId'},'legacyCopy.inventory':set(),'legacyCopy.preview':{'rootId'},'legacyCopy.purge':{'commandId','previewId','previewHash','expectedHistoryRevision'}}
SAFE_FILES={'metadata.json':1024*1024,'transcript.jsonl':MAX_HISTORY,'checkpoint.json':MAX_HISTORY}
IDENTITY=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z')
PRESERVED=['canonical native history','events.jsonl','unique or unrecognized private files','session directory','workspace files']

def directory(path):
    path=Path(path)
    if not path.is_absolute():raise ValueError('Retired copy roots must be absolute')
    for part in (path,*path.parents):
        if part.is_symlink():raise ValueError('Retired copy paths cannot traverse symlinks')
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode):raise ValueError('Retired copy root must be an existing directory')
    return path.resolve(),{'device':info.st_dev,'inode':info.st_ino}

def roots(owner):
    values=owner.server.config.get('retiredSessionCopyRoots',[])
    if not isinstance(values,list) or len(values)>16:raise ValueError('At most16 explicit retired roots may be configured')
    result={}
    protected=[home(owner.server),Path(owner.server.config['home']).resolve(),Path(owner.cwd),owner.directory]
    for value in values:
        if not isinstance(value,dict) or set(value)!={'id','directory','format','writerState'} or not isinstance(value['id'],str) or not IDENTITY.fullmatch(value['id']) or value['format']!='unified-session-store-v1' or value['writerState']!='retired':raise ValueError('Exact retired session-store declaration required')
        if value['id'] in result:raise ValueError('Retired root IDs must be distinct')
        path,stamp=directory(value['directory'])
        if any(path==p or path in p.parents or p in path.parents for p in protected):raise ValueError('Retired roots cannot overlap current native/app/workspace authority')
        if any(path==p[0] or path in p[0].parents or p[0] in path.parents for p in result.values()):raise ValueError('Retired roots cannot overlap one another')
        result[value['id']]=(path,stamp)
    return result

def snapshot(path,maximum):
    info=path.lstat()
    if info.st_nlink!=1:raise ValueError('Reviewed duplicate must not have hard links')
    body=regular(path,maximum);after=path.lstat()
    if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('Reviewed duplicate changed during capture')
    return body,{'device':info.st_dev,'inode':info.st_ino,'bytes':info.st_size,'modifiedNs':info.st_mtime_ns,'changedNs':info.st_ctime_ns,'sha256':hashlib.sha256(body).hexdigest()}

@contextmanager
def selected(owner,root_id):
    configured=roots(owner)
    if root_id not in configured:raise ValueError('Choose an explicitly configured retired root ID')
    root,root_stamp=configured[root_id]
    if not IDENTITY.fullmatch(owner.identity):raise ValueError('Invalid selected session ID')
    selected,stamp=directory(root/owner.identity)
    # Cooperating purge processes serialize here; this is NOT a legacy-writer lease.
    locks=home(owner.server)/'sessions'/owner.identity/'retired-copy-locks';locks.mkdir(parents=True,exist_ok=True,mode=0o700)
    with FileLock(str(locks/(fingerprint(str(root))+'.lock')),timeout=0):
        yield selected,{'root':root_stamp,'directory':stamp,'declaration':fingerprint({'id':root_id,'directory':str(root),'writerState':'retired','format':'unified-session-store-v1'})}

def capture(owner,root_id):
    if root_id not in roots(owner):raise ValueError('Choose an explicitly configured retired root ID')
    guard_copy_source(owner.server,owner.identity)
    inspected=owner.inspect()
    if inspected['location']['blocked']:raise ValueError('Native relocation outcome is unresolved')
    rows,metadata,revision=canonical(owner.directory,inspected['historyRevision'])
    if metadata.get('session_id')!=owner.identity:raise ValueError('Canonical native identity is unavailable')
    native={name:snapshot(owner.directory/name,SAFE_FILES[name]) for name in ('metadata.json','transcript.jsonl')}
    if owner.inspect()['historyRevision']!=revision or json.loads(native['metadata.json'][0])!=metadata:raise ValueError('Canonical copy changed during proof capture')
    with selected(owner,root_id) as (source,identity):
        names=[]
        with os.scandir(source) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names)>256:raise ValueError('Selected retired directory exceeds256 entries; no partial purge review')
        removable=[];retained=[]
        for name in sorted(names):
            if name not in SAFE_FILES:
                retained.append({'name':name[:240],'reason':'event-log-preserved' if name=='events.jsonl' else 'unrecognized-file-preserved'});continue
            try:
                body,stamp=snapshot(source/name,SAFE_FILES[name])
                if name=='checkpoint.json':
                    value=json.loads(body)
                    duplicate=isinstance(value,dict) and set(value)=={'version','messages','metadata'} and value['version']==1 and value['messages']==[row for _,row in rows] and isinstance(value['metadata'],dict) and value['metadata'].get('session_id')==owner.identity and all(key in metadata and metadata[key]==val for key,val in value['metadata'].items())
                else:duplicate=body==native[name][0]
                if not duplicate:retained.append({'name':name,'reason':'complete-canonical-copy-unproven'});continue
                removable.append({'name':name,**stamp})
            except (ValueError,FileNotFoundError,json.JSONDecodeError):
                retained.append({'name':name,'reason':'regular-stable-bounded-copy-unproven'})
        proof={'rootId':root_id,'sessionId':owner.identity,'historyCwd':owner.cwd,'historyRevision':revision,'identity':identity,'canonical':{name:value[1] for name,value in native.items()},'files':removable,'retained':retained}
        return proof

def public_review(value):
    return {key:value[key] for key in ('previewId','previewHash','expiresAt','rootId','sessionId','historyRevision','retained')}|{'files':[{'name':f['name'],'bytes':f['bytes'],'sha256':f['sha256']} for f in value['files']],'removableBytes':sum(f['bytes'] for f in value['files']),'canonicalFilesDeleted':0,'preserved':PRESERVED,'legacyWriterCoverage':'launcher-attested-retired','behavior':'remove-proven-duplicate-files-only'}

def perform(owner,operation,args):
    if operation not in FIELDS or not isinstance(args,dict) or set(args)!=FIELDS[operation] or len(json.dumps(args).encode())>4096:raise ValueError('Exact bounded retired-copy operation required')
    if 'rootId' in args and (not isinstance(args['rootId'],str) or not IDENTITY.fullmatch(args['rootId'])):raise ValueError('Explicit configured retired root ID required')
    if operation=='legacyCopy.receipt':return {'receipt':owner.saved(args.get('commandId'))}
    if operation=='legacyCopy.inventory':return {'roots':[{'id':identity,'format':'unified-session-store-v1','writerState':'retired'} for identity in roots(owner)],'selectedSessionId':owner.identity,'coverage':'configured-retired-roots-only','canonicalFilesRead':False,'limits':{'roots':16,'selectedEntries':256,'fileBytes':MAX_HISTORY,'aggregateReadBytes':4*MAX_HISTORY+2*1024*1024,'expirySeconds':600},'preserved':PRESERVED}
    owner.app.mkdir(parents=True,exist_ok=True,mode=0o700)
    with FileLock(str(owner.app/'lifecycle.lock'),timeout=0):
        if operation=='legacyCopy.purge':
            command=args.get('commandId');previous=owner.saved(command);signature=fingerprint({'operation':operation,'args':args})
            if previous:
                if previous['signature']!=signature:raise ValueError('Retired-copy command conflicts with original request')
                return {**previous.get('result',{}),'receipt':previous,'replayed':False}
        with owner.own():
            if operation=='legacyCopy.preview':
                proof=capture(owner,args.get('rootId'));preview_id=str(uuid.uuid4());expires=time.time()+600
                value={**proof,'previewId':preview_id,'expiresAt':expires};value['previewHash']=fingerprint(value)
                reviews=owner.app/'retired-copy-reviews';reviews.mkdir(exist_ok=True,mode=0o700)
                count=0
                for path in reviews.iterdir():
                    count+=1
                    if count>64:raise ValueError('Selected retired-copy review capacity reached')
                    if path.is_file() and not path.is_symlink() and record(path).get('expiresAt',float('inf'))<time.time():path.unlink()
                write(reviews/(preview_id+'.json'),value);return public_review(value)
            preview_id=args.get('previewId')
            if not isinstance(preview_id,str) or str(uuid.UUID(preview_id))!=preview_id:raise ValueError('Exact retired-copy preview ID required')
            preview=record(owner.app/'retired-copy-reviews'/(preview_id+'.json'))
            if preview['previewHash']!=args.get('previewHash') or preview['expiresAt']<=time.time() or preview['historyRevision']!=args.get('expectedHistoryRevision'):raise ValueError('Retired-copy preview expired or changed')
            with owner.receipts() as db:
                if db.execute("SELECT 1 FROM receipts WHERE json_extract(body,'$.operation')='legacyCopy.purge' AND json_extract(body,'$.status') IN ('running','unknown') LIMIT 1").fetchone():raise ValueError('Prior retired-copy removal is unresolved; retained evidence requires inspection')
            current=capture(owner,preview['rootId'])
            if any(current[key]!=preview[key] for key in current):raise ValueError('Retired or canonical copy changed; review again')
            if not current['files']:raise ValueError('No completely verified duplicate files were reviewed')
            with selected(owner,current['rootId']) as (source,identity):
                if identity!=current['identity']:raise ValueError('Retired root identity changed')
                receipt={'commandId':command,'operation':operation,'status':'running','createdAt':time.time(),'replayed':False}
                owner.save(command,signature,receipt)
                try:
                    removed=remove_reviewed(source,current['files'],command)
                    result={'sessionId':owner.identity,'historyCwd':owner.cwd,'historyRevision':current['historyRevision'],'rootId':current['rootId'],'previewId':preview_id,'previewHash':preview['previewHash'],'removed':removed,'retained':current['retained'],'canonicalFilesDeleted':0,'eventsDeleted':0,'preserved':PRESERVED,'legacyWriterCoverage':'launcher-attested-retired'}
                    receipt={**receipt,'status':'completed','result':result};owner.save(command,signature,receipt)
                    return {**result,'receipt':receipt}
                except BaseException:
                    owner.save(command,signature,{**receipt,'status':'unknown'});raise

def remove_reviewed(source,files,command):
    """Descriptor-relative stage/verify/unlink; uncertainty retains staged evidence."""
    flags=os.O_RDONLY|os.O_DIRECTORY|getattr(os,'O_NOFOLLOW',0)
    parent=os.open(source,flags);stage_fd=None;stage='.retired-copy-'+fingerprint(command)
    try:
        os.mkdir(stage,0o700,dir_fd=parent);stage_fd=os.open(stage,flags,dir_fd=parent);os.fsync(parent)
        removed=[]
        for item in files:
            name=item['name'];os.rename(name,name,src_dir_fd=parent,dst_dir_fd=stage_fd);os.fsync(parent);os.fsync(stage_fd)
            fd=os.open(name,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0),dir_fd=stage_fd)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(item['device'],item['inode'],item['bytes'],item['modifiedNs']):raise ValueError('Staged duplicate changed; it remains retained')
                digest=hashlib.sha256()
                with os.fdopen(fd,'rb',closefd=False) as stream:
                    for block in iter(lambda:stream.read(65536),b''):digest.update(block)
                if digest.hexdigest()!=item['sha256']:raise ValueError('Staged duplicate bytes changed; it remains retained')
            finally:os.close(fd)
            os.unlink(name,dir_fd=stage_fd);os.fsync(stage_fd);removed.append({'name':name,'bytes':item['bytes'],'sha256':item['sha256']})
        os.close(stage_fd);stage_fd=None;os.rmdir(stage,dir_fd=parent);os.fsync(parent);return removed
    finally:
        if stage_fd is not None:os.close(stage_fd)
        os.close(parent)
