"""Explicit cold full-native authority inventory and disk-backed archive format."""
from __future__ import annotations
import hashlib,json,os,sqlite3,stat,tarfile,time
from pathlib import Path
from .maintenance import CHUNK,file_stamp
from .maintenance_gate import checked
from .private_store_snapshot import backup,require_admin

PART='full-native-authority'
FORMAT='amplifier-native-authority'
VERSION=1
WRITER_PROTECTION={'policy':'operator-attested-stopped','declaredBy':'trusted-launcher','nativeWriterGate':'exclusive-held-through-capture','nativeAdminGate':'exclusive-held-through-capture','cooperativeSessionLeasesHeld':False,'noncooperatingWriters':'not-independently-observed','requiresOperatorVerification':True}
TRANSIENT_NAMES={'owner.lock','session.lock','generation-owner.lock','lifecycle.lock','.unified-migration.lock','.amplifier-acp-maintenance.lock','.amplifier-acp-admin-writers.lock','settings.yaml.lock','settings.local.yaml.lock','keys.env.lock','admin-settings.yaml.lock'}
HOME_NAMES={'projects','sessions','settings.yaml','settings.local.yaml','config.yaml','routing','routing.local','bundles','agents','contexts','skills','memory','todos','cache','keys.env'}
APP_NAMES={'sessions','runtime-reports','diagnostics','restore-receipts','updates','runtime','foundation','source-store','config','routing','bundles','history-imports','voice-preferences','permissions','maintenance','admin-settings.yaml'}

def state_root():
    if os.environ.get('AMPLIFIER_SESSION_STATE_HOME'):return Path(os.environ['AMPLIFIER_SESSION_STATE_HOME']).expanduser().absolute()
    return (Path(os.environ['XDG_STATE_HOME']).expanduser()/'amplifier/sessions' if os.environ.get('XDG_STATE_HOME') else Path.home()/'.local/state/amplifier/sessions').absolute()

