"""Held administration intake, including writers that outlive their request."""
from __future__ import annotations
import asyncio,copy,os,uuid
from .maintenance_gate import Gate,MaintenanceBusy

READS={'maintenance.capture.inspect','maintenance.cache.page','maintenance.artifact.inspect','maintenance.restore.inspect','permissions.get','permissions.receipt','maintenance.restore.manifest','voice.preferences.receipt','history.import.receipt','maintenance.archive.manifest','voice.configuration','maintenance.receipt','maintenance.artifact.read','maintenance.lease','generations.receipt','generations.inspect','generations.current'}
CONTEXT={'fenceId','commandId','purpose','instanceId','dataScope'}
SERVICE_IDENTITY={'installationId','dataScope','ownerId','instanceId','releaseDigest'}

class AdminFenced(ValueError):
    def __init__(self,message='Native administration intake is held; no new effect was admitted'):
        super().__init__(message)
        self.data={'executed':False,'replayed':False,'reason':'admin-quiescence-fenced'}

class AdminGate(Gate):
    def __init__(self,config,*,exclusive=False):
        super().__init__(config,exclusive=exclusive,filename='.amplifier-acp-admin-writers.lock')


def context(args):
    result={key:args.get(key) for key in CONTEXT}
    if any(not isinstance(value,str) or not 1<=len(value)<=200 or any(ord(c)<32 for c in value) for value in result.values()):raise ValueError('Exact bounded administration fence context required')
    if result['purpose'] not in {'recovery','distribution-update','service-stop'}:raise ValueError('Unknown administration quiescence purpose')
    if result['purpose']=='service-stop':
        identity=service_identity(args.get('serviceIdentity'))
        if any(identity[key]!=result[key] for key in ('instanceId','dataScope')):raise ValueError('Service identity must bind the administration context')
        result['serviceIdentity']=identity
    elif 'serviceIdentity' in args:raise ValueError('Service identity requires service-stop purpose')
    return result

def service_identity(value):
    if not isinstance(value,dict) or set(value)!=SERVICE_IDENTITY or any(not isinstance(v,str) or not 1<=len(v)<=200 or any(ord(c)<32 for c in v) for v in value.values()):raise ValueError('Exact bounded service identity required')
    return dict(value)

def service_release(expected,outcome,proof):
    """Validate binding only; the configured host authenticates platform receipts."""
    try:
        allowed={'verified','fenceId','commandId','outcome','instanceId','dataScope','receiptId','kind','serviceOutcome','expected','observed','resumeCommandId','exitReceiptId','readyReceiptId','refusalReceiptId'}
        if not isinstance(proof,dict) or set(proof)-allowed:raise ValueError()
        original=service_identity(proof.get('expected'));observed=service_identity(proof.get('observed'))
        if proof.get('kind')!='service-lifecycle' or proof.get('verified') is not True or original!=expected['serviceIdentity'] or any(proof.get(k)!=expected[k] for k in ('fenceId','commandId','dataScope')) or proof.get('outcome')!=outcome or proof.get('instanceId')!=observed['instanceId']:raise ValueError()
        if any(original[k]!=observed[k] for k in SERVICE_IDENTITY-{'instanceId'}):raise ValueError()
        if outcome=='unchanged' and proof.get('serviceOutcome')=='stop-refused' and original==observed and not {'resumeCommandId','exitReceiptId','readyReceiptId'}&set(proof):keys=['receiptId','refusalReceiptId']
        elif outcome=='ready' and proof.get('serviceOutcome')=='resumed' and original['instanceId']!=observed['instanceId'] and 'refusalReceiptId' not in proof:keys=['receiptId','resumeCommandId','exitReceiptId','readyReceiptId']
        else:raise ValueError()
        if any(not isinstance(proof.get(k),str) or not 1<=len(proof[k])<=200 or any(ord(c)<32 for c in proof[k]) for k in keys):raise ValueError()
    except (AttributeError,KeyError,TypeError,ValueError):raise AdminFenced('Exact authenticated service lifecycle release proof required') from None


