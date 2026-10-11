"""Selected-session lifecycle authority. No runtime startup and no history replay."""
from __future__ import annotations
import base64,copy,hashlib,json,os,sqlite3,stat,time,uuid
from contextlib import contextmanager
from pathlib import Path
from filelock import FileLock
from . import history
from .configuration_clone import fingerprint,location
from .relocation import home,state,write

MAX_HISTORY=64*1024*1024
PRESERVED=['canonical transcript','events.jsonl','workspace files','tool and operation receipts','child session history']

def regular(path,maximum=1024*1024):
    path=Path(path)
    for part in (path,*path.parents):
        if part.is_symlink():raise ValueError('Native lifecycle files cannot traverse symbolic links')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>maximum:raise ValueError('Native lifecycle file is not a bounded regular file')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        opened=os.fstat(fd)
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or not stat.S_ISREG(opened.st_mode):raise ValueError('Native lifecycle file changed during open')
        with os.fdopen(fd,'rb',closefd=False) as stream:value=stream.read(maximum+1)
        if len(value)>maximum:raise ValueError('Native lifecycle file exceeds its bound')
    finally:os.close(fd)
    return value

def record(path):return json.loads(regular(path))
def lifecycle_state(server,identity,cwd):
    path=location(home(server),identity)/'lifecycle.json'
    value=record(path) if path.exists() else {'sessionId':identity,'historyCwd':cwd,'deleted':False}
    if value.get('sessionId')!=identity or value.get('historyCwd')!=cwd:raise ValueError('Native lifecycle identity does not match history workspace')
    return value

def guard_copy_source(server,identity):
    """Copy only a coherent settled authority image; never finish its journals."""
    app=location(home(server),identity)
    if (app/'history-removal.json').exists():raise ValueError('Shared history removal was admitted; inspect its original receipt. This identity cannot be restarted or copied.')
    from .native.history_revision import guard_context_clear
    guard_context_clear(home(server), identity)
    for name in ('history-edit-pending.json','maintenance-reset-pending.json'):
        if (app/name).exists():raise ValueError('Canonical native history or configuration is unresolved; source preserved')
    for name in ('history-fork.json','history-import.json'):
        path=app/name
        if path.exists() and record(path).get('state')!='committed':raise ValueError('Native creation outcome is unresolved; source preserved')

def guard_start(server,identity,cwd):
    if (location(home(server),identity)/'history-removal.json').exists():raise ValueError('This saved conversation was permanently removed or its removal is unresolved; inspect the original receipt. No work replayed.')
    value=lifecycle_state(server,identity,cwd)
    if value['deleted']:raise ValueError('This native session was removed from discovery; restore it before continuing. Its history is preserved.')
    maintenance=location(home(server),identity)/'maintenance-reset-pending.json'
    if maintenance.exists():raise ValueError('Native configuration reset outcome is unresolved; inspect its exact maintenance receipt and retained originals before execution.')
    pending=location(home(server),identity)/'history-edit-pending.json'
    if pending.exists():raise ValueError('Native history edit outcome is unresolved; inspect its receipt before execution. No input replayed.')
    path=location(home(server),identity)/'history-fork.json'
    fork=record(path) if path.exists() else None
    if fork and fork.get('state')!='committed':raise ValueError('History fork creation is unresolved; inspect its original receipt. No work replayed.')
    import_path=location(home(server),identity)/'history-import.json'
    imported=record(import_path) if import_path.exists() else None
    if imported and imported.get('state')!='committed':raise ValueError('History import creation is unresolved; inspect its original receipt. No work replayed.')
    return imported.get('bundle') if imported else fork.get('bundle') if fork else None

def message_id(row,position,epoch=None):
    return history.message_id(row,position,epoch)

class HistoryRows(list):
    def __init__(self, values=(), epoch=None):
        super().__init__(values);self.epoch=epoch
    def __getitem__(self, key):
        value=super().__getitem__(key)
        return HistoryRows(value,self.epoch) if isinstance(key,slice) else value