def roots(owner):
    if owner.config.get('maintenanceFullNative') is not True:raise ValueError('Full native backup requires explicit trusted launcher enablement')
    declared=owner.config.get('maintenanceFullNativeRoots',{})
    if not isinstance(declared,dict):raise ValueError('Full native external roots must be explicitly configured')
    if set(declared)-{'checkpoint','events','registry','sources','bundle','runtimeManifest'}:raise ValueError('Unknown full-native external root kind')
    result=[{'id':'native-home','path':str(owner.home),'kind':'directory'},{'id':'app-home','path':str(owner.app),'kind':'directory'}]
    if owner.home==owner.app or owner.home in owner.app.parents or owner.app in owner.home.parents:raise ValueError('Full native home and adapter home must be disjoint')
    if (owner.home/'.amplifier-acp-restore-pending.json').exists():raise ValueError('Uncertain native restore must be inspected before full backup')
    effective={'checkpoint':state_root(),
      'events':Path(os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH') or owner.home/'projects').expanduser().absolute(),
      'registry':Path(owner.config.get('registryHome') or owner.app/'foundation').expanduser().absolute(),
      'sources':Path(os.environ.get('AMPLIFIER_SOURCE_STORE') or owner.app/'source-store').expanduser().absolute()}
    bundle=owner.config.get('bundle')
    if isinstance(bundle,str) and (bundle.startswith(('/','~','file://'))):effective['bundle']=Path(bundle.removeprefix('file://')).expanduser().absolute()
    if owner.config.get('runtimeManifest'):effective['runtimeManifest']=Path(owner.config['runtimeManifest']).expanduser().absolute()
    for name,path in effective.items():
        path=checked(path)
        if path.exists() and name not in {'bundle','runtimeManifest'} and not path.is_dir():raise ValueError('Native external directory authority is not a directory')
        if name=='runtimeManifest' and path.exists() and not path.is_file():raise ValueError('Native runtime manifest must be a file')
        inside=next((row for row in result if row['kind']=='directory' and (path==Path(row['path']) or Path(row['path']) in path.parents)),None)
        if inside:continue
        if name not in declared or checked(Path(declared[name]).expanduser().absolute())!=path:raise ValueError('Full native backup requires the exact launcher-declared external '+name+' root')
        if any(path==Path(row['path']) or path in Path(row['path']).parents or Path(row['path']) in path.parents for row in result):raise ValueError('External native authority roots overlap ambiguously')
        result.append({'id':name,'path':str(path),'kind':'file' if name=='runtimeManifest' or name=='bundle' and not path.is_dir() else 'directory'})
    return result

def source_config(owner,root_rows):
    # Only deliberate native source selectors; never the process environment or
    # arbitrary launcher credentials/commands. Path references remain evidence.
    allowed=('bundle','registryHome','runtimeManifest','generationProfiles')
    result={key:owner.config[key] for key in allowed if key in owner.config}
    if any(not isinstance(value,str) or len(value)>8192 for key,value in result.items() if key!='generationProfiles'):raise ValueError('Unsupported native source selector')
    profiles=result.get('generationProfiles',[])
    if not isinstance(profiles,list) or len(profiles)>128 or any(not isinstance(value,str) or len(value)>8192 for value in profiles):raise ValueError('Unsupported generation profile selection')
    managed=owner.config.get('managedSessionRoots',[])
    if not isinstance(managed,list) or len(managed)>16 or any(not isinstance(value,str) or not Path(value).is_absolute() or len(value)>8192 for value in managed):raise ValueError('Unsupported managed allocation source references')
    if managed:result['managedAllocations']={'roots':managed,'authority':'external-host-owner','included':False,'requiredForManagedActivation':True}
    return result

def prepare(owner,plan,parts,check,command_id,include_credentials):
    authority=require_admin(owner)
    if owner.config.get('maintenanceExternalWriters')!='stopped':raise ValueError('Full native backup requires launcher-verified stopped external writers')
    selected=roots(owner);directory=checked(owner.root/'plans'/plan/'full-native');directory.mkdir(mode=0o700)
    payload=directory/'payload';payload.mkdir(mode=0o700)
    index=sqlite3.connect(directory/'inventory.sqlite3');os.chmod(directory/'inventory.sqlite3',0o600)
    index.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,body TEXT NOT NULL)')
    index.execute('CREATE TABLE workspaces(cwd TEXT PRIMARY KEY)')
    timeout=owner.config.get('maintenanceFullTimeoutSeconds',600)
    if type(timeout)is not int or not 1<=timeout<=7200:raise ValueError('Full native inventory deadline must be1–7200seconds')
    total=0;count=0;deadline=time.monotonic()+timeout;excluded=0
    incomplete=0;native_sessions=0;source_cut=time.time();current_plan=owner.root/'plans'/plan
    def bounded():
        check()
        if require_admin(owner)!=authority:raise ValueError('Full native administration lease changed')
        if time.monotonic()>deadline:raise ValueError('Full native inventory deadline exceeded')
    def record(row):
        nonlocal count,excluded,incomplete
        count+=1
        if count>owner.archives().max_entries:raise ValueError('Full native inventory exceeds configured entry limit')
        if row['status']=='excluded':
            excluded+=1;incomplete+=row['classification']=='authoritative'
        index.execute('INSERT INTO entries VALUES(?,?)',(row['path'],json.dumps(row,sort_keys=True,separators=(',',':'))))
    def omit(label,reason,classification):record({'path':label,'status':'excluded','reason':reason,'classification':classification})
    def copy(label,path,depth=0,root_id=None):
        nonlocal total,native_sessions
        bounded();path=checked(path)
        if depth>64:raise ValueError('Full native source directory nesting exceeds64')
        if path==current_plan:return  # exclude before traversing our new output
        if not path.exists():record({'path':label,'status':'missing'});return
        info=path.lstat()
        if path.name.endswith('-shm') or path.name in TRANSIENT_NAMES:
            omit(label,'transient writer or SQLite coordination state','transient');return
        if root_id=='checkpoint' and path.name in {'owner.json','session.lock'}:omit(label,'advisory writer lifetime, never restored as ownership','transient');return
        if path==owner.app/'updates/worker-leases':omit(label,'process lifetime references; no workers are restored','transient');return
        if path.name=='__pycache__':omit(label,'Python bytecode regenerated from preserved source','derived');return
        if path.name=='.venv' and (owner.app/'runtime') in path.parents:
            # Never call a locally modified installed graph a disposable cache.
            verify_runtime(owner,path.parent)
            omit(label,'qualified installed environment; preserved lock/source receipts authorize separate repair','derived');return
        credentials=path.name=='keys.env' or path.name.endswith('-oauth.json') or 'chatgpt-sign-in' in path.parts
        if credentials and not include_credentials:omit(label,'credential storage was not explicitly selected and reviewed','authoritative');return
        if path.name.endswith(('-wal','-journal')):
            database=Path(str(path).rsplit('-',1)[0])
            if not database.is_file() or path.name.endswith('-journal'):raise ValueError('Unresolved SQLite journal requires owner inspection before full backup')
            omit(label,'committed records captured by the standalone database image','transient');return
        if stat.S_ISDIR(info.st_mode):
            target=checked(payload/label);target.mkdir(mode=0o700,parents=True,exist_ok=True)
            record({'path':label,'status':'directory'})
            with os.scandir(path) as scan:
                for entry in scan:
                    if depth==0:
                        known=HOME_NAMES if root_id=='native-home' else APP_NAMES if root_id=='app-home' else None
                        if known is not None and entry.name not in known and entry.name not in TRANSIENT_NAMES and not entry.name.endswith('-oauth.json'):raise ValueError('Unknown '+root_id+' entry prevents complete native coverage')
                        if root_id=='checkpoint' and entry.name!='v1':raise ValueError('Unknown Foundation checkpoint authority version')
                    copy(label+'/'+entry.name,Path(entry.path),depth+1,root_id)
            return
        before=file_stamp(path)
        if root_id=='native-home' and path.name=='metadata.json' and 'sessions' in path.parts and 'projects' in path.parts:
            native_sessions+=1
            from .session_lifecycle import regular
            try:
                metadata=json.loads(regular(path,4*1024*1024));cwd=metadata.get('working_dir') or metadata.get('workspace')
                if not isinstance(cwd,str) or not cwd or len(cwd)>8192 or not Path(cwd).is_absolute():raise ValueError('Missing native workspace identity')
                index.execute('INSERT OR IGNORE INTO workspaces VALUES(?)',(cwd,))
            except (ValueError,TypeError):omit(label+'/unresolved-workspace-authority','Canonical metadata could not identify its native project configuration; original metadata is retained','authoritative')
        target=checked(payload/label);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if before[2]>owner.maximum-total:raise ValueError('Full native bytes exceed configured archive budget')
        # Detect SQLite by its header as owner files do not all use one suffix.
        with path.open('rb') as stream:header=stream.read(16)
        is_db=header==b'SQLite format 3\x00'
        if is_db:backup(path,target,owner.maximum-total,bounded)
        else:
            fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
            try:
                with os.fdopen(fd,'rb',closefd=False) as source,target.open('xb') as output:
                    os.chmod(target,0o600);opened=os.fstat(fd)
                    if [opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns]!=before:raise ValueError('Native authority changed during file open')
                    while block:=source.read(CHUNK):bounded();output.write(block)
                    output.flush();os.fsync(output.fileno())
            finally:os.close(fd)
            if file_stamp(path)!=before:raise ValueError('Native authority changed during stopped-writer inventory')
        size=file_stamp(target)[2];total+=size
        if total>owner.maximum:raise ValueError('Full native images exceed configured archive budget')
        digest=hashlib.sha256()
        with target.open('rb') as stream:
            while block:=stream.read(CHUNK):bounded();digest.update(block)
        record({'path':label,'status':'included','bytes':size,'sha256':digest.hexdigest(),'format':'sqlite-backup-image' if is_db else 'file'})
    try:
        for row in selected:copy(row['id'] if row['kind']=='directory' else row['id']+'/source',Path(row['path']),root_id=row['id'])
        # Metadata selects only identities; trusted configured workspace roots
        # still authorize each read. No untrusted path can grant file access.
        selected.append({'id':'workspace-settings','path':None,'kind':'collection'})
        config_root=payload/'workspace-settings';config_root.mkdir(mode=0o700)
        record({'path':'workspace-settings','status':'directory'})
        for root in owner.admin.roots:index.execute('INSERT OR IGNORE INTO workspaces VALUES(?)',(str(root),))
        for cwd, in index.execute('SELECT cwd FROM workspaces ORDER BY cwd'):
            bounded();key=hashlib.sha256(cwd.encode()).hexdigest();label='workspace-settings/'+key
            workspace=checked(Path(cwd));config_path=workspace/'.amplifier'
            if not workspace.exists():record({'path':label,'status':'missing','reason':'recorded workspace no longer exists'});continue
            try:owner.admin.workspace(cwd)
            except ValueError:
                omit(label,'Existing workspace configuration is outside launcher-authorized roots','authoritative');continue
            entry=config_root/key;entry.mkdir(mode=0o700)
            from .relocation import write
            write(entry/'workspace.json',{'version':1,'workspace':cwd,'restoration':'staged-only-never-overwrite-workspace'})
            # Generated mapping is manifest authority too, with no copied source.
            mapping=entry/'workspace.json';size=file_stamp(mapping)[2];total+=size
            if total>owner.maximum:raise ValueError('Workspace mapping exceeds archive budget')
            record({'path':label,'status':'directory'})
            record({'path':label+'/workspace.json','status':'included','bytes':size,'sha256':hashlib.sha256(mapping.read_bytes()).hexdigest(),'format':'file'})
            copy(label+'/.amplifier',config_path,root_id='workspace-settings')
        index.commit()
    finally:index.close()
    descriptor={'format':FORMAT,'version':VERSION,'roots':[{**row,'destination':row['id']} for row in selected],'sourceConfiguration':source_config(owner,selected),'cutoff':{'capturedAt':source_cut,'phase':'before-current-plan-seal','excludedPlanId':plan,'prepareCommandId':command_id,'unfinishedReceipts':'uncertain-never-replay'},'coverage':{'completeNativeBackup':incomplete==0,'completeProductBackup':False,'scope':'all-configured-native-authority-roots','authoritativeOmissions':incomplete,'excludedEntries':excluded,'credentialsIncluded':include_credentials,'workspaceFiles':'native-configuration-captured-workspace-content-external','workspaceRestoration':'staged-only-never-overwrite-existing-workspaces','externalDependencies':'source paths outside declared native roots remain external requirements'},'inventory':{'entries':count,'bytes':total,'knownNativeSessions':native_sessions}}
    descriptor['writerProtection']=dict(WRITER_PROTECTION)
    if hasattr(owner,'capture_scope') and owner.capture_scope.active():descriptor['capture']=owner.capture_scope.context()
    from .relocation import write
    write(directory/'descriptor.json',descriptor)
    def sync(path):
        bounded()
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):sync(Path(entry.path))
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    sync(directory)
    return descriptor

