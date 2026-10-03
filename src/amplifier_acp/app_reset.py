"""Reviewed app-local bundle default reset. Shared settings and auth are never touched."""
from __future__ import annotations
import base64,hashlib,json,os,time,uuid
from filelock import FileLock
from .maintenance_gate import Gate,checked
from .session_lifecycle import regular

PART='native.app-bundle-default'
OWNER='native-app-defaults'
PRESERVED=['unrelated app settings','shared Amplifier settings and keys.env','workspace settings','sign-in profiles','canonical history/events','runtime generations and caches']
FIELDS={
 'maintenance.appReset.prepare':{'commandId','parts','privateContentReviewed','credentialsReviewed','restoreCommandId'},
 'maintenance.appReset.apply':{'commandId','preparedId','reviewHash'},
 'maintenance.appReset.restore':{'commandId','preparedId','reviewHash','resetCommandId','expectedPostResetRevision'},
 'maintenance.appReset.inspect':{'commandId','preparedId','reviewHash'},
}

def refused(message):
    error=ValueError(message);error.data={'executed':False,'reason':'native-app-reset-refused','replayed':False};raise error

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class AppReset:
    def __init__(self,maintenance):
        self.owner=maintenance;self.path=checked(maintenance.app/'admin-settings.yaml')
        with self.owner.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS app_reset_revision(id INTEGER PRIMARY KEY,stamp TEXT,revision TEXT); CREATE TABLE IF NOT EXISTS app_reset_previews(id TEXT PRIMARY KEY,workspace TEXT,body TEXT,private TEXT);')
            db.execute("CREATE INDEX IF NOT EXISTS app_reset_pending ON commands(json_extract(body,'$.operation'),json_extract(body,'$.state'))")
    def current(self):
        raw=regular(self.path,65536) if self.path.exists() else None
        info=self.path.stat() if raw is not None else None
        if info is not None and info.st_nlink!=1:refused('App settings cannot use hard links')
        # Reads can change atime on a strict-atime filesystem. Preserve content,
        # replacement and writer metadata in CAS, never read-induced changes.
        stamp={'bytes':base64.b64encode(raw).decode() if raw is not None else None,'stat':{key:getattr(info,key) for key in ('st_dev','st_ino','st_mode','st_nlink','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns')} if info is not None else None}
        # The private digest never leaves the owner's store. Public revisions are random.
        hashed=digest(stamp)
        with self.owner.db() as db:
            row=db.execute('SELECT stamp,revision FROM app_reset_revision WHERE id=1').fetchone()
            revision=row[1] if row and row[0]==hashed else uuid.uuid4().hex
            if not row or row[0]!=hashed:db.execute('INSERT OR REPLACE INTO app_reset_revision VALUES(1,?,?)',(hashed,revision))
        return raw,revision
    def clear(self,raw):
        if raw is None:return None
        import yaml
        try:
            body=yaml.safe_load(raw)
            if body is None:body={}
            if not isinstance(body,dict) or 'bundle' in body and not isinstance(body['bundle'],dict):refused('App settings structure is unsupported; original preserved')
            bundle=body.get('bundle',{})
            if 'active' in bundle and bundle['active'] is not None and not isinstance(bundle['active'],str):refused('App bundle default is malformed; original preserved')
            if 'active' not in bundle:return raw
            bundle.pop('active',None)
            return yaml.safe_dump(body,sort_keys=False,allow_unicode=True).encode()
        except Exception as error:
            if getattr(error,'data',{}).get('executed') is False:raise
            refused('App settings could not be parsed safely; original preserved')
    def unresolved(self,command=None):
        with self.owner.db() as db:
            return bool(db.execute("SELECT 1 FROM commands WHERE id!=? AND json_extract(body,'$.operation') IN ('maintenance.appReset.apply','maintenance.appReset.restore') AND json_extract(body,'$.state') IN ('running','unknown') LIMIT 1",(command or '',)).fetchone())
    def load(self,workspace,args):
        with self.owner.db() as db:row=db.execute('SELECT workspace,body,private FROM app_reset_previews WHERE id=?',(args.get('preparedId'),)).fetchone()
        if not row or row[0]!=workspace:refused('Exact app reset review is unavailable')
        body=json.loads(row[1]);private=json.loads(row[2])
        if args.get('reviewHash')!=body['reviewHash']:refused('App reset review identity changed')
        return body,private
    def applicable(self,body,revision):return time.time()<body['expiresAt'] and body['revision']==revision
    def prepare(self,workspace,args):
        if args.get('parts')!=[PART] or args.get('privateContentReviewed') is not True or args.get('credentialsReviewed') not in (None,False):refused('Explicit native app bundle-default part required; credentials are outside this owner')
        if self.unresolved(args['commandId']):refused('An earlier app reset is unresolved; inspect it without replay')
        raw,revision=self.current();self.clear(raw)
        original=args.get('restoreCommandId')
        if original:
            prior=self.owner.receipt(workspace,original)
            if not prior or prior[1]['state']!='succeeded' or prior[1]['operation']!='maintenance.appReset.apply' or prior[1]['result']['postResetRevision']!=revision:refused('Original reset is unavailable or app settings changed afterward')
        prepared=uuid.uuid4().hex
        body={'ownerId':OWNER,'preparedId':prepared,'parts':[PART],'revision':revision,'expiresAt':time.time()+600,'containsPrivateContent':True,'credentialsIncluded':False,'coverage':'explicit-app-local-parts','preserved':PRESERVED,'omissions':['other product configuration','shared settings and credentials'],'restoresCommandId':original,'items':[{'part':PART,'present':raw is not None,'operation':'restore' if original else 'clear-bundle-active-only'}]}
        body['reviewHash']=digest(body)
        private={'before':base64.b64encode(raw).decode() if raw is not None else None}
        with self.owner.db() as db:db.execute('INSERT INTO app_reset_previews VALUES(?,?,?,?)',(prepared,workspace,json.dumps(body),json.dumps(private)))
        return body
    def mutate(self,workspace,args,restore=False):
        body,private=self.load(workspace,args);raw,revision=self.current()
        if not self.applicable(body,revision):refused('App reset review expired or settings changed; prepare again')
        if self.unresolved(args['commandId']):refused('An earlier app reset is unresolved')
        if restore:
            prior=self.owner.receipt(workspace,args.get('resetCommandId'))
            if not prior or prior[1]['state']!='succeeded' or prior[1]['operation']!='maintenance.appReset.apply':refused('Exact completed original reset is required')
            result=prior[1]['result']
            if body['restoresCommandId']!=args['resetCommandId'] or revision!=args.get('expectedPostResetRevision') or result['postResetRevision']!=revision:refused('App settings changed after reset; restore would overwrite newer work')
            old,_=self.load(workspace,{'preparedId':result['preparedId'],'reviewHash':result['reviewHash']})
            _,saved=self.load(workspace,old)
            after=base64.b64decode(saved['before'],validate=True) if saved['before'] is not None else None
        else:
            if body['restoresCommandId']:refused('Restore review cannot authorize a reset')
            after=self.clear(raw)
        # Before-image is already durable in the private owner SQLite database.
        # The outer maintenance command was committed as running before any effect.
        if after is None:
            if self.path.exists():
                self.path.unlink()
                fd=os.open(self.path.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        else:
            from .native.shared_settings import atomic_write
            atomic_write(self.path,after.decode(),private=True)
        _,post=self.current()
        return {'ownerId':OWNER,'preparedId':body['preparedId'],'reviewHash':body['reviewHash'],'parts':[PART],'postResetRevision':post,'restored':restore,'preserved':PRESERVED,'canonicalFilesChanged':0,'sharedSettingsChanged':False,'replayed':False}
    def inspect(self,workspace,args):
        if 'commandId' in args:
            if set(args)!={'commandId'}:refused('Inspect an exact command or an exact review, not both')
            prior=self.owner.receipt(workspace,args['commandId'])
            if prior and not prior[1]['operation'].startswith('maintenance.appReset.'):refused('Receipt belongs to a different operation')
            return {'ownerId':OWNER,'receipt':prior[1] if prior else None,'replayed':False}
        body,_=self.load(workspace,args);_,revision=self.current()
        return {'ownerId':OWNER,'preparedId':body['preparedId'],'reviewHash':body['reviewHash'],'revision':revision,'applicable':self.applicable(body,revision) and not self.unresolved(),'replayed':False}
    def perform(self,workspace,operation,args):
        if operation.endswith('.inspect') and set(args)=={'commandId'}:return self.inspect(workspace,args)
        if not operation.endswith('.inspect'):
            from .private_store_snapshot import require_admin
            try:require_admin(self.owner)
            except ValueError:refused('App reset requires the exact held recovery administration lease')
        # Join real file work inside Maintenance.run. Never borrow a stale lease.
        with Gate(self.owner.config,exclusive=True),FileLock(str(self.path)+'.lock',timeout=0):
            if operation.endswith('.inspect'):return self.inspect(workspace,args)
            if operation.endswith('.prepare'):return self.prepare(workspace,args)
            return self.mutate(workspace,args,operation.endswith('.restore'))
