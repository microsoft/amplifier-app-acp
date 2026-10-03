"""Launcher-authorized, reviewed import of inert native history.

Uploads and prepared evidence are bounded private SQLite blobs. Only commit
creates native files, using the public Foundation writer and ownership lease.
A crash after reservation never authorizes a second execution of the command.
"""
from __future__ import annotations
import base64,copy,hashlib,json,os,re,sqlite3,time,uuid
from pathlib import Path
from .maintenance_gate import checked,native_home
from .relocation import write
from . import history

MAX_BYTES=1048576
CHUNK_BYTES=16384
MAX_MESSAGES=10000
OPERATIONS={f'history.import.{name}' for name in ('begin','chunk','preview','commit','receipt')}


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(value).hexdigest()
def token(value,name,maximum=200):
    if not isinstance(value,str) or not 1<=len(value)<=maximum or any(ord(c)<32 for c in value):raise ValueError(f'Bounded {name} required')
    return value

def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON object keys are unsupported')
            result[key]=value
        return result
    def invalid(_):raise ValueError('Non-finite JSON numbers are unsupported')
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    except (RecursionError,UnicodeError) as exc:raise ValueError('Unsupported or deeply nested UTF-8 JSON') from exc


def normalize(raw,spec):
    text=raw.decode('utf-8');metadata={}
    value=[strict_json(line) for line in text.splitlines() if line.strip()] if spec['format']=='jsonl' else strict_json(text)
    if isinstance(value,dict):metadata=value.get('metadata',{});value=value.get('messages')
    if not isinstance(metadata,dict):raise ValueError('Import metadata must be an object')
    if not isinstance(value,list) or not 1<=len(value)<=MAX_MESSAGES:raise ValueError('Import requires 1 to 10000 message rows')
    rows=[];omissions=[];roles={};rewritten=0
    for index,original in enumerate(value):
        if not isinstance(original,dict) or original.get('role') not in {'user','assistant','system','developer','tool'}:raise ValueError('Unsupported message role or event-log format')
        row=copy.deepcopy(original)
        if 'content' not in row and isinstance(row.get('text'),str):row['content']=row.pop('text');rewritten+=1
        calls=row.get('tool_calls')
        if calls is not None and (not isinstance(calls,list) or any(not isinstance(call,dict) or 'function' in call and not isinstance(call['function'],dict) for call in calls)):raise ValueError('Tool calls must be typed objects')
        content=row.get('content')
        if not isinstance(content,(str,list)) or isinstance(content,list) and any(not isinstance(block,dict) or not isinstance(block.get('type'),str) for block in content):raise ValueError('Message content must be text or typed content blocks')
        original_metadata=row.get('metadata') or {}
        if not isinstance(original_metadata,dict):raise ValueError('Message metadata must be an object')
        # Historical claims are retained only in the private original upload.
        # Imported IDs cannot collide with future host admissions or grant consent.
        row['metadata']={'inputOrigin':'import','importSource':{'sha256':spec['sha256'],'row':index,'authorization':'unverified-import'}}
        if row['role']=='user':row['metadata']['amplifier_input']={'id':f"import:{spec['sha256'][:24]}:{index}",'kind':'user','source':'user'}
        for name in ('inputOrigin','questionId','questionRevision','answerProvenance','voiceId','voiceItemId','scheduledRunId','id'):
            row.pop(name,None)
        roles[row['role']]=roles.get(row['role'],0)+1;rows.append(row)
    from .native.session_store import complete_tool_exchanges
    positions={};prepared=complete_tool_exchanges(rows,positions=positions);original_positions=set(positions.values())
    interrupted=0
    for index,row in enumerate(prepared):
        if index not in original_positions:
            interrupted+=1;body=json.loads(row['content']);body['message']='This call was incomplete in the imported file. Its outcome is unconfirmed; no work was replayed.';row['content']=json.dumps(body)
            row['metadata']={'inputOrigin':'import','importSource':{'sha256':spec['sha256'],'syntheticInterruptedReceipt':True,'authorization':'unverified-import'}}
    title=spec.get('title') or 'Imported transcript'
    bundle=spec.get('bundle') or metadata.get('bundle_name') or metadata.get('bundle') or spec.get('defaultBundle')
    if not isinstance(bundle,str) or not bundle.strip() or len(bundle)>4096:raise ValueError('Import requires an explicit bundle override, recorded bundle, or launcher default')
    bundle=bundle.strip()
    if any(ord(c)<32 for c in bundle):raise ValueError('Bundle identity contains control characters')
    omissions=[{'code':'executable_authority_not_imported'},{'code':'original_metadata_retained_in_private_source_only'},{'code':'external_attachment_and_artifact_payloads_not_copied'}]
    if rewritten:omissions.append({'code':'export_text_normalized_to_content','count':rewritten})
    summary={'messageCount':len(prepared),'sourceMessageCount':len(value),'interruptedToolCalls':interrupted,'roles':roles,'title':title,'bundle':bundle,'omissions':omissions}
    result={'messages':prepared,'summary':summary}
    if len(encoded(result))>4*MAX_BYTES:raise ValueError('Normalized import exceeds its 4 MiB bound')
    return result


