"""Connection-owned exclusion from before native copying through product sealing.

Supervisor evidence is opaque provenance authenticated by the trusted caller.
This owner proves only its live native/admin leases and exact artifact bindings.
"""
from __future__ import annotations
import copy,time,uuid
from .maintenance_gate import Gate
from .private_store_snapshot import require_admin

OPERATIONS={'maintenance.capture.acquire','maintenance.capture.inspect','maintenance.capture.release'}
FIELDS={
 'maintenance.capture.acquire':{'commandId','nonce','instanceId','dataScope','stopReceiptSha256'},
 'maintenance.capture.inspect':{'captureId','nonce'},
 'maintenance.capture.release':{'commandId','captureId','nonce','outcome','artifactId','artifactSha256','manifestDigest','productSha256'},
}
class CaptureRefused(ValueError):
    def __init__(self,message):super().__init__(message);self.data={'executed':False,'replayed':False,'reason':'native-capture-refused'}

def text(value,name,minimum=1,maximum=200):
    if not isinstance(value,str) or not minimum<=len(value)<=maximum or any(ord(c)<32 for c in value):raise CaptureRefused('Bounded '+name+' required')
    return value

def hexvalue(value,name,size=64):
    if not isinstance(value,str) or len(value)!=size or any(c not in '0123456789abcdef' for c in value):raise CaptureRefused('Exact '+name+' required')
    return value

class BorrowedGate:
    def __init__(self,capture):self.capture=capture;capture.valid()
    def __enter__(self):self.capture.valid();return self
    def __exit__(self,*_):pass
    def release(self):pass  # Snapshot expiry must not release the capture gate.

class Capture:
    def __init__(self,owner):self.owner=owner;self.row=None;self.gate=None
    def valid(self):
        row=self.row
        if row is None or row['released'] or self.gate is None or self.gate.fd is None or self.owner.closing:raise CaptureRefused('Live capture exclusion is unavailable; a receipt cannot restore it')
        try:authority=require_admin(self.owner)
        except ValueError:raise CaptureRefused('Capture administration authority is unavailable') from None
        if authority!=row['adminAuthority']:raise CaptureRefused('Capture administration authority changed')
        return row
    def active(self):
        try:self.valid();return True
        except CaptureRefused:return False
    def context(self):
        row=self.valid()
        return {k:row[k] for k in ('captureId','nonce','commandId','adminInstanceId','instanceId','dataScope','stopReceiptSha256','acquiredAt')}
    def authorize(self,workspace,args):
        row=self.valid()
        if row['workspace']!=workspace or args.get('captureId')!=row['captureId'] or args.get('nonce')!=row['nonce']:raise CaptureRefused('Exact live capture identity and workspace required')
        return row
    def acquire(self,workspace,args):
        if self.row and not self.row['released']:raise CaptureRefused('This connection already retains capture exclusion')
        if self.owner.lease and not self.owner.lease['released']:raise CaptureRefused('Acquire capture exclusion before preparing or acquiring an archive')
        if self.owner.config.get('maintenanceFullNative') is not True or self.owner.config.get('maintenanceExternalWriters')!='stopped':raise CaptureRefused('Full native capture requires explicitly configured stopped external writers')
        nonce=text(args.get('nonce'),'capture nonce',32,128);instance=text(args.get('instanceId'),'host instance');scope=text(args.get('dataScope'),'data scope');stop=hexvalue(args.get('stopReceiptSha256'),'stopped-service receipt digest')
        try:authority=require_admin(self.owner)
        except ValueError:raise CaptureRefused('Capture requires the held recovery administration lease') from None
        fence=self.owner.admin.lifecycle.fence
        if fence['instanceId']!=instance or fence['dataScope']!=scope:raise CaptureRefused('Capture context differs from the authenticated administration coordinator')
        gate=Gate(self.owner.config,exclusive=True)
        self.gate=gate;self.row={'captureId':uuid.uuid4().hex,'nonce':nonce,'commandId':args['commandId'],'adminInstanceId':authority['adminInstanceId'],'instanceId':instance,'dataScope':scope,'stopReceiptSha256':stop,'acquiredAt':time.time(),'workspace':workspace,'adminAuthority':authority,'artifacts':[],'released':False}
        return self.inspect(workspace,{'captureId':self.row['captureId'],'nonce':nonce})
    def inspect(self,workspace,args):
        identity=hexvalue(args.get('captureId'),'capture identity',32);nonce=text(args.get('nonce'),'capture nonce',32,128)
        if not self.row or self.row['captureId']!=identity or self.row['nonce']!=nonce or self.row['workspace']!=workspace:return {'version':1,'captureId':identity,'active':False,'reason':'no-matching-live-connection-capture','adminInstanceId':self.owner.admin.lifecycle.instance,'replayed':False}
        row=self.row
        return {'version':1,**{k:row[k] for k in ('captureId','nonce','commandId','adminInstanceId','instanceId','dataScope','stopReceiptSha256','acquiredAt')},'active':self.active(),'released':row['released'],'artifacts':copy.deepcopy(row['artifacts']),'gate':'native-home-exclusive-os-lease','externalWriterPolicy':'operator-attested-stopped','stopEvidenceAuthentication':'trusted-offline-coordinator','replayed':False}
    def verify_plan(self,context):
        if context!=self.context():raise CaptureRefused('Archive was not prepared under this exact live capture exclusion')
    def bind(self,workspace,description,manifest):
        row=self.valid()
        if row['workspace']!=workspace:raise CaptureRefused('Capture artifact workspace changed')
        if len(row['artifacts'])>=16:raise CaptureRefused('Capture artifact limit reached')
        binding={'artifactId':hexvalue(description.get('artifactId'),'artifact identity',32),'sha256':hexvalue(description.get('sha256'),'artifact digest'),'manifestDigest':hexvalue(manifest.get('sha256'),'manifest digest'),'manifestDigestKind':'sha256-exact-native-manifest-sqlite3-bytes','bytes':description['bytes'],'previewHash':description['previewHash']}
        row['artifacts'].append(binding)
        return {'context':self.context(),'binding':copy.deepcopy(binding)}
    def release(self,workspace,args):
        row=self.authorize(workspace,args)
        if self.owner.lease and not self.owner.lease['released']:raise CaptureRefused('Release the archive lease before capture exclusion')
        outcome=args.get('outcome')
        if outcome not in {'sealed','aborted'}:raise CaptureRefused('Capture release requires sealed or aborted outcome')
        if outcome=='sealed':
            identity=hexvalue(args.get('artifactId'),'artifact identity',32);digest=hexvalue(args.get('artifactSha256'),'artifact digest');manifest=hexvalue(args.get('manifestDigest'),'manifest digest');hexvalue(args.get('productSha256'),'sealed product archive digest')
            if not any(b['artifactId']==identity and b['sha256']==digest and b['manifestDigest']==manifest for b in row['artifacts']):raise CaptureRefused('Product seal does not bind an artifact from this live capture')
        elif any(k in args for k in ('artifactId','artifactSha256','manifestDigest','productSha256')):raise CaptureRefused('Aborted capture cannot assert product sealing')
        result={**self.inspect(workspace,args),'active':False,'released':True,'outcome':outcome,**({'productSha256':args['productSha256']} if outcome=='sealed' else {})}
        row['released']=True;self.gate.release();self.gate=None
        return result
    def close(self):
        if self.row:self.row['released']=True
        if self.gate:self.gate.release();self.gate=None
