import json
import pytest
from client import Client
from native_setup import configuration


@pytest.mark.asyncio
async def test_peer_input_uses_host_original_and_native_terminal_without_replay(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config);approved=False;calls=[]
    async def reply(row):
        if row['method']=='session/request_permission':
            return {'outcome':{'outcome':'selected','optionId':'deny'}}
        assert row['method']=='_amplifier/host' and row['params']['operation']=='coordination.delivery.admit'
        args=row['params']['args'];calls.append(args)
        return {'admitted':approved,'message':{'text':'Host saved ORBIT 572 request','peerEnvelope':{'requestId':args['inputId'],'grantId':args['grantId'],'mode':'queue','senderSessionId':'source','recipientSessionId':'target'}}}
    client.response_handler=reply
    try:
        initialized=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/host':{'version':1}}}})
        assert initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['peerTurns']['guarded']
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        args={'inputId':'peer:one','grantId':'scope-one','taskId':None,'taskRevision':None,'text':'UNTRUSTED CALLER SUBSTITUTE'}
        async def control():return await client.request('_amplifier/native',{'sessionId':sid,'operation':'peer.submit','args':args})
        refused=await control();assert refused['result']['accepted'] is False
        approved=True;settled=await control();assert 'result' in settled,settled
        assert settled['result']['accepted'] and settled['result']['stopReason']=='end_turn',settled
        duplicate=await control();assert duplicate['error']['data']['outcome']=='already-admitted'
        assert len(calls)==3
        home=json.loads(config.read_text())['home']
        from pathlib import Path
        rows=[json.loads(line) for path in Path(home).rglob('transcript.jsonl') for line in path.read_text().splitlines()]
        peer=next(row for row in rows if row.get('metadata',{}).get('inputOrigin')=='peer')
        assert peer['metadata']['peerEnvelope']['requestId']=='peer:one'
        assert 'Host saved ORBIT 572' in json.dumps(peer) and 'UNTRUSTED CALLER SUBSTITUTE' not in json.dumps(rows)
    finally:await client.close()