class HistoryImport:
    def __init__(self,config):
        self.config=config;self.native=native_home(config)
        self.app=checked(Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve())
        self.root=checked(self.app/'history-imports');self.path=self.root/'imports.sqlite3'
    def db(self,create=True):
        if not create:
            if not self.path.exists():return None
            checked(self.path)
            con=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=0,isolation_level=None);con.row_factory=sqlite3.Row
            return con
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);checked(self.path)
        con=sqlite3.connect(self.path,timeout=0,isolation_level=None);os.chmod(self.path,0o600);con.row_factory=sqlite3.Row
        con.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,signature TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,spec TEXT NOT NULL,received INTEGER NOT NULL DEFAULT 0,body BLOB NOT NULL,prepared BLOB,preview TEXT UNIQUE,consumed TEXT,target TEXT);
        ''')
        if 'target' not in {row[1] for row in con.execute('PRAGMA table_info(uploads)')}:con.execute('ALTER TABLE uploads ADD COLUMN target TEXT')
        con.execute('CREATE INDEX IF NOT EXISTS uploads_command ON uploads(consumed)')
        return con
    def receipt(self,cwd,identity):
        con=self.db(False)
        if con is None:return None
        try:
            row=con.execute('SELECT cwd,body FROM commands WHERE id=?',(identity,)).fetchone()
            if not row:return None
            if row['cwd']!=cwd:raise ValueError('Import receipt belongs to another authorized workspace')
            receipt=json.loads(row['body'])
            if receipt['status']=='unknown' and receipt['operation']=='history.import.commit':
                upload=con.execute('SELECT target FROM uploads WHERE consumed=?',(identity,)).fetchone()
                if upload and upload['target']:
                    from .session_lifecycle import record
                    marker=checked(self.app/'sessions'/upload['target']/'history-import.json')
                    if marker.exists():
                        proof=record(marker)
                        if proof.get('state')=='committed' and proof.get('commandId')==identity and proof.get('result',{}).get('historyCwd')==cwd:
                            return {**receipt,'status':'succeeded','result':proof['result']}
            return receipt
        finally:con.close()
    def perform(self,cwd,operation,args):
        allowed={
            'begin':{'commandId','format','bytes','sha256','title','bundle'},'chunk':{'commandId','uploadId','offset','contentBase64'},
            'preview':{'commandId','uploadId'},'commit':{'commandId','previewHash'},'receipt':{'commandId'}}
        name=operation.rsplit('.',1)[-1]
        if name not in allowed or set(args)-allowed[name]:raise ValueError('Unexpected history import field')
        identity=token(args.get('commandId'),'commandId')
        if name=='receipt':return self.receipt(cwd,identity)
        signature=digest(encoded({'cwd':cwd,'operation':operation,'args':args}));con=self.db()
        try:
            con.execute('BEGIN IMMEDIATE')
            old=con.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if old:
                if old['signature']!=signature:raise ValueError('Import command identity already binds different arguments')
                con.execute('ROLLBACK');return json.loads(old['body'])
            receipt={'commandId':identity,'operation':operation,'status':'unknown','replayed':False}
            con.execute('INSERT INTO commands VALUES(?,?,?,?)',(identity,cwd,signature,encoded(receipt).decode()))
            if name=='commit':
                prepared=self.prepare_commit(con,cwd,args)
                con.execute('COMMIT')  # Durable reservation before native-file effects.
                try:result=self.commit(cwd,identity,*prepared)
                except BaseException:
                    # Reserved target and partial files remain fenced; no automatic replay.
                    raise
                con.execute('BEGIN IMMEDIATE')
            else:result=getattr(self,name)(con,cwd,args)
            receipt.update(status='succeeded',result=result)
            con.execute('UPDATE commands SET body=? WHERE id=?',(encoded(receipt).decode(),identity));con.execute('COMMIT');return receipt
        except (ValueError,UnicodeError,TypeError,KeyError,RecursionError) as exc:
            if con.in_transaction:
                con.execute('ROLLBACK')
                # Conclusive pre-effect validation; never overwrite a prior command.
                failed={'commandId':identity,'operation':operation,'status':'failed','executed':False,'replayed':False,'error':str(exc)[:1000]}
                con.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,?)',(identity,cwd,signature,encoded(failed).decode()))
                if con.execute('SELECT signature FROM commands WHERE id=?',(identity,)).fetchone()[0]!=signature:raise
                return failed
            raise
        finally:
            if con.in_transaction:con.execute('ROLLBACK')
            con.close()
    def upload(self,con,cwd,identity):
        token(identity,'uploadId',64);row=con.execute('SELECT * FROM uploads WHERE id=?',(identity,)).fetchone()
        if not row or row['cwd']!=cwd:raise ValueError('Upload is unavailable in this authorized workspace')
        return row
    def begin(self,con,cwd,args):
        if args.get('format') not in {'json','jsonl'}:raise ValueError('Choose JSON or JSONL message history')
        if type(args.get('bytes')) is not int or not 1<=args['bytes']<=MAX_BYTES:raise ValueError('Import size must be 1 to 1048576 bytes')
        if not isinstance(args.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',args['sha256']):raise ValueError('Exact lowercase source SHA256 required')
        if 'title' in args:token(args['title'],'title',500)
        if 'bundle' in args:token(args['bundle'],'bundle',4096)
        identity=uuid.uuid4().hex;spec={k:v for k,v in args.items() if k!='commandId'}
        if isinstance(self.config.get('bundle'),str):spec['defaultBundle']=self.config['bundle']
        con.execute('INSERT INTO uploads(id,cwd,spec,body) VALUES(?,?,?,?)',(identity,cwd,encoded(spec).decode(),b''))
        return {'uploadId':identity,'receivedBytes':0}
    def chunk(self,con,cwd,args):
        row=self.upload(con,cwd,args.get('uploadId'))
        if row['preview'] or row['consumed']:raise ValueError('Sealed upload cannot accept more bytes')
        if type(args.get('offset')) is not int or args['offset']!=row['received']:raise ValueError('Chunk offset must exactly match received bytes')
        data=args.get('contentBase64')
        if not isinstance(data,str) or not 1<=len(data)<=21848:raise ValueError('Chunk must contain at most 16384 bytes')
        try:raw=base64.b64decode(data,validate=True)
        except ValueError:raise ValueError('Invalid chunk base64') from None
        if not 1<=len(raw)<=CHUNK_BYTES or base64.b64encode(raw).decode()!=data:raise ValueError('Canonical base64 of 1 to 16384 bytes required')
        total=row['received']+len(raw)
        if total>json.loads(row['spec'])['bytes']:raise ValueError('Chunk exceeds declared import size')
        con.execute('UPDATE uploads SET body=?,received=? WHERE id=?',(row['body']+raw,total,row['id']))
        return {'uploadId':row['id'],'receivedBytes':total}
    def preview(self,con,cwd,args):
        row=self.upload(con,cwd,args.get('uploadId'));spec=json.loads(row['spec'])
        if row['consumed']:raise ValueError('Import has already reserved a native target')
        if row['received']!=spec['bytes'] or digest(row['body'])!=spec['sha256']:raise ValueError('Uploaded source length or SHA256 does not match')
        prepared=normalize(row['body'],spec);preview=digest(encoded({'uploadId':row['id'],'cwd':cwd,'sourceSha256':spec['sha256'],'prepared':prepared}))
        con.execute('UPDATE uploads SET prepared=?,preview=? WHERE id=?',(encoded(prepared),preview,row['id']))
        return {'previewHash':preview,'uploadId':row['id'],'sourceSha256':spec['sha256'],'summary':prepared['summary'],'workReplayed':False}
    def prepare_commit(self,con,cwd,args):
        preview=token(args.get('previewHash'),'previewHash',64);row=con.execute('SELECT * FROM uploads WHERE preview=? AND cwd=?',(preview,cwd)).fetchone()
        if not row or not row['prepared']:raise ValueError('Exact reviewed import preview is unavailable')
        if row['consumed']:raise ValueError('Preview already reserved a native target; inspect the original command')
        target=str(uuid.uuid4());con.execute('UPDATE uploads SET consumed=?,target=? WHERE id=?',(args['commandId'],target,row['id']))
        return target,dict(row)
    def commit(self,cwd,identity,target,row):
        from .native.session_files import project_slug
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session.shared_state import SharedSessionStore
        prepared=json.loads(row['prepared']);summary=prepared['summary'];spec=json.loads(row['spec'])
        app=checked(self.app/'sessions'/target);app.mkdir(parents=True,exist_ok=False,mode=0o700)
        marker={'state':'reserved','commandId':identity,'bundle':summary['bundle'],'sourceSha256':spec['sha256'],'previewHash':row['preview']}
        write(app/'history-import.json',marker)
        directory=checked(self.native/'projects'/project_slug(cwd)/'sessions'/target);directory.mkdir(parents=True,exist_ok=False,mode=0o700)
        metadata={'session_id':target,'parent_id':None,'created_at':time.time(),'status':'imported','name':summary['title'],'name_source':'import','working_dir':cwd,'bundle':summary['bundle'],'bundle_name':summary['bundle'],'preserve_system':True,'imported_file':True,'jobs_replayed':False,'import_source':{'sha256':spec['sha256'],'format':spec['format'],'command_id':identity,'authorization':'unverified-import'}}
        lease=SharedSessionStore(cwd,target).acquire(app='amplifier-acp-import',pid=os.getpid())
        try:
            SessionHistoryStore(directory,session_id=target).save(prepared['messages'],metadata,preserve_system=True)
            write(app/'execution-location.json',{'sessionId':target,'historyCwd':cwd,'executionDirectory':cwd,'executionRevision':0})
            result={'nativeSessionId':target,'historyCwd':cwd,'executionDirectory':cwd,'historyRevision':history.inspect(directory)['revision'],'title':summary['title'],'bundle':summary['bundle'],'creationConfirmed':True,'workReplayed':False,'sourceSha256':spec['sha256'],'previewHash':row['preview'],'importCommandId':identity}
            write(app/'history-import.json',{**marker,'state':'committed','result':result});return result
        finally:lease.release()
