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

def guard_start(server,identity,cwd):
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

def message_id(row,position):
    digest=hashlib.sha256((str(position)+':'+json.dumps(row,sort_keys=True,separators=(',',':'))).encode()).hexdigest()[:24]
    voice=(row.get('metadata') or {}).get('amplifier_voice')
    if voice:digest=hashlib.sha256((voice['callId']+'\0'+voice['itemId']).encode()).hexdigest()[:24]
    return 'native:'+digest

def canonical(directory,expected):
    path,diagnostics=history.source(directory);revision,size=history.stamp(path)
    if diagnostics:raise ValueError('Recovered backup is not a complete current lifecycle snapshot; repair the source first')
    if revision!=expected:raise ValueError('Native history changed; inspect a fresh revision')
    if size>MAX_HISTORY:raise ValueError('Selected native history exceeds the 64 MiB lifecycle bound; files preserved')
    raw=regular(path,MAX_HISTORY);rows=[];offset=0
    for line in raw.splitlines(keepends=True):
        row=json.loads(line)
        if not isinstance(row,dict) or not isinstance(row.get('role'),str):raise ValueError('Malformed native history; files preserved')
        rows.append((offset,row));offset+=len(line)
    if history.stamp(path)[0]!=revision:raise ValueError('Native history changed during capture')
    metadata=record(directory/'metadata.json')
    return rows,metadata,revision

