import base64,hashlib,json,sqlite3
from pathlib import Path
import pytest
from client import Client
from amplifier_acp.history_import import HistoryImport,CHUNK_BYTES,MAX_BYTES

NATIVE='amplifier.dev/native'

@pytest.fixture(autouse=True)
def isolate_writer_state(tmp_path,monkeypatch):
 monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writer-state'))

def setup(tmp_path,enabled=True):
 workspace=tmp_path/'workspace';workspace.mkdir();home=tmp_path/'home';app=tmp_path/'app'
 values={'home':str(home),'appHome':str(app),'adminWorkspaceRoots':[str(workspace)],'adminHistoryImport':enabled,'workerCommand':['/no/worker/allowed'],'bundle':'fixture-bundle'}
 path=tmp_path/'agent.json';path.write_text(json.dumps(values));return path,workspace,values

async def start(path):
 client=await Client.launch(path);init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{NATIVE:{'version':1}}}})
 return client,init

async def call(client,cwd,op,args):
 result=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'history.import.'+op,'args':args})
 assert 'error' not in result,result
 return result['result']

async def upload(client,cwd,raw,prefix='one',**overrides):
 spec={'commandId':prefix+'-begin','format':'json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),**overrides}
 begin=await call(client,cwd,'begin',spec);assert begin['status']=='succeeded',begin
 uid=begin['result']['uploadId']
 for offset in range(0,len(raw),CHUNK_BYTES):
  chunk={'commandId':f'{prefix}-chunk-{offset}','uploadId':uid,'offset':offset,'contentBase64':base64.b64encode(raw[offset:offset+CHUNK_BYTES]).decode()}
  assert len(json.dumps(chunk).encode())<32768
  response=await call(client,cwd,'chunk',chunk);assert response['status']=='succeeded',response
 preview=await call(client,cwd,'preview',{'commandId':prefix+'-preview','uploadId':uid})
 return uid,preview

@pytest.mark.asyncio
async def test_protocol_import_cross_process_receipt_preserves_original_no_worker(tmp_path):
 path,cwd,config=setup(tmp_path);client,init=await start(path)
 assert init['result']['agentCapabilities']['_meta'][NATIVE]['admin']['historyImport']['chunkBytes']==CHUNK_BYTES
 rows=[{'role':'system','content':'Historical system context'},{'role':'user','content':'old question','metadata':{'inputOrigin':'user','answerProvenance':{'approved':True},'amplifier_input':{'id':'host-command'}}},{'role':'assistant','content':'old answer','tool_calls':[{'id':'call1','type':'function','function':{'name':'never_execute','arguments':'{}'}}]}]
 raw=json.dumps({'messages':rows,'metadata':{'parent_id':'untrusted-parent','bundle_name':'recorded-bundle','jobs':[{'execute':True}]}}).encode()
 try:
  uid,preview=await upload(client,cwd,raw,title='Reviewed title',bundle='override-bundle')
  assert preview['result']['summary']['interruptedToolCalls']==1
  command={'commandId':'commit','previewHash':preview['result']['previewHash']}
  committed=await call(client,cwd,'commit',command);assert committed['status']=='succeeded',committed
  assert await call(client,cwd,'commit',command)==committed
  assert (await call(client,cwd,'commit',{**command,'commandId':'second-commit'}))['status']=='failed'
  assert not any(event['method']=='session/update' for event in client.events)
 finally:await client.close()
 result=committed['result'];directory=next(Path(config['home']).glob('projects/*/sessions/'+result['nativeSessionId']))
 saved=[json.loads(line) for line in (directory/'transcript.jsonl').read_text().splitlines()]
 assert saved[0]['content']=='Historical system context'
 assert saved[1]['metadata']['inputOrigin']=='import' and saved[1]['metadata']['amplifier_input']['id']!='host-command'
 assert 'answerProvenance' not in saved[1]['metadata']
 assert json.loads(saved[-1]['content'])['outcome']=='unconfirmed'
 metadata=json.loads((directory/'metadata.json').read_text());assert metadata['parent_id'] is None and 'jobs' not in metadata and metadata['name']=='Reviewed title'
 with sqlite3.connect(Path(config['appHome'])/'history-imports/imports.sqlite3') as con:assert con.execute('SELECT body FROM uploads WHERE id=?',(uid,)).fetchone()[0]==raw
 client,_=await start(path)
 try:
  assert await call(client,cwd,'receipt',{'commandId':'commit'})==committed
  missing=await call(client,cwd,'receipt',{'commandId':'absent'});assert missing is None
  denied=await client.request('_amplifier/admin',{'cwd':str(tmp_path),'operation':'history.import.receipt','args':{'commandId':'commit'}});assert 'error' in denied
 finally:await client.close()
 assert len([p for p in Path(config['home']).glob('projects/*/sessions/*') if p.is_dir()])==1

@pytest.mark.asyncio
@pytest.mark.parametrize('raw,format',[(b'{"messages":[],"messages":[]}','json'),(b'[{"role":"event","content":"x"}]','json'),(b'[{"role":"user","content":NaN}]','json'),(b'[{"role":"tool","tool_call_id":"orphan","content":"x"}]','json'),(b'not JSON','jsonl')])
async def test_strict_rejection_does_not_create_history(tmp_path,raw,format):
 path,cwd,config=setup(tmp_path);client,_=await start(path)
 try:
  _,preview=await upload(client,cwd,raw,format=format)
  assert preview['status']=='failed' and preview['executed'] is False
  assert not (Path(config['home'])/'projects').exists()
 finally:await client.close()

@pytest.mark.asyncio
async def test_full_size_staged_upload_and_exact_chunk_receipts(tmp_path):
 path,cwd,config=setup(tmp_path);client,_=await start(path)
 raw=b'[{"role":"user","content":"'+b'x'*(MAX_BYTES-30)+b'"}]'
 assert len(raw)<=MAX_BYTES
 try:
  uid,preview=await upload(client,cwd,raw)
  assert preview['status']=='succeeded',preview
  assert (await call(client,cwd,'begin',{'commandId':'oversize','format':'json','bytes':MAX_BYTES+1,'sha256':'0'*64}))['status']=='failed'
  different=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'history.import.chunk','args':{'commandId':'one-chunk-0','uploadId':uid,'offset':0,'contentBase64':'eA=='}})
  assert 'error' in different
  assert not (Path(config['home'])/'projects').exists()
 finally:await client.close()

