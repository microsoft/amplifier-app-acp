"""Reviewed native archive restoration into a new launcher-owned destination.

No overwrite, worker startup, model call or uncertain-command replay. A durable
pending marker fences partial destinations after process loss.
"""
from __future__ import annotations
import hashlib,json,os,re,sqlite3,tarfile,time
from pathlib import Path,PurePosixPath
from .maintenance import CHUNK,file_stamp,identity,token
from .maintenance_gate import checked,Gate
from .private_store_snapshot import require_admin
from .full_native_archive import FORMAT,VERSION
from .history_import import strict_json
from .relocation import write
from .session_lifecycle import regular

OPERATIONS={'maintenance.restore.preview','maintenance.restore.manifest','maintenance.restore.apply','maintenance.restore.reconcile'}
ROOT_IDS={'native-home','app-home','checkpoint','events','registry','sources','bundle','runtimeManifest','workspace-settings'}
PENDING='.amplifier-acp-restore-pending.json'

class Refused(ValueError):
    data={'executed':False,'reason':'native-restore-refused','replayed':False}

def digest(path,check):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(CHUNK):check();result.update(block)
    return result.hexdigest()

def path_name(value,roots):
    if not isinstance(value,str) or len(value)>8192 or '\\' in value or '\x00' in value:raise ValueError('Unsafe native archive path')
    path=PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in {'','..','.'} for p in path.parts) or str(path)!=value or path.parts[0] not in roots:raise ValueError('Native archive path escapes its declared authority root')
    if PENDING in path.parts:raise ValueError('Uncertain restore markers cannot be imported as a completed archive')
    return path

