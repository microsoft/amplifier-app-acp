"""Held passive-transfer intake and native-home writer lifetime."""
from __future__ import annotations
import asyncio,os,sys,uuid
from .admin_lifecycle import AdminGate,AdminFenced,context,service_release
from .maintenance_gate import Gate

def managed_probe(config):
    """Only the built-in probe owns independent child-process writer leases."""
    command=config.get('transferProbeCommand')
    return command is None or command==[sys.executable,'-m','amplifier_acp.native.portability_probe']

class TransferLifecycle:
    def __init__(self,server):
        self.server=server;self.instance=uuid.uuid4().hex;self.fence=None;self.requests=set();self.closing=False;self.releases={}
    def inspect(self):
        return {'version':1,'processId':self.instance,'pid':os.getpid(),'intakeClosed':self.closing or self.fence is not None,'fence':self.fence,'requests':len(self.requests),'workerStarted':False}
    def control(self,operation,args):
        if operation=='inspect':
            if args:raise ValueError('Transfer lifecycle inspection takes no selectors')
            return self.inspect()
        expected=context(args)
        if operation=='acquire':
            if not managed_probe(self.server.config):return {'acquired':False,'executed':False,'intakeClosed':False,'reason':'custom-probe-lifetime-unproven'}
            if set(args)!=set(expected):raise ValueError('Unexpected transfer fence selectors')
            if self.fence and self.fence!=expected:raise AdminFenced('Transfer intake belongs to another fence')
            if self.closing or self.requests:return {'acquired':False,'executed':False,'intakeClosed':bool(self.fence) or self.closing,'reason':'native-transfer-work'}
            if expected['fenceId'] in self.releases:raise ValueError('Released transfer fence cannot acquire again')
            self.fence=expected;return {'acquired':True,**self.inspect()}
        if operation=='release':
            outcome=args.get('outcome');proof=args.get('proof');receipt={'context':expected,'outcome':outcome,'proof':proof}
            if self.fence is None:
                if self.releases.get(expected['fenceId'])==receipt:return {'released':True,**self.inspect()}
                raise AdminFenced('Exact held transfer fence required')
            if self.fence!=expected:raise AdminFenced('Exact held transfer fence required')
            if outcome=='unknown':return {'released':False,**self.inspect()}
            if self.requests:raise AdminFenced('Native transfer work remains active')
            abort=outcome=='unchanged' and proof=={'kind':'admission-refused'}
            if not abort and expected['purpose']=='service-stop':service_release(expected,outcome,proof)
            elif not abort:
                if not isinstance(proof,dict) or proof.get('verified') is not True or outcome not in {'unchanged','ready'} or any(proof.get(k)!=expected[k] for k in ('fenceId','commandId','dataScope')) or proof.get('outcome')!=outcome or not proof.get('receiptId'):raise AdminFenced('Exact authenticated host release proof required')
                if not isinstance(proof.get('instanceId'),str) or (proof['instanceId']==expected['instanceId'])!=(outcome=='unchanged'):raise AdminFenced('Transfer release instance differs from the declared outcome')
            self.releases[expected['fenceId']]=receipt
            if len(self.releases)>256:self.releases.pop(next(iter(self.releases)))
            self.fence=None;return {'released':True,**self.inspect()}
        raise ValueError('Unknown transfer lifecycle operation')
    async def perform(self,params):
        if self.closing or self.fence:raise AdminFenced('Native transfer intake is held; no transfer effect admitted')
        # Both OS gates are acquired before imports, index initialization, threads,
        # readiness probes, or canonical writes. The single admin participant owns
        # the exclusive side; individual transfer peers hold only shared writers.
        admin=AdminGate(self.server.config)
        try:maintenance=Gate(self.server.config)
        except BaseException:admin.release();raise
        request=asyncio.current_task();self.requests.add(request)
        effect=asyncio.create_task(self.execute(params))
        try:return await asyncio.shield(effect)
        except asyncio.CancelledError:
            # asyncio.to_thread cancellation does not stop its filesystem effects.
            # Retain writer proof until the complete owned effect really settles.
            while not effect.done():
                try:await asyncio.shield(effect)
                except asyncio.CancelledError:continue
                except BaseException:break
            raise
        finally:
            self.requests.discard(request);maintenance.release();admin.release()
    async def execute(self,params):
        from .transfer import TransferAuthority
        owner=await asyncio.to_thread(TransferAuthority,self.server,params)
        if params.get('operation')=='destination.policy':
            from .transfer_readiness import policy
            return await asyncio.to_thread(policy,owner)
        if params.get('operation')=='destination.check':
            from .transfer_readiness import check
            return await check(owner)
        return await asyncio.to_thread(owner.perform,params.get('operation'))
    async def close(self):
        self.closing=True
        if self.requests:await asyncio.gather(*list(self.requests),return_exceptions=True)