@pytest.mark.asyncio
async def test_disabled_import_and_held_intake_exact_receipt(tmp_path):
 path,cwd,config=setup(tmp_path,False);client,init=await start(path)
 try:
  assert 'historyImport' not in init['result']['agentCapabilities']['_meta'][NATIVE]['admin']
  denied=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'history.import.receipt','args':{'commandId':'absent'}});assert 'error' in denied
  assert not Path(config['appHome']).exists()
 finally:await client.close()
 from amplifier_acp.admin import Administration
 from test_admin_lifecycle import CONTEXT
 config['adminHistoryImport']=True;owner=Administration(config)
 try:
  assert owner.lifecycle.perform('acquire',CONTEXT)['acquired']
  assert await owner.perform(str(cwd),'history.import.receipt',{'commandId':'absent'}) is None
  with pytest.raises(ValueError,match='intake is held'):await owner.perform(str(cwd),'history.import.begin',{'commandId':'blocked'})
  assert owner.setup is None
 finally:await owner.close()

def test_uncertain_commit_never_repeats_and_partial_target_fenced(tmp_path,monkeypatch):
 path,cwd,config=setup(tmp_path);owner=HistoryImport(config)
 raw=b'[{"role":"user","content":"old"}]'
 begin=owner.perform(str(cwd),'history.import.begin',{'commandId':'begin','format':'json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
 uid=begin['result']['uploadId'];owner.perform(str(cwd),'history.import.chunk',{'commandId':'chunk','uploadId':uid,'offset':0,'contentBase64':base64.b64encode(raw).decode()})
 preview=owner.perform(str(cwd),'history.import.preview',{'commandId':'preview','uploadId':uid})['result']['previewHash']
 from amplifier_foundation.session.history import SessionHistoryStore
 def broken(*args,**kwargs):raise OSError('fixture loss after reservation')
 monkeypatch.setattr(SessionHistoryStore,'save',broken)
 with pytest.raises(OSError):owner.perform(str(cwd),'history.import.commit',{'commandId':'commit','previewHash':preview})
 receipt=owner.perform(str(cwd),'history.import.commit',{'commandId':'commit','previewHash':preview});assert receipt['status']=='unknown'
 assert owner.perform(str(cwd),'history.import.commit',{'commandId':'other','previewHash':preview})['status']=='failed'
 app=next((Path(config['appHome'])/'sessions').iterdir())
 from amplifier_acp.session_lifecycle import guard_start
 from types import SimpleNamespace
 with pytest.raises(ValueError,match='import creation is unresolved'):guard_start(SimpleNamespace(config=config),app.name,str(cwd))

@pytest.mark.asyncio
async def test_imported_history_resumes_actual_native_graph_without_replaying_old_input(tmp_path):
 from native_setup import configuration
 path,cwd=configuration(tmp_path);config=json.loads(path.read_text());config.update(adminHistoryImport=True,adminWorkspaceRoots=[str(cwd)]);path.write_text(json.dumps(config))
 client,_=await start(path)
 try:
  raw=b'{"messages":[{"role":"user","content":"historical question"},{"role":"assistant","content":"historical answer"}]}'
  _,preview=await upload(client,cwd,raw)
  committed=await call(client,cwd,'commit',{'commandId':'commit','previewHash':preview['result']['previewHash']});sid=committed['result']['nativeSessionId']
  assert not client.events
  resumed=await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]});assert 'result' in resumed,resumed
  assert not any(e.get('params',{}).get('event',{}).get('type')=='generation.started' for e in client.events)
  response=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'new explicit input'}]});assert response['result']['stopReason']=='end_turn',response
  page=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50});assert 'result' in page,page
  users=[u['content']['text'] for u in page['result']['updates'] if u['sessionUpdate']=='user_message_chunk']
  assert users==['historical question','new explicit input']
  imported=next(u for u in page['result']['updates'] if u.get('content',{}).get('text')=='historical question')
  assert imported['_meta']['amplifier.dev/history']['inputOrigin']=='import'
  assert imported['_meta']['amplifier.dev/history']['authorization']=='unverified-native-history'
 finally:await client.close()


