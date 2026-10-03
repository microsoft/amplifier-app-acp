"""Launcher-authorized native transfer authority; no input execution or replay."""
from __future__ import annotations
import base64,hashlib,json,os
from pathlib import Path
from .session_lifecycle import regular,record
from .configuration_clone import location
from .relocation import home,write,state
from .history import inspect as inspect_history

FILES={'transcript.jsonl','transcript.jsonl.backup','metadata.json','metadata.json.backup','events.jsonl','context-intelligence/metadata.json','context-intelligence/events.jsonl'}
MAX_FILE=32*1024*1024
MAX_TOTAL=64*1024*1024

def digest(data):return hashlib.sha256(data).hexdigest()
def decode(value):
    from amplifier_portability.capsule import validate_bytes,validate_public
    raw=base64.b64decode(value['data'],validate=True)
    if len(raw)!=value['bytes'] or len(raw)>MAX_FILE or digest(raw)!=value['sha256']:raise ValueError('Native transfer file integrity failed')
    validate_bytes(raw)
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate native transfer JSON key')
            result[key]=value
        return result
    try:value=json.loads(raw,object_pairs_hook=unique)
    except json.JSONDecodeError:
        for line in raw.splitlines():
            if line.strip():validate_public(json.loads(line,object_pairs_hook=unique))
    else:validate_public(value)
    return raw

