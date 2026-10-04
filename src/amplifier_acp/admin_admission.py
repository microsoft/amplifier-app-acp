"""Distribution admission evidence in the existing maintenance command journal.

Reserved rows bind the configured native home. Absence is never a refusal.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,os
from .maintenance_gate import checked,native_home
from .journal_schema import connect,JournalUnavailable

PREFIX='native-admin-admission:v1:'
INTAKE=PREFIX+'intake'

def signature(value):return json.dumps(value,sort_keys=True,separators=(',',':'))

class AdmissionJournal:
    def __init__(self,config):
        self.home=str(checked(native_home(config)))
        app=checked(Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve())
        self.path=checked(app/'maintenance'/'receipts.sqlite3')
    @contextmanager
    def db(self,write=False):
        if write:self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        db=connect(self.path,'maintenance',write=write)
        if db is None:yield None;return
        try:
            with db:
                if write:db.execute('BEGIN IMMEDIATE')
                yield db
        finally:db.close()
    def key(self,context):return PREFIX+hashlib.sha256(context['fenceId'].encode()).hexdigest()
    def read(self,db,key):
        row=db.execute('SELECT workspace,signature,body FROM commands WHERE id=?',(key,)).fetchone() if db else None
        if row is None:return None
        if row[0]!=self.home or not isinstance(row[2],str) or len(row[2])>16384:raise JournalUnavailable('Native admission authority identity is unavailable')
        value=json.loads(row[2])
        if value.get('version')!=1 or row[1]!=signature(value['context']):raise JournalUnavailable('Native admission authority binding is unavailable')
        return value
    def put(self,db,key,row):
        db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?,?)',(key,self.home,signature(row['context']),signature(row)))
    def intake(self):
        with self.db() as db:return self.read(db,INTAKE)
    def begin(self,context,refusal=None):
        with self.db(True) as db:
            prior=self.read(db,self.key(context))
            if prior:
                if prior['context']!=context:raise ValueError('Original native admission binds different context')
                return prior,False
            held=self.read(db,INTAKE)
            if held:raise ValueError('Another durable native admission remains unresolved')
            row={'version':1,'context':context,'state':'refused' if refusal else 'pending'}
            if refusal:row['result']=refusal
            self.put(db,self.key(context),row)
            if not refusal:self.put(db,INTAKE,row)
            return row,True
    def update(self,context,**changes):
        with self.db(True) as db:
            row=self.read(db,self.key(context));held=self.read(db,INTAKE)
            if not row or row['context']!=context:raise ValueError('Original native admission evidence is unavailable')
            row.update(changes);self.put(db,self.key(context),row)
            if held and held['context']==context:
                if row['state'] in {'refused','released','aborted'}:db.execute('DELETE FROM commands WHERE id=?',(INTAKE,))
                else:self.put(db,INTAKE,row)
            return row
    def receipt(self,context):
        with self.db() as db:row=self.read(db,self.key(context))
        if not row or row['context']!=context:raise ValueError('Original native admission evidence is unavailable')
        return row
