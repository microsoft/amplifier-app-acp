"""Selected native worker observations and no-replay resident input receipts."""
import asyncio,hashlib,json,sqlite3,time
from pathlib import Path
from contextlib import contextmanager

ACTIVE={'working','running','starting','ready','queued','pending','stopping','idle'}
FIELDS={'sessionId','parentSessionId','callId','runId','agent','status','persistent','reports','reportId','reportTruncated','reportSourceChars','reportWindow','event','kind','taskId','phase'}
def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def bounded_id(value):
    if not isinstance(value,str) or not 1<=len(value)<=200:raise ValueError('Bounded worker or command identity required')
    return value
class Store:
    def __init__(self,home,sid):self.path=Path(home)/'sessions'/sid/'coordination.sqlite3'
    @contextmanager
    def db(self,create=False):
        for p in (self.path,*self.path.parents):
            if p.is_symlink():raise ValueError('Native coordination cannot traverse symbolic links')
        if not create and not self.path.exists():yield None;return
        if create:self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                if create:db.executescript('CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY,body TEXT NOT NULL,sequence INTEGER NOT NULL DEFAULT 0);CREATE INDEX IF NOT EXISTS workers_status ON workers(json_extract(body,\'$.status\'));CREATE INDEX IF NOT EXISTS workers_call ON workers(json_extract(body,\'$.callId\'),json_extract(body,\'$.kind\'));CREATE INDEX IF NOT EXISTS workers_visible ON workers(id) WHERE json_extract(body,\'$.supersededBy\') IS NULL;CREATE TABLE IF NOT EXISTS reports(worker TEXT,sequence INTEGER,id TEXT,body TEXT,PRIMARY KEY(worker,sequence),UNIQUE(worker,id));CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT,body TEXT);CREATE INDEX IF NOT EXISTS command_disposition ON commands(json_extract(body,\'$.disposition\'));CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,revision INTEGER);INSERT OR IGNORE INTO state VALUES(1,0);')
                yield db
        finally:db.close()
    def observe(self,event):
        kind=event.get('type','');wid=None;patch={};report=None
        if kind=='child.updated':
            wid=event.get('sessionId');patch={k:v for k,v in event.items() if k in FIELDS};patch.update(workerId=wid,kind='session');report=event.get('report')
        elif kind.startswith('job.'):
            wid=event.get('job_id');statuses={'queued':'queued','returned':'completed','failed':'error','cancelled':'cancelled','cancel_requested':'stopping','recovered':'interrupted'}
            patch={'workerId':wid,'kind':'job','status':statuses.get(kind[4:]) or event.get('status') or 'running','callId':event.get('call_id'),'agent':event.get('agent','Delegated work'),'persistent':False}
        elif kind=='runtime.closed':self.lost();return True
        else:return False
        if not isinstance(wid,str) or not 1<=len(wid)<=200:return False
        if len(json.dumps(patch).encode())>16384:raise ValueError('Native worker metadata exceeds16KiB')
        with self.db(True) as db:
            previous=db.execute('SELECT body,sequence FROM workers WHERE id=?',(wid,)).fetchone();body=json.loads(previous[0]) if previous else {};sequence=previous[1] if previous else 0
            body={**body,**patch,'updatedAt':time.time()}
            if isinstance(report,str) and report:
                rid=event.get('reportId') or 'legacy-'+signature([event.get('runId'),event.get('callId'),report]);bounded_id(rid)
                if not db.execute('SELECT 1 FROM reports WHERE worker=? AND id=?',(wid,rid)).fetchone():
                    sequence+=1;raw=report.encode();text=raw[-20000:].decode('utf-8',errors='ignore');receipt={'id':rid,'sequence':sequence,'text':text,'source':'worker','runId':event.get('runId'),'sourceTruncated':bool(event.get('reportTruncated') or len(raw)>20000),'sourceChars':event.get('reportSourceChars',len(report)),'window':event.get('reportWindow','tail')}
                    db.execute('INSERT INTO reports VALUES(?,?,?,?)',(wid,sequence,rid,json.dumps(receipt)));db.execute('DELETE FROM reports WHERE worker=? AND sequence<=?',(wid,sequence-32))
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?,?)',(wid,json.dumps(body),sequence))
            self.link_delegate(db,body)
            db.execute('UPDATE state SET revision=revision+1 WHERE id=1')
        return True
    @staticmethod
    def link_delegate(db,body):
        # A live delegated tool job and its actual native child describe one
        # visible worker. Keep both receipts; join only unique active call proof.
        call=body.get('callId')
        if not call:return
        active="json_extract(body,'$.status') IN ('working','running','starting','ready','queued','pending','stopping','idle')"
        jobs=db.execute("SELECT id FROM workers WHERE json_extract(body,'$.callId')=? AND json_extract(body,'$.kind')='job' AND "+active+' LIMIT 2',(call,)).fetchall()
        children=db.execute("SELECT id FROM workers WHERE json_extract(body,'$.callId')=? AND json_extract(body,'$.kind')='session' AND "+active+' LIMIT 2',(call,)).fetchall()
        if len(jobs)==1 and len(children)==1:
            db.execute("UPDATE workers SET body=json_set(body,'$.supersededBy',?) WHERE id=?",(children[0][0],jobs[0][0]))
            db.execute("UPDATE workers SET body=json_set(body,'$.jobId',?) WHERE id=?",(jobs[0][0],children[0][0]))
        elif len(children)>1 or len(jobs)>1:
            # Ambiguous native call identities must stay visible, not guessed.
            db.execute("UPDATE workers SET body=json_remove(body,'$.supersededBy') WHERE json_extract(body,'$.callId')=? AND json_extract(body,'$.kind')='job'",(call,))
    def lost(self):
        with self.db() as db:
            if not db:return
            rows=db.execute("SELECT id,body FROM workers WHERE json_extract(body,'$.status') IN ('working','running','starting','ready','queued','pending','stopping','idle')").fetchall()
            for wid,raw in rows:
                body={**json.loads(raw),'status':'interrupted','residency':'stopped'};db.execute('UPDATE workers SET body=? WHERE id=?',(json.dumps(body),wid))
            db.execute("UPDATE commands SET body=json_set(body,'$.disposition','unknown','$.reason','resident-runtime-lost') WHERE json_extract(body,'$.disposition')='reserved'")
            if rows:db.execute('UPDATE state SET revision=revision+1 WHERE id=1')
    def page(self,args,resident=False):
        limit=args.get('limit',50);cursor=args.get('cursor','');count=args.get('resultLimit',32);after=args.get('afterSequence',0)
        if type(limit)is not int or not 1<=limit<=100 or not isinstance(cursor,str) or len(cursor)>200 or type(count)is not int or not 1<=count<=32 or type(after)is not int or after<0:raise ValueError('Bounded worker page required')
        with self.db() as db:
            if not db:return {'available':False,'items':[],'nextCursor':None,'revision':0,'source':'native-worker-index','resident':resident}
            revision=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
            if args.get('workerId'):
                wid=bounded_id(args['workerId']);row=db.execute('SELECT body,sequence FROM workers WHERE id=?',(wid,)).fetchone()
                if not row:raise ValueError('Worker is not indexed for this native parent')
                results=[json.loads(r[0]) for r in db.execute('SELECT body FROM reports WHERE worker=? AND sequence>? ORDER BY sequence LIMIT ?',(wid,after,count))]
                return {'available':True,'item':self.public(row,resident),'results':results,'latestSequence':row[1],'revision':revision,'source':'native-worker-index','resident':resident}
            rows=db.execute("SELECT id,body,sequence FROM workers WHERE id>? AND json_extract(body,'$.supersededBy') IS NULL ORDER BY id LIMIT ?",(cursor,limit+1)).fetchall()
            return {'available':True,'items':[self.public((r[1],r[2]),resident) for r in rows[:limit]],'nextCursor':rows[limit-1][0] if len(rows)>limit else None,'revision':revision,'source':'native-worker-index','resident':resident}
    @staticmethod
    def public(row,resident):
        body=json.loads(row[0]);return {**body,'latestSequence':row[1],'residency':'resident' if resident else 'unverified','canFollowup':bool(resident and body.get('persistent') and body.get('status') in {'idle','running'}),'canInterrupt':bool(resident and body.get('status') in ACTIVE)}
    def command(self,identity,args=None):
        bounded_id(identity)
        with self.db() as db:row=db.execute('SELECT signature,body FROM commands WHERE id=?',(identity,)).fetchone() if db else None
        if row and args is not None and row[0]!=signature(args):raise ValueError('Worker command identity has different content')
        if not row:return None
        value=json.loads(row[1]);return {**value,**({'disposition':'unknown','reason':'admission-unconfirmed'} if value['disposition']=='reserved' else {}),'replayed':False}
    def save(self,args,body):
        with self.db(True) as db:db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',(args['commandId'],signature(args),json.dumps({'commandId':args['commandId'],'workerId':args['workerId'],**body})))