class Restores:
    def __init__(self,owner):self.owner=owner;self.root=checked(owner.root/'restore-plans');self.root.mkdir(mode=0o700,exist_ok=True)
    def target(self,value):
        if self.owner.config.get('maintenanceFullNative') is not True:raise Refused('Full native restore requires explicit trusted launcher enablement')
        roots=self.owner.config.get('maintenanceRestoreRoots',{})
        if not isinstance(roots,dict) or not isinstance(value,dict) or set(value)!={'rootId','name'}:raise Refused('Choose a launcher-authorized new restore destination')
        key=value['rootId'];name=value['name']
        if not isinstance(key,str) or not 1<=len(key)<=100 or key not in roots or not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,99}',name):raise Refused('Invalid new restore destination identity')
        root=checked(Path(roots[key]).expanduser().absolute())
        if not root.is_dir():raise Refused('Restore parent must already exist and be launcher-owned')
        for native in [self.owner.home,self.owner.app]:
            if root==native or native in root.parents:raise Refused('Restore destination cannot be inside an active native authority home')
        return checked(root/name)
    def artifact(self,workspace,args):
        artifact=identity(args.get('artifactId'));sha=token(args.get('sha256'));directory=checked(self.owner.root/'artifacts'/artifact)
        description=strict_json(regular(directory/'description.json',1024*1024))
        if description.get('workspace')!=workspace or description.get('sha256')!=sha or description.get('format')!=FORMAT or description.get('version')!=VERSION:raise Refused('Exact supported native archive identity and authorized workspace required')
        path=checked(directory/'snapshot.tar')
        if type(description.get('bytes'))is not int or not 1<=description['bytes']<=self.owner.maximum+self.owner.archives().max_entries*2048+257*1024*1024:raise Refused('Archive exceeds configured restore envelope budget')
        if file_stamp(path)[2]!=description['bytes']:raise Refused('Native archive size changed')
        return path,description
    def check(self):
        self.owner.ensure_open()
        if time.monotonic()>getattr(self,'deadline',float('inf')):raise ValueError('Native restore deadline exceeded')
    def start_check(self):
        timeout=self.owner.config.get('maintenanceFullTimeoutSeconds',600)
        if type(timeout)is not int or not 1<=timeout<=7200:raise Refused('Restore deadline must be1–7200seconds')
        self.deadline=time.monotonic()+timeout
    def header(self,archive):
        first=archive.next()
        if first is None or first.name!='native-archive.json' or not first.isfile() or not 1<=first.size<=1024*1024:raise ValueError('Native archive header is absent or unsupported')
        header=strict_json(archive.extractfile(first).read())
        if set(header)!={'format','version','nativeAuthority','manifest','previewHash'} or header['format']!=FORMAT or type(header['version'])is not int or header['version']!=VERSION:raise ValueError('Unsupported native archive version')
        authority=header['nativeAuthority']
        if not isinstance(authority,dict) or authority.get('format')!=FORMAT or type(authority.get('version'))is not int or authority.get('version')!=VERSION:raise ValueError('Unsupported native authority descriptor')
        rows=authority.get('roots');roots={}
        if not isinstance(rows,list) or not 2<=len(rows)<=32:raise ValueError('Bounded native authority roots required')
        for row in rows:
            if not isinstance(row,dict) or row.get('id') not in ROOT_IDS or row['id'] in roots or row.get('destination')!=row['id'] or row.get('kind') not in {'file','directory','collection'}:raise ValueError('Unknown or duplicate native authority root')
            if set(row)!={'id','destination','kind','path'}:raise ValueError('Unsupported native root descriptor')
            if row['id']=='workspace-settings':
                if row['kind']!='collection' or row['path'] is not None:raise ValueError('Workspace settings must remain a staged collection')
            elif row['kind']=='collection' or not isinstance(row['path'],str) or len(row['path'])>8192 or not Path(row['path']).is_absolute():raise ValueError('Native source roots require absolute evidence paths')
            roots[row['id']]=row
        if not {'native-home','app-home'}<=roots.keys():raise ValueError('Native and adapter authority roots are required')
        if any(roots[key]['kind']!='directory' for key in ('native-home','app-home')):raise ValueError('Native homes must be directory roots')
        coverage=authority.get('coverage')
        if not isinstance(coverage,dict) or any(type(coverage.get(key))is not bool for key in ('completeNativeBackup','completeProductBackup','credentialsIncluded')) or coverage['completeProductBackup'] is not False:raise ValueError('Native coverage declaration is missing or unsupported')
        source=authority.get('sourceConfiguration',{})
        if not isinstance(source,dict) or set(source)-{'bundle','registryHome','runtimeManifest','generationProfiles'}:raise ValueError('Unsupported native source configuration')
        if any(not isinstance(value,str) or len(value)>8192 for key,value in source.items() if key!='generationProfiles'):raise ValueError('Unsupported native source selector')
        profiles=source.get('generationProfiles',[])
        if not isinstance(profiles,list) or len(profiles)>128 or any(not isinstance(value,str) or len(value)>8192 for value in profiles):raise ValueError('Unsupported generation profile selection')
        return header,roots
    def scan(self,path,manifest,*,expected=None,destination=None):
        """Stream TAR once; only the bounded header and one row are in memory."""
        before=file_stamp(path);members=0;total=0
        timeout=self.owner.config.get('maintenanceFullTimeoutSeconds',600)
        if type(timeout)is not int or not 1<=timeout<=7200:raise ValueError('Restore deadline must be1–7200seconds')
        deadline=time.monotonic()+timeout
        def check():
            self.check()
            if time.monotonic()>deadline:raise ValueError('Native restore verification deadline exceeded')
        with tarfile.open(path,mode='r|',stream=True) as archive:
            header,roots=self.header(archive)
            if expected is not None and header!=expected:raise ValueError('Reviewed native archive header changed')
            entry=archive.next();spec=header['manifest']
            if not isinstance(spec,dict) or set(spec)!={'path','bytes','sha256'} or spec['path']!='native-manifest.sqlite3':raise ValueError('Exact native manifest descriptor required')
            if entry is None or entry.name!=spec['path'] or not entry.isfile() or type(spec['bytes'])is not int or entry.size!=spec['bytes'] or not 1<=entry.size<=256*1024*1024:raise ValueError('Native manifest size or member is invalid')
            token(spec['sha256']);raw_hash=hashlib.sha256();temporary=manifest.with_suffix('.partial')
            with temporary.open('xb') as output:
                os.chmod(temporary,0o600);source=archive.extractfile(entry)
                while block:=source.read(CHUNK):check();output.write(block);raw_hash.update(block)
                output.flush();os.fsync(output.fileno())
            if raw_hash.hexdigest()!=spec['sha256']:raise ValueError('Native manifest digest disagrees with reviewed archive')
            os.replace(temporary,manifest)
            db=sqlite3.connect(manifest.as_uri()+'?mode=ro&immutable=1',uri=True)
            try:
                db.set_progress_handler(lambda:(check(),0)[1],1000)
                db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF');db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,65536)
                schema=db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if schema!=[('table','entries','CREATE TABLE entries(path TEXT PRIMARY KEY,body TEXT NOT NULL)')]:raise ValueError('Unsupported native archive manifest schema')
                if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('Native manifest is corrupt')
                expected_files=0;names=sqlite3.connect(manifest.with_suffix('.seen.sqlite3'))
                try:
                    names.execute('CREATE TABLE seen(path TEXT PRIMARY KEY)')
                    for name,body in db.execute('SELECT path,body FROM entries ORDER BY path'):
                        check();members+=1
                        if members>self.owner.archives().max_entries:raise ValueError('Native archive exceeds restore entry bound')
                        relative=path_name(name,roots);row=strict_json(body)
                        if len(relative.parts)>65:raise ValueError('Native archive nesting exceeds64')
                        if roots[relative.parts[0]]['kind']=='file' and relative.parts[1:]!=('source',):raise ValueError('Invalid native file-root member')
                        if row.get('path')!=name or row.get('status') not in {'included','directory','missing','excluded'}:raise ValueError('Invalid native manifest entry')
                        if row['status'] in {'included','directory'}:
                            for parent in relative.parents:
                                if str(parent)=='.':continue
                                parent_row=db.execute('SELECT body FROM entries WHERE path=?',(str(parent),)).fetchone()
                                if parent_row and strict_json(parent_row[0])['status']!='directory':raise ValueError('Native manifest file conflicts with parent authority')
                        if row['status']=='directory' and destination:checked(destination/str(relative)).mkdir(mode=0o700,parents=True,exist_ok=True)
                        if row['status']=='included':
                            if len(relative.parts)<2:raise ValueError('Native root cannot be a payload file')
                            if type(row.get('bytes'))is not int or row['bytes']<0:raise ValueError('Invalid native file byte count')
                            token(row.get('sha256'));total+=row['bytes'];expected_files+=1
                    if total>self.owner.maximum:raise ValueError('Native restore exceeds configured disk byte budget')
                    received=0
                    while (item:=archive.next()) is not None:
                        check();relative=path_name(item.name,roots)
                        if not item.isfile() or item.pax_headers and any(k in item.pax_headers for k in ('linkpath','GNU.sparse.name')):raise ValueError('Links and special native archive members are unsupported')
                        row=db.execute('SELECT body FROM entries WHERE path=?',(item.name,)).fetchone()
                        if row is None:raise ValueError('Native archive contains an undeclared member')
                        row=strict_json(row[0])
                        if row['status']!='included' or item.size!=row['bytes']:raise ValueError('Native archive member size differs from its manifest')
                        try:names.execute('INSERT INTO seen VALUES(?)',(item.name,))
                        except sqlite3.IntegrityError:raise ValueError('Duplicate native archive member') from None
                        target=checked(destination/str(relative)) if destination else None
                        if target:
                            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);output=target.open('xb');os.chmod(target,0o600)
                        else:output=None
                        try:
                            raw_hash=hashlib.sha256();source=archive.extractfile(item);size=0
                            while block:=source.read(CHUNK):
                                check();size+=len(block);raw_hash.update(block)
                                if output:output.write(block)
                            if size!=row['bytes'] or raw_hash.hexdigest()!=row['sha256']:raise ValueError('Native file digest validation failed')
                            if output:output.flush();os.fsync(output.fileno())
                        finally:
                            if output:output.close()
                        received+=1
                    if received!=expected_files:raise ValueError('Native archive is missing declared members')
                finally:names.close();manifest.with_suffix('.seen.sqlite3').unlink(missing_ok=True)
            finally:db.close()
        if file_stamp(path)!=before:raise ValueError('Native archive changed during verification')
        return header,{'entries':members,'files':received,'bytes':total}
    def preview(self,workspace,args):
        try:return self.preview_checked(workspace,args)
        except Exception as error:
            raise Refused(str(error)) from None
    def preview_checked(self,workspace,args):
        self.start_check()
        if set(args)-{'artifactId','sha256','destination','privateContentReviewed','credentialsReviewed'} or args.get('privateContentReviewed') is not True:raise Refused('Review private content before planning native restore')
        target=self.target(args.get('destination'))
        if target.exists():raise Refused('Native restore requires a new absent destination; existing data is never overwritten')
        path,description=self.artifact(workspace,args)
        if digest(path,self.check)!=description['sha256']:raise Refused('Native archive digest changed')
        import uuid
        directory=checked(self.root/uuid.uuid4().hex);directory.mkdir(mode=0o700)
        header,inventory=self.scan(path,directory/'manifest.sqlite3')
        if header['nativeAuthority']['coverage'].get('credentialsIncluded') and args.get('credentialsReviewed') is not True:raise Refused('Restoring captured credentials requires a separate explicit review')
        body={'version':1,'kind':'native-new-destination-restore','workspace':workspace,'artifactId':args['artifactId'],'sha256':args['sha256'],'destination':args['destination'],'header':header,'inventory':inventory,'privatePlanId':directory.name,'manifestStamp':file_stamp(directory/'manifest.sqlite3'),'destinationAuthority':'new-owned-directory-only','startsWorker':False,'replaysInputs':False,'overwritesExisting':False,'requiresRuntimeQualification':True,'requiresConfigurationReview':True}
        from .configuration_clone import fingerprint
        value=fingerprint(body);write(self.root/(value+'.json'),body)
        return {'previewHash':value,**{k:v for k,v in body.items() if k not in {'workspace','header','privatePlanId','manifestStamp'}},'coverage':header['nativeAuthority']['coverage']}
    def reviewed(self,workspace,value):
        from .configuration_clone import fingerprint
        value=token(value);body=strict_json(regular(self.root/(value+'.json'),1024*1024))
        if body.get('kind')!='native-new-destination-restore' or body.get('version')!=1 or body.get('workspace')!=workspace or fingerprint(body)!=value:raise Refused('Restore preview identity or authority changed')
        self.target(body['destination']);return body
    def page(self,workspace,args):
        body=self.reviewed(workspace,args.get('previewHash'));limit=args.get('limit',50);cursor=args.get('cursor','')
        if type(limit)is not int or not 1<=limit<=100 or not isinstance(cursor,str) or len(cursor)>8192:raise Refused('Bounded restore manifest page required')
        path=checked(self.root/body['privatePlanId']/'manifest.sqlite3')
        if file_stamp(path)!=body['manifestStamp']:raise Refused('Reviewed restore manifest changed')
        with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:rows=db.execute('SELECT path,body FROM entries WHERE path>? ORDER BY path LIMIT ?',(cursor,limit+1)).fetchall()
        items=[strict_json(row[1]) for row in rows[:limit]]
        if len(json.dumps(items).encode())>512*1024:raise Refused('Restore manifest page exceeds transport bound')
        return {'previewHash':args['previewHash'],'items':items,'nextCursor':rows[limit-1][0] if len(rows)>limit else None,'coverage':body['header']['nativeAuthority']['coverage']}
    def apply(self,workspace,args):
        self.start_check()
        try:
            require_admin(self.owner);body=self.reviewed(workspace,args.get('previewHash'));target=self.target(body['destination'])
            if target.exists():raise Refused('Restore destination is no longer absent')
            path,description=self.artifact(workspace,body)
            if digest(path,self.check)!=body['sha256']:raise Refused('Reviewed archive changed before restore')
        except Exception as error:raise Refused(str(error)) from None
        with Gate(self.owner.config,exclusive=True):
            try:target.mkdir(mode=0o700,exist_ok=False)
            except FileExistsError:raise Refused('Restore destination is no longer absent') from None
            try:
                native=target/'native-home';native.mkdir(mode=0o700)
                marker={'version':1,'phase':'writing','commandId':args['commandId'],'previewHash':args['previewHash'],'artifactId':body['artifactId'],'sha256':body['sha256']}
                write(native/PENDING,marker)
                with Gate({'home':str(native)},exclusive=True),Gate({'home':str(native)},exclusive=True,filename='.amplifier-acp-admin-writers.lock'):
                    self.scan(path,target/'restore-manifest.sqlite3',expected=body['header'],destination=target)
                    config,environment=self.launcher(body,target)
                    write(target/'native-launcher.json',config);write(target/'native-environment.json',environment)
                    result={'restored':True,'destination':body['destination'],'directory':str(target),'artifactId':body['artifactId'],'sha256':body['sha256'],'previewHash':args['previewHash'],'launcherPath':str(target/'native-launcher.json'),'environmentPath':str(target/'native-environment.json'),'requiresRuntimeQualification':True,'requiresConfigurationReview':True,'startsWorker':False,'replaysInputs':False,'overwritesExisting':False,'inventory':body['inventory']}
                    write(native/PENDING,{**marker,'phase':'files-written','result':result})
                    self.sync(target)
                    return result
            except Exception:
                raise ValueError('Native restore may have written destination files; inspect the original receipt. No replay.') from None
    def launcher(self,body,target):
        authority=body['header']['nativeAuthority'];roots={row['id']:row for row in authority['roots']}
        config={'home':str(target/'native-home'),'appHome':str(target/'app-home')}
        source=authority.get('sourceConfiguration',{})
        for key in ('bundle','registryHome','runtimeManifest','generationProfiles'):
            if key in source:config[key]=source[key]
        mapping={row['path']:str(target/row['id'] if row['kind']=='directory' else target/row['id']/'source') for row in roots.values() if row['kind']!='collection'}
        for key in ('bundle','registryHome','runtimeManifest'):
            if isinstance(config.get(key),str):
                for original,destination in sorted(mapping.items(),key=lambda row:len(row[0]),reverse=True):
                    if config[key]==original or config[key].startswith(original+'/'):config[key]=destination+config[key][len(original):];break
        env={}
        for root,name in [('checkpoint','AMPLIFIER_SESSION_STATE_HOME'),('events','AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'),('sources','AMPLIFIER_SOURCE_STORE')]:
            if root in roots:env[name]=str(target/root)
        return config,env
    def sync(self,path):
        self.check()
        with os.scandir(path) as scan:
            for entry in scan:
                if entry.is_dir(follow_symlinks=False):self.sync(Path(entry.path))
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def finalize(self,result,command):
        target=self.target(result['destination'])
        config={'home':str(target/'native-home')}
        with Gate(config,exclusive=True),Gate(config,exclusive=True,filename='.amplifier-acp-admin-writers.lock'):
            self.finalize_owned(result,command)
    def finalize_owned(self,result,command):
        target=self.target(result['destination']);path=target/'native-home'/PENDING
        marker=strict_json(regular(path,1024*1024))
        if marker.get('commandId')!=command or marker.get('phase')!='files-written' or marker.get('result')!=result:raise ValueError('Restore completion marker differs from exact receipt')
        receipt=target/'app-home/restore-receipts'/ (hashlib.sha256(command.encode()).hexdigest()+'.json')
        completion={'version':1,'commandId':command,'phase':'completed','result':result,'inputsReplayed':False}
        if receipt.exists() and strict_json(regular(receipt,1024*1024))!=completion:raise ValueError('Restore completion identity conflicts with retained history; destination remains fenced')
        write(receipt,completion)
        path.unlink();fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def reconcile(self,workspace,args):
        require_admin(self.owner);previous=self.owner.receipt(workspace,args.get('restoreCommandId'))
        if previous is None or previous[1].get('operation')!='maintenance.restore.apply' or previous[1]['state']!='succeeded':raise Refused('Only a conclusively completed original restore may clear its leftover fence')
        result=previous[1]['result'];target=self.target(result['destination']);pending=target/'native-home'/PENDING
        if pending.exists():
            with Gate(self.owner.config,exclusive=True):self.finalize(result,args['restoreCommandId'])
        return {'reconciled':True,'restoreCommandId':args['restoreCommandId'],'directory':str(target),'replayed':False}
