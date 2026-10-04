"""Shared non-secret voice defaults with in-lock CAS and exact command receipts."""
from __future__ import annotations
import hashlib,json,os,sqlite3
from pathlib import Path
from .maintenance_gate import checked
from .history_import import encoded,token

OPERATIONS={'voice.preferences.update','voice.preferences.receipt'}
FIELDS={'preferredModel':'preferred_model','fallbackModel':'fallback_model','voice':'voice','interruptions':'interruptions'}
DEFAULTS={'preferredModel':'gpt-live-1','fallbackModel':'gpt-realtime-2.1','voice':'marin','interruptions':True}
CATALOG=json.loads(Path(__file__).with_name('voice_options.json').read_text())
MODELS={row['id']:{voice['id'] for voice in row['voices']} for row in CATALOG['models']}
MARKER='_amplifier_acp_preferences'


def values(settings):
    voice=settings.get('voice') or {}
    if not isinstance(voice,dict):raise ValueError('Saved voice settings must be an object; original file preserved')
    return {field:voice.get(key,DEFAULTS[field]) for field,key in FIELDS.items()}

def revision(current):return hashlib.sha256(encoded(current)).hexdigest()

def projection(settings,enabled=False):
    current=values(settings)
    return {**current,'preferencesRevision':revision(current),'preferenceFields':list(FIELDS) if enabled else [],'models':CATALOG['models'],'fallbackBehavior':'stored-only'}


class Invalid(ValueError):pass


class Conflict(ValueError):
    def __init__(self,current):
        super().__init__('Shared voice preferences changed; refresh and review the new values before saving')
        self.current=current


class VoicePreferences:
    def __init__(self,config):
        from .maintenance_gate import native_home
        from .native.preferences import SettingsStore
        app=Path(config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
        self.root=checked(app/'voice-preferences');self.path=self.root/'receipts.sqlite3'
        self.store=SettingsStore(app,shared_home=native_home(config))
    def db(self,create=False):
        from .journal_schema import connect
        return connect(self.path,'voice-preferences',write=create,timeout=5 if create else 0,isolation_level=None)
    def receipt(self,workspace,identity):
        con=self.db()
        if con is None:return None
        try:
            row=con.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if not row:return None
            receipt=json.loads(row['body'])
            if receipt['status']=='unknown':
                settings=self.store.read(workspace);current=values(settings);marker=settings.get('voice',{}).get(MARKER,{})
                if isinstance(marker,dict) and marker.get('commandId')==identity and marker.get('signature')==row['signature'] and marker.get('revision')==revision(current):
                    return {**receipt,'status':'succeeded','result':projection(settings,True)}
            return receipt
        finally:con.close()
    def perform(self,workspace,operation,args):
        fields={'commandId'} if operation=='voice.preferences.receipt' else {'commandId','expectedRevision','patch'}
        if operation not in OPERATIONS or set(args)-fields:raise ValueError('Unexpected voice preference field')
        identity=token(args.get('commandId'),'commandId')
        if operation=='voice.preferences.receipt':return self.receipt(workspace,identity)
        signature=hashlib.sha256(encoded({'operation':operation,'args':args})).hexdigest();con=self.db(True)
        try:
            con.execute('BEGIN IMMEDIATE');old=con.execute('SELECT * FROM commands WHERE id=?',(identity,)).fetchone()
            if old:
                if old['signature']!=signature:raise ValueError('Voice preference command identity already binds different arguments')
                con.execute('ROLLBACK');return json.loads(old['body'])
            receipt={'commandId':identity,'operation':operation,'status':'unknown','replayed':False}
            con.execute('INSERT INTO commands VALUES(?,?,?)',(identity,signature,encoded(receipt).decode()));con.execute('COMMIT')
            try:
                patch=args.get('patch');expected=args.get('expectedRevision')
                if not isinstance(patch,dict) or not patch or set(patch)-set(FIELDS):raise Invalid('Choose one or more advertised non-secret voice preference fields')
                if not isinstance(expected,str) or len(expected)!=64:raise Invalid('Exact voice preferences revision required')
                def mutate(settings):
                    current=values(settings)
                    prior=(settings.get('voice') or {}).get(MARKER,{})
                    if isinstance(prior,dict) and prior.get('commandId')!=identity and prior.get('revision')==revision(current):
                        old_row=con.execute('SELECT signature,body FROM commands WHERE id=?',(prior.get('commandId'),)).fetchone()
                        if old_row and old_row['signature']==prior.get('signature'):
                            old_receipt=json.loads(old_row['body'])
                            if old_receipt.get('status')=='unknown':
                                old_receipt.update(status='succeeded',result=projection(settings,True))
                                con.execute('UPDATE commands SET body=? WHERE id=?',(encoded(old_receipt).decode(),prior['commandId']))
                    if revision(current)!=expected:raise Conflict(revision(current))
                    updated={**current,**patch}
                    if any(not isinstance(updated[name],str) or updated[name] not in MODELS for name in ('preferredModel','fallbackModel')):raise Invalid('Choose an advertised voice model')
                    if 'preferredModel' in patch and 'voice' not in patch and (not isinstance(updated['voice'],str) or updated['voice'] not in MODELS[updated['preferredModel']]):updated['voice']='marin'
                    if not isinstance(updated['voice'],str) or updated['voice'] not in MODELS[updated['preferredModel']]:raise Invalid('Choose a voice available for the preferred model')
                    if type(updated['interruptions']) is not bool:raise Invalid('Voice interruptions must be on or off')
                    voice=settings.setdefault('voice',{})
                    for field,key in FIELDS.items():voice[key]=updated[field]
                    # One bounded proof marker is atomic with these exact preferences.
                    # It contains no credential, account identity, or executable policy.
                    voice[MARKER]={'commandId':identity,'signature':signature,'revision':revision(updated)}
                saved=self.store.update(workspace,'global',mutate)
                receipt.update(status='succeeded',result=projection(saved,True))
            except (Conflict,Invalid) as exc:
                # These errors arise from validation inside the locked mutator,
                # before Foundation's atomic settings replacement.
                receipt.update(status='failed',executed=False,reason='preferences_changed' if isinstance(exc,Conflict) else 'invalid_preferences',error=str(exc)[:1000])
                if isinstance(exc,Conflict):receipt['currentRevision']=exc.current
            con.execute('UPDATE commands SET body=? WHERE id=?',(encoded(receipt).decode(),identity));return receipt
        finally:
            if con.in_transaction:con.execute('ROLLBACK')
            con.close()
