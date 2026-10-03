import base64,json
from pathlib import Path
import pytest
from client import Client
from native_setup import configuration
from test_session_lifecycle import prepared,prompt,initialize
from amplifier_portability.protocol import TransferNode
from amplifier_portability.capsule import write_capsule
from amplifier_worktrees.git import atomic

async def operation(client,sid,cwd,op,transfer,**args):
    result=await client.request('_amplifier/transfer',{'sessionId':sid,'cwd':str(cwd),'operation':op,'args':{'transferId':transfer,**args}})
    assert 'result' in result,result
    return result['result']

@pytest.mark.asyncio
async def test_native_two_host_fences_preserve_canonical_identity_without_prompt_replay(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
    (tmp_path/'source').mkdir();(tmp_path/'target').mkdir()
    source,sid,cwd,config=await prepared(tmp_path/'source');await prompt(source,sid,'Preserve this native history','original');await source.close()
    value=json.loads(config.read_text());value.update(transferAuthorityDirectory=str(tmp_path/'source/transfer'),transferWorkspaceRoots=[str(cwd)])
    config.write_text(json.dumps(value));source=await Client.launch(config);await initialize(source)
    targetconfig,targetcwd=configuration(tmp_path/'target');targetvalue=json.loads(targetconfig.read_text());targetvalue.update(transferAuthorityDirectory=str(tmp_path/'target/transfer'),transferWorkspaceRoots=[str(targetcwd)],workerCommand=['/impossible-worker'])
    targetconfig.write_text(json.dumps(targetvalue));target=await Client.launch(targetconfig);await initialize(target)
    a=TransferNode(value['transferAuthorityDirectory'],'Source');b=TransferNode(targetvalue['transferAuthorityDirectory'],'Target')
    atomic(a.directory/'peers.json',{b.identity['id']:b.identity});atomic(b.directory/'peers.json',{a.identity['id']:a.identity})
    controls=Path(value['appHome'])/'sessions'/sid/'control-state.json';saved=json.loads(controls.read_text());saved['selection']={'provider':'fixture','model':'fixture-model'};controls.write_text(json.dumps(saved))
    native=next((tmp_path/'source/home/projects').glob('*/sessions/'+sid));(native/'events.jsonl').write_text('{"event":"preserved"}\n');original=(native/'transcript.jsonl').read_bytes()
    try:
        outgoing=a.begin(sid,b.identity['id'],'move',{'workspace':str(cwd)});identity=outgoing['id']
        fenced=await operation(source,sid,cwd,'source.fence',identity);assert fenced['quiesced'] and fenced['fence']['phase']=='staged'
        early=await source.request('_amplifier/transfer',{'sessionId':sid,'cwd':str(cwd),'operation':'source.commit','args':{'transferId':identity}})
        assert 'error' in early
        denied=await source.request('_amplifier/transfer',{'sessionId':sid,'cwd':str(targetcwd),'operation':'inspect','args':{'transferId':identity}})
        assert 'outside launcher authority' in denied['error']['message']
        snapshot=await operation(source,sid,cwd,'source.capture',identity);files={} 
        for name,info in snapshot['files'].items():
            offset=0;parts=[]
            while True:
                part=await operation(source,sid,cwd,'source.read',identity,name=name,offset=offset,limit=19);parts.append(base64.b64decode(part['data']))
                if part['nextOffset'] is None:break
                offset=part['nextOffset']
            files[name]={**info,'data':base64.b64encode(b''.join(parts)).decode()}
        outgoing=a.prepared(identity,{'nativeIdentity':sid,'native':files,'intent':snapshot['intent']})
        envelope=json.loads(Path(outgoing['package']).read_text());incoming=b.receive(envelope,{'workspace':str(targetcwd)})
        write_capsule(b.directory/'packages'/(identity+'.json'),envelope)
        await operation(target,sid,targetcwd,'destination.fence',identity)
        blocked=await source.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]});assert 'error' in blocked
        # Boundary fixture proof only: no claim a real model/account was checked.
        incoming=b.ready(identity,{'workspace':str(targetcwd)},{'runtimeVerified':True,'accountVerified':True,'nativeFenceVerified':True,'credentialsOrigin':'destination'})
        await operation(source,sid,cwd,'source.verify',identity)
        outgoing=a.releasing(identity,incoming['readyReceipt'],outgoing['revision'],a.directory/'archives'/identity)
        committed=await operation(source,sid,cwd,'source.commit',identity);assert committed['fence']['phase']=='committed'
        outgoing=a.release(identity,incoming['readyReceipt'],outgoing['revision'])
        premature=await operation(target,sid,targetcwd,'inspect',identity);assert premature['fence']['phase']=='staged'
        attempt=await target.request('_amplifier/transfer',{'sessionId':sid,'cwd':str(targetcwd),'operation':'destination.activate','args':{'transferId':identity}})
        assert 'error' in attempt
        b.activating(identity,outgoing['releaseCertificate'],incoming['revision'])
        installed=await operation(target,sid,targetcwd,'destination.install',identity);assert installed['phase']=='installed'
        b.active(identity)
        activated=await operation(target,sid,targetcwd,'destination.activate',identity);assert activated['phase']=='activated' and activated['inputsReplayed'] is False
        assert (await operation(target,sid,targetcwd,'inspect',identity))['receipt']==activated
        assert await operation(target,sid,targetcwd,'destination.activate',identity)==activated
        copied=next((tmp_path/'target/home/projects').glob('*/sessions/'+sid));assert (copied/'transcript.jsonl').read_bytes()==original
        assert (native/'transcript.jsonl').read_bytes()==original and (native/'events.jsonl').read_bytes()==(copied/'events.jsonl').read_bytes()
        assert (await operation(source,sid,cwd,'inspect',identity))['fence']['phase']=='committed'
    finally:await source.close();await target.close()

