"""Scoped write-path settings. No runtime mount, arbitrary file reads or tool effects."""
from __future__ import annotations
import hashlib,json,os,sqlite3
from pathlib import Path
from .maintenance_gate import checked,native_home
from .history_import import encoded,token

OPERATIONS={'permissions.get','permissions.save','permissions.receipt'}
SCOPES=('global','project','local')
FIELDS={'allowed':'allowed_write_paths','denied':'denied_write_paths'}
MARKER='_amplifier_acp_permissions'
MAX_PATHS=64
MAX_PATH_LENGTH=4096
MAX_SETTINGS_BYTES=2*1024*1024

class Invalid(ValueError):
    data={'executed':False,'replayed':False,'reason':'invalid-permissions'}

class Conflict(Invalid):
    def __init__(self,current):
        super().__init__('File-access settings changed; load and review the current scope before saving')
        self.current=current
        self.data={**self.data,'reason':'permissions-changed','currentRevision':current}

def digest(value):return hashlib.sha256(encoded(value)).hexdigest()

def paths(values,workspace):
    if not isinstance(values,list) or len(values)>MAX_PATHS:raise Invalid('Choose at most 64 directories per list')
    result=[]
    for value in values:
        if not isinstance(value,str) or not value.strip() or len(value)>MAX_PATH_LENGTH or any(ord(c)<32 for c in value):raise Invalid('Each directory must be a bounded non-empty path without control characters')
        path=Path(value).expanduser()
        path=(path if path.is_absolute() else Path(workspace)/path).resolve()
        if path.exists() and not path.is_dir():raise Invalid('File-access entries must name directories, not files')
        if len(str(path))>MAX_PATH_LENGTH:raise Invalid('Resolved directory exceeds the path limit')
        if str(path) not in result:result.append(str(path))
    return result

def selected(settings):
    value=settings
    for key in ('overrides','tool-filesystem','config'):
        if not isinstance(value,dict):raise Invalid('Saved file-access configuration is malformed; original settings preserved')
        value=value.get(key,{})
    if not isinstance(value,dict):raise Invalid('Saved file-access configuration is malformed; original settings preserved')
    result={}
    for field,key in FIELDS.items():
        if key not in value:continue
        v=value[key]
        if not isinstance(v,list) or len(v)>MAX_PATHS or any(not isinstance(p,str) or len(p)>MAX_PATH_LENGTH for p in v):raise Invalid('Saved file-access list exceeds supported bounds; original settings preserved')
        result[field]=list(v)
    return result

