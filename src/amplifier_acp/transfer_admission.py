"""Transfer admission records in the launcher's existing owner commands authority.

The maintenance writer lock orders admission with actual cross-process effects.
It is never held while acquiring the separate administration writer lock.
"""
from __future__ import annotations
import hashlib,json,os,sqlite3,stat
from contextlib import closing
from pathlib import Path
from .admin_lifecycle import AdminFenced,context
from .maintenance_gate import Gate,MaintenanceBusy,native_home

FIELDS={'commandId','fenceId','instanceId','dataScope','purpose'}
BINDING=FIELDS-{'purpose'}
PROOF=FIELDS|{'kind','verified','receiptId'}
ATTEMPTS='native.transfer.admission.attempt'
ACTIVE='native.transfer.admission.active'

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def token(value):
    if not isinstance(value,str) or not 1<=len(value)<=200 or any(ord(c)<32 for c in value):raise ValueError('Exact bounded transfer admission identity required')
    return value

def admission_request(args,*,abort=False):
    extras={'proof'} if abort else set()
    if not isinstance(args,dict) or set(args)!=FIELDS|{'ownerId'}|extras:raise ValueError('Exact transfer admission selectors required')
    ctx=context(args)
    if ctx['purpose']!='distribution-update':raise ValueError('Transfer admission abort supports distribution-update only')
    owner=token(args['ownerId'])
    if abort:
        p=args['proof']
        if not isinstance(p,dict) or set(p)!=PROOF or p.get('kind')!='distribution-admission-abort' or p.get('verified') is not True or any(p.get(k)!=ctx[k] for k in FIELDS):raise AdminFenced('Distinct authenticated transfer admission-abort proof required')
        token(p['receiptId'])
    return ctx,owner