@pytest.mark.asyncio
async def test_destination_readiness_uses_owned_configuration_and_never_repeats_probe(tmp_path,monkeypatch):
    import yaml
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
    config,cwd=configuration(tmp_path)
    value=json.loads(config.read_text());value.update(transferAuthorityDirectory=str(tmp_path/'transfer'),transferWorkspaceRoots=[str(cwd)],workerCommand=['/impossible-worker'],transferProbeCommand=[__import__('sys').executable,'-m','amplifier_acp.native.portability_probe'])
    config.write_text(json.dumps(value))
    audit=tmp_path/'probe-audit.txt'
    (tmp_path/'home/settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':'fixture-readiness','app':[]},'config':{'providers':[{'id':'fixture','module':'provider-fixture','config':{'reasoning_effort':'low','readinessAudit':str(audit)}}]}}))
    source=TransferNode(tmp_path/'source','Source');target=TransferNode(tmp_path/'transfer','Target')
    atomic(target.directory/'peers.json',{source.identity['id']:source.identity});atomic(source.directory/'peers.json',{target.identity['id']:target.identity})
    sid='fixture-native-readiness';row=source.begin(sid,target.identity['id'],'probe',{})
    row=source.prepared(row['id'],{'intent':{'bundle':'fixture-readiness','selection':{'provider':'fixture','model':'fixture-model','effort':'low'}}});capsule=json.loads(Path(row['package']).read_text())
    incoming=target.receive(capsule,{});write_capsule(target.directory/'packages'/(row['id']+'.json'),capsule)
    client=await Client.launch(config);await initialize(client)
    try:
        policy=await operation(client,sid,cwd,'destination.policy',row['id'])
        incoming.update(readinessPolicy=policy['policy'],readinessPolicyHash=policy['policyHash']);target.save(incoming)
        checked=await operation(client,sid,cwd,'destination.check',row['id']);assert checked['completionReceipt']['generation_requests']==1
        assert checked['accountVerificationScope']=='selected-model-request-accepted'
        assert await operation(client,sid,cwd,'destination.check',row['id'])==checked
        assert audit.read_text().splitlines()==['one offline request']
        # This exercised the real public Core request/response + Foundation fence
        # graph with an offline provider, never a live model/account credential.
    finally:await client.close()
