"""Canonical naming metadata and exact command receipts; no history or model replay."""
from __future__ import annotations
import hashlib,json,time
from datetime import datetime,UTC
from pathlib import Path
from amplifier_foundation.session.metadata import SessionMetadataStore,metadata_lock
from .configuration_clone import location
from .native.naming import automatic_metadata,accept_generated

CAPABILITY={'version':1,'method':'_amplifier/naming','passiveMetadata':True,'commandIds':True,'replayUnknown':False,'titlePreview':True,'prepareGeneration':True,'backfill':{'version':1,'cold':True,'boundedExcerpt':True,'durableAttempts':True}}
MAX_BYTES=16384

def encoded(value):
    body=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(body.encode())>MAX_BYTES:raise ValueError('Naming request exceeds its bound')
    return body

def identity(value):
    if not isinstance(value,str) or not 1<=len(value)<=200 or any(ord(c)<32 for c in value):raise ValueError('Original naming command identity required')
    return value

def revision(value):
    return type(value) is int and value>=0

def projection(current,applied=False):
    return {'applied':applied,'title':current.get('name',''),'naming':{'automatic':automatic_metadata(current),
        'nameRevision':current.get('name_revision',0),'policyRevision':current.get('name_policy_revision',0)}}

class NamingUnknown(RuntimeError):
    data={'outcome':'unknown','replayed':False}

class Commands:
    def __init__(self,home,session,directory):
        self.path=location(home,session)/'lifecycle-receipts.sqlite3'
        self.scope={'sessionId':session,'directory':str(Path(directory).resolve())}
    def connect(self,write=False):
        from .journal_schema import connect
        return connect(self.path,'lifecycle',write=write,timeout=0,isolation_level=None)
    def key(self,command):return 'naming:'+hashlib.sha256(identity(command).encode()).hexdigest()
    def receipt(self,command):
        db=self.connect()
        if db is None:return None
        try:
            row=db.execute('SELECT CASE WHEN length(CAST(body AS BLOB))<=? THEN body END FROM receipts WHERE id=?',(MAX_BYTES,self.key(command))).fetchone()
            if not row:return None
            if row[0] is None:raise NamingUnknown('Naming receipt exceeds its bound; preserve original authority')
            value=json.loads(row[0])
            if value.get('scope')!=self.scope:return None
            return value
        finally:db.close()
    def claim(self,operation,args):
        command=identity(args.get('commandId'));key=self.key(command)
        signature=hashlib.sha256(encoded({'scope':self.scope,'operation':operation,'args':args}).encode()).hexdigest()
        db=self.connect(True)
        try:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT signature,CASE WHEN length(CAST(body AS BLOB))<=? THEN body END FROM receipts WHERE id=?',(MAX_BYTES,key)).fetchone()
            if old:
                if old[0]!=signature:raise ValueError('Naming command identity conflicts with its original request')
                if old[1] is None:raise NamingUnknown('Original naming receipt is unavailable')
                row=json.loads(old[1]);db.execute('COMMIT');return False,row
            row={'version':1,'commandId':command,'operation':operation,'scope':self.scope,'status':'unknown','replayed':False,'createdAt':time.time()}
            db.execute('INSERT INTO receipts VALUES(?,?,?)',(key,signature,encoded(row)));db.execute('COMMIT');return True,row
        finally:db.close()
    def finish(self,row,result):
        value={**row,'status':'succeeded','result':result,'completedAt':time.time()};body=encoded(value)
        db=self.connect(True)
        try:db.execute('UPDATE receipts SET body=? WHERE id=?',(body,self.key(row['commandId'])))
        finally:db.close()
    async def perform(self,operation,args,invoke):
        fresh,row=self.claim(operation,args)
        if not fresh:
            if row['status']=='succeeded':return row['result']
            raise NamingUnknown('Original naming outcome is unknown; inspect its receipt without replay')
        result=await invoke()
        self.finish(row,result)
        return result

