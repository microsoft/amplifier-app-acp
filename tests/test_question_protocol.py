import hashlib,json
import pytest
from client import Client
from native_setup import configuration

@pytest.mark.asyncio
async def test_question_requires_exact_host_proof_streams_and_preserves_provenance(tmp_path):
    config,cwd=configuration(tmp_path);client=await Client.launch(config);approved=False;calls=[]
    async def reply(row):
        assert row['method']=='_amplifier/host' and row['params']['operation']=='questions.delivery.admit'
        args=row['params']['args'];calls.append(args)
        return {'admitted':approved,**{name:args[name] for name in ('inputId','questionId','questionRevision')},'answerHash':hashlib.sha256(args['text'].encode()).hexdigest()}
    client.response_handler=reply
    try:
        await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},'amplifier.dev/host':{'version':1,'features':{'questions':True}}}}})
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        args={'inputId':'question:q1:answer','text':'Use the checked option','questionId':'q1','questionRevision':3,'answerProvenance':{'kind':'explicit-user','messageId':'answer1'}}
        request={'sessionId':sid,'operation':'question.submit','args':args}
        denied=await client.request('_amplifier/native',request);assert denied['result']['accepted'] is False and denied['result']['executed'] is False
        assert not any(r.get('params',{}).get('event',{}).get('type')=='generation.started' for r in client.events)
        approved=True
        settled=await client.request('_amplifier/native',request)
        assert settled['result']=={'accepted':True,'inputId':args['inputId'],'stopReason':'end_turn'},settled
        assert any(r.get('params',{}).get('update',{}).get('sessionUpdate')=='agent_message_chunk' for r in client.events)
        duplicate=await client.request('_amplifier/native',request);assert duplicate['error']['data']['outcome']=='already-admitted'
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd)})
        update=next(u for u in history['result']['updates'] if u['sessionUpdate']=='user_message_chunk')
        provenance=update['_meta']['amplifier.dev/history'];assert provenance['inputOrigin']=='question' and provenance['questionId']=='q1' and provenance['questionRevision']==3
        assert provenance['answerProvenance']==args['answerProvenance']
        assert provenance['authorization']=='unverified-native-history'
        await client.request('session/close',{'sessionId':sid})
        await client.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        replay=await client.request('_amplifier/native',request);assert replay['error']['data']['outcome']=='already-admitted'
        assert len(calls)==2
    finally:await client.close()

@pytest.mark.asyncio
async def test_question_provenance_reservation_survives_uncertain_admission(tmp_path):
    from amplifier_acp.native.input_provenance import reserve,Context
    path=tmp_path/'input-provenance.sqlite3';metadata={'inputOrigin':'question','questionId':'q'}
    reserve(path,'uncertain',metadata)
    with pytest.raises(ValueError,match='not replayed'):reserve(path,'uncertain',metadata)
    class Underlying:
        async def add_message(self,row):self.row=row
    inner=Underlying();wrapper=Context(inner,path)
    await wrapper.add_message({'role':'user','content':'answer','metadata':{'amplifier_input':{'id':'uncertain'}}})
    assert inner.row['metadata']['questionId']=='q'
