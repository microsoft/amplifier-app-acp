"""Held administration intake, including writers that outlive their request."""
from __future__ import annotations
import asyncio,copy,os,uuid
from .maintenance_gate import Gate,MaintenanceBusy
from .admin_admission import AdmissionJournal

READS={'maintenance.bundleReferences.page','bundle.receipt','maintenance.appReset.inspect','maintenance.capture.inspect','maintenance.cache.page','maintenance.artifact.inspect','maintenance.restore.inspect','permissions.get','permissions.receipt','maintenance.restore.manifest','voice.preferences.receipt','history.import.receipt','maintenance.archive.manifest','voice.configuration','maintenance.receipt','maintenance.artifact.read','maintenance.lease','generations.receipt','generations.inspect','generations.current'}
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
        self.owner=owner;self.instance=uuid.uuid4().hex;self.requests={};self.fence=None;self.gate=None;self.closing=False;self.waiting_idle=False;self.releases={};self.admission=AdmissionJournal(owner.config)
    def work(self):
        operations={}
        for row in self.requests.values():operations[row['operation']]=operations.get(row['operation'],0)+1
        background=sum(not task.done() for task in self.owner.background_gates)
        maintenance=getattr(self.owner,'maintenance',None)
        return {'requests':len(self.requests),'operations':operations,'providerSignIns':background,'maintenanceLease':bool(maintenance and maintenance.lease_view().get('active')),'captureLease':bool(maintenance and hasattr(maintenance,'capture_scope') and maintenance.capture_scope.active())}
    def inspect(self):
        return {'version':1,'adminInstanceId':self.instance,'pid':os.getpid(),'intakeClosed':self.fence is not None or self.closing or self.admission.intake() is not None,'lease':dict(self.fence) if self.fence else None,'work':self.work(),'workerStarted':False}
    def enter(self,operation,authorization=None):
        if self.closing:raise AdminFenced('Native administration is closing')
        gate=None
        if self.fence:
            permit=authorization if isinstance(authorization,dict) else {}
            matching=all(permit.get(key)==self.fence[key] for key in ('fenceId','commandId','leaseId'))
            if operation not in READS and not (matching and self.fence['purpose']=='recovery' and operation.startswith('maintenance.')):raise AdminFenced()
        else:
            gate=AdminGate(self.owner.config)
            try:
                if operation not in READS and self.admission.intake():raise AdminFenced()
            except BaseException:gate.release();raise
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
        if operation=='reconcileRelease':
            expected=context(args)
            if set(args)!=CONTEXT|{'outcome','proof'} or expected['purpose']!='distribution-update':raise AdminFenced('Exact original distribution release required')
            outcome=args['outcome'];proof=args['proof']
            keys={'verified','fenceId','commandId','outcome','instanceId','dataScope','receiptId'}
            if not isinstance(proof,dict) or set(proof)!=keys or proof.get('verified') is not True or outcome not in {'ready','unchanged'} or proof.get('outcome')!=outcome or any(proof.get(k)!=expected[k] for k in ('fenceId','commandId','dataScope')):raise AdminFenced('Authenticated exact distribution release proof required')
            if any(not isinstance(proof.get(k),str) or not 1<=len(proof[k])<=200 or any(ord(c)<32 for c in proof[k]) for k in ('instanceId','receiptId')) or (proof['instanceId']==expected['instanceId'])!=(outcome=='unchanged'):raise AdminFenced('Distribution release must bind the observed host instance')
            row=self.admission.receipt(expected)
            if row.get('reconcileRequest'):
                if row['reconcileRequest']!=args:raise AdminFenced('Distribution release receipt binds different proof')
                return copy.deepcopy(row['reconcileResult'])
            # Do not reacquire the original admission or infer that a missing,
            # refused, pending or aborted attempt reached retirement.
            lease=row.get('result',{}).get('lease',{})
            if row['state']!='acquired' or row.get('result',{}).get('acquired') is not True or any(lease.get(k)!=v for k,v in expected.items()):raise AdminFenced('Original acquired distribution admission required')
            durable=self.admission.intake()
            if not durable or durable['context']!=expected or self.fence or self.closing or any(self.work().values()):raise AdminFenced('Native administration cannot reconcile this original admission')
            try:gate=AdminGate(self.owner.config,exclusive=True)
            except MaintenanceBusy:raise AdminFenced('Another native writer remains active') from None
            try:
                if self.admission.receipt(expected)!=row or self.admission.intake()!=durable:raise AdminFenced('Original distribution admission changed during reconciliation')
                result={'released':True,'reconciled':True,'intakeClosed':False,'lease':copy.deepcopy(lease),'adminInstanceId':self.instance,'workerStarted':False}
                # One transaction records proof and completion and removes only
                # this original durable hold while actual writer exclusion lasts.
                self.admission.update(expected,state='released',reconcileRequest=copy.deepcopy(args),reconcileResult=result)
                return result
            finally:gate.release()
        if operation in {'abortAdmission','admissionReceipt','refuseAdmission'}:
            expected=context(args)
            if expected['purpose']!='distribution-update':raise AdminFenced('Admission abort requires the original distribution admission')
            if operation=='abortAdmission':
                if set(args)!=CONTEXT|{'proof'}:raise ValueError('Exact admission abort request required')
                proof=args['proof']
                if not isinstance(proof,dict) or set(proof)!=CONTEXT|{'kind','verified','receiptId'} or proof.get('kind')!='distribution-admission-abort' or proof.get('verified') is not True or any(proof.get(k)!=v for k,v in expected.items()) or not isinstance(proof.get('receiptId'),str) or not 1<=len(proof['receiptId'])<=200 or any(ord(c)<32 for c in proof['receiptId']):raise AdminFenced('Exact authenticated admission abort proof required')
                work=self.work()
                if self.closing or any(work.values()):raise AdminFenced('Native administration still owns work')
                row=self.admission.receipt(expected)
                if row.get('abortProof') is not None and row['abortProof']!=proof:raise AdminFenced('Admission abort binds different proof')
                if row['state']=='aborted':return copy.deepcopy(row['abortReceipt'])
                if row['state']=='released':
                    acquired=row.get('result',{});released=row.get('releaseResult',{});request=row.get('releaseRequest',{});lease=acquired.get('lease',{})
                    if acquired.get('acquired') is not True or any(lease.get(k)!=v for k,v in expected.items()) or released.get('released') is not True or released.get('lease')!=lease or request.get('outcome')!='unchanged' or any(request.get(k)!=lease.get(k) for k in ('fenceId','commandId','leaseId')):raise AdminFenced('Original unchanged release evidence is unavailable')
                elif row['state'] not in {'pending','acquired','refused'}:raise AdminFenced('Original admission cannot establish pre-retirement abort')
                durable=self.admission.intake()
                if durable and durable['context']!=expected:raise AdminFenced('Another native admission owns intake')
                if self.fence and any(self.fence.get(k)!=v for k,v in expected.items()):raise AdminFenced()
                try:gate=self.gate or AdminGate(self.owner.config,exclusive=True)
                except MaintenanceBusy:raise AdminFenced('Another native writer remains active') from None
                try:
                    row=self.admission.receipt(expected)
                    if row.get('abortProof') is not None and row['abortProof']!=proof:raise AdminFenced('Admission abort binds different proof')
                    if row['state']=='aborted':return copy.deepcopy(row['abortReceipt'])
                    row=self.admission.update(expected,abortProof=proof)
                    result={'ownerId':'native-admin','status':'not-acquired' if row['state']=='refused' else 'released','receiptId':row.get('abortReceiptId') or uuid.uuid4().hex,**{k:expected[k] for k in ('commandId','fenceId','instanceId','dataScope')}}
                    # Persist intent before unlocking. A lost final transaction retains
                    # durable intake, so replacement writers still cannot enter.
                    self.admission.update(expected,abortReceiptId=result['receiptId'])
                    gate.release();self.gate=None;self.fence=None
                    self.admission.update(expected,state='aborted',abortReceipt=result)
                    return result
                finally:
                    if gate is not self.gate:gate.release()
            if set(args)!=CONTEXT:raise ValueError('Exact original admission selectors required')
            if operation=='admissionReceipt':return self.admission.receipt(expected)
            refusal={'acquired':False,'executed':False,'intakeClosed':False,'reason':'node-no-dispatch'}
            row,created=self.admission.begin(expected,refusal)
            if row['state']!='refused':raise AdminFenced('Original admission already dispatched or unresolved')
            return copy.deepcopy(row['result'])
        if operation=='acquire':
            expected=context(args)
            if set(args)!=set(expected):raise ValueError('Unexpected administration acquisition field')
            if self.closing:return {'acquired':False,'executed':False,'reason':'closing','intakeClosed':True}
            if self.fence:
                if all(self.fence[k]==v for k,v in expected.items()):return {'acquired':True,**self.inspect()}
                return {'acquired':False,'executed':False,'reason':'already-fenced','intakeClosed':True}
            distribution=expected['purpose']=='distribution-update'
            if distribution:
                row,created=self.admission.begin(expected)
                if not created:
                    if row['state']=='refused':return copy.deepcopy(row['result'])
                    raise AdminFenced('Original admission remains durable; inspect or abort without reacquisition')
            elif self.admission.intake():raise AdminFenced()
            work=self.work()
            if work['requests'] or work['providerSignIns'] or work['maintenanceLease'] or work['captureLease']:
                self.waiting_idle=True
                result={'acquired':False,'executed':False,'reason':'owned-work','intakeClosed':False,'work':work}
                if distribution:self.admission.update(expected,state='refused',result=result)
                return result
            try:gate=AdminGate(self.owner.config,exclusive=True)
            except MaintenanceBusy:
                result={'acquired':False,'executed':False,'reason':'other-native-admin-writer','intakeClosed':False,'work':work}
                if distribution:self.admission.update(expected,state='refused',result=result)
                return result
            # No await occurs between checking native-owned activity and closing intake.
            self.gate=gate;self.fence={**expected,'leaseId':uuid.uuid4().hex,'adminInstanceId':self.instance}
            result={'acquired':True,**self.inspect()}
            if distribution:self.admission.update(expected,state='acquired',result=result)
            return result
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
            if old['purpose']=='distribution-update':self.admission.update({k:old[k] for k in CONTEXT},state='released',releaseRequest=args,releaseResult=result)
            self.releases[old['leaseId']]=copy.deepcopy({'request':args,'result':result})
            if len(self.releases)>256:self.releases.pop(next(iter(self.releases)))
            return result
        raise ValueError('Unknown administration lifecycle operation')
    def close(self):
        self.closing=True
        if self.requests:raise AdminFenced('Administration requests must settle before lifetime release')
        if self.gate:self.gate.release();self.gate=None
        self.fence=None