def binary(path,data):
    import tempfile
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(prefix='.transfer-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:Path(temp).unlink(missing_ok=True)

class TransferAuthority:
    def __init__(self,server,params):
        from amplifier_portability.protocol import TransferNode
        from amplifier_foundation.session.shared_state import SharedSessionStore
        self.server=server;self.identity=params.get('sessionId');self.cwd=params.get('cwd')
        roots=server.config.get('transferWorkspaceRoots',[])
        if not isinstance(self.cwd,str) or not Path(self.cwd).is_absolute() or not Path(self.cwd).is_dir():raise ValueError('Existing absolute native transfer workspace required')
        self.cwd=str(Path(self.cwd).resolve(strict=True))
        if not roots or not any(Path(self.cwd).is_relative_to(Path(p).resolve(strict=True)) for p in roots):raise ValueError('Transfer workspace is outside launcher authority')
        self.native=server.directory(self.cwd,self.identity);self.app=location(home(server),self.identity)
        self.node=TransferNode(server.config['transferAuthorityDirectory'],'Native transfer authority')
        self.args=params.get('args',{});self.transfer=self.args.get('transferId')
        self.row=self.node.get(self.transfer)
        if self.row['sessionId']!=self.identity:raise ValueError('Transfer receipt belongs to another native session')
        self.root=self.node.directory/'native'/self.transfer;self.receipt_path=self.root/'receipt.json'
        self.store=SharedSessionStore(self.cwd,self.identity)
        self.receipt=record(self.receipt_path) if self.receipt_path.exists() else None
        if self.receipt and (self.receipt['nativeSessionId']!=self.identity or self.receipt['historyCwd']!=self.cwd):raise ValueError('Native transfer receipt workspace changed')
    def result(self,phase,**extra):
        value={'version':1,'transferId':self.transfer,'nativeSessionId':self.identity,'historyCwd':self.cwd,'executionDirectory':state(self.server,self.identity,self.cwd)['executionDirectory'],'phase':phase,'source':self.row['source'],'destination':self.row['destination'],'inputsReplayed':False,'authority':'foundation-transfer-fence',**extra}
        write(self.receipt_path,value);self.receipt=value;return value
    def expect(self,direction,phases):
        own='source' if direction=='outgoing' else 'destination'
        if self.row['direction']!=direction or self.row[own]!=self.node.identity['id'] or self.row['phase'] not in phases:raise ValueError('Configured local transfer receipt does not authorize this native transition')
    def fence(self,role):
        fence=self.store.transfer_fence()
        if fence:
            if fence['transfer_id']!=self.transfer or fence['role']!=role or fence['destination_host']!=self.row['destination']:raise ValueError('Another native transfer fence already owns this session')
            return fence
        if role=='source' and not self.native.is_dir():raise ValueError('Source native history is missing')
        if role=='destination' and self.native.exists():raise ValueError('Destination already has native history; no overwrite permitted')
        held=self.store.acquire(app='amplifier-acp-transfer')
        try:return held.fence_transfer(self.transfer,self.row['destination'],role=role)
        finally:held.release()
    def package(self):
        from amplifier_portability.capsule import read_capsule
        from amplifier_portability.protocol import encoded
        path=self.node.directory/'packages'/(self.transfer+'.json')
        package=read_capsule(path)
        if self.row['direction']=='incoming':body=self.node.verify(package,kind='capsule',signer=self.row['source'])
        else:
            # Source authenticates its own exact private package hash; its key is
            # deliberately not a peer trust root.
            body=package['body']
        if digest(encoded(body))!=self.row.get('capsuleHash'):raise ValueError('Transfer capsule differs from its exact receipt')
        if any(body.get(key)!=self.row[key] for key in ('id','sessionId','generation','source','destination')):raise ValueError('Capsule native ownership identity changed')
        return body['payload']
    def perform(self,operation):
        if operation=='inspect':return {'fence':self.store.transfer_fence(),'receipt':self.receipt,'transferPhase':self.row['phase']}
        with self.node.lock:
            if operation=='source.fence':
                self.expect('outgoing',{'preparing','prepared','unknown'})
                fence=self.fence('source');return self.result('source-fenced',fence=fence,quiesced=True)
            if operation=='destination.fence':
                self.expect('incoming',{'staging'});fence=self.fence('destination');return self.result('destination-fenced',fence=fence)
            if operation=='source.capture':return self.capture()
            if operation=='source.read':return self.read()
            if operation=='source.verify':return self.verify_source()
            if operation=='source.commit':
                self.expect('outgoing',{'releasing','released'})
                if self.row['phase']=='releasing':self.verify_source()
                fence=self.store.transfer_fence()
                if not fence or fence['transfer_id']!=self.transfer or fence['role']!='source':raise ValueError('Exact source fence is required')
                if fence['phase']=='committed':fence=self.store.confirm_transfer_commit(self.transfer,app='amplifier-acp-transfer')
                else:
                    held=self.store.acquire_transfer(self.transfer,app='amplifier-acp-transfer')
                    try:fence=held.commit_transfer()
                    finally:held.release()
                return self.result('source-committed',fence=fence,canonicalFilesPreserved=True)
            if operation=='source.cancel':
                self.expect('outgoing',{'cancelled'})
                if self.receipt and self.receipt['phase']=='cancelled' and self.store.transfer_fence() is None:return self.receipt
                held=self.store.acquire_transfer(self.transfer,app='amplifier-acp-transfer')
                try:held.clear_transfer()
                finally:held.release()
                return self.result('cancelled')
            if operation=='destination.install':return self.install()
            if operation=='destination.activate':
                self.expect('incoming',{'active'});self.node.readiness_checks(self.row)
                body=self.node.verify(self.row['releaseCertificate'],kind='release',signer=self.row['source']);self.node.match(self.row,body)
                release_hash=digest(__import__('amplifier_portability.protocol',fromlist=['encoded']).encoded(self.row['releaseCertificate']))
                if self.receipt and self.receipt['phase']=='activated' and self.receipt['releaseHash']==release_hash and self.store.transfer_fence() is None:return self.receipt
                if not self.receipt or self.receipt['phase']!='installed':raise ValueError('Native transfer installation has no committed receipt; inspect original outcome')
                expected=self.receipt['historyRevision']
                if inspect_history(self.native)['revision']!=expected:raise ValueError('Installed native history changed before activation')
                self.result('activating',releaseHash=release_hash,historyRevision=expected)
                held=self.store.acquire_transfer(self.transfer,app='amplifier-acp-transfer')
                try:held.clear_transfer()
                finally:held.release()
                return self.result('activated',releaseHash=release_hash,historyRevision=expected)
            raise ValueError('Unsupported native transfer operation')
    def capture(self):
        self.expect('outgoing',{'preparing'})
        fence=self.store.transfer_fence()
        if not fence or fence['transfer_id']!=self.transfer or fence['role']!='source':raise ValueError('Capture requires the exact native source fence')
        path=self.root/'snapshot.json'
        if path.exists():return record(path)
        metadata=record(self.native/'metadata.json');controls=record(self.app/'control-state.json') if (self.app/'control-state.json').exists() else {}
        selection=controls.get('selection',{})
        if not selection.get('model') or not (selection.get('instance') or selection.get('provider')):raise ValueError('Transfer requires an explicit selected provider and model')
        bundle=metadata.get('bundle') or metadata.get('bundle_name') or self.server.config.get('bundle')
        if not isinstance(bundle,str) or not bundle:raise ValueError('Native bundle identity is unavailable')
        intent={'bundle':bundle,'selection':selection,'controls':{k:controls[k] for k in ('budget','capacity','mode') if k in controls}}
        files={};total=0
        for name in sorted(FILES):
            source=self.native/name
            if not source.exists():continue
            raw=regular(source,MAX_FILE);total+=len(raw)
            if total>MAX_TOTAL:raise ValueError('Native transfer history exceeds64MiB bound; originals preserved')
            value={'data':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':digest(raw)};decode(value)
            binary(self.root/'snapshot'/name,raw);files[name]={k:value[k] for k in ('bytes','sha256')}
        if not {'transcript.jsonl','metadata.json'}<=set(files):raise ValueError('Canonical native history is required')
        value={'version':1,'nativeIdentity':self.identity,'files':files,'intent':intent,'origin':{'historyHome':self.cwd,'executionDirectory':state(self.server,self.identity,self.cwd)['executionDirectory']},'omissions':[{'kind':'attachments','reason':'external attachment payloads are not included; canonical references retained'},{'kind':'active-controls','reason':'task, goal, approvals and live jobs remain historical, never executable on destination'}]}
        write(path,value);return value
    def read(self):
        self.expect('outgoing',{'preparing','prepared'});manifest=record(self.root/'snapshot.json');name=self.args.get('name');offset=self.args.get('offset',0);limit=self.args.get('limit',65536)
        if name not in manifest['files'] or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=65536:raise ValueError('Bounded exact native snapshot window required')
        info=manifest['files'][name];path=self.root/'snapshot'/name
        if path.is_symlink() or path.stat().st_size!=info['bytes']:raise ValueError('Native snapshot changed')
        with path.open('rb') as stream:stream.seek(offset);raw=stream.read(limit)
        return {'data':base64.b64encode(raw).decode(),'encoding':'base64','offset':offset,'nextOffset':offset+len(raw) if offset+len(raw)<info['bytes'] else None,**info}
    def verify_source(self):
        self.expect('outgoing',{'prepared','releasing'});manifest=record(self.root/'snapshot.json')
        for name,value in manifest['files'].items():
            raw=regular(self.native/name,MAX_FILE)
            if len(raw)!=value['bytes'] or digest(raw)!=value['sha256']:raise ValueError('Source canonical history changed after capture')
        return {'unchanged':True,'inputsReplayed':False}
    def install(self):
        self.expect('incoming',{'activating'});self.node.readiness_checks(self.row)
        body=self.node.verify(self.row['releaseCertificate'],kind='release',signer=self.row['source']);self.node.match(self.row,body)
        if self.receipt and self.receipt['phase']=='installed':return self.receipt
        if not self.receipt or self.receipt['phase']!='destination-fenced':raise ValueError('Destination installation was already attempted; inspect without replay')
        payload=self.package();native=payload.get('native',{})
        if not isinstance(native,dict) or set(native)-FILES or not {'transcript.jsonl','metadata.json'}<=set(native):raise ValueError('Exact whitelisted native history files required')
        values={name:decode(value) for name,value in native.items()}
        if sum(map(len,values.values()))>MAX_TOTAL:raise ValueError('Native transfer exceeds64MiB bound')
        if self.native.exists() and any(path.name!='transfer-fence.json' for path in self.native.iterdir()):raise ValueError('Destination native history already exists; never overwrite')
        metadata=json.loads(values['metadata.json'])
        if metadata.get('session_id',self.identity)!=self.identity:raise ValueError('Transferred native metadata has a different session identity')
        selection=payload['intent']['selection']
        from amplifier_portability.capsule import validate_public
        validate_public(selection)
        # Configuration/model intent is applied to destination-owned modules only.
        controls={'selection':selection}
        self.result('installing');self.native.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name,raw in values.items():binary(self.native/name,raw)
        write(self.app/'control-state.json',controls)
        write(self.app/'execution-location.json',{'sessionId':self.identity,'historyCwd':self.cwd,'executionDirectory':self.cwd,'executionRevision':0})
        return self.result('installed',historyRevision=inspect_history(self.native)['revision'])
