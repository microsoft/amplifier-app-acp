"""Private native archives, reviewed restore and reversible configuration reset.

Only explicitly enabled full-native maintenance performs a cold global inventory.
No arbitrary output path, worker mount, transcript rewrite, credential presentation
or inferred cross-owner atomicity lives here.
"""
from __future__ import annotations
import asyncio,base64,hashlib,json,os,sqlite3,stat,tarfile,time,uuid
from contextlib import contextmanager
from pathlib import Path
from filelock import FileLock
from .maintenance_gate import Gate,checked,native_home
from .session_lifecycle import regular
from .configuration_clone import fingerprint,location
from .relocation import write

OPERATIONS={'maintenance.preview','maintenance.acquire','maintenance.snapshot',
 'maintenance.release','maintenance.lease','maintenance.receipt','maintenance.artifact.read','maintenance.artifact.inspect',
 'maintenance.reset.preview','maintenance.reset.apply','maintenance.reset.restore','maintenance.reset.reconcile',
 'maintenance.archive.create','maintenance.archive.add','maintenance.archive.prepare','maintenance.archive.manifest',
 'maintenance.restore.preview','maintenance.restore.manifest','maintenance.restore.apply','maintenance.restore.reconcile','maintenance.restore.inspect'}
CACHE_OPERATIONS={'maintenance.cache.scan','maintenance.cache.page','maintenance.cache.preview','maintenance.cache.clear'}
CAPTURE_OPERATIONS={'maintenance.capture.acquire','maintenance.capture.inspect','maintenance.capture.release'}
from .app_reset import FIELDS as APP_RESET_FIELDS
from .bundle_reference_repair import FIELDS as BUNDLE_REFERENCE_FIELDS
from .workspace_cleanup import FIELDS as WORKSPACE_CLEANUP_FIELDS
from .backup_cleanup import FIELDS as BACKUP_CLEANUP_FIELDS
OPERATIONS |= CACHE_OPERATIONS | CAPTURE_OPERATIONS | set(APP_RESET_FIELDS) | set(BUNDLE_REFERENCE_FIELDS) | set(WORKSPACE_CLEANUP_FIELDS) | set(BACKUP_CLEANUP_FIELDS)
PARTS={'session-history','session-state','native-settings'}
CHUNK=256*1024
MAX_FILES=2048
CONFIG_FILES=('configuration.json','effective-configuration.json','control-state.json')
RESET_KEYS={'selection','configurator','mode','budget'}
OMISSIONS=['unselected and delegated session histories','independent product and client stores',
 'workspace files and artifacts outside selected native directories','module source, caches and runtime generations',
 'external credential stores, environment variables and authentication sessions',
 'Foundation live writer leases and transfer authority','shared routing and bundle files outside selected directories']