class Permissions:
    def __init__(self,config):
        from .native.preferences import SettingsStore
        app=Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
        self.root=checked(app/'permissions');self.path=self.root/'receipts.sqlite3'
        self.store=SettingsStore(app,shared_home=native_home(config))
    def scope(self,workspace,scope):
        if scope not in SCOPES:raise Invalid('Choose global, project, or local file-access scope')
        path=checked(self.store.path(workspace,scope))
        if path.exists() and path.stat().st_size>MAX_SETTINGS_BYTES:raise Invalid('Selected settings exceed the bounded read limit; original settings preserved')
        return path
    def read(self,workspace,scope):
        self.scope(workspace,scope)
        try:return self.store.read(workspace,scope)
        except Exception as exc:raise Invalid('Selected settings could not be read; original file preserved') from exc
    def revision(self,workspace,scope,settings):return digest({'workspace':workspace,'scope':scope,'values':selected(settings)})
    def view(self,workspace,scope,settings=None):
        if settings is None:settings=self.read(workspace,scope)
        current=selected(settings);effective={};sources={};available=[];layers=[]
        for source in SCOPES:
            try:value=settings if source==scope else self.read(workspace,source)
            except ValueError:
                # Managed-chat workspaces intentionally expose global settings only.
                from .native.shared_settings import settings_paths
                if source not in settings_paths(workspace,shared_home=self.store.shared_home):continue
                raise
            available.append(source);layer=selected(value);layers.append({"scope":source,"values":layer})
            for field,value in layer.items():effective[field]=paths(value,workspace);sources[field]=source
        return {'scope':scope,'workspace':workspace,'allowed':current.get('allowed',[]),'denied':current.get('denied',[]),'configured':list(current),'revision':self.revision(workspace,scope,settings),
                'scopes':available,'limits':{'pathsPerList':MAX_PATHS,'pathChars':MAX_PATH_LENGTH,'requestBytes':262144},'application':'next-idle-execution',
                'effective':{'allowed':list(dict.fromkeys([workspace,*effective.get('allowed',[])])),'denied':effective.get('denied',[]),'sources':sources,'revision':digest(layers),'coverage':'module-default-settings-only','deniedTakesPrecedence':True},
                'detail':'Saved scopes are merged global, shared workspace, then private workspace. The execution directory remains allowed by default. Bundle, session and tool-instance policies can add restrictions; this is not an inspection of a running tool. Idle sessions reload changed settings before their next input; active work keeps its mounted policy.'}
    def db(self,create=False):
        from .journal_schema import connect
        return connect(self.path,'permissions',write=create,timeout=5 if create else 0,isolation_level=None)
    def receipt(self,workspace,identity):
        con=self.db()
        if con is None:return None
        try:
            row=con.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if not row:return None
            if row['workspace']!=workspace:raise Invalid('Permission receipt belongs to a different authorized workspace')
            receipt=json.loads(row['body'])
            if receipt['status']=='unknown':
                settings=self.read(workspace,row['scope']);marker=settings.get(MARKER,{})
                if isinstance(marker,dict) and marker=={'commandId':identity,'signature':row['signature'],'revision':self.revision(workspace,row['scope'],settings)}:
                    return {**receipt,'status':'succeeded','result':self.view(workspace,row['scope'],settings)}
            return receipt
        finally:con.close()
    def perform(self,workspace,operation,args):
        workspace=str(Path(workspace).resolve(strict=True))
        allowed={'permissions.get':{'scope'},'permissions.save':{'scope','commandId','expectedRevision','allowed','denied'},'permissions.receipt':{'commandId'}}
        if operation not in OPERATIONS or not isinstance(args,dict) or set(args)-allowed[operation]:raise Invalid('Unexpected file-access operation or field')
        if len(encoded(args))>262144:raise Invalid('File-access request exceeds 256 KiB')
        if operation=='permissions.receipt':return self.receipt(workspace,token(args.get('commandId'),'commandId'))
        scope=args.get('scope','global');self.scope(workspace,scope)
        if operation=='permissions.get':return self.view(workspace,scope)
        identity=token(args.get('commandId'),'commandId');expected=args.get('expectedRevision')
        if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise Invalid('Exact selected file-access revision required')
        signature=digest({'workspace':workspace,'operation':operation,'args':args});con=self.db(True)
        try:
            con.execute('BEGIN IMMEDIATE');old=con.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if old:
                if old['signature']!=signature:raise Invalid('Permission command identity already binds different arguments or workspace')
                con.execute('ROLLBACK');return json.loads(old['body'])
            receipt={'commandId':identity,'operation':operation,'status':'unknown','replayed':False}
            con.execute('INSERT INTO commands VALUES(?,?,?,?,?)',(identity,signature,workspace,scope,encoded(receipt).decode()));con.execute('COMMIT')
            try:
                updated={field:paths(args.get(field),workspace) for field in FIELDS}
                def mutate(settings):
                    # Foundation serializes this mutator with atomic replacement. No
                    # credentials or unrelated module configuration enter projection.
                    current=self.revision(workspace,scope,settings)
                    prior=settings.get(MARKER,{})
                    if isinstance(prior,dict) and prior.get('commandId')!=identity:
                        row=con.execute('SELECT * FROM commands WHERE id=?',(prior.get('commandId'),)).fetchone()
                        if row and row['signature']==prior.get('signature') and prior.get('revision')==self.revision(row['workspace'],row['scope'],settings):
                            old_receipt=json.loads(row['body'])
                            if old_receipt['status']=='unknown':
                                old_receipt.update(status='succeeded',result=self.view(row['workspace'],row['scope'],settings));con.execute('UPDATE commands SET body=? WHERE id=?',(encoded(old_receipt).decode(),row['id']))
                    if current!=expected:raise Conflict(current)
                    values=settings.setdefault('overrides',{}).setdefault('tool-filesystem',{}).setdefault('config',{})
                    for field,key in FIELDS.items():values[key]=updated[field]
                    settings[MARKER]={'commandId':identity,'signature':signature,'revision':self.revision(workspace,scope,settings)}
                saved=self.store.update(workspace,scope,mutate)
            except Invalid as exc:
                receipt.update(status='failed',executed=False,error=str(exc)[:1000],reason='permissions-changed' if isinstance(exc,Conflict) else 'invalid-permissions')
                if isinstance(exc,Conflict):receipt['currentRevision']=exc.current
            except Exception as exc:
                raise RuntimeError('Permission save outcome requires exact receipt inspection; original evidence preserved') from exc
            else:
                # Presentation failure after replacement is uncertain, never a
                # fabricated pre-effect refusal. Exact marker recovery is passive.
                try:view=self.view(workspace,scope,saved)
                except Exception as exc:raise RuntimeError('Permission save outcome requires exact receipt inspection; no write was replayed') from exc
                receipt.update(status='succeeded',result=view)
            con.execute('UPDATE commands SET body=? WHERE id=?',(encoded(receipt).decode(),identity));return receipt
        finally:
            if con.in_transaction:con.execute('ROLLBACK')
            con.close()
