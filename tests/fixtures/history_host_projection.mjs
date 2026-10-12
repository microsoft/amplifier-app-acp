// Offline cross-component regression: real passive Native ACP -> Host -> official AHP reducer.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {createHost}=await import(process.env.HISTORY_HOST_MODULE);
const {AhpClient}=await import(pathToFileURL(process.env.HISTORY_AHP_SDK+'/dist/client/index.js'));
const {WebSocketTransport}=await import(pathToFileURL(process.env.HISTORY_AHP_SDK+'/dist/ws/index.js'));
const [directory,config,python,missing]=process.argv.slice(2);
const uri='ahp-session:/history-tool-fixture',chat='ahp-chat:/history-tool-fixture',stamp='2026-10-04T00:00:00.000Z';
const record={uri,engineId:'native-history',nativeSessionId:'history-tool-fixture',workingDirectory:directory,title:'Owned canonical history',createdAt:stamp,modifiedAt:stamp};
const host=await createHost({stateDirectory:directory+'/host',allowedWorkspaceRoots:[directory],engines:[{id:record.engineId,command:python,args:['-I','-B','-m','amplifier_acp','--config',config]}],catalog:{list:async()=>({items:[record]}),get:async id=>id===uri?record:undefined,upsert:async()=>{}}});
const client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'history-fixture',protocolVersions:['0.9.0']});
try{
 await client.request('listSessions',{channel:'ahp-root://',limit:10});
 const response=await client.subscribe(chat,{view:{turns:1}}),state=response.result.snapshot.state;
 const turn=state.turns.find(turn=>turn.id==='saved-input');assert.ok(turn,'canonical input identity must survive');
 const tool=turn.responseParts.find(part=>part.kind==='toolCall')?.toolCall;
 assert.ok(tool,'canonical tool must survive the official reducer');assert.equal(tool.toolCallId,'exact-call');assert.equal(tool.displayName,'read_file');assert.equal(tool.toolInput,'{"path":"owned.txt"}');
 console.log(JSON.stringify({turnId:turn.id,tool,turnState:turn.state,activeAgents:host.diagnostics().activeAgents}));
 assert.equal(host.diagnostics().activeAgents,0,'passive history must never start a worker');
 if(missing==='true'){
  assert.notEqual(tool.success,true,'missing canonical result cannot be invented as success');
  assert.equal(tool.structuredContent,undefined);assert.equal(tool.status,'cancelled');
 }else{
  assert.equal(tool.status,'completed');assert.deepEqual(tool.structuredContent,{output:[{type:'text',text:'Full owned result'}]},'saved canonical result must reach official tool state');
 }
}finally{await client.shutdown();await host.close();}