def verify_runtime(owner,project):
    from .native.runtime_environment import project_path
    from .native.runtime_qualification import verify_recorded
    def candidates():
        yield None,owner.app/'updates/baseline-runtime'
        releases=owner.app/'updates/releases'
        if releases.exists():
            with os.scandir(releases) as scan:
                for entry in scan:
                    if entry.is_dir(follow_symlinks=False):yield entry.name,Path(entry.path)
    for generation,receipt in candidates():
        if project_path(owner.app,generation)==project:
            verify_recorded(project,receipt);return
    raise ValueError('Installed runtime has no verified recovery receipt; full backup will not discard it as cache')

def snapshot(plans,workspace,args,lease):
    import uuid
    owner=plans.owner;body=lease['body'];check=lambda:owner.valid(lease['leaseId']);plans.verify_sources(workspace,body,check)
    artifact=uuid.uuid4().hex;directory=checked(owner.root/'artifacts'/artifact);directory.mkdir(mode=0o700,parents=True)
    manifest=directory/'native-manifest.sqlite3';db=sqlite3.connect(manifest);os.chmod(manifest,0o600)
    try:
        db.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,body TEXT NOT NULL)')
        with plans.db(body['archivePlanId']) as source:
            for row in plans.rows(source):
                check();clean={k:v for k,v in row.items() if k not in {'source','stamp','identity'}}
                db.execute('INSERT INTO entries VALUES(?,?)',(row['path'],json.dumps(clean,sort_keys=True,separators=(',',':'))))
        db.commit()
    finally:db.close()
    digest=hashlib.sha256()
    with manifest.open('rb') as stream:
        while block:=stream.read(CHUNK):check();digest.update(block)
        os.fsync(stream.fileno())
    header={'format':FORMAT,'version':VERSION,'nativeAuthority':body['fullNative'],'manifest':{'path':'native-manifest.sqlite3','bytes':file_stamp(manifest)[2],'sha256':digest.hexdigest()},'previewHash':lease['previewHash']}
    from .relocation import write
    header_path=directory/'native-archive.json'
    if len(json.dumps(header).encode())>1024*1024:raise ValueError('Native archive header exceeds1MiB')
    write(header_path,header)
    partial=directory/'snapshot.partial.tar';target=directory/'snapshot.tar'
    with tarfile.open(partial,'w') as archive:
        os.chmod(partial,0o600)
        archive.add(header_path,arcname='native-archive.json',recursive=False);archive.add(manifest,arcname='native-manifest.sqlite3',recursive=False)
        with plans.db(body['archivePlanId']) as source:
            for row in plans.rows(source):
                check()
                if row['status']!='included':continue
                path=checked(Path(row['source']));before=file_stamp(path)
                if before!=row['stamp']:raise ValueError('Full native image changed before capture')
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    class Reader:
                        def read(self,size=-1):
                            check();block=stream.read(min(CHUNK,size) if size>=0 else CHUNK);digest.update(block);return block
                    info=tarfile.TarInfo(row['path']);info.size=row['bytes'];info.mode=0o600;archive.addfile(info,Reader())
                if file_stamp(path)!=before or digest.hexdigest()!=row['sha256']:raise ValueError('Full native image changed during capture')
    plans.verify_sources(workspace,body,check)
    with partial.open('rb') as stream:os.fsync(stream.fileno())
    os.replace(partial,target);fd=os.open(directory,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    digest=hashlib.sha256()
    with target.open('rb') as stream:
        while block:=stream.read(CHUNK):check();digest.update(block)
    result={'artifactId':artifact,'sha256':digest.hexdigest(),'bytes':file_stamp(target)[2],'contentType':'application/x-tar','workspace':workspace,'previewHash':lease['previewHash'],'coverage':'full-configured-native-authority','format':FORMAT,'version':VERSION,'manifest':{'fullNative':body['fullNative'],'inventory':body['inventory'],'previewHash':lease['previewHash']}}
    if 'capture' in body['fullNative']:
        owner.capture().verify_plan(body['fullNative']['capture'])
        result['capture']=owner.capture().bind(workspace,result,header['manifest'])
    write(directory/'description.json',result);return result


def inspect_artifact(owner,workspace,args):
    """Verify immutable bytes and return evidence only, never a private path."""
    import tarfile
    from .maintenance import identity,token
    from .native_restore import Restores,digest
    from .session_lifecycle import regular
    from .history_import import strict_json
    artifact=identity(args.get('artifactId'));expected=token(args.get('sha256'))
    directory=checked(owner.root/'artifacts'/artifact)
    description=strict_json(regular(directory/'description.json',1024*1024))
    if description.get('workspace')!=workspace or description.get('artifactId')!=artifact or description.get('sha256')!=expected or description.get('format')!=FORMAT or description.get('version')!=VERSION:raise ValueError('Exact full-native artifact authority required')
    path=checked(directory/'snapshot.tar');before=file_stamp(path)
    if before[2]!=description.get('bytes'):raise ValueError('Private native artifact size changed')
    deadline=time.monotonic()+owner.full_operation_seconds()
    def check():
        owner.ensure_open()
        if time.monotonic()>=deadline:raise ValueError('Native artifact inspection deadline exceeded')
    if digest(path,check)!=expected:raise ValueError('Private native artifact hash changed')
    with tarfile.open(path,'r|') as archive:
        header,roots=Restores.header(archive);manifest=header.get('manifest',{})
        if header['previewHash']!=description.get('previewHash'):raise ValueError('Native artifact preview identity changed')
        member=archive.next()
        if member is None or not member.isfile() or member.name!='native-manifest.sqlite3' or manifest.get('path')!=member.name or type(manifest.get('bytes'))is not int or not 1<=member.size<=256*1024*1024 or member.size!=manifest['bytes']:raise ValueError('Native artifact manifest is absent or unsupported')
        expected_manifest=token(manifest.get('sha256'));actual=hashlib.sha256()
        stream=archive.extractfile(member)
        while block:=stream.read(CHUNK):check();actual.update(block)
        if actual.hexdigest()!=expected_manifest:raise ValueError('Native artifact manifest hash changed')
    if file_stamp(path)!=before:raise ValueError('Native artifact changed during inspection')
    authority=header['nativeAuthority'];coverage=authority['coverage']
    if authority.get('writerProtection')!=WRITER_PROTECTION:raise ValueError('Archive has no supported sealed writer-exclusion evidence; no policy inference is permitted')
    for key in ('authoritativeOmissions','excludedEntries'):
        if type(coverage.get(key))is not int or coverage[key]<0:raise ValueError('Unsupported native authority omission count')
    if coverage['completeNativeBackup']!=(coverage['authoritativeOmissions']==0) or coverage.get('scope')!='all-configured-native-authority-roots':raise ValueError('Inconsistent native authority completeness')
    return {'version':1,'artifactId':artifact,'sha256':expected,'bytes':before[2],'contentType':'application/x-tar','format':FORMAT,'archiveVersion':VERSION,'previewHash':header['previewHash'],
      'manifest':{**manifest,'digestKind':'sha256-exact-native-manifest-sqlite3-bytes'},
      'authority':{**{key:coverage[key] for key in ('scope','completeNativeBackup','completeProductBackup','authoritativeOmissions','excludedEntries','credentialsIncluded')},'declaredRootIds':sorted(roots),'externalWriters':dict(WRITER_PROTECTION)},
      'integrity':{'artifactHashVerified':True,'manifestHashVerified':True},
      **({'capture':{'context':authority['capture'],'binding':{'artifactId':artifact,'sha256':expected,'manifestDigest':expected_manifest,'manifestDigestKind':'sha256-exact-native-manifest-sqlite3-bytes','bytes':before[2],'previewHash':header['previewHash']},'liveAuthority':'maintenance.capture.inspect on original connection required'}} if 'capture' in authority else {})}