def token(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Exact native maintenance hash required')
    return value

def identity(value):
    if not isinstance(value,str) or len(value)!=32 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Opaque native maintenance artifact or lease identity required')
    return value

def release_all(held):
    errors=[]
    for item in reversed(held):
        try:item.release()
        except Exception as error:errors.append(type(error).__name__)
    if errors:raise ValueError('Native maintenance lease cleanup failed after attempting every release; close the private admin process and inspect ownership before continuing')


def file_stamp(path):
    info=checked(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Snapshot files must be regular files without hard links')
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


class Maintenance:
    def __init__(self,admin):
        self.admin=admin;self.config=admin.config;self.home=checked(native_home(self.config))
        self.app=checked(Path(self.config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve())
        self.root=checked(self.app/'maintenance');self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=asyncio.Lock();self.lease=None;self.timer=None;self.closing=False
        self.maximum=self.config.get('maintenanceMaxBytes',256*1024*1024)
        limit=1024**4 if self.config.get('maintenanceFullNative') is True else 4*1024**3
        if type(self.maximum)is not int or not 1<=self.maximum<=limit:raise ValueError('maintenanceMaxBytes exceeds the launcher-authorized disk archive limit')
        with self.db(write=False):pass

    @contextmanager
    def db(self,write=True,*,namespace=None):
        from .journal_schema import connect
        db=connect(self.root/'receipts.sqlite3','maintenance',write=write,namespace=namespace)
        if db is None:yield None;return
        try:
            with db:yield db
        finally:db.close()

    def receipt(self,workspace,command):
        if not isinstance(command,str) or command.startswith('native-admin-admission:') or not 1<=len(command)<=200:raise ValueError('Bounded original commandId required')
        with self.db(write=False) as db:row=db.execute('SELECT workspace,signature,body FROM commands WHERE id=?',(command,)).fetchone() if db else None
        if row is None:return None
        if row[0]!=workspace:raise ValueError('Maintenance receipt belongs to another authorized workspace')
        result=json.loads(row[2])
        if result['state']=='running':result={**result,'state':'unknown','replayed':False}
        return row[1],result

    def claim(self,workspace,signature,row):
        if row['commandId'].startswith('native-admin-admission:'):raise ValueError('Reserved native admission receipt namespace')
        with self.db() as db:
            return db.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,?)',(row['commandId'],workspace,signature,json.dumps(row))).rowcount==1

    def save(self,workspace,signature,row):
        if row['commandId'].startswith('native-admin-admission:'):raise ValueError('Reserved native admission receipt namespace')
        with self.db() as db:db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?,?)',(row['commandId'],workspace,signature,json.dumps(row)))

    def spec(self,args,*,reset=False):
        allowed={'sessions','parts','includeCredentials','credentialsReviewed','privateContentReviewed'}
        if reset:allowed={'nativeSessionId','cwd','scope','privateContentReviewed'}
        if set(args)-allowed:raise ValueError('Unsupported maintenance preview fields')
        if args.get('privateContentReviewed') is not True:raise ValueError('Review the private native content before creating a snapshot or reset preview')
        if reset:
            if args.get('scope')!='session-configuration':raise ValueError('Only selected noncanonical session-configuration reset is supported')
            sessions=[{'nativeSessionId':args.get('nativeSessionId'),'cwd':args.get('cwd')}];parts=['session-state']
        else:sessions=args.get('sessions',[]);parts=args.get('parts')
        if not isinstance(parts,list) or not parts or len(parts)!=len(set(parts)) or set(parts)-PARTS:raise ValueError('Choose explicit native snapshot parts')
        if not isinstance(sessions,list) or len(sessions)>32:raise ValueError('At most 32 explicit native sessions may be selected')
        if any(part.startswith('session-') for part in parts) and not sessions:raise ValueError('Select explicit native sessions for session content')
        if sessions and self.config.get('maintenanceExternalWriters') not in {'foundation-cooperative','stopped'}:
            raise ValueError('Selected history requires launcher-verified Foundation cooperative writers or stopped external writers; old noncooperative CLI writers are unprotected')
        selected=[];seen=set()
        from .native.session_files import validate_id,project_slug
        for row in sessions:
            if not isinstance(row,dict) or set(row)!={'nativeSessionId','cwd'}:raise ValueError('Session selection requires nativeSessionId and cwd only')
            sid=validate_id(row['nativeSessionId']);cwd=self.admin.workspace(row['cwd'])
            if sid in seen:raise ValueError('Duplicate native session identity')
            seen.add(sid);directory=checked(self.home/'projects'/project_slug(cwd)/'sessions'/sid)
            if not directory.is_dir():raise ValueError('Selected native history does not exist; discovery is never implicit')
            metadata=json.loads(regular(directory/'metadata.json'))
            if metadata.get('session_id',sid)!=sid:raise ValueError('Native metadata identity disagrees with selected session')
            selected.append({'nativeSessionId':sid,'cwd':cwd})
        include=args.get('includeCredentials',False)
        if type(include)is not bool or type(args.get('credentialsReviewed',False))is not bool:raise ValueError('Credential review flags must be booleans')
        if include and ('native-settings' not in parts or args.get('credentialsReviewed') is not True):raise ValueError('Private keys.env inclusion requires explicit credential review and native-settings selection')
        return {'sessions':sorted(selected,key=lambda r:r['nativeSessionId']),'parts':sorted(parts),'includeCredentials':include,
                'credentialsReviewed':args.get('credentialsReviewed') is True,'privateContentReviewed':True,
                'externalWriterPolicy':self.config.get('maintenanceExternalWriters','unverified'),**({'resetScope':'session-configuration'} if reset else {})}

    def roots(self,spec):
        from .native.session_files import project_slug
        for row in spec['sessions']:
            sid=row['nativeSessionId']
            if 'session-history' in spec['parts']:
                yield 'history/'+sid,checked(self.home/'projects'/project_slug(row['cwd'])/'sessions'/sid),True
                if 'session-state' not in spec['parts']:yield 'history-revisions/'+sid,checked(location(self.app,sid)/'history-revisions'),True
            if 'session-state' in spec['parts']:yield 'state/'+sid,checked(location(self.app,sid)),True
        if 'native-settings' in spec['parts']:
            yield 'settings/shared/settings.yaml',checked(self.home/'settings.yaml'),False
            yield 'settings/native/admin-settings.yaml',checked(self.app/'admin-settings.yaml'),False
            if spec['includeCredentials']:yield 'settings/shared/keys.env',checked(self.home/'keys.env'),False

    def inventory(self,spec,check=lambda:None):
        rows=[];excluded=[];missing=[];total=0
        for label,root,directory in self.roots(spec):
            check()
            if not root.exists():missing.append(label);continue
            pending=[(label,root)]
            while pending:
                name,path=pending.pop();check();checked(path)
                if path.is_dir():
                    # Only the explicitly selected session directory, never home/projects.
                    with os.scandir(path) as scan:
                        children=[]
                        for entry in scan:
                            if len(children)+len(rows)+len(pending)>=MAX_FILES:raise ValueError('Selected native file inventory exceeds 2048 entries; choose fewer sessions')
                            children.append((name+'/'+entry.name,Path(entry.path)))
                    pending.extend(sorted(children,reverse=True));continue
                if path.name.endswith(('.lock','-shm')):
                    excluded.append({'path':name,'reason':'transient-lock-or-sqlite-shared-memory'});continue
                before=file_stamp(path);total+=before[2]
                if total>self.maximum:raise ValueError('Selected native files exceed the explicit snapshot byte limit; no files changed')
                digest=hashlib.sha256()
                fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
                try:
                    with os.fdopen(fd,'rb',closefd=False) as stream:
                        while chunk:=stream.read(CHUNK):check();digest.update(chunk)
                finally:os.close(fd)
                if file_stamp(path)!=before:raise ValueError('Native file changed during preview; quiesce its writer and preview again')
                rows.append({'path':name,'bytes':before[2],'sha256':digest.hexdigest(),'stamp':before})
        result={'files':sorted(rows,key=lambda r:r['path']),'missing':sorted(missing),'excluded':excluded,'bytes':total}
        if len(json.dumps(result).encode())>512*1024:raise ValueError('Selected native inclusion manifest exceeds 512 KiB')
        return result

    def archives(self):
        if not hasattr(self,'archive_plans'):
            from .archive_plan import ArchivePlans
            self.archive_plans=ArchivePlans(self)
        return self.archive_plans

    def restores(self):
        if not hasattr(self,'restore_plans'):
            from .native_restore import Restores
            self.restore_plans=Restores(self)
        return self.restore_plans

    def capture(self):
        if not hasattr(self,'capture_scope'):
            from .capture_scope import Capture
            self.capture_scope=Capture(self)
        return self.capture_scope

    def archive_gate(self,exclusive=False):
        if hasattr(self,'capture_scope') and self.capture_scope.active():
            from .capture_scope import BorrowedGate
            return BorrowedGate(self.capture_scope)
        return Gate(self.config,exclusive=exclusive)

    def caches(self):
        if not hasattr(self,'cache_plans'):
            from .cache_maintenance import Caches
            self.cache_plans=Caches(self)
        return self.cache_plans

    def ensure_open(self):
        if self.closing:raise ValueError('Native maintenance connection is closing; work was not replayed')

    def preview(self,workspace,args,reset=False):
        spec=self.spec(args,reset=reset)
        if reset:self.reset_safe(spec)
        with Gate(self.config):inventory=self.inventory(spec,self.ensure_open)
        body={'version':1,'workspace':workspace,'spec':spec,'inventory':inventory,'omissions':OMISSIONS,
              'coverage':'explicit-selected-native-only','containsPrivateContent':True,
              'credentialCoverage':'keys.env included by explicit review' if spec['includeCredentials'] else 'keys.env excluded; inline secrets in selected history or configuration may remain'}
        digest=fingerprint(body);write(self.root/'previews'/(digest+'.json'),body)
        return {'previewHash':digest,**body,**({'reset':{'scope':'session-configuration','removedFiles':list(CONFIG_FILES[:2]),'clearedControlKeys':sorted(RESET_KEYS),'canonicalFilesChanged':0,'preserved':['goal','task','task and capacity receipts','canonical history/events','workspace','bundle identity','execution ownership and transfer fences']}} if reset else {})}

    def reviewed(self,workspace,digest):
        digest=token(digest);path=self.root/'previews'/(digest+'.json');body=json.loads(regular(path,1024*1024))
        if body.get('workspace')!=workspace or fingerprint(body)!=digest:raise ValueError('Maintenance preview identity or workspace changed')
        if 'archivePlanId' in body:self.archives().reviewed(workspace,body)
        # Reapply launcher authority, even when a preview was created by another process.
        for row in body['spec']['sessions']:self.admin.workspace(row['cwd'])
        return body

    def valid(self,lease_id=None):
        row=self.lease
        if self.closing or row is None or row['released'] or time.monotonic()>=row['deadline'] or lease_id is not None and row['leaseId']!=lease_id:
            raise ValueError('Native maintenance lease is absent, expired or released; no execution replay is permitted')
        return row

    def begin_snapshot(self,lease_id):
        # Idle leases remain short. Once a full archive has been admitted while
        # still valid, its joined operation owns the gate for the explicit full
        # operation budget. The expiry coroutine waits on self.lock, so neither
        # deadline nor caller cancellation releases a running file thread.
        row=self.valid(lease_id)
        if 'fullNative' in row['body']:
            seconds=self.full_operation_seconds()
            row['deadline']=time.monotonic()+seconds;row['expiresAt']=time.time()+seconds

    def full_operation_seconds(self):
        seconds=self.config.get('maintenanceFullTimeoutSeconds',600)
        if type(seconds)is not int or not 1<=seconds<=7200:raise ValueError('Full native operation deadline must be1–7200seconds')
        return seconds

    def lease_view(self):
        row=self.lease
        if row is None:return {'active':False}
        return {'active':not row['released'] and not self.closing and time.monotonic()<row['deadline'],
                'leaseId':row['leaseId'],'previewHash':row['previewHash'],'expiresAt':row['expiresAt'],'coverage':'explicit-selected-native-only'}

    def acquire(self,workspace,args):
        if self.lease and not self.lease['released']:raise ValueError('This connection already holds a native maintenance lease')
        ttl=args.get('ttlSeconds',60)
        if type(ttl)is not int or not 1<=ttl<=300:raise ValueError('Maintenance lease duration must be 1–300 seconds')
        body=self.reviewed(workspace,args.get('previewHash'))
        if 'archivePlanId' in body:return self.archives().acquire(workspace,args,body,ttl)
        spec=body['spec'];held=[]
        deadline=time.monotonic()+ttl;expires=time.time()+ttl
        def check():
            self.ensure_open()
            if time.monotonic()>=deadline:raise ValueError('Native maintenance acquisition expired before its reviewed writer lease was ready')
        try:
            held.append(Gate(self.config,exclusive=True))
            from amplifier_foundation.session.shared_state import SharedSessionStore
            for row in spec['sessions']:
                held.append(SharedSessionStore(row['cwd'],row['nativeSessionId']).acquire(app='amplifier-acp-maintenance',pid=os.getpid()))
            if 'native-settings' in spec['parts']:
                for _,path,is_directory in self.roots(spec):
                    if is_directory:continue
                    checked(path.parent).mkdir(parents=True,exist_ok=True,mode=0o700)
                    lock=FileLock(str(checked(Path(str(path)+'.lock'))),timeout=0,thread_local=False);lock.acquire();held.append(lock)
            if self.inventory(spec,check)!=body['inventory']:raise ValueError('Selected files changed after review; release and preview again')
            self.lease={'leaseId':uuid.uuid4().hex,'previewHash':args['previewHash'],'workspace':workspace,'body':body,'held':held,'released':False,'deadline':deadline,'expiresAt':expires}
            return self.lease_view()
        except BaseException:
            release_all(held)
            raise

    def release(self,lease_id=None):
        row=self.lease
        if not row:return {'released':False,'active':False}
        if lease_id is not None and row['leaseId']!=lease_id:raise ValueError('Lease identity does not match this connection')
        if not row['released']:
            row['released']=True
            release_all(row['held'])
        return {'released':True,**self.lease_view()}

    async def expire(self,lease_id,seconds):
        await asyncio.sleep(seconds)
        async with self.lock:
            if self.lease and self.lease['leaseId']==lease_id:await self.run(self.release,lease_id)

    def paths(self,spec):
        roots=list(self.roots(spec))
        def resolve(name):
            for label,path,is_directory in roots:
                if name==label:return checked(path)
                if is_directory and name.startswith(label+'/'):return checked(path/name[len(label)+1:])
            raise ValueError('Snapshot file escaped its selected source root')
        return resolve

    def snapshot(self,workspace,args):
        lease=self.valid(identity(args.get('leaseId')))
        if lease['workspace']!=workspace or args.get('previewHash')!=lease['previewHash']:raise ValueError('Snapshot requires the exact reviewed lease')
        body=lease['body']
        if 'archivePlanId' in body:return self.archives().snapshot(workspace,args,lease)
        check=lambda:self.valid(lease['leaseId'])
        if self.inventory(body['spec'],check)!=body['inventory']:raise ValueError('Selected source changed while fenced; snapshot refused')
        artifact=uuid.uuid4().hex;directory=checked(self.root/'artifacts'/artifact);directory.mkdir(parents=True,mode=0o700)
        partial=directory/'snapshot.partial.tar';target=directory/'snapshot.tar';resolve=self.paths(body['spec'])
        manifest={**body,'capturedAt':time.time(),'previewHash':lease['previewHash'],'writerProtection':{'nativeHome':'exclusive-os-gate','selectedSessions':'foundation-v1-exclusive-writer-leases','externalPolicy':body['spec']['externalWriterPolicy'],'settings':'Foundation settings file locks'}}
        write(directory/'manifest.json',manifest)
        class Guarded:
            def __init__(self,stream):self.stream=stream;self.digest=hashlib.sha256()
            def read(self,size=-1):
                check();data=self.stream.read(min(CHUNK,size) if size>=0 else CHUNK);self.digest.update(data);return data
        with tarfile.open(partial,'w') as archive:
            os.chmod(partial,0o600)
            archive.add(directory/'manifest.json',arcname='manifest.json',recursive=False)
            for row in body['inventory']['files']:
                check();path=resolve(row['path'])
                if file_stamp(path)!=row['stamp']:raise ValueError('Selected file changed before capture')
                fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
                try:
                    with os.fdopen(fd,'rb',closefd=False) as stream:
                        reader=Guarded(stream);info=tarfile.TarInfo(row['path']);info.size=row['bytes'];info.mode=0o600;archive.addfile(info,reader)
                    if reader.digest.hexdigest()!=row['sha256'] or file_stamp(path)!=row['stamp']:raise ValueError('Selected file changed during capture')
                finally:os.close(fd)
        if self.inventory(body['spec'],check)!=body['inventory']:raise ValueError('Selected source changed during capture')
        check()
        with partial.open('rb') as stream:os.fsync(stream.fileno())
        os.replace(partial,target)
        fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        digest=hashlib.sha256()
        with target.open('rb') as stream:
            while chunk:=stream.read(CHUNK):check();digest.update(chunk)
        description={'artifactId':artifact,'sha256':digest.hexdigest(),'bytes':target.stat().st_size,'contentType':'application/x-tar','workspace':workspace,'previewHash':lease['previewHash'],'coverage':'explicit-selected-native-only','manifest':manifest}
        write(directory/'description.json',description)
        return description

    def artifact(self,workspace,args):
        artifact=identity(args.get('artifactId'));directory=checked(self.root/'artifacts'/artifact)
        description=json.loads(regular(directory/'description.json',1024*1024))
        if description['workspace']!=workspace or args.get('sha256')!=description['sha256']:raise ValueError('Artifact authority or exact hash does not match')
        offset=args.get('offset',0);maximum=args.get('maxBytes',CHUNK)
        if type(offset)is not int or not 0<=offset<=description['bytes'] or type(maximum)is not int or not 1<=maximum<=CHUNK:raise ValueError('Bounded artifact chunk required')
        path=checked(directory/'snapshot.tar')
        if file_stamp(path)[2]!=description['bytes']:raise ValueError('Private artifact size changed')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            with os.fdopen(fd,'rb',closefd=False) as stream:stream.seek(offset);data=stream.read(maximum)
        finally:os.close(fd)
        return {'artifactId':artifact,'sha256':description['sha256'],'bytes':description['bytes'],'offset':offset,'data':base64.b64encode(data).decode(),'encoding':'base64','chunkSha256':hashlib.sha256(data).hexdigest(),'nextOffset':offset+len(data) if offset+len(data)<description['bytes'] else None}

    def reset_safe(self,spec):
        if len(spec['sessions'])!=1:raise ValueError('Reset selects exactly one native session')
        row=spec['sessions'][0];app=location(self.app,row['nativeSessionId'])
        for name in ('configuration-transaction.json','bundle-transition.json','history-edit-pending.json','maintenance-reset-pending.json'):
            if (app/name).exists():raise ValueError('Resolve the pending native transaction before selected reset')
        if (app/'execution-relocation.json').exists():
            value=json.loads(regular(app/'execution-relocation.json'))
            if value.get('status')!='applied':raise ValueError('Resolve uncertain relocation before selected reset')

    def config_files(self,spec):
        app=location(self.app,spec['sessions'][0]['nativeSessionId'])
        return app,{name:(regular(app/name,8*1024*1024) if (app/name).exists() else None) for name in CONFIG_FILES}

    def config_hash(self,files):return fingerprint({name:hashlib.sha256(raw).hexdigest() if raw is not None else None for name,raw in files.items()})

    def retain(self,directory,files):
        directory.mkdir(parents=True,exist_ok=False,mode=0o700)
        for name,raw in files.items():
            if raw is not None:
                from .transfer import binary
                binary(directory/name,raw)
        write(directory/'index.json',{'files':{name:hashlib.sha256(raw).hexdigest() if raw is not None else None for name,raw in files.items()}})

    def install(self,app,files,check):
        from .transfer import binary
        for name,raw in files.items():
            check()
            if raw is None:(app/name).unlink(missing_ok=True)
            else:binary(app/name,raw)

    def reset(self,workspace,args,restore=False):
        lease=self.valid(identity(args.get('leaseId')));spec=lease['body']['spec'];check=lambda:self.valid(lease['leaseId'])
        if lease['workspace']!=workspace:raise ValueError('Reset workspace disagrees with exclusive lease')
        if spec.get('resetScope')!='session-configuration' or args.get('previewHash')!=lease['previewHash']:raise ValueError('Selected reset requires its exact reviewed reset preview')
        self.reset_safe(spec)
        if self.inventory(spec,check)!=lease['body']['inventory']:raise ValueError('Selected state changed after reset review')
        app,before=self.config_files(spec);retained=uuid.uuid4().hex;directory=checked(self.root/'retained'/retained)
        if restore:
            prior=self.receipt(workspace,args.get('resetCommandId'))
            if prior is None or prior[1]['operation']!='maintenance.reset.apply' or prior[1]['state']!='succeeded':raise ValueError('Restore requires a successfully completed exact reset receipt')
            result=prior[1]['result']
            if result['nativeSessionId']!=spec['sessions'][0]['nativeSessionId'] or result['postResetHash']!=args.get('expectedPostResetHash') or self.config_hash(before)!=result['postResetHash']:raise ValueError('Configuration changed after reset; automatic restore would overwrite newer work')
            original=checked(self.root/'retained'/identity(result['retainedId']));index=json.loads(regular(original/'index.json'))['files']
            if set(index)!=set(CONFIG_FILES):raise ValueError('Retained configuration index is invalid')
            after={name:regular(original/name,8*1024*1024) if digest is not None else None for name,digest in index.items()}
            if any(raw is not None and hashlib.sha256(raw).hexdigest()!=index[name] for name,raw in after.items()):raise ValueError('Retained original configuration changed')
        else:
            state=json.loads(before['control-state.json']) if before['control-state.json'] is not None else None
            if state is not None and not isinstance(state,dict):raise ValueError('Selected control state is malformed; preserved unchanged')
            after={**before,'configuration.json':None,'effective-configuration.json':None,
                   'control-state.json':json.dumps({k:v for k,v in state.items() if k not in RESET_KEYS},ensure_ascii=False).encode() if state is not None else None}
        self.retain(directory,before);check()
        marker=app/'maintenance-reset-pending.json'
        write(marker,{'commandId':args['commandId'],'retainedId':retained,'scope':'session-configuration'})
        self.install(app,after,check);check()
        return {'nativeSessionId':spec['sessions'][0]['nativeSessionId'],'retainedId':retained,'postResetHash':self.config_hash(after),'canonicalFilesChanged':0,'restored':restore,'scope':'session-configuration','automaticResume':False}

    def finalize_reset(self,result,command):
        app=location(self.app,result['nativeSessionId']);marker=app/'maintenance-reset-pending.json'
        value=json.loads(regular(marker))
        if value.get('commandId')!=command:raise ValueError('Reset fence identity changed; originals retained')
        marker.unlink()
        fd=os.open(app,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def reconcile_reset(self,workspace,args):
        lease=self.valid(identity(args.get('leaseId')));spec=lease['body']['spec']
        if lease['workspace']!=workspace or args.get('previewHash')!=lease['previewHash'] or len(spec['sessions'])!=1:raise ValueError('Reconciliation requires one exact reviewed session lease')
        previous=self.receipt(workspace,args.get('resetCommandId'))
        if previous is None or previous[1]['operation'] not in {'maintenance.reset.apply','maintenance.reset.restore'} or previous[1]['state']!='succeeded':raise ValueError('Only a conclusively completed reset may clear its leftover fence; unknown writes remain fenced without replay')
        result=previous[1]['result']
        if result['nativeSessionId']!=spec['sessions'][0]['nativeSessionId']:raise ValueError('Completed reset belongs to another selected session')
        app,current=self.config_files(spec)
        if self.config_hash(current)!=result['postResetHash']:raise ValueError('Completed reset configuration changed; fence retained')
        if (app/'maintenance-reset-pending.json').exists():self.finalize_reset(result,args['resetCommandId'])
        return {'reconciled':True,'resetCommandId':args['resetCommandId'],'nativeSessionId':result['nativeSessionId'],'executedAgain':False}

    async def run(self,function,*args):
        # Cancellation must not release writer fences while a file thread is
        # still changing state. Abort at its next bounded chunk, then join it.
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.closing=True
            try:await task
            except BaseException:pass
            raise

    async def perform(self,workspace,operation,args):
        fields={'maintenance.preview':None,'maintenance.reset.preview':None,'maintenance.acquire':{'commandId','previewHash','ttlSeconds'},
          'maintenance.snapshot':{'commandId','leaseId','previewHash'},'maintenance.release':{'leaseId'},'maintenance.lease':set(),
          'maintenance.receipt':{'commandId'},'maintenance.artifact.read':{'artifactId','sha256','offset','maxBytes'},'maintenance.artifact.inspect':{'artifactId','sha256'},
          'maintenance.reset.reconcile':{'commandId','leaseId','previewHash','resetCommandId'},'maintenance.reset.apply':{'commandId','leaseId','previewHash'},'maintenance.reset.restore':{'commandId','leaseId','previewHash','resetCommandId','expectedPostResetHash'}}
        fields.update({'maintenance.archive.create':{'commandId','parts','privateContentReviewed','includeCredentials','credentialsReviewed'},'maintenance.archive.add':{'commandId','planId','expectedRevision','sessions','workspaces'},'maintenance.archive.prepare':{'commandId','planId','expectedRevision'},'maintenance.archive.manifest':{'previewHash','cursor','limit'}})
        fields.update({'maintenance.restore.preview':{'artifactId','sha256','destination','privateContentReviewed','credentialsReviewed'},'maintenance.restore.manifest':{'previewHash','cursor','limit'},'maintenance.restore.apply':{'commandId','previewHash'},'maintenance.restore.reconcile':{'commandId','restoreCommandId'},'maintenance.restore.inspect':{'restoreCommandId'}})
        from .cache_maintenance import FIELDS as CACHE_FIELDS
        fields.update(CACHE_FIELDS)
        fields.update(APP_RESET_FIELDS)
        fields.update(BUNDLE_REFERENCE_FIELDS)
        fields.update(WORKSPACE_CLEANUP_FIELDS)
        fields.update(BACKUP_CLEANUP_FIELDS)
        from .capture_scope import FIELDS as CAPTURE_FIELDS
        fields.update(CAPTURE_FIELDS)
        if fields[operation] is not None and set(args)-fields[operation]:raise ValueError('Unsupported maintenance operation fields')
        async with self.lock:
            if operation in BACKUP_CLEANUP_FIELDS and not hasattr(self,'backups'):
                from .backup_cleanup import Backups
                self.backups=Backups(self)
            if operation in {'maintenance.backups.inspect','maintenance.backups.preview'}:return await self.run(getattr(self.backups,operation.rsplit('.',1)[1]),workspace,args)
            if operation in WORKSPACE_CLEANUP_FIELDS and not hasattr(self,'workspace_cleanup'):
                from .workspace_cleanup import WorkspaceCleanup
                self.workspace_cleanup=WorkspaceCleanup(self)
            if operation=='maintenance.workspace.preview':return await self.run(self.workspace_cleanup.preview,workspace,args)
            if operation in BUNDLE_REFERENCE_FIELDS and not hasattr(self,'bundle_references'):
                from .bundle_reference_repair import BundleReferences
                self.bundle_references=BundleReferences(self)
            if operation in {'maintenance.bundleReferences.preview','maintenance.bundleReferences.page'}:
                return await self.run(getattr(self.bundle_references,operation.rsplit('.',1)[1]),workspace,args)
            if operation in APP_RESET_FIELDS and not hasattr(self,'app_reset'):
                from .app_reset import AppReset
                self.app_reset=AppReset(self)
            if operation=='maintenance.appReset.inspect':return await self.run(self.app_reset.perform,workspace,operation,args)
            if operation in {'maintenance.preview','maintenance.reset.preview'}:return await self.run(self.preview,workspace,args,operation.endswith('reset.preview'))
            if operation=='maintenance.receipt':
                row=await self.run(self.receipt,workspace,args.get('commandId'));return {'receipt':row[1] if row else None}
            if operation=='maintenance.artifact.read':return await self.run(self.artifact,workspace,args)
            if operation=='maintenance.artifact.inspect':
                from .full_native_archive import inspect_artifact
                return await self.run(inspect_artifact,self,workspace,args)
            if operation=='maintenance.lease':return self.lease_view()
            if operation=='maintenance.capture.inspect':return self.capture().inspect(workspace,args)
            if operation in {'maintenance.cache.page','maintenance.cache.preview'}:return await self.run(getattr(self.caches(),operation.rsplit('.',1)[1]),workspace,args)
            if operation=='maintenance.archive.manifest':return await self.run(self.archives().page,workspace,args)
            if operation in {'maintenance.restore.preview','maintenance.restore.manifest'}:return await self.run(self.restores().preview if operation.endswith('.preview') else self.restores().page,workspace,args)
            if operation=='maintenance.restore.inspect':return await self.run(self.restores().inspect,workspace,args)
            if operation=='maintenance.release':return await self.run(self.release,identity(args.get('leaseId')))
            command=args.get('commandId');previous=await self.run(self.receipt,workspace,command);signature=fingerprint({'workspace':workspace,'operation':operation,'args':args})
            if previous:
                if previous[0]!=signature:raise ValueError('Maintenance command identity conflicts with its exact saved request')
                result={'receipt':previous[1],**previous[1].get('result',{}),'replayed':False}
                if operation=='maintenance.acquire':result['active']=self.lease_view().get('active',False) and self.lease_view().get('leaseId')==result.get('leaseId')
                if operation=='maintenance.capture.acquire':result['active']=self.capture().inspect(workspace,{'captureId':result.get('captureId','0'*32),'nonce':args.get('nonce')}).get('active',False)
                if previous[1]['state']=='refused':result.update(accepted=False,executed=False)
                return result
            receipt={'commandId':command,'operation':operation,'state':'running','createdAt':time.time(),'replayed':False}
            if not await self.run(self.claim,workspace,signature,receipt):
                # Another private admin process owns this exact admission. Do not
                # overwrite its receipt or repeat its file effects.
                prior=await self.run(self.receipt,workspace,command)
                if prior is None or prior[0]!=signature:raise ValueError('Maintenance command identity conflicts with its exact saved request')
                result={'receipt':prior[1],**prior[1].get('result',{}),'replayed':False}
                if operation=='maintenance.acquire':result['active']=self.lease_view().get('active',False) and self.lease_view().get('leaseId')==result.get('leaseId')
                if prior[1]['state']=='refused':result.update(accepted=False,executed=False)
                return result
            try:
                if operation=='maintenance.backups.remove':result=await self.run(self.backups.remove,workspace,args)
                elif operation=='maintenance.workspace.remove':result=await self.run(self.workspace_cleanup.remove,workspace,args)
                elif operation=='maintenance.bundleReferences.apply':result=await self.run(self.bundle_references.apply,workspace,args)
                elif operation in APP_RESET_FIELDS:result=await self.run(self.app_reset.perform,workspace,operation,args)
                elif operation=='maintenance.acquire':
                    result=await self.run(self.acquire,workspace,args)
                    self.timer=asyncio.create_task(self.expire(result['leaseId'],max(0,result['expiresAt']-time.time())))
                elif operation in CAPTURE_OPERATIONS:result=await self.run(getattr(self.capture(),operation.rsplit('.',1)[1]),workspace,args)
                elif operation in CACHE_OPERATIONS:result=await self.run(getattr(self.caches(),operation.rsplit('.',1)[1]),workspace,args)
                elif operation.startswith('maintenance.archive.'):
                    method={'maintenance.archive.create':'create','maintenance.archive.add':'add','maintenance.archive.prepare':'prepare'}[operation]
                    result=await self.run(getattr(self.archives(),method),workspace,args)
                elif operation=='maintenance.restore.apply':result=await self.run(self.restores().apply,workspace,args)
                elif operation=='maintenance.restore.reconcile':result=await self.run(self.restores().reconcile,workspace,args)
                elif operation=='maintenance.snapshot':
                    self.begin_snapshot(args.get('leaseId'))
                    result=await self.run(self.snapshot,workspace,args)
                elif operation=='maintenance.reset.reconcile':result=await self.run(self.reconcile_reset,workspace,args)
                else:result=await self.run(self.reset,workspace,args,operation=='maintenance.reset.restore')
                receipt={**receipt,'state':'succeeded','result':result,'settledAt':time.time()}
            except BaseException as error:
                archive_failure=operation.startswith('maintenance.archive.') and isinstance(error,Exception)
                known=(operation=='maintenance.acquire' and isinstance(error,Exception) and (self.lease is None or self.lease['released'])) or ((operation.startswith('maintenance.restore.') or operation in CACHE_OPERATIONS | CAPTURE_OPERATIONS | set(APP_RESET_FIELDS) | set(BUNDLE_REFERENCE_FIELDS) | set(WORKSPACE_CLEANUP_FIELDS) | set(BACKUP_CLEANUP_FIELDS)) and getattr(error,'data',{}).get('executed') is False)
                receipt={**receipt,'state':'refused' if known else 'failed' if archive_failure else 'unknown','errorType':type(error).__name__,'settledAt':time.time(),**({'executed':False} if known else {}),**({'settled':True,'canonicalFilesChanged':0} if archive_failure else {})}
                await self.run(self.save,workspace,signature,receipt)
                if archive_failure:
                    failed=ValueError(str(error));failed.data={'reason':'native-archive-plan-failed','settled':True,'canonicalFilesChanged':0,'commandId':command,'replayed':False}
                    raise failed from None
                if known:
                    refused=ValueError(str(error));refused.data={**getattr(error,'data',{}),'reason':getattr(error,'data',{}).get('reason','native-maintenance-refused'),'executed':False,'replayed':False}
                    raise refused from None
                raise
            await self.run(self.save,workspace,signature,receipt)
            if operation in {'maintenance.reset.apply','maintenance.reset.restore'}:await self.run(self.finalize_reset,result,command)
            if operation=='maintenance.restore.apply':await self.run(self.restores().finalize,result,command)
            return {**result,'receipt':receipt}

    async def close(self):
        self.closing=True
        if self.timer:self.timer.cancel()
        async with self.lock:
            await self.run(self.release)
            if hasattr(self,'capture_scope'):self.capture_scope.close()