class AdminLifecycle:
    def __init__(self,owner):
        self.owner=owner;self.instance=uuid.uuid4().hex;self.requests={};self.fence=None;self.gate=None;self.closing=False;self.waiting_idle=False;self.releases={}
    def work(self):
        operations={}
        for row in self.requests.values():operations[row['operation']]=operations.get(row['operation'],0)+1
        background=sum(not task.done() for task in self.owner.background_gates)
        maintenance=getattr(self.owner,'maintenance',None)
        return {'requests':len(self.requests),'operations':operations,'providerSignIns':background,'maintenanceLease':bool(maintenance and maintenance.lease_view().get('active')),'captureLease':bool(maintenance and hasattr(maintenance,'capture_scope') and maintenance.capture_scope.active())}
    def inspect(self):
        return {'version':1,'adminInstanceId':self.instance,'pid':os.getpid(),'intakeClosed':self.fence is not None or self.closing,'lease':dict(self.fence) if self.fence else None,'work':self.work(),'workerStarted':False}
    def enter(self,operation,authorization=None):
        if self.closing:raise AdminFenced('Native administration is closing')
        gate=None
        if self.fence:
            permit=authorization if isinstance(authorization,dict) else {}
            matching=all(permit.get(key)==self.fence[key] for key in ('fenceId','commandId','leaseId'))
            if operation not in READS and not (matching and self.fence['purpose']=='recovery' and operation.startswith('maintenance.')):raise AdminFenced()
        else:gate=AdminGate(self.owner.config)
        identity=uuid.uuid4().hex;self.requests[identity]={'operation':operation,'gate':gate};return identity
    def maybe_idle(self):
        work=self.work()
        if self.waiting_idle and not self.closing and not self.fence and not any(work.values()):
            self.waiting_idle=False
            if self.owner.on_idle:
                task=asyncio.create_task(self.owner.on_idle())
                task.add_done_callback(lambda done:done.exception() if not done.cancelled() else None)
    def leave(self,identity):
        row=self.requests.pop(identity,None)
        if row and row['gate']:row['gate'].release()
        self.maybe_idle()
    def perform(self,operation,args):
        if operation=='inspect':
            if args:raise ValueError('Administration lifecycle inspection takes no selectors')
            return self.inspect()
        if operation=='acquire':
            expected=context(args)
            if set(args)!=set(expected):raise ValueError('Unexpected administration acquisition field')
            if self.closing:return {'acquired':False,'executed':False,'reason':'closing','intakeClosed':True}
            if self.fence:
                if all(self.fence[k]==v for k,v in expected.items()):return {'acquired':True,**self.inspect()}
                return {'acquired':False,'executed':False,'reason':'already-fenced','intakeClosed':True}
            work=self.work()
            if work['requests'] or work['providerSignIns'] or work['maintenanceLease'] or work['captureLease']:
                self.waiting_idle=True
                return {'acquired':False,'executed':False,'reason':'owned-work','intakeClosed':False,'work':work}
            try:gate=AdminGate(self.owner.config,exclusive=True)
            except MaintenanceBusy:return {'acquired':False,'executed':False,'reason':'other-native-admin-writer','intakeClosed':False,'work':work}
            # No await occurs between checking native-owned activity and closing intake.
            self.gate=gate;self.fence={**expected,'leaseId':uuid.uuid4().hex,'adminInstanceId':self.instance}
            return {'acquired':True,**self.inspect()}
        if operation=='release':
            if set(args)-{'fenceId','commandId','leaseId','outcome','proof'}:raise ValueError('Unexpected administration release field')
            prior=self.releases.get(args.get('leaseId'))
            if prior:
                if prior['request']!=args or self.fence:raise AdminFenced('Administration release receipt binds different evidence or intake')
                return copy.deepcopy(prior['result'])
            if not self.fence or any(args.get(key)!=self.fence[key] for key in ('fenceId','commandId','leaseId')):raise AdminFenced('Exact held administration lease required')
            if args.get('outcome')=='unknown':return {'released':False,**self.inspect()}
            if args.get('outcome') not in {'unchanged','ready'}:raise ValueError('Unknown administration release outcome')
            proof=args.get('proof') or {}
            # This is a private launcher-owned channel. The product host authenticates
            # the proof; protocol assertions themselves never grant browser authority.
            if proof=={'kind':'admission-refused'} and args['outcome']=='unchanged':
                # Exact live leaseId permits only trusted acquisition rollback;
                # a replacement process has no such native lease to release.
                pass
            elif self.fence['purpose']=='service-stop':service_release(self.fence,args['outcome'],proof)
            else:
                if proof.get('verified') is not True or proof.get('fenceId')!=self.fence['fenceId'] or proof.get('commandId')!=self.fence['commandId'] or proof.get('dataScope')!=self.fence['dataScope'] or proof.get('outcome')!=args['outcome'] or not isinstance(proof.get('receiptId'),str):raise AdminFenced('Authenticated coordinator release proof required')
                if args['outcome']=='unchanged' and proof.get('instanceId')!=self.fence['instanceId']:raise AdminFenced('Unchanged release must bind the same host instance')
            work=self.work()
            if work['requests'] or work['providerSignIns'] or work['maintenanceLease'] or work['captureLease']:raise AdminFenced('Administration still owns work or a native maintenance lease')
            old=dict(self.fence);self.gate.release();self.gate=None;self.fence=None
            result={'released':True,'intakeClosed':False,'lease':old,'adminInstanceId':self.instance,'workerStarted':False}
            self.releases[old['leaseId']]=copy.deepcopy({'request':args,'result':result})
            if len(self.releases)>256:self.releases.pop(next(iter(self.releases)))
            return result
        raise ValueError('Unknown administration lifecycle operation')
    def close(self):
        self.closing=True
        if self.requests:raise AdminFenced('Administration requests must settle before lifetime release')
        if self.gate:self.gate.release();self.gate=None
        self.fence=None