def inspect(server,params):
    cwd=Path(params.get('cwd','')).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():raise ValueError('Existing absolute native history workspace required')
    native=server.directory(str(cwd.resolve()),params.get('sessionId'))
    if not (native/'metadata.json').is_file():raise ValueError('Native session is absent from this authorized workspace')
    from .relocation import home
    store=Store(home(server),params['sessionId']);session=server.sessions.get(params['sessionId'])
    resident=bool(session and session.worker and not session.worker.closed and session.worker.process.returncode is None and not session.worker_retirement_pending)
    if params.get('commandId'):return {'receipt':store.command(params['commandId'])}
    return store.page(params,resident)

async def submit(session,operation,args):
    wid=bounded_id(args.get('workerId'));command=bounded_id(args.get('inputId') if operation=='worker.followup' else args.get('commandId'))
    if set(args)-({'workerId','inputId','text'} if operation=='worker.followup' else {'workerId','commandId'}):raise ValueError('Unsupported worker admission fields')
    if operation=='worker.followup' and (not isinstance(args.get('text'),str) or not args['text'].strip() or len(args['text'])>100000):raise ValueError('Nonempty bounded worker follow-up required')
    request={'commandId':command,'workerId':wid,'operation':operation,**({'text':args['text']} if operation=='worker.followup' else {})}
    async with session.coordination_lock:
        previous=session.coordination.command(command,request)
        if previous:return previous
        worker=session.worker
        if not worker or worker.closed or worker.process.returncode is not None or session.worker_retirement_pending or session.retiring or session.retired:return {'accepted':False,'executed':False,'reason':'Original resident worker is unavailable; no process started'}
        row=session.coordination.page({'workerId':wid},True)['item']
        if not row['canFollowup' if operation=='worker.followup' else 'canInterrupt']:return {'accepted':False,'executed':False,'reason':'Requested worker cannot accept this control'}
        session.coordination.save(request,{'disposition':'reserved','accepted':False})
        try:
            result=await worker.command('worker.message' if operation=='worker.followup' else 'worker.stop',worker_id=wid,input_id=command,text=args.get('text',''),expected_run_id=row.get('runId'))
            value={**result,'disposition':'accepted' if result.get('accepted') else 'rejected','replayed':False};session.coordination.save(request,value);return value
        except BaseException:
            session.coordination.save(request,{'disposition':'unknown','reason':'admission-or-effect-unconfirmed','replayed':False});raise