def canonical(directory,expected,*,app_home=None):
    path,diagnostics=history.source(directory);revision,size=history.stamp(path)
    if diagnostics:raise ValueError('Recovered backup is not a complete current lifecycle snapshot; repair the source first')
    if revision!=expected:raise ValueError('Native history changed; inspect a fresh revision')
    if size>MAX_HISTORY:raise ValueError('Selected native history exceeds the 64 MiB lifecycle bound; files preserved')
    from .native.history_revision import context_head
    epoch=context_head(app_home,directory.name) if app_home is not None else None
    raw=regular(path,MAX_HISTORY);rows=HistoryRows(epoch=epoch);offset=0
    for line in raw.splitlines(keepends=True):
        row=json.loads(line)
        if not isinstance(row,dict) or not isinstance(row.get('role'),str):raise ValueError('Malformed native history; files preserved')
        rows.append((offset,row));offset+=len(line)
    if history.stamp(path)[0]!=revision:raise ValueError('Native history changed during capture')
    metadata=record(directory/'metadata.json')
    if (history.stamp(path)[0]!=revision or
            (app_home is not None and context_head(app_home,directory.name)!=epoch)):
        raise ValueError('Native history changed during capture')
    return rows,metadata,revision

def bound(rows,identity,*,user=False):
    if isinstance(identity,str) and identity.startswith('archive:'):
        raise ValueError('Archived context is viewing-only; canonical fork/edit/export cannot target it')
    matches=[i for i,(position,row) in enumerate(rows) if message_id(row,position,getattr(rows,"epoch",None))==identity or (row.get('metadata') or {}).get('amplifier_input',{}).get('id')==identity]
    if len(matches)!=1:raise ValueError('The selected native message boundary is unavailable or ambiguous')
    index=matches[0]
    if not history.visible(rows[index][1]) or user and rows[index][1].get('role')!='user':raise ValueError('Choose a visible native user message boundary')
    return index

def scope_rows(rows,args,*,fork=False):
    if fork and args.get('throughMessageId'):
        begin=bound(rows,args['throughMessageId'],user=True)
        end=next((i for i in range(begin+1,len(rows)) if rows[i][1].get('role')=='user' and history.visible(rows[i][1])),len(rows))
        return rows[:end]
    if fork:return rows
    scope=args.get('scope','all')
    if scope not in {'all','from','range'}:raise ValueError('Unsupported export scope')
    if scope=='all' and any(k in args for k in ('fromMessageId','throughMessageId')):raise ValueError('All-history exports cannot supply range boundaries')
    start=bound(rows,args.get('fromMessageId')) if scope in {'from','range'} else 0
    end=bound(rows,args.get('throughMessageId'))+1 if scope=='range' else len(rows)
    if end<=start:raise ValueError('Export boundaries are reversed')
    return rows[start:end]

