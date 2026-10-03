"""Explicit, disk-backed native archive plans; no global discovery or restore."""
from __future__ import annotations
import hashlib,json,os,sqlite3,stat,tarfile,time,uuid
from contextlib import contextmanager
from pathlib import Path
from .maintenance import CHUNK,file_stamp,identity,release_all,token
from .maintenance_gate import Gate,checked
from .configuration_clone import fingerprint,location
from .relocation import write
from .session_lifecycle import regular

OPERATIONS={'maintenance.archive.create','maintenance.archive.add','maintenance.archive.prepare','maintenance.archive.manifest'}
PARTS={'session-history','session-state','shared-configuration','workspace-configuration','native-import-records','native-preference-receipts','native-maintenance-records','native-retained-archives'}
OMISSIONS=['unselected native and delegated histories','independent product and client stores','workspace files outside selected .amplifier configuration','module source, caches and runtime generations','external credential stores, environment variables and authentication sessions','Foundation live writer leases and transfer authority','unknown shared-home entries outside the documented native configuration roots']
EXCLUSIONS=['transient .lock files and SQLite -shm files']

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()

def digest_rows(rows):
    digest=hashlib.sha256()
    for row in rows:digest.update(encoded(row)+b'\n')
    return digest.hexdigest()

class ArchivePlans:
    def __init__(self,maintenance):
        self.owner=maintenance;self.root=checked(maintenance.root/'plans');self.root.mkdir(mode=0o700,exist_ok=True)
        self.max_entries=maintenance.config.get('maintenanceMaxManifestEntries',100000)
        if type(self.max_entries)is not int or not 1<=self.max_entries<=1000000:raise ValueError('Manifest entry limit must be1–1000000')
    @contextmanager
    def db(self,plan,create=False,mutate=False):
        directory=checked(self.root/identity(plan));path=checked(directory/'manifest.sqlite3')
        if create:directory.mkdir(mode=0o700)
        elif not path.is_file():raise ValueError('Native archive plan is unavailable')
        db=sqlite3.connect(path if create or mutate else path.as_uri()+'?mode=ro',uri=not(create or mutate),timeout=0)
        if create:os.chmod(path,0o600)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                if create or mutate:db.execute('BEGIN IMMEDIATE')
                if create:
                    db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,body TEXT)')
                    db.execute('CREATE TABLE selections(kind TEXT,id TEXT,cwd TEXT,PRIMARY KEY(kind,id))')
                    db.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,body TEXT)')
                yield db
        finally:db.close()
    def meta(self,db,workspace):
        value=json.loads(db.execute('SELECT body FROM meta WHERE id=1').fetchone()[0])
        if value['workspace']!=workspace:raise ValueError('Archive plan belongs to another authorized workspace')
        return value
    def save(self,db,value):db.execute('INSERT OR REPLACE INTO meta VALUES(1,?)',(json.dumps(value),))
    def create(self,workspace,args):
        parts=args.get('parts')
        if not isinstance(parts,list) or not parts or len(set(parts))!=len(parts) or set(parts)-PARTS:raise ValueError('Choose explicit native archive parts')
        if 'native-retained-archives' in parts and 'native-maintenance-records' not in parts:raise ValueError('Retained archive bodies require their maintenance records selection')
        if args.get('privateContentReviewed') is not True:raise ValueError('Private content review is required')
        include=args.get('includeCredentials',False)
        if type(include)is not bool or type(args.get('credentialsReviewed',False))is not bool:raise ValueError('Credential flags must be booleans')
        if include and (args.get('credentialsReviewed') is not True or 'shared-configuration' not in parts):raise ValueError('keys.env requires explicit credential review and shared configuration')
        plan=uuid.uuid4().hex;value={'planId':plan,'workspace':workspace,'phase':'open','revision':0,'parts':sorted(parts),'includeCredentials':include,'privateContentReviewed':True,'credentialsReviewed':args.get('credentialsReviewed') is True,'externalWriterPolicy':self.owner.config.get('maintenanceExternalWriters','unverified')}
        with self.db(plan,True) as db:self.save(db,value)
        return {'planId':plan,'revision':0,'phase':'open','coverage':'explicit-selected-native-only'}
    def add(self,workspace,args):
        sessions=args.get('sessions',[]);workspaces=args.get('workspaces',[])
        if not isinstance(sessions,list) or len(sessions)>32 or not isinstance(workspaces,list) or len(workspaces)>32 or not sessions and not workspaces:raise ValueError('Append1–32 explicit sessions and/or workspaces per batch')
        with self.db(args.get('planId'),mutate=True) as db:
            value=self.meta(db,workspace)
            if value['phase']!='open' or type(args.get('expectedRevision'))is not int or args['expectedRevision']!=value['revision']:raise ValueError('Archive selection changed or is already sealed')
            selected=self.owner.spec({'sessions':sessions,'parts':['session-history'],'privateContentReviewed':True})['sessions'] if sessions else []
            for row in selected:
                prior=db.execute('SELECT cwd FROM selections WHERE kind=? AND id=?',('session',row['nativeSessionId'])).fetchone()
                if prior and prior[0]!=row['cwd']:raise ValueError('Native identity is already bound to another history directory')
                db.execute('INSERT OR IGNORE INTO selections VALUES(?,?,?)',('session',row['nativeSessionId'],row['cwd']))
            for cwd in workspaces:
                cwd=self.owner.admin.workspace(cwd);key=hashlib.sha256(cwd.encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO selections VALUES(?,?,?)',('workspace',key,cwd))
            if db.execute('SELECT count(*) FROM selections').fetchone()[0]>100000:raise ValueError('Explicit archive selection exceeds100000 entries')
            value['revision']+=1;self.save(db,value)
            return {'planId':value['planId'],'revision':value['revision'],'phase':'open','sessions':db.execute("SELECT count(*) FROM selections WHERE kind='session'").fetchone()[0],'workspaces':db.execute("SELECT count(*) FROM selections WHERE kind='workspace'").fetchone()[0]}
    def roots(self,db,value):
        from .native.session_files import project_slug
        parts=value['parts']
        for kind,key,cwd in db.execute('SELECT kind,id,cwd FROM selections ORDER BY kind,id'):
            self.owner.admin.workspace(cwd)
            if kind=='session':
                if 'session-history' in parts:yield 'history/'+key,checked(self.owner.home/'projects'/project_slug(cwd)/'sessions'/key)
                if 'session-state' in parts:yield 'state/'+key,checked(location(self.owner.app,key))
            elif 'workspace-configuration' in parts:
                for name in ('settings.yaml','settings.local.yaml','routing','routing.local','bundles'):
                    yield 'workspace-configuration/'+key+'/'+name,checked(Path(cwd)/'.amplifier'/name)
        for row in value.get('nativeStores',[]):
            name=row['part']+('.sqlite3' if row['format']=='sqlite-backup-image' else '')
            yield 'native-owned-stores/'+name,checked(self.root/value['planId']/'native-stores'/name)
        if 'shared-configuration' in parts:
            for name in ('settings.yaml','routing','bundles',*(['keys.env'] if value['includeCredentials'] else [])):
                yield 'shared-configuration/'+name,checked(self.owner.home/name)
            yield 'adapter-configuration/admin-settings.yaml',checked(self.owner.app/'admin-settings.yaml')
    def walk(self,label,path,check,depth=0):
        check();checked(path)
        if depth>64:raise ValueError('Native archive directory nesting exceeds64')
        if not path.exists():yield {'path':label,'source':str(path),'status':'missing'};return
        info=path.lstat()
        if stat.S_ISDIR(info.st_mode):
            yield {'path':label,'source':str(path),'status':'directory','identity':[info.st_dev,info.st_ino]}
            with os.scandir(path) as entries:
                for entry in entries:yield from self.walk(label+'/'+entry.name,Path(entry.path),check,depth+1)
            return
        stamp=file_stamp(path)
        if path.name.endswith(('.lock','-shm')):return
        if stamp[2]>self.owner.maximum:raise ValueError('Native archive file exceeds configured byte limit')
        digest=hashlib.sha256();fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        try:
            with os.fdopen(fd,'rb',closefd=False) as stream:
                opened=os.fstat(fd)
                if [opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns]!=stamp:raise ValueError('Native archive file changed during open')
                while chunk:=stream.read(CHUNK):check();digest.update(chunk)
        finally:os.close(fd)
        if file_stamp(path)!=stamp:raise ValueError('Native archive source changed during inspection')
        yield {'path':label,'source':str(path),'status':'included','stamp':stamp,'bytes':stamp[2],'sha256':digest.hexdigest()}
    def rows(self,db):
        for encoded_row, in db.execute('SELECT body FROM entries ORDER BY path'):yield json.loads(encoded_row)
    def summary(self,db,value):
        counts=dict(db.execute('SELECT kind,count(*) FROM selections GROUP BY kind'))
        count=files=missing=total=0
        for row in self.rows(db):
            count+=1;files+=row['status']=='included';missing+=row['status']=='missing';total+=row.get('bytes',0)
        return {'entries':count,'files':files,'missing':missing,'bytes':total,'sessions':counts.get('session',0),'workspaces':counts.get('workspace',0),'manifestHash':digest_rows(self.rows(db)),'selectionHash':digest_rows(list(row) for row in db.execute('SELECT kind,id,cwd FROM selections ORDER BY kind,id'))}
    def prepare(self,workspace,args):
        from . import private_store_snapshot as stores
        with self.db(args.get('planId'),mutate=True) as db:
            value=self.meta(db,workspace)
            if value['phase']!='open' or type(args.get('expectedRevision'))is not int or args.get('expectedRevision')!=value['revision']:raise ValueError('Exact open archive revision required')
            counts=dict(db.execute('SELECT kind,count(*) FROM selections GROUP BY kind'))
            if any(p.startswith('session-') for p in value['parts']) and not counts.get('session'):raise ValueError('Explicit sessions required for native session content')
            if 'workspace-configuration' in value['parts'] and not counts.get('workspace'):raise ValueError('Explicit workspaces required for workspace configuration')
            if (set(value['parts'])&{'shared-configuration','workspace-configuration'} or counts.get('session',0)>32) and value['externalWriterPolicy']!='stopped':raise ValueError('Configuration trees or more than32 sessions require launcher-verified stopped external writers')
            if set(value['parts'])&stores.PARTS:stores.require_admin(self.owner)
            value['phase']='building';self.save(db,value)
        # Persist building before filesystem inspection. Interrupted seal remains
        # an uncertain immutable plan; a new command must not restart its effects.
        with self.db(args['planId'],mutate=True) as db:
            value=self.meta(db,workspace);count=total=0
            with Gate(self.owner.config,exclusive=bool(set(value['parts'])&stores.PARTS)):
                if set(value['parts'])&stores.PARTS:
                    value['nativeStores']=stores.prepare(self.owner,value['planId'],value['parts'],self.owner.ensure_open,args['commandId']);self.save(db,value)
                for label,path in self.roots(db,value):
                    for row in self.walk(label,path,self.owner.ensure_open):
                        count+=1;total+=row.get('bytes',0)
                        if count>self.max_entries or total>self.owner.maximum:raise ValueError('Explicit native archive exceeds configured entry/byte limits')
                        db.execute('INSERT INTO entries VALUES(?,?)',(row['path'],json.dumps(row)))
            summary=self.summary(db,value)
            body={'version':2,'workspace':workspace,'archivePlanId':value['planId'],'spec':{'sessions':[],'parts':value['parts'],'includeCredentials':value['includeCredentials'],'externalWriterPolicy':value['externalWriterPolicy']},'inventory':summary,'ownerCoverage':stores.coverage(value['parts'],value.get('nativeStores',[])),'omissions':OMISSIONS,'exclusions':EXCLUSIONS,'coverage':'explicit-selected-native-only','containsPrivateContent':True,'authorityHash':fingerprint({'roots':sorted(str(root) for root in self.owner.admin.roots),'home':str(self.owner.home),'app':str(self.owner.app)}),'credentialCoverage':'keys.env included by explicit review' if value['includeCredentials'] else 'keys.env excluded; inline secrets in selected files may remain'}
            preview=fingerprint(body);value.update(phase='sealed',previewHash=preview,summary=summary);self.save(db,value)
            write(self.owner.root/'previews'/(preview+'.json'),body)
        write(self.root/value['planId']/'sealed.json',{'previewHash':preview,'stamp':file_stamp(self.root/value['planId']/'manifest.sqlite3')})
        return {'previewHash':preview,**body}
    def reviewed(self,workspace,body):
        with self.db(body['archivePlanId']) as db:
            value=self.meta(db,workspace)
            if value['phase']!='sealed' or value['previewHash']!=fingerprint(body):raise ValueError('Immutable native manifest changed')
            sealed=json.loads(regular(self.root/value['planId']/'sealed.json'))
            if sealed.get('previewHash')!=value['previewHash'] or sealed.get('stamp')!=file_stamp(self.root/value['planId']/'manifest.sqlite3'):raise ValueError('Immutable manifest file changed')
            if body['authorityHash']!=fingerprint({'roots':sorted(str(root) for root in self.owner.admin.roots),'home':str(self.owner.home),'app':str(self.owner.app)}):raise ValueError('Configured archive authority changed')
            if self.owner.config.get('maintenanceExternalWriters','unverified')!=value['externalWriterPolicy']:raise ValueError('External writer authority changed since review')
            return value
    def page(self,workspace,args):
        body=self.owner.reviewed(workspace,args.get('previewHash'))
        if 'archivePlanId' not in body:raise ValueError('Paged archive plan required')
        cursor=args.get('cursor','');limit=args.get('limit',50)
        if not isinstance(cursor,str) or len(cursor)>8192 or type(limit)is not int or not 1<=limit<=100:raise ValueError('Bounded manifest page required')
        with self.db(body['archivePlanId']) as db:
            rows=list(db.execute('SELECT path,body FROM entries WHERE path>? ORDER BY path LIMIT ?',(cursor,limit+1)))
        items=[{k:v for k,v in json.loads(row[1]).items() if k not in {'source','stamp','identity'}} for row in rows[:limit]]
        if len(encoded(items))>512*1024:raise ValueError('Selected manifest page exceeds transport bound')
        return {'previewHash':args['previewHash'],'items':items,'nextCursor':rows[limit-1][0] if len(rows)>limit else None,'inventory':body['inventory'],'coverage':body['coverage'],'omissions':body['omissions'],'exclusions':body['exclusions'],**({'ownerCoverage':body['ownerCoverage']} if 'ownerCoverage' in body else {})}
    def verify_sources(self,workspace,body,check):
        value=self.reviewed(workspace,body);count=0
        from .private_store_snapshot import verify
        verify(self.owner,value.get('nativeStores',[]))
        with self.db(value['planId']) as db:
            if self.summary(db,value)!=body['inventory']:raise ValueError('Immutable manifest content changed')
            for label,path in self.roots(db,value):
                for row in self.walk(label,path,check):
                    old=db.execute('SELECT body FROM entries WHERE path=?',(row['path'],)).fetchone();count+=1
                    if old is None or json.loads(old[0])!=row:raise ValueError('Native archive selection changed after review')
            if count!=body['inventory']['entries']:raise ValueError('Native archive selection changed after review')
    def acquire(self,workspace,args,body,ttl):
        self.reviewed(workspace,body);held=[];deadline=time.monotonic()+ttl;expires=time.time()+ttl
        def check():
            self.owner.ensure_open()
            if time.monotonic()>=deadline:raise ValueError('Native archive acquisition expired')
        try:
            held.append(Gate(self.owner.config,exclusive=True))
            if body['spec']['externalWriterPolicy']!='stopped':
                from amplifier_foundation.session.shared_state import SharedSessionStore
                with self.db(body['archivePlanId']) as db:
                    for sid,cwd in db.execute("SELECT id,cwd FROM selections WHERE kind='session' ORDER BY id"):
                        check();held.append(SharedSessionStore(cwd,sid).acquire(app='amplifier-acp-archive',pid=os.getpid()))
            self.verify_sources(workspace,body,check)
            self.owner.lease={'leaseId':uuid.uuid4().hex,'previewHash':args['previewHash'],'workspace':workspace,'body':body,'held':held,'released':False,'deadline':deadline,'expiresAt':expires}
            return self.owner.lease_view()
        except BaseException:release_all(held);raise
    def snapshot(self,workspace,args,lease):
        body=lease['body'];check=lambda:self.owner.valid(lease['leaseId']);self.verify_sources(workspace,body,check)
        artifact=uuid.uuid4().hex;directory=checked(self.owner.root/'artifacts'/artifact);directory.mkdir(mode=0o700,parents=True);partial=directory/'snapshot.partial.tar';target=directory/'snapshot.tar'
        summary={**body,'previewHash':lease['previewHash'],'capturedAt':time.time(),'writerProtection':{'nativeHome':'exclusive-os-gate','externalPolicy':body['spec']['externalWriterPolicy'],'selectedSessions':'stopped-external-writers' if body['spec']['externalWriterPolicy']=='stopped' else 'foundation-v1-exclusive-writer-leases'}}
        manifest=directory/'manifest.json'
        with manifest.open('w') as stream:
            os.chmod(manifest,0o600);stream.write('{"summary":'+json.dumps(summary)+',"entries":[');first=True
            with self.db(body['archivePlanId']) as db:
                for row in self.rows(db):
                    check();stream.write(('' if first else ',')+json.dumps({k:v for k,v in row.items() if k not in {'source','stamp','identity'}}));first=False
            stream.write(']}');stream.flush();os.fsync(stream.fileno())
        with tarfile.open(partial,'w') as archive:
            os.chmod(partial,0o600);archive.add(manifest,arcname='manifest.json',recursive=False)
            with self.db(body['archivePlanId']) as db:
                for row in self.rows(db):
                    check()
                    if row['status']!='included':continue
                    path=checked(Path(row['source']))
                    if file_stamp(path)!=row['stamp']:raise ValueError('Native archive file changed before capture')
                    digest=hashlib.sha256()
                    class Reader:
                        def read(self,size=-1):
                            check();chunk=stream.read(min(CHUNK,size) if size>=0 else CHUNK);digest.update(chunk);return chunk
                    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
                    try:
                        with os.fdopen(fd,'rb',closefd=False) as stream:
                            info=tarfile.TarInfo(row['path']);info.size=row['bytes'];info.mode=0o600;archive.addfile(info,Reader())
                        if digest.hexdigest()!=row['sha256'] or file_stamp(path)!=row['stamp']:raise ValueError('Native archive file changed during capture')
                    finally:os.close(fd)
        self.verify_sources(workspace,body,check);check()
        with partial.open('rb') as stream:os.fsync(stream.fileno())
        os.replace(partial,target);fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        digest=hashlib.sha256()
        with target.open('rb') as stream:
            while chunk:=stream.read(CHUNK):check();digest.update(chunk)
        description={'artifactId':artifact,'sha256':digest.hexdigest(),'bytes':target.stat().st_size,'contentType':'application/x-tar','workspace':workspace,'previewHash':lease['previewHash'],'coverage':'explicit-selected-native-only','manifest':summary}
        write(directory/'description.json',description);return description
