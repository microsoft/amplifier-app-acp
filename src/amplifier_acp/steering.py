"""Durable anchored-steering receipts. They never replay an uncertain input."""
import hashlib,json,sqlite3,time
from pathlib import Path
from contextlib import contextmanager

def validate(args):
    if not isinstance(args,dict) or set(args)-{'inputId','activeInputId','text','provenance','public_message'}:raise ValueError('Unsupported steering fields')
    if 'public_message' in args:
        from .public_message import validate as validate_public
        validate_public(args['public_message'],args.get('inputId'))
    for name in ('inputId','activeInputId'):
        if not isinstance(args.get(name),str) or not 1<=len(args[name])<=128:raise ValueError('Bounded steering and active input IDs required')
    if not isinstance(args.get('text'),str) or not args['text'].strip() or len(args['text'])>16000:raise ValueError('Bounded nonempty steering text required')
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
        session.steering.reserve(args,generation)
        try:
            result=await session.worker.control('steer.submit',{**args,'generationId':generation})
            if not result.get('accepted'):
                return session.steering.update(args['inputId'],{**result,'disposition':'rejected'})
            return session.steering.update(args['inputId'],result)
        except BaseException:
            session.steering.update(args['inputId'],{'disposition':'unknown','reason':'admission-or-effect-unconfirmed'});raise