class Lifecycle:
    def __init__(self,server,identity,cwd):
        self.server=server;self.identity=identity
        path=Path(cwd)
        if not path.is_absolute():raise ValueError('Absolute history workspace required')
        if not path.is_dir():
            from .managed_session import history_scope
            if not history_scope(str(path),identity,server.config):raise ValueError('Existing absolute history workspace required')
        self.cwd=str(path.resolve());self.directory=server.directory(self.cwd,identity)
        self.app=location(home(server),identity)
        if not self.directory.is_dir():raise ValueError('Native session does not exist in this history workspace')
        lifecycle_state(server,identity,self.cwd)
    @contextmanager
    def receipts(self,write=True):
        from .journal_schema import connect
        db=connect(self.app/'lifecycle-receipts.sqlite3','lifecycle',write=write)
        if db is None:yield None;return
        try:
            with db:yield db
        finally:db.close()
    def saved(self,command):
        if not isinstance(command,str) or not 1<=len(command)<=200:raise ValueError('Bounded original commandId required')
        with self.receipts(write=False) as db:row=db.execute('SELECT signature,body FROM receipts WHERE id=?',(command,)).fetchone() if db else None
        if not row:return None
        value=json.loads(row[1]);value['signature']=row[0]
        if value['status']=='running':value['status']='unknown'
        return value
    def save(self,command,signature,value):
        with self.receipts() as db:db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)',(command,signature,json.dumps(value)))
    @contextmanager
    def own(self):
        from amplifier_foundation.session.shared_state import SharedSessionStore,SessionBusyError
        from .lifecycle_family import OwnershipBusy
        store=SharedSessionStore(self.cwd,self.identity) if not (self.app/'managed-files-fence.json').exists() else self.historical_store(self.identity,self.cwd)
        try:handle=store.acquire(app='amplifier-acp-lifecycle',pid=os.getpid())
        except SessionBusyError:raise OwnershipBusy() from None
        try:yield handle
        finally:handle.release()
    def historical_store(self,identity,cwd):
        from .managed_disposal import historical_store
        return historical_store(self.server.config,identity,cwd,source_identity=self.identity)
    def inspected_location(self):
        pending=self.app/'managed-files-fence.json'
        if pending.exists():
            from .managed_session import history_scope
            if not history_scope(self.cwd,self.identity,self.server.config):raise ValueError('Managed historical authority is unavailable')
            location=record(self.app/'execution-location.json')
            if location.get('sessionId')!=self.identity or location.get('historyCwd')!=self.cwd:raise ValueError('Managed historical location differs')
            return {**location,'blocked':False,'executionAvailable':False,'managedFiles':record(pending)['state']}
        return state(self.server,self.identity,self.cwd)
    def inspect(self):
        from .lifecycle_failure import read as failure
        result=history.inspect(self.directory);metadata=record(self.directory/'metadata.json')
        fields=('session_id','parent_id','name','name_source','description','created','created_at','updated_at','bundle','bundle_name','status','turn_count','fork','recovery')
        selected={key:metadata[key] for key in fields if key in metadata}
        if len(json.dumps(selected).encode())>32768:raise ValueError('Native metadata inspection exceeds its bound')
        return {'sessionId':self.identity,'historyRevision':result['revision'],'metadata':selected,'diagnostics':result['diagnostics'],'location':self.inspected_location(),'state':lifecycle_state(self.server,self.identity,self.cwd),'preserved':PRESERVED,'failure':failure(self.server,self.identity),'forkBundles':[{'id':key,'name':key,'revision':fingerprint([key,value])} for key,value in self.fork_bundles().items()]}
    def perform(self,operation,args):
        if operation not in {'inspect','receipt','export.read'} and (self.app/'history-removal.json').exists():
            raise ValueError('Shared history removal was admitted; inspect its original receipt. This identity cannot be restored or changed.')
        if operation.startswith('managedFiles.'):
            from .managed_disposal import perform
            return perform(self,operation,args)
        if operation=='restore' and (self.app/'managed-files-fence.json').exists():raise ValueError('Removed managed files cannot be restored by changing discovery; create an independent recovery copy')
        if operation.startswith('legacyCopy.'):
            from .retired_copy import perform
            return perform(self,operation,args)
        fields={'inspect':set(),'receipt':{'commandId'},'export.read':{'snapshotId','offset','limit'},'delete.preview':set(),'fork':{'commandId','targetSessionId','expectedHistoryRevision','throughMessageId','mode','bundle','bundleRevision','resetModel','descendants','managedTarget'},'export':{'commandId','expectedHistoryRevision','format','scope','fromMessageId','throughMessageId','minimal','includeExecution','includeConversationId','includeTimestamps','includeReferenceIds','exportContext'},'delete':{'commandId','expectedHistoryRevision','confirmationToken'},'delete.standard':{'commandId','expectedHistoryRevision'},'restore':{'commandId','expectedHistoryRevision'},'retention.hide':{'commandId','expectedHistoryRevision','modifiedBefore','descendants'}}
        if operation not in fields or not isinstance(args,dict) or set(args)-fields[operation]:raise ValueError('Unsupported lifecycle operation or arguments')
        if len(json.dumps({key:value for key,value in args.items() if key!='exportContext'}).encode())>32768:raise ValueError('Lifecycle arguments exceed their bound')
        if 'exportContext' in args:
            from .conversation_export import contribution
            contribution(args['exportContext'])
        if operation=='inspect':return self.inspect()
        if operation=='receipt':return {'receipt':self.saved(args.get('commandId'))}
        if operation=='export.read':return self.read_export(args)
        self.app.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=self.app/'lifecycle.lock'
        if lock.is_symlink():raise ValueError('Lifecycle lock cannot be a symlink')
        with FileLock(str(lock),timeout=0):
            if operation=='delete.preview':return self.preview()
            if operation not in {'fork','export','delete','delete.standard','restore','retention.hide'}:raise ValueError('Unknown native lifecycle operation')
            command=args.get('commandId');previous=self.saved(command);signature=fingerprint({'operation':operation,'args':args})
            if previous:
                if previous['signature']!=signature:raise ValueError('Lifecycle commandId conflicts with its original request')
                return {**previous.get('result',{}),'receipt':previous,'replayed':False}
            from .lifecycle_family import own_descendants
            from contextlib import nullcontext
            # Export freezes an exact saved revision and writes only its private
            # snapshot/receipt. It does not acquire or retire the execution owner.
            with (nullcontext() if operation=='export' else self.own()), (own_descendants(self,args.get('descendants',[]),check=(lambda sid,cwd:Lifecycle(self.server,sid,cwd).validate_retention(args,None)) if operation=='retention.hide' else None) if operation in {'fork','retention.hide'} else nullcontext()) as family:
                inspected=self.inspect()
                if inspected['location']['blocked']:raise ValueError('Resolve native execution relocation before lifecycle changes')
                if args.get('expectedHistoryRevision')!=inspected['historyRevision']:raise ValueError('Native history changed; review a fresh revision')
                if operation=='fork':prepared=self.prepare_fork(args)
                elif operation=='export':prepared=self.prepare_export(args)
                elif operation=='delete':prepared=self.validate_delete(args,inspected)
                elif operation=='retention.hide':prepared=self.validate_retention(args,inspected)
                else:prepared=None
                receipt={'commandId':command,'operation':operation,'status':'running','createdAt':time.time(),'replayed':False}
                self.save(command,signature,receipt)
                try:
                    if operation=='fork':result={**self.fork(args,prepared),'familyGuard':family}
                    elif operation=='export':result=self.export(args,prepared)
                    else:
                        value={'sessionId':self.identity,'historyCwd':self.cwd,'deleted':operation in {'delete','delete.standard','retention.hide'},'commandId':command,'updatedAt':time.time()}
                        write(self.app/'lifecycle.json',value)
                        result={**value,'preserved':PRESERVED,'historyRevision':inspected['historyRevision'],**({'retention':prepared,'familyGuard':family} if operation=='retention.hide' else {})}
                    receipt={**receipt,'status':'completed','result':result};self.save(command,signature,receipt)
                    return {**result,'receipt':receipt}
                except BaseException:
                    self.save(command,signature,{**receipt,'status':'unknown'});raise
    def validate_retention(self,args,inspected):
        import math
        from datetime import datetime
        cutoff=args.get('modifiedBefore')
        if type(cutoff) not in (int,float) or not math.isfinite(cutoff) or not 0<cutoff<time.time():raise ValueError('Explicit past retention cutoff required')
        guard_copy_source(self.server,self.identity)
        # Pending command evidence is authoritative; stale worker status alone is not.
        from .steering import Receipts
        from .coordination import Store
        with Receipts(home(self.server),self.identity).db() as db:
            if db and db.execute("SELECT 1 FROM receipts WHERE json_extract(body,'$.disposition') IN ('reserved','queued','held','unknown') LIMIT 1").fetchone():raise ValueError('Native steering intent is pending or unresolved')
        with Store(home(self.server),self.identity).db() as db:
            if db and db.execute("SELECT 1 FROM commands WHERE json_extract(body,'$.disposition') IN ('reserved','unknown') LIMIT 1").fetchone():raise ValueError('Native worker command is unresolved')
        metadata=record(self.directory/'metadata.json')
        if metadata.get('session_id')!=self.identity:raise ValueError('Canonical source metadata identity is unavailable or different')
        latest=0
        for name in ('metadata.json','transcript.jsonl','events.jsonl'):
            path=self.directory/name
            if not path.exists():
                if name!='events.jsonl':raise ValueError('Required native retention source is unavailable')
                continue
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():raise ValueError('Native retention source must be a regular file')
            latest=max(latest,info.st_mtime)
        for field in ('created','created_at','updated_at','modified_at'):
            value=metadata.get(field)
            if value is None:continue
            if isinstance(value,str):
                try:value=datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
                except ValueError:raise ValueError('Native activity timestamp is unavailable') from None
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Native activity timestamp is unavailable')
            latest=max(latest,value)
        if latest>=cutoff:raise ValueError('Native activity is newer than the reviewed retention cutoff')
        return {'modifiedBefore':cutoff,'latestObservedActivity':latest,'canonicalFilesDeleted':0,'behavior':'remove-from-discovery'}

    def preview(self):
        inspected=self.inspect();token=uuid.uuid4().hex
        value={'confirmationToken':token,'sessionId':self.identity,'historyCwd':self.cwd,'historyRevision':inspected['historyRevision'],'expiresAt':time.time()+600,'summary':{'behavior':'remove-from-discovery','canonicalFilesDeleted':0},'preserved':PRESERVED}
        write(self.app/'delete-preview.json',value);return value
    def validate_delete(self,args,inspected):
        preview=record(self.app/'delete-preview.json')
        if args.get('confirmationToken')!=preview['confirmationToken'] or time.time()>preview['expiresAt'] or preview['historyRevision']!=inspected['historyRevision']:raise ValueError('Deletion preview expired or changed; review again')
        return preview
    def prepare_fork(self,args):
        from .managed_session import copy_target
        mode=args.get('mode','complete')
        if mode not in {'complete','recovery'}:raise ValueError('Choose complete history or readable recovery mode')
        if type(args.get('resetModel',False)) is not bool:raise ValueError('resetModel must be a boolean')
        if args.get('resetModel') and not args.get('bundle'):raise ValueError('Reset model requires an explicitly selected bundle')
        if args.get('bundle') is not None and (not isinstance(args['bundle'],str) or args['bundle'] not in self.fork_bundles()):raise ValueError('Choose a launcher-configured fork bundle identity')
        if args.get('bundle') and args.get('bundleRevision')!=fingerprint([args['bundle'],self.fork_bundles()[args['bundle']]]):raise ValueError('Configured fork bundle changed; review a fresh selection')
        if args.get('bundleRevision') is not None and not args.get('bundle'):raise ValueError('Bundle revision requires a selected bundle')
        guard_copy_source(self.server,self.identity)
        target=args.get('targetSessionId')
        if not isinstance(target,str) or str(uuid.UUID(target))!=target:raise ValueError('Reserved target UUID required')
        managed=copy_target(self.server,self.identity,self.cwd,target,args.get('managedTarget'))
        destination=self.server.directory(managed['workspace'] if managed else self.cwd,target);target_app=location(home(self.server),target)
        if destination.exists() or target_app.exists():raise ValueError('Fork target already exists; inspect original creation receipt, never replay')
        rows,metadata,revision=canonical(self.directory,args['expectedHistoryRevision'],app_home=home(self.server))
        if metadata.get('session_id')!=self.identity:raise ValueError('Canonical source metadata identity is unavailable or different')
        selected=scope_rows(rows,args,fork=True)
        from .native.session_store import complete_tool_exchanges
        from .native.host.session import repair_interrupted_receipts
        if mode=='recovery':
            from .native.session_health import recovery_context
            context,positions=recovery_context([row for _,row in selected])
            if not context:raise ValueError('Selected source contains no coherent readable recovery history')
        else:
            positions={}
            context=repair_interrupted_receipts(complete_tool_exchanges([row for _,row in selected],positions=positions))
        copied={}
        for name in ('configuration.json','effective-configuration.json','control-state.json'):
            path=self.app/name
            if path.exists():copied[name]=json.loads(regular(path,4*1024*1024))
        controls=copied.get('control-state.json',{})
        copied['control-state.json']={key:copy.deepcopy(controls[key]) for key in ('selection','configurator','mode','budget') if key in controls}
        if 'capacity' in controls:copied['control-state.json']['capacity']={**copy.deepcopy(controls['capacity']),'revision':0}
        if 'effective-configuration.json' in copied:
            plan=copied.pop('effective-configuration.json');saved=copied.get('configuration.json',{})
            for section in ('providers','tools','hooks'):
                identities={row.get('instance_id') or row.get('id') or row.get('module') for row in plan.get(section,[])}
                plan.setdefault(section,[]).extend(row for row in saved.get(section,[]) if (row.get('instance_id') or row.get('id') or row.get('module')) not in identities)
            copied['configuration.json']=plan
        if not isinstance(metadata.get('bundle') or metadata.get('bundle_name'),str):raise ValueError('The saved native bundle identity is unavailable; resume and inspect configuration before forking')
        if args.get('bundle'):
            metadata={**metadata,'bundle':self.fork_bundles()[args['bundle']],'bundle_name':self.fork_bundles()[args['bundle']]}
            retained=[(index,row) for index,row in enumerate(context) if row.get('role') not in {'system','developer'}]
            remap={index:new for new,(index,_) in enumerate(retained)}
            positions={source:remap[target] for source,target in positions.items() if target in remap}
            context=[row for _,row in retained]
            controls=copied.get('control-state.json',{})
            copied={'control-state.json':({'selection':controls['selection']} if 'selection' in controls and not args.get('resetModel') else {})}
        from .public_message import row_envelope, COPY_SOURCE, HISTORY_ONLY, locator
        for source_index,target_index in positions.items():
            position,original=selected[source_index]
            envelope=row_envelope(original)
            if envelope is not None:
                target_row=context[target_index]
                target_row['metadata']={**(target_row.get('metadata') or {}),COPY_SOURCE:{'version':1,
                    'source':locator(self.identity,original,position,getattr(rows,'epoch',None)),
                    'disposition':'history-only' if (target_row.get('metadata') or {}).get(HISTORY_ONLY) is True else 'retained'}}
        from .message_copy import prepare
        reactions=prepare(self.server,self.identity,target,selected,positions,context)
        boundary_index=None
        if args.get('throughMessageId'):
            boundary_index=positions.get(bound(selected,args['throughMessageId'],user=True))
            if boundary_index is None or context[boundary_index].get('role')!='user':
                raise ValueError('The selected user boundary is not retained in the fork')
        return context,metadata,revision,copied,managed,reactions,boundary_index
    def fork_bundles(self):
        choices=self.server.config.get('forkBundles',{})
        if not isinstance(choices,dict) or len(choices)>64 or any(not isinstance(k,str) or not 1<=len(k)<=200 or not isinstance(v,str) or not 1<=len(v)<=4096 for k,v in choices.items()):raise ValueError('Launcher fork bundle choices are invalid')
        return choices
    def fork(self,args,prepared):
        from amplifier_foundation.session.history import SessionHistoryStore
        context,metadata,revision,copied,managed,reactions,boundary_index=prepared;target=args['targetSessionId'];target_app=location(home(self.server),target)
        from .managed_session import reserve_copy,complete_copy,copied_configuration,location as managed_location
        cwd=managed['workspace'] if managed else self.cwd
        target_app.mkdir(parents=True,exist_ok=False,mode=0o700)
        managed_record=reserve_copy(self.server,managed,{'operation':'fork','sessionId':self.identity,'historyRevision':revision,'commandId':args['commandId']}) if managed else None
        marker={'state':'reserved','commandId':args['commandId'],'sourceSessionId':self.identity,'sourceRevision':revision,'bundle':metadata.get('bundle') or metadata['bundle_name']}
        write(target_app/'history-fork.json',marker)
        destination=self.server.directory(cwd,target);destination.mkdir(parents=True,exist_ok=False,mode=0o700)
        execution=cwd if managed else state(self.server,self.identity,self.cwd)['executionDirectory']
        safe={key:copy.deepcopy(metadata[key]) for key in ('bundle','bundle_name','working_dir','name','name_source','description','model','provider') if key in metadata}
        if managed:
            safe.update(working_dir=cwd,location=managed_location(managed))
            copied=copied_configuration(copied,self.cwd,cwd)
        preserve_system=not (args.get('mode')=='recovery' or args.get('bundle'))
        safe.update(session_id=target,parent_id=None,created_at=time.time(),status='forked',preserve_system=preserve_system,fork={'source_session_id':self.identity,'source_revision':revision,'through_message_id':args.get('throughMessageId'),'jobs_replayed':False})
        if args.get('mode')=='recovery':safe['recovery']={'source_session_id':self.identity,'mode':'readable_history','work_replayed':False,'source_effects':'not_rolled_back'}
        from amplifier_foundation.session.shared_state import SharedSessionStore
        target_handle=SharedSessionStore(cwd,target).acquire(app='amplifier-acp-fork',pid=os.getpid())
        try:
            SessionHistoryStore(destination,session_id=target).save(context,safe,preserve_system=preserve_system)
            from .message_copy import publish
            target_revision=history.inspect(destination)['revision']
            verify_originals=any((row.get('metadata') or {}).get('amplifier_public_message') is not None for row in context)
            target_rows,_,_=canonical(destination,target_revision,app_home=home(self.server)) if reactions or verify_originals or boundary_index is not None else ([],None,None)
            if target_rows or verify_originals:
                expected=[row for row in context if preserve_system or row.get('role') not in {'system','developer'}]
                if [row for _,row in target_rows] != expected:raise ValueError('Persisted target rows differ from prepared copy; creation remains unknown')
            reaction_copy=publish(self.server,target,target_rows,reactions)
            for name,value in copied.items():write(target_app/name,value)
            write(target_app/'execution-location.json',{'sessionId':target,'historyCwd':cwd,'executionDirectory':execution,'executionRevision':0})
            result={'sessionId':target,'creationConfirmed':True,'historyCwd':cwd,'executionDirectory':execution,'historyRevision':history.inspect(destination)['revision'],'source':{'sessionId':self.identity,'historyRevision':revision,**({'throughMessageId':args['throughMessageId']} if args.get('throughMessageId') else {})},'workReplayed':False,'mode':args.get('mode','complete'),'bundleSelection':args.get('bundle'),'preservedSource':True,'messageAnnotations':reaction_copy}
            if boundary_index is not None:
                # IDs include canonical bytes/offsets. A copied row can acquire a
                # different ID even when its visible text is identical.
                index=sum(1 for row in context[:boundary_index] if preserve_system or row.get('role') not in {'system','developer'})
                position,row=target_rows[index]
                result['forkBoundary']={'sourceMessageId':args['throughMessageId'],
                    'targetMessageId':history.message_id(row,position,getattr(target_rows,'epoch',None)),
                    'historyRevision':target_revision}
            if managed_record:result.update(location=managed_location(managed),configurationScope='global-and-session',runtimeStarted=False,generatedFilesCopied=False)
            write(target_app/'history-fork.json',{**marker,'state':'committed','result':result})
            if managed_record:complete_copy(self.server,managed_record,title=safe.get('name','Copied chat'),bundle=safe['bundle'])
            return result
        finally:target_handle.release()
    def prepare_export(self,args):
        from .conversation_export import options,render
        options(args)
        guard_copy_source(self.server,self.identity)
        rows,metadata,revision=canonical(self.directory,args['expectedHistoryRevision'],app_home=home(self.server));rows=scope_rows(rows,args)
        guard_copy_source(self.server,self.identity)
        public=[];omissions=[];attachments=0;aliases={}
        for position,row in rows:
            updates,issues=history.content_updates(row,position);omissions.extend(issues)
            if not history.visible(row):continue
            chunks=[u for u in updates if u['sessionUpdate'] in {'user_message_chunk','agent_message_chunk'}]
            if not chunks:continue
            from .public_message import row_envelope
            original=row_envelope(row)
            texts=[];refs=[]
            for update in chunks:
                block=update['content']
                if block['type']=='text':texts.append(block['text'])
                else:
                    attachments+=1
                    from .public_message import row_envelope
                    original=row_envelope(row)
                    index=(update.get('_meta',{}).get('amplifier.dev/history') or {}).get('originalBlockIndex')
                    refs.append({**original['blocks'][index],'omittedPayload':True} if original is not None else
                                {'type':block['type'],**({'uri':block['uri']} if 'uri' in block else {}),'omittedPayload':True})
            marker=(row.get('metadata') or {}).get('amplifier_input') or {}
            if isinstance(marker.get('id'),str):aliases[marker['id']]=message_id(row,position,getattr(rows,'epoch',None))
            public.append({'id':message_id(row,position,getattr(rows,'epoch',None)),'role':row['role'],'text':('' if original is not None else '\n').join(texts),**({'publicMessage':original} if original is not None else {}),**({'attachments':refs} if refs else {}),'provenance':history.provenance(row)})
        fmt=args.get('format','json')
        body,details=render(public,args,self.identity,revision,aliases)
        mime='application/json' if fmt=='json' else 'text/markdown';extension='json' if fmt=='json' else 'md'
        if len(body)>MAX_HISTORY:raise ValueError('Export exceeds selected 64 MiB bound')
        return body,{'filename':f'conversation.{extension}' if not details['options']['includeConversationId'] else f'conversation-{self.identity[:20]}.{extension}','mimeType':mime,'summary':{'format':fmt,'scope':args.get('scope','all'),'minimal':args.get('minimal',False),'requestedBoundaries':{key:args[key] for key in ('fromMessageId','throughMessageId') if key in args},'resolvedBoundaries':{'fromMessageId':public[0]['id'] if public else None,'throughMessageId':public[-1]['id'] if public else None},'messageCount':len(public),'bytes':len(body),'attachmentCount':attachments,'artifactCount':details['artifactCount'],'executionSummaryCount':details['executionSummaryCount'],'options':details['options'],'omissions':[{'code':'tool_receipts_and_private_reasoning_excluded'},{'code':'artifact_and_attachment_payloads_excluded'},*details['omissions'],*omissions[:100]]},'historyRevision':revision}
    def export(self,args,prepared):
        body,description=prepared;digest=hashlib.sha256(body).hexdigest();snapshot=fingerprint({'commandId':args['commandId'],'digest':digest,'sessionId':self.identity})
        directory=self.app/'exports';directory.mkdir(exist_ok=True,mode=0o700)
        from .native.host.config import write_private
        write_private(directory/(snapshot+'.body'),body.decode())
        result={**description,'snapshotId':snapshot,'contentHash':digest,'capturedAt':time.time(),'sessionId':self.identity}
        write(directory/(snapshot+'.json'),{**result,'bodyStamp':history.stamp(directory/(snapshot+'.body'))[0]});return result
    def read_export(self,args):
        snapshot=args.get('snapshotId')
        if not isinstance(snapshot,str) or len(snapshot)!=64 or any(c not in '0123456789abcdef' for c in snapshot):raise ValueError('Invalid snapshot identity')
        offset=args.get('offset',0);limit=args.get('limit',65536)
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=65536:raise ValueError('Bounded export byte window required')
        description=record(self.app/'exports'/(snapshot+'.json'));path=self.app/'exports'/(snapshot+'.body')
        for part in (path,*path.parents):
            if part.is_symlink():raise ValueError('Export resource cannot traverse symbolic links')
        info=path.lstat()
        if history.stamp(path)[0]!=description.pop('bodyStamp'):raise ValueError('Immutable export body changed')
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size!=description['summary']['bytes']:raise ValueError('Export body changed; immutable snapshot unavailable')
        with path.open('rb') as stream:stream.seek(offset);body=stream.read(limit)
        return {**description,'offset':offset,'data':base64.b64encode(body).decode(),'encoding':'base64','nextOffset':offset+len(body) if offset+len(body)<info.st_size else None}
