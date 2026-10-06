"""Durable anchored-steering receipts. They never replay an uncertain input."""
import hashlib,json,sqlite3,time
from pathlib import Path
from contextlib import contextmanager

def validate(args):
    if not isinstance(args,dict) or set(args)-{'inputId','activeInputId','text','prompt','reply','provenance','public_message','voiceDelivery'}:raise ValueError('Unsupported steering fields')
    if 'voiceDelivery' in args:
        from .native.input_provenance import validate_prompt
        validate_prompt({'inputOrigin':'voice','voiceDelivery':args['voiceDelivery']})
    if 'public_message' in args:
        from .public_message import validate as validate_public
        validate_public(args['public_message'],args.get('inputId'))
    for name in ('inputId','activeInputId'):
        if not isinstance(args.get(name),str) or not 1<=len(args[name])<=128:raise ValueError('Bounded steering and active input IDs required')
    if 'prompt' in args:
        blocks=args['prompt']
        if 'text' in args or not isinstance(blocks,list) or not 1<=len(blocks)<=33 or not isinstance(blocks[0],dict) or blocks[0].get('type')!='text' or not isinstance(blocks[0].get('text'),str) or len(blocks[0]['text'])>16000 or len(json.dumps(blocks,allow_nan=False).encode())>8*1024*1024:
            raise ValueError('Bounded steering content required')
    elif not isinstance(args.get('text'),str) or not args['text'].strip() or len(args['text'])>16000:raise ValueError('Bounded nonempty steering text required')
    if 'reply' in args and 'prompt' not in args:raise ValueError('Quoted steering requires negotiated content')
    if 'provenance' in args and (not isinstance(args['provenance'],dict) or len(json.dumps(args['provenance'],allow_nan=False).encode())>8192):raise ValueError('Bounded steering provenance required')

def signature(args):return hashlib.sha256(json.dumps(args,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
class Receipts:
    def __init__(self,home,session_id):self.path=Path(home)/'sessions'/session_id/'steering-receipts.sqlite3'
    @contextmanager
    def db(self,create=False,*,write=False):
        from .journal_schema import connect
        db=connect(self.path,'steering',write=create or write,create=create)
        if db is None:yield None;return
        try:
            with db:yield db
        finally:db.close()
    def read(self,identity,args=None):
        with self.db() as db:row=db.execute('SELECT signature,body FROM receipts WHERE id=?',(identity,)).fetchone() if db else None
        if row and args is not None and row[0]!=signature(args):raise ValueError('Steering input identity conflicts with its durable request')
        return json.loads(row[1]) if row else None
    def reserve(self,args,generation):
        value={'inputId':args['inputId'],'activeInputId':args['activeInputId'],'generationId':generation,'disposition':'reserved','accepted':False,'createdAt':time.time()}
        with self.db(create=True) as db:db.execute('INSERT INTO receipts VALUES (?,?,?)',(args['inputId'],signature(args),json.dumps(value)))
        return value
    def update(self,identity,values):
        with self.db(write=True) as db:
            if not db:return None
            row=db.execute('SELECT body FROM receipts WHERE id=?',(identity,)).fetchone()
            if not row:return None
            previous=json.loads(row[0]);old=previous['disposition'];new=values.get('disposition',old)
            if old in {'applied','held','unknown','rejected'} and new in {'reserved','queued'}:return previous
            if old=='applied' and new!='applied':return previous
            value={**previous,**values,'updatedAt':time.time()};db.execute('UPDATE receipts SET body=? WHERE id=?',(json.dumps(value),identity));return value
    def uncertain(self,generation=None):
        with self.db() as db:
            if not db:return []
            # One active native runtime admits at most 2,000 IDs. This selected
            # session scan is limited to the in-flight states, not history.
            rows=db.execute("SELECT id,body FROM receipts WHERE json_extract(body,'$.disposition') IN ('reserved','queued') LIMIT 2000").fetchall()
        result=[]
        for identity,body in rows:
            row=json.loads(body)
            if generation is None or row['generationId']==generation:result.append(self.update(identity,{'disposition':'unknown','reason':'native-disposition-unconfirmed'}))
        return result

async def submit(session,args):
    validate(args)
    async with session.steering_lock:
        receipt=session.steering.read(args['inputId'],args)
        if receipt:
            if receipt['disposition']=='reserved':receipt=session.steering.update(args['inputId'],{'disposition':'unknown','reason':'admission-unconfirmed'})
            return {**receipt,'replayed':False}
        if not session.prompt or session.prompt.done() or session.input_id!=args['activeInputId'] or not session.generation_id or not session.worker or session.worker.closed:
            return {'accepted':False,'executed':False,'reason':'The requested active generation is no longer available.'}
        generation=session.generation_id
        from .message_metadata import guard_input_identity
        from .relocation import home
        command={**args,'generationId':generation}
        if 'prompt' in args:
            from .content import prompt_content
            text,attachments=prompt_content(session.server.config,args['prompt'])
            command={**command,'text':text,'attachments':attachments,'contentVersion':1}
        store=None
        actor=session.server.message_principal(session.identity) if session.server.message_principal else None
        guard_input_identity(home(session.server)/'sessions'/session.identity/'message-interactions.sqlite3',
            args['inputId'],reference=args.get('reply'),input_payload=args,actor_id=actor)
        if 'reply' in args:
            if not session.server.message_principal:raise ValueError('Quoted steering requires a configured message principal')
            from .message_metadata import MessageMetadata,reserve_quote
            from .native.history_revision import context_head
            store=MessageMetadata(home(session.server),session.identity,actor_id=actor)
            quote=reserve_quote(store,session.server.directory(session.cwd,session.identity),args['reply'],
                input_id=args['inputId'],input_payload=args,epoch=context_head(home(session.server),session.identity,session.cwd))
        if store is not None:command['reply_context']=quote
        session.steering.reserve(args,generation)
        try:
            result=await session.worker.control('steer.submit',command)
            if not result.get('accepted'):
                return session.steering.update(args['inputId'],{**result,'disposition':'rejected'})
            if store is not None:
                from .message_metadata import quote_admitted
                quote_admitted(store,args['inputId'])
            return session.steering.update(args['inputId'],result)
        except BaseException:
            session.steering.update(args['inputId'],{'disposition':'unknown','reason':'admission-or-effect-unconfirmed'});raise