class TransferAdmission:
    def __init__(self,config):
        self.config=config
        value=config.get('transferAuthorityDirectory')
        if not isinstance(value,str) or not Path(value).is_absolute():raise AdminFenced('Existing launcher transfer authority required')
        self.path=Path(value)/'owner.sqlite3'
        self.home=str(native_home(config))
        self.identity={'authority':str(self.path.absolute()),'nativeHome':self.home}
        self.home_id=digest(self.identity)

    def checked(self,*,optional=False):
        path=self.path
        for part in (path,*path.parents):
            if part.is_symlink():raise AdminFenced('Transfer authority cannot traverse symbolic links')
        present=any(os.path.lexists(str(path)+suffix) for suffix in ('','-wal','-shm','-journal'))
        if not present and optional:return False
        for suffix in ('','-wal','-shm','-journal'):
            selected=Path(str(path)+suffix)
            if os.path.lexists(selected) and not stat.S_ISREG(selected.lstat().st_mode):raise AdminFenced('Existing regular transfer authority required')
        if not path.is_file():raise AdminFenced('Original transfer authority is missing; no admission outcome inferred')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
            columns=db.execute('PRAGMA table_info(commands)').fetchall()
            if [(r[1],r[2].upper(),r[5]) for r in columns]!=[('scope','TEXT',1),('id','TEXT',2),('signature','TEXT',0),('body','TEXT',0)]:raise AdminFenced('Original transfer commands authority schema required')
            db.execute('SELECT scope,id,signature,body FROM commands LIMIT 0')
        return True

    def connection(self):
        self.checked()
        db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=0)
        try:db.execute('PRAGMA synchronous=FULL')
        except BaseException:db.close();raise
        return db

    def load(self,db,scope,key,signature):
        row=db.execute('SELECT signature,body FROM commands WHERE scope=? AND id=?',(scope,key)).fetchone()
        if row is None:return None
        if row[0]!=signature:raise AdminFenced('Original transfer admission binding changed')
        value=json.loads(row[1])
        if not isinstance(value,dict) or value.get('version')!=1 or value.get('identity')!=self.identity:raise AdminFenced('Original transfer admission authority differs')
        return value

    def save(self,db,scope,key,signature,value):
        db.execute('INSERT OR REPLACE INTO commands(scope,id,signature,body) VALUES(?,?,?,?)',(scope,key,signature,encoded(value)))

    def active(self,db):
        value=self.load(db,ACTIVE,self.home_id,digest(self.identity))
        if value is not None:
            if not isinstance(value.get('owners'),dict):raise AdminFenced('Transfer active admission evidence is invalid')
            for owner,row in value['owners'].items():
                token(owner)
                if not isinstance(row,dict) or set(row)!=FIELDS or context(row)!=row or row['purpose']!='distribution-update':raise AdminFenced('Transfer active admission context differs')
            if len({encoded(row) for row in value['owners'].values()})>1:raise AdminFenced('Transfer active admission contexts disagree')
        return value or {'version':1,'identity':self.identity,'owners':{}}

    def held(self):
        if not self.checked(optional=True):return None
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
            active=self.active(db);self.require_resolved(db,active);owners=active['owners']
            return next(iter(owners.values()),None)

    def require_resolved(self,db,active):
        # A retained uncertain intent or acquired record without its atomic
        # active pointer is evidence of uncertainty, never evidence of idle.
        rows=db.execute("SELECT body FROM commands WHERE scope=? AND json_extract(body,'$.identity.nativeHome')=? AND json_extract(body,'$.phase') IN ('entering','intent','unknown','acquired') LIMIT 66",(ATTEMPTS,self.home)).fetchall()
        if len(rows)>65:raise AdminFenced('Transfer admission coverage exceeds its bound')
        for (raw,) in rows:
            row=json.loads(raw)
            if row.get('identity')!=self.identity or row.get('version')!=1 or row.get('phase')!='acquired' or active['owners'].get(row.get('ownerId'))!=row.get('context'):raise AdminFenced('Retained transfer admission is uncertain; intake remains closed')
        for owner,ctx in active['owners'].items():
            key=digest([self.identity,owner,ctx['fenceId']]);signature=digest([self.identity,owner,ctx])
            row=self.original(db,key,signature,ctx,owner)
            if not row or row['phase']!='acquired' or row.get('result',{}).get('acquired') is not True:raise AdminFenced('Active transfer pointer lacks original acquisition proof')

    def assert_open(self):
        if self.held() is not None:raise AdminFenced('Durable native transfer intake is held; no transfer effect admitted')

    def selectors(self,args,extras=()):
        if not isinstance(args,dict) or set(args)!=FIELDS|{'ownerId'}|set(extras):raise ValueError('Exact transfer admission selectors required')
        ctx=context(args)
        if ctx['purpose']!='distribution-update':raise ValueError('Transfer admission abort supports distribution-update only')
        owner=token(args['ownerId']);key=digest([self.identity,owner,ctx['fenceId']])
        signature=digest([self.identity,owner,ctx])
        return ctx,owner,key,signature

    def original(self,db,key,signature,ctx,owner):
        row=self.load(db,ATTEMPTS,key,signature)
        if row is not None and (row.get('context')!=ctx or row.get('ownerId')!=owner):raise AdminFenced('Original transfer admission identity differs')
        if row is not None and row.get('phase') in {'acquired','refused','released','aborted'}:
            result=row.get('result')
            if not isinstance(result,dict) or result.get('ownerId')!=owner or any(result.get(k)!=ctx[k] for k in BINDING) or type(result.get('acquired')) is not bool or result.get('executed') is not False or type(result.get('intakeClosed')) is not bool:raise AdminFenced('Original transfer acquisition disposition is incomplete')
            token(result.get('receiptId'))
            if (row['phase']=='acquired' and result['acquired'] is not True) or (row['phase']=='refused' and result['acquired'] is not False):raise AdminFenced('Original transfer acquisition disposition disagrees')
        return row

    def prior_acquire(self,row,active,ctx,owner):
        if row['phase']=='refused':return row['result']
        if row['phase']=='acquired' and active['owners'].get(owner)==ctx:return row['result']
        raise AdminFenced('Original transfer attempt cannot be acquired again; inspect without replay')

    def acquire(self,args,*,busy=False,managed=True,inspection=None):
        ctx,owner,key,signature=self.selectors(args)
        # Completed observations never need an OS lock already owned by the
        # subsequently acquired general Admin participant.
        with closing(self.connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            row=self.original(db,key,signature,ctx,owner);active=self.active(db)
            if row:return self.prior_acquire(row,active,ctx,owner)
            self.require_resolved(db,active)
        gate=None
        reason='native-transfer-work' if busy else ('custom-probe-lifetime-unproven' if not managed else None)
        if reason is None:
            try:gate=Gate(self.config,exclusive=True)
            except MaintenanceBusy:reason='other-native-transfer-writer'
        try:
            with closing(self.connection()) as db,db:
                db.execute('BEGIN IMMEDIATE')
                row=self.original(db,key,signature,ctx,owner);active=self.active(db)
                if row:return self.prior_acquire(row,active,ctx,owner)
                self.require_resolved(db,active)
                if owner in active['owners'] or any(value!=ctx for value in active['owners'].values()):reason='another-transfer-admission'
                acquired=reason is None
                result={**(inspection or {}),'acquired':acquired,'executed':False,'intakeClosed':acquired or bool(active['owners']),**{k:ctx[k] for k in BINDING},'ownerId':owner,'receiptId':'native-transfer-acquire:'+digest([self.identity,owner,ctx,reason])}
                if acquired:
                    result['fence']=ctx;active['owners'][owner]=ctx
                    self.save(db,ACTIVE,self.home_id,digest(self.identity),active)
                else:result['reason']=reason
                row={'version':1,'identity':self.identity,'context':ctx,'ownerId':owner,'phase':'acquired' if acquired else 'refused','result':result}
                self.save(db,ATTEMPTS,key,signature,row)
                return result
        finally:
            if gate:gate.release()

    def release(self,args,*,busy=False):
        ctx,owner,key,signature=self.selectors(args,('outcome','proof'))
        outcome=args['outcome'];p=args['proof']
        rollback=outcome=='unchanged' and p=={'kind':'admission-refused'}
        if not rollback and (not isinstance(p,dict) or p.get('verified') is not True or outcome not in {'unchanged','ready'} or any(p.get(k)!=ctx[k] for k in BINDING-{'instanceId'}) or p.get('outcome')!=outcome or not p.get('receiptId') or not isinstance(p.get('instanceId'),str) or (p['instanceId']==ctx['instanceId'])!=(outcome=='unchanged')):raise AdminFenced('Exact authenticated transfer release proof required')
        request={'outcome':outcome,'proof':p}
        with closing(self.connection()) as db,db:
            db.execute('BEGIN IMMEDIATE');row=self.original(db,key,signature,ctx,owner);active=self.active(db)
            if row and row.get('releaseRequest')==request and row['phase']=='released' and owner not in active['owners']:return row['releaseResult']
            if busy or not row or row['phase']!='acquired' or active['owners'].get(owner)!=ctx:raise AdminFenced('Exact durable held transfer attempt required')
            del active['owners'][owner]
            result={'released':True,'ownerId':owner,**{k:ctx[k] for k in BINDING},'ownerIntakeClosed':False,'intakeClosed':bool(active['owners']),'fence':next(iter(active['owners'].values()),None),'workerStarted':False}
            row.update(phase='released',releaseRequest=request,releaseResult=result)
            self.save(db,ACTIVE,self.home_id,digest(self.identity),active);self.save(db,ATTEMPTS,key,signature,row)
            return result

    def abort(self,args,*,busy=False):
        admission_request(args,abort=True)
        ctx,owner,key,signature=self.selectors(args,('proof',));p=args['proof']
        with closing(self.connection()) as db:
            row=self.original(db,key,signature,ctx,owner);active=self.active(db)
            if row and row['phase']=='aborted':
                if row.get('abortProof')!=p or owner in active['owners']:raise AdminFenced('Original abort proof or intake changed')
                return row['abortResult']
        if busy:raise AdminFenced('Native transfer work remains active; abort remains closed')
        # This checks original effects in OTHER ACP processes and readiness
        # children. It never requests the Admin exclusive gate, avoiding the
        # later general Admin participant's retained-lock deadlock.
        with Gate(self.config,exclusive=True),closing(self.connection()) as db,db:
            db.execute('BEGIN IMMEDIATE');row=self.original(db,key,signature,ctx,owner);active=self.active(db)
            if not row:raise AdminFenced('Durable original transfer attempt required; legacy outcome remains unknown')
            if row['phase']=='aborted':
                if row.get('abortProof')!=p or owner in active['owners']:raise AdminFenced('Original abort proof or intake changed')
                return row['abortResult']
            if row['phase']=='acquired' and active['owners'].get(owner)==ctx:status='released';del active['owners'][owner]
            elif row['phase']=='refused' and row.get('result',{}).get('acquired') is False and row['result'].get('executed') is False and owner not in active['owners']:status='not-acquired'
            elif row['phase']=='released' and row.get('result',{}).get('acquired') is True and row.get('releaseRequest',{}).get('outcome')=='unchanged' and row.get('releaseResult',{}).get('released') is True and owner not in active['owners']:status='released'
            else:raise AdminFenced('Original transfer disposition is uncertain; abort remains closed')
            result={'ownerId':owner,**{k:ctx[k] for k in BINDING},'status':status,'receiptId':'native-transfer-abort:'+digest([self.identity,owner,ctx,p,status])}
            row.update(phase='aborted',abortProof=p,abortResult=result)
            self.save(db,ACTIVE,self.home_id,digest(self.identity),active);self.save(db,ATTEMPTS,key,signature,row)
            return result