def test_atomic_chunk_validation_and_committed_marker_reconciliation(tmp_path,monkeypatch):
 _,cwd,config=setup(tmp_path);owner=HistoryImport(config);raw=b'{"role":"user","content":"message"}\n'
 result=owner.perform(str(cwd),'history.import.begin',{'commandId':'begin','format':'jsonl','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()});uid=result['result']['uploadId']
 args={'commandId':'chunk','uploadId':uid,'offset':0,'contentBase64':base64.b64encode(raw).decode()}
 first=owner.perform(str(cwd),'history.import.chunk',args);assert owner.perform(str(cwd),'history.import.chunk',args)==first
 assert owner.perform(str(cwd),'history.import.chunk',{**args,'commandId':'wrong-offset','offset':1})['executed'] is False
 preview=owner.perform(str(cwd),'history.import.preview',{'commandId':'preview','uploadId':uid})['result']['previewHash']
 original=owner.commit
 def lost_ack(*args):original(*args);raise OSError('fixture lost native completion acknowledgement')
 monkeypatch.setattr(owner,'commit',lost_ack)
 with pytest.raises(OSError):owner.perform(str(cwd),'history.import.commit',{'commandId':'commit','previewHash':preview})
 receipt=HistoryImport(config).perform(str(cwd),'history.import.receipt',{'commandId':'commit'})
 assert receipt['status']=='succeeded' and receipt['result']['creationConfirmed']
 # Read-only proof recovery doesn't mutate or reexecute the original command.
 with sqlite3.connect(owner.path) as con:assert json.loads(con.execute('SELECT body FROM commands WHERE id=?',('commit',)).fetchone()[0])['status']=='unknown'
 assert len([p for p in Path(config['home']).glob('projects/*/sessions/*') if p.is_dir()])==1

@pytest.mark.asyncio
async def test_process_death_after_native_write_keeps_unknown_and_refuses_replay(tmp_path):
 import asyncio,sys
 path,cwd,config=setup(tmp_path);client,_=await start(path)
 try:
  _,preview=await upload(client,cwd,b'[{"role":"user","content":"preserve after loss"}]')
 finally:await client.close()
 script="from amplifier_foundation.session.history import SessionHistoryStore\nimport os\noriginal=SessionHistoryStore.save\ndef lost(self,*args,**kwargs):\n original(self,*args,**kwargs)\n os._exit(71)\nSessionHistoryStore.save=lost\nfrom amplifier_acp.server import main\nmain()"
 process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,'--config',str(path),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 broken=Client(process)
 await broken.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{NATIVE:{'version':1}}}})
 args={'commandId':'crashed','previewHash':preview['result']['previewHash']}
 await broken.send({'jsonrpc':'2.0','id':999,'method':'_amplifier/admin','params':{'cwd':str(cwd),'operation':'history.import.commit','args':args}})
 assert await asyncio.wait_for(process.wait(),10)==71;await broken.close()
 transcripts=list(Path(config['home']).glob('projects/*/sessions/*/transcript.jsonl'));assert len(transcripts)==1;before=transcripts[0].read_bytes()
 client,_=await start(path)
 try:
  receipt=await call(client,cwd,'receipt',{'commandId':'crashed'});assert receipt['status']=='unknown'
  assert await call(client,cwd,'commit',args)==receipt
  assert (await call(client,cwd,'commit',{**args,'commandId':'do-not-retry'}))['status']=='failed'
  resumed=await client.request('session/resume',{'sessionId':transcripts[0].parent.name,'cwd':str(cwd),'mcpServers':[]})
  assert 'import creation is unresolved' in resumed['error']['message']
  assert not client.events and transcripts[0].read_bytes()==before
 finally:await client.close()