def validate(operation,args):
    keys={'commit':{'commandId','candidate'},'policy':{'commandId','automatic','expectedNameRevision','expectedPolicyRevision'},'rename':{'commandId','title'},'generate':{'commandId'}}
    if operation not in keys or set(args)!=keys[operation]:raise ValueError('Exact naming operation arguments required')
    identity(args['commandId']);encoded(args)
    if operation=='commit':
        candidate=args['candidate']
        if not isinstance(candidate,dict) or set(candidate)-{'name','description','name_revision','name_policy_revision'} or not {'name','name_revision','name_policy_revision'}<=set(candidate):raise ValueError('Exact naming candidate required')
        if not isinstance(candidate['name'],str) or not candidate['name'].strip() or len(candidate['name'])>200:raise ValueError('Bounded generated name required')
        if not revision(candidate['name_revision']) or not revision(candidate['name_policy_revision']):raise ValueError('Candidate revisions required')
        if 'description' in candidate and (not isinstance(candidate['description'],str) or len(candidate['description'])>1000):raise ValueError('Bounded naming description required')
    elif operation=='policy':
        if type(args['automatic']) is not bool or not revision(args['expectedNameRevision']) or not revision(args['expectedPolicyRevision']):raise ValueError('Exact naming policy and revisions required')
    elif operation=='rename':
        if not isinstance(args['title'],str) or not args['title'].strip() or len(args['title'])>200:raise ValueError('A nonempty name up to 200 characters is required')

def mutate(directory,operation,args):
    if operation=='commit':
        current,applied=accept_generated(directory,args['candidate'],explicit=True)
        return projection(current,applied)
    store=SessionMetadataStore(directory)
    with metadata_lock(directory):
        current=store.read();name_revision=current.get('name_revision',0);policy_revision=current.get('name_policy_revision',0)
        if operation=='policy':
            if (name_revision,policy_revision)!=(args['expectedNameRevision'],args['expectedPolicyRevision']):return {**projection(current),'reason':'naming-revision-conflict','executed':False}
            current.update(name_auto=args['automatic'],name_auto_revision=name_revision,name_policy_revision=policy_revision+1)
        else:
            current.update(name=args['title'].strip(),name_source='manual',name_revision=name_revision+1,
                name_updated_at=datetime.now(UTC).isoformat(),name_auto=False,name_auto_revision=name_revision+1,name_policy_revision=policy_revision+1)
        store.history._save_metadata_unlocked(current)
        return projection(current,True)

async def handle(server,params):
    if set(params)!={'sessionId','cwd','operation','args'}:raise ValueError('Exact canonical naming selectors required')
    from .session_lifecycle import Lifecycle
    from .relocation import home
    owner=Lifecycle(server,params['sessionId'],params['cwd']);directory=owner.directory
    if not (directory/'metadata.json').is_file():raise ValueError('Canonical naming metadata is unavailable')
    operation=params['operation'];args=params['args']
    if not isinstance(args,dict):raise ValueError('Naming arguments must be an object')
    commands=Commands(home(server),owner.identity,directory)
    if operation=='read':
        if args:raise ValueError('Naming inspection takes no arguments')
        return projection(SessionMetadataStore(directory).read())
    if operation=='preview':
        if args:raise ValueError('Title preview takes no arguments')
        from .chat_title_preview import read
        metadata=SessionMetadataStore(directory).read()
        return read(directory,{'title':metadata.get('name','Conversation '+owner.identity[:8]),'titleSource':metadata.get('name_source')})
    if operation=='receipt':
        if set(args)!={'commandId'}:raise ValueError('Exact original naming command required')
        return {'receipt':commands.receipt(args['commandId']),'replayed':False}
    if operation=='prepare':
        if args:raise ValueError('Naming preparation takes no arguments')
        from .maintenance_gate import Gate
        from .native.naming import prepare_regeneration
        with Gate(server.config):return projection(prepare_regeneration(directory))
    if operation=='backfill':
        if args:raise ValueError('Automatic naming takes no client-provided prompt or command')
        from .maintenance_gate import Gate
        from .naming_backfill import perform
        with Gate(server.config):return await perform(owner,commands)
    validate(operation,args)
    if operation=='generate':raise ValueError('Generation requires the selected naming lifecycle')
    from .maintenance_gate import Gate
    with Gate(server.config):
        async def invoke():return mutate(directory,operation,args)
        return await commands.perform(operation,args,invoke)