def bound(rows,identity,*,user=False):
    matches=[i for i,(position,row) in enumerate(rows) if message_id(row,position)==identity or (row.get('metadata') or {}).get('amplifier_input',{}).get('id')==identity]
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
        if not path.is_absolute() or not path.is_dir():raise ValueError('Existing absolute history workspace required')
        self.cwd=str(path.resolve());self.directory=server.directory(self.cwd,identity)
        self.app=location(home(server),identity)
        if not self.directory.is_dir():raise ValueError('Native session does not exist in this history workspace')
        lifecycle_state(server,identity,self.cwd)
    @contextmanager
    def receipts(self):
        self.app.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.app/'lifecycle-receipts.sqlite3'
        if path.is_symlink():raise ValueError('Lifecycle receipt cannot be a symlink')
        db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,signature TEXT,body TEXT)')
                yield db
        finally:db.close()
    def saved(self,command):
        if not isinstance(command,str) or not 1<=len(command)<=200:raise ValueError('Bounded original commandId required')
        with self.receipts() as db:row=db.execute('SELECT signature,body FROM receipts WHERE id=?',(command,)).fetchone()
        if not row:return None
        value=json.loads(row[1]);value['signature']=row[0]
        if value['status']=='running':value['status']='unknown'
        return value
    def save(self,command,signature,value):
        with self.receipts() as db:db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)',(command,signature,json.dumps(value)))
    @contextmanager
    def own(self):
        from amplifier_foundation.session.shared_state import SharedSessionStore
        handle=SharedSessionStore(self.cwd,self.identity).acquire(app='amplifier-acp-lifecycle',pid=os.getpid())
        try:yield handle
        finally:handle.release()
    def inspect(self):
        result=history.inspect(self.directory);metadata=record(self.directory/'metadata.json')
        fields=('session_id','parent_id','name','name_source','description','created','created_at','updated_at','bundle','bundle_name','status','turn_count','fork','recovery')
        selected={key:metadata[key] for key in fields if key in metadata}
        if len(json.dumps(selected).encode())>32768:raise ValueError('Native metadata inspection exceeds its bound')
        return {'sessionId':self.identity,'historyRevision':result['revision'],'metadata':selected,'diagnostics':result['diagnostics'],'location':state(self.server,self.identity,self.cwd),'state':lifecycle_state(self.server,self.identity,self.cwd),'preserved':PRESERVED}
    def perform(self,operation,args):
        fields={'inspect':set(),'receipt':{'commandId'},'export.read':{'snapshotId','offset','limit'},'delete.preview':set(),'fork':{'commandId','targetSessionId','expectedHistoryRevision','throughMessageId'},'export':{'commandId','expectedHistoryRevision','format','scope','fromMessageId','throughMessageId','minimal'},'delete':{'commandId','expectedHistoryRevision','confirmationToken'},'delete.standard':{'commandId','expectedHistoryRevision'},'restore':{'commandId','expectedHistoryRevision'}}
        if operation not in fields or not isinstance(args,dict) or set(args)-fields[operation]:raise ValueError('Unsupported lifecycle operation or arguments')
        if len(json.dumps(args).encode())>32768:raise ValueError('Lifecycle arguments exceed their bound')
        if operation=='inspect':return self.inspect()
        if operation=='receipt':return {'receipt':self.saved(args.get('commandId'))}
        if operation=='export.read':return self.read_export(args)
        self.app.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=self.app/'lifecycle.lock'
        if lock.is_symlink():raise ValueError('Lifecycle lock cannot be a symlink')
        with FileLock(str(lock),timeout=0):
            if operation=='delete.preview':return self.preview()
            if operation not in {'fork','export','delete','delete.standard','restore'}:raise ValueError('Unknown native lifecycle operation')
            command=args.get('commandId');previous=self.saved(command);signature=fingerprint({'operation':operation,'args':args})
            if previous:
                if previous['signature']!=signature:raise ValueError('Lifecycle commandId conflicts with its original request')
                return {**previous.get('result',{}),'receipt':previous,'replayed':False}
            with self.own():
                inspected=self.inspect()
                if inspected['location']['blocked']:raise ValueError('Resolve native execution relocation before lifecycle changes')
                if args.get('expectedHistoryRevision')!=inspected['historyRevision']:raise ValueError('Native history changed; review a fresh revision')
                if operation=='fork':prepared=self.prepare_fork(args)
                elif operation=='export':prepared=self.prepare_export(args)
                elif operation=='delete':prepared=self.validate_delete(args,inspected)
                else:prepared=None
                receipt={'commandId':command,'operation':operation,'status':'running','createdAt':time.time(),'replayed':False}
                self.save(command,signature,receipt)
                try:
                    if operation=='fork':result=self.fork(args,prepared)
                    elif operation=='export':result=self.export(args,prepared)
                    else:
                        value={'sessionId':self.identity,'historyCwd':self.cwd,'deleted':operation in {'delete','delete.standard'},'commandId':command,'updatedAt':time.time()}
                        write(self.app/'lifecycle.json',value)
                        result={**value,'preserved':PRESERVED,'historyRevision':inspected['historyRevision']}
                    receipt={**receipt,'status':'completed','result':result};self.save(command,signature,receipt)
                    return {**result,'receipt':receipt}
                except BaseException:
                    self.save(command,signature,{**receipt,'status':'unknown'});raise
    def preview(self):
        inspected=self.inspect();token=uuid.uuid4().hex
        value={'confirmationToken':token,'sessionId':self.identity,'historyCwd':self.cwd,'historyRevision':inspected['historyRevision'],'expiresAt':time.time()+600,'summary':{'behavior':'remove-from-discovery','canonicalFilesDeleted':0},'preserved':PRESERVED}
        write(self.app/'delete-preview.json',value);return value
    def validate_delete(self,args,inspected):
        preview=record(self.app/'delete-preview.json')
        if args.get('confirmationToken')!=preview['confirmationToken'] or time.time()>preview['expiresAt'] or preview['historyRevision']!=inspected['historyRevision']:raise ValueError('Deletion preview expired or changed; review again')
        return preview
    def prepare_fork(self,args):
        target=args.get('targetSessionId')
        if not isinstance(target,str) or str(uuid.UUID(target))!=target:raise ValueError('Reserved target UUID required')
        destination=self.server.directory(self.cwd,target);target_app=location(home(self.server),target)
        if destination.exists() or target_app.exists():raise ValueError('Fork target already exists; inspect original creation receipt, never replay')
        rows,metadata,revision=canonical(self.directory,args['expectedHistoryRevision']);selected=scope_rows(rows,args,fork=True)
        from .native.session_store import complete_tool_exchanges
        from .native.host.session import repair_interrupted_receipts
        context=repair_interrupted_receipts(complete_tool_exchanges([row for _,row in selected]))
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
        return context,metadata,revision,copied
    def fork(self,args,prepared):
        from amplifier_foundation.session.history import SessionHistoryStore
        context,metadata,revision,copied=prepared;target=args['targetSessionId'];target_app=location(home(self.server),target)
        target_app.mkdir(parents=True,exist_ok=False,mode=0o700)
        marker={'state':'reserved','commandId':args['commandId'],'sourceSessionId':self.identity,'sourceRevision':revision,'bundle':metadata.get('bundle') or metadata['bundle_name']}
        write(target_app/'history-fork.json',marker)
        destination=self.server.directory(self.cwd,target);destination.mkdir(parents=True,exist_ok=False,mode=0o700)
        execution=state(self.server,self.identity,self.cwd)['executionDirectory']
        safe={key:copy.deepcopy(metadata[key]) for key in ('bundle','bundle_name','working_dir','name','name_source','description','model','provider') if key in metadata}
        safe.update(session_id=target,parent_id=None,created_at=time.time(),status='forked',preserve_system=True,fork={'source_session_id':self.identity,'source_revision':revision,'through_message_id':args.get('throughMessageId'),'jobs_replayed':False})
        from amplifier_foundation.session.shared_state import SharedSessionStore
        target_handle=SharedSessionStore(self.cwd,target).acquire(app='amplifier-acp-fork',pid=os.getpid())
        try:
            SessionHistoryStore(destination,session_id=target).save(context,safe,preserve_system=True)
            for name,value in copied.items():write(target_app/name,value)
            write(target_app/'execution-location.json',{'sessionId':target,'historyCwd':self.cwd,'executionDirectory':execution,'executionRevision':0})
            result={'sessionId':target,'creationConfirmed':True,'historyCwd':self.cwd,'executionDirectory':execution,'historyRevision':history.inspect(destination)['revision'],'source':{'sessionId':self.identity,'historyRevision':revision,**({'throughMessageId':args['throughMessageId']} if args.get('throughMessageId') else {})},'workReplayed':False}
            write(target_app/'history-fork.json',{**marker,'state':'committed','result':result});return result
        finally:target_handle.release()
    def prepare_export(self,args):
        if type(args.get('minimal',False)) is not bool:raise ValueError('Export minimal flag must be a boolean')
        rows,metadata,revision=canonical(self.directory,args['expectedHistoryRevision']);rows=scope_rows(rows,args)
        public=[];omissions=[];attachments=0
        for position,row in rows:
            updates,issues=history.content_updates(row,position);omissions.extend(issues)
            if not history.visible(row):continue
            chunks=[u for u in updates if u['sessionUpdate'] in {'user_message_chunk','agent_message_chunk'}]
            if not chunks:continue
            texts=[];refs=[]
            for update in chunks:
                block=update['content']
                if block['type']=='text':texts.append(block['text'])
                else:
                    attachments+=1;refs.append({'type':block['type'],**({'uri':block['uri']} if 'uri' in block else {}),'omittedPayload':True})
            public.append({'id':message_id(row,position),'role':row['role'],'text':'\n'.join(texts),**({'attachments':refs} if refs else {}),**({} if args.get('minimal') else {'provenance':history.provenance(row)})})
        fmt=args.get('format','json')
        if fmt=='json':body=json.dumps({'sessionId':self.identity,'historyRevision':revision,'messages':public},ensure_ascii=False,indent=2).encode();mime='application/json';extension='json'
        elif fmt=='markdown':
            body=('\n\n'.join('## '+row['role'].title()+'\n\n'+row['text']+(''.join('\n\n[Attachment reference: '+r.get('uri',r['type'])+'; payload omitted]' for r in row.get('attachments',[]))) for row in public)+'\n').encode();mime='text/markdown';extension='md'
        else:raise ValueError('Unsupported export format')
        if len(body)>MAX_HISTORY:raise ValueError('Export exceeds selected 64 MiB bound')
        return body,{'filename':f'conversation-{self.identity[:20]}.{extension}','mimeType':mime,'summary':{'format':fmt,'scope':args.get('scope','all'),'minimal':args.get('minimal',False),'requestedBoundaries':{key:args[key] for key in ('fromMessageId','throughMessageId') if key in args},'resolvedBoundaries':{'fromMessageId':public[0]['id'] if public else None,'throughMessageId':public[-1]['id'] if public else None},'messageCount':len(public),'bytes':len(body),'attachmentCount':attachments,'artifactCount':0,'omissions':[{'code':'tool_receipts_and_private_reasoning_excluded'},{'code':'artifact_and_attachment_payloads_excluded'},*omissions[:100]]},'historyRevision':revision}
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
