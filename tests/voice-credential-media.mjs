import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const {AdminConnection}=await import(process.env.MEDIA_NATIVE_ADMIN_MODULE);
const {createMediaCapability}=await import(process.env.MEDIA_CAPABILITY_MODULE);
const directory=await mkdtemp(join(tmpdir(),'voice-credential-installed-')),workspace=join(directory,'workspace'),home=join(directory,'home');await mkdir(workspace);await mkdir(home);
const config=join(directory,'native.json');await writeFile(config,JSON.stringify({home,appHome:join(directory,'app'),adminWorkspaceRoots:[workspace],adminVoiceCredentials:true,workerCommand:['/no/model-worker-allowed']}));
// The public launcher merges its own environment; isolate this fixture process
// as well as its child without using an empty override that shadows saved keys.
for(const name of ['OPENAI_API_KEY','AMPLIFIER_VOICE_API_KEY','AMPLIFIER_OPENAI_API_KEY'])delete process.env[name];
const environment={...process.env};
const setup=()=>new AdminConnection({command:process.env.MEDIA_NATIVE_PYTHON,args:['-I','-B','-m','amplifier_acp','--config',config],cwd:workspace,env:environment,resolveWorkspace:()=>workspace});
let admin=setup(),owner,reads=0,previews=0;const key='synthetic-provider-connection-key';
const native=(operation,args={})=>admin.perform(operation,args,{clientId:''});
try{
 owner=await createMediaCapability({directory:join(directory,'media'),inspectSession:async()=>({}),delegate:async()=>{throw Error('No delegation allowed')},recordTranscript:async()=>({recorded:true}),settings:()=>native('voice.configuration'),configure:({args})=>native('voice.configure',args),resolveCredential:async()=>{reads++;return (await native('voice.credential')).apiKey},workerFactory:()=>({async request(method,args){assert.equal(method,'preview');assert.ok(args.apiKey===key,'Selected fixture credential mismatch');assert.equal(args.model,'gpt-live-1');assert.equal(args.voice,'marin');previews++;return {model:args.model,voice:args.voice,mimeType:'audio/wav',audio:'Zml4dHVyZQ=='}},close(){}})});
 const token=(await owner.action({channel:'ahp-session:/owned',operation:'media.attach'},{account:'owned',clientId:'client'})).result.lease;
 const post=async(path,args={})=>{const result=await owner.handleFetch(new Request('http://fixture/media/voice/'+path,{method:'POST',headers:{'X-Amplifier-Media-Lease':token},body:JSON.stringify(args)}),{account:'owned'});assert.equal(result.status,200);const row=await result.json();assert(!JSON.stringify(row).includes(key));return row};
 assert.equal((await post('config')).available,false);
 const saved=await native('providers.save',{id:'openai',module:'provider-openai',apiKey:key,scope:'global'});assert(!JSON.stringify(saved).includes(key));
 const configured=await post('config');assert.equal(configured.available,true);assert.equal(configured.environmentAvailable,true);assert.equal(configured.privateKeyAvailable,false);assert.equal(reads,0);assert.equal(previews,0);
 assert.equal((await post('configure',{source:'environment'})).available,true);assert.equal(reads,0);
 assert.equal((await post('preview',{commandId:'one-explicit-preview',model:'gpt-live-1',voice:'marin'})).mimeType,'audio/wav');assert.equal(reads,1);assert.equal(previews,1);
 await admin.close();admin=setup();assert.equal((await post('config')).available,true);assert.equal((await native('voice.configuration')).environmentVariable,'AMPLIFIER_OPENAI_API_KEY');
 await native('providers.remove',{id:'openai',scope:'global'});assert.equal((await post('config')).available,false);assert.equal(reads,1);
 console.log(JSON.stringify({status:'PASS',actualProviderSave:true,generatedReferenceReady:true,environmentSelectionConfirmed:true,explicitPreviewRequests:previews,credentialResolutions:reads,restartPreserved:true,removedConnectionUnavailable:true,providerNetworkCalls:0,executionWorkers:0,physicalAudio:'unverified'}));
}finally{await owner?.close();await admin.close();await rm(directory,{recursive:true,force:true})}
