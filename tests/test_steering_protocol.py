"""Actual Core/Foundation, subprocess protocol, public loop-live anchored capability."""
import asyncio,json
from pathlib import Path
import pytest,yaml
from client import Client
from native_setup import configuration
from amplifier_acp.steering import Receipts

async def opened(tmp_path,*,principal=False,**provider_config):
    config,cwd=configuration(tmp_path)
    bundle=tmp_path/'fixture.yaml';value=yaml.safe_load(bundle.read_text());value['providers'][0]['config']={'gateDirectory':str(tmp_path/'gates'),**provider_config};bundle.write_text(yaml.safe_dump(value))
    client=await Client.launch(config)
    await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1},**({'amplifier.dev/messagePrincipal':{'version':1,'account':'fixture-user'}} if principal else {})}}})
    made=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]});assert 'result' in made,made
    return client,made['result']['sessionId'],cwd

async def entered(tmp_path,name):
    async with asyncio.timeout(20):
        while not (tmp_path/'gates'/f'{name}.entered').exists():await asyncio.sleep(.01)

def release(tmp_path,name):(tmp_path/'gates'/f'{name}.release').touch()
def events(client,kind):return [r['params']['event'] for r in client.events if r.get('method')=='_amplifier/event' and r['params']['event']['type']==kind]
async def steer(client,sid,args):return await client.request('_amplifier/native',{'sessionId':sid,'operation':'steer.submit','args':args})
async def receipt(client,sid,cwd,input_id):return (await client.request('_amplifier/steering',{'sessionId':sid,'cwd':str(cwd),'inputId':input_id}))['result']['receipt']

def test_receipt_monotonic_exact_identity_unknown_never_replayed(tmp_path):
    store=Receipts(tmp_path,'fixture');args={'inputId':'edit1','activeInputId':'turn1','text':'correction'}
    store.reserve(args,'generation1');store.update('edit1',{'accepted':True,'disposition':'applied'})
    assert store.update('edit1',{'disposition':'queued'})['disposition']=='applied'
    with pytest.raises(ValueError,match='conflicts'):store.read('edit1',{**args,'text':'different'})
    store.reserve({**args,'inputId':'edit2'},'generation1')
    assert store.uncertain('generation1')[0]['disposition']=='unknown'
    assert not store.uncertain('generation1')

@pytest.mark.asyncio
async def test_actual_anchored_applied_same_collector_exact_duplicate_and_canonical_provenance(tmp_path):
    client,sid,cwd=await opened(tmp_path)
    try:
        prompt=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}],'_meta':{'amplifier.dev/command':{'commandId':'turn1'}}}))
        await entered(tmp_path,'tool')
        assert events(client,'generation.started')[0]['activeInputId']=='turn1'
        args={'inputId':'edit1','activeInputId':'turn1','text':'Use the corrected direction','provenance':{'source':'fixture'}}
        accepted=(await steer(client,sid,args))['result'];assert accepted['accepted'] and accepted['disposition']=='queued',accepted
        duplicate=(await steer(client,sid,args))['result'];assert duplicate['disposition']=='queued' and duplicate['replayed'] is False
        conflict=await steer(client,sid,{**args,'text':'different'});assert 'error' in conflict
        release(tmp_path,'tool');assert (await prompt)['result']['stopReason']=='end_turn'
        row=await receipt(client,sid,cwd,'edit1');assert row['disposition']=='applied',row
        applied=events(client,'steering.applied');assert len(applied)==1 and applied[0]['activeInputId']=='turn1' and applied[0]['target_generation_id']==row['generationId']
        assert len(events(client,'generation.started'))==1
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        assert sum(u.get('content',{}).get('text')=='Use the corrected direction' for u in history['result']['updates'])==1
        transcript=next((tmp_path/'home/projects').glob('*/sessions/*/transcript.jsonl')).read_text()
        assert '"inputOrigin": "steering"' in transcript and '"activeInputId": "turn1"' in transcript
        assert (await steer(client,sid,{'inputId':'late','activeInputId':'turn1','text':'No generation'}))['result']['executed'] is False
    finally:await client.close()

@pytest.mark.asyncio
async def test_checkpoint_steering_continues_same_turn_and_passive_receipt_survives_restart(tmp_path):
    client,sid,cwd=await opened(tmp_path)
    args={'inputId':'late1','activeInputId':'turn1','text':'This missed the request boundary'}
    try:
        prompt=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-late'}],'_meta':{'amplifier.dev/command':{'commandId':'turn1'}}}))
        await entered(tmp_path,'checkpoint')
        assert (await steer(client,sid,args))['result']['accepted']
        release(tmp_path,'checkpoint');assert (await prompt)['result']['stopReason']=='end_turn'
        saved=await receipt(client,sid,cwd,args['inputId']);assert saved['disposition']=='applied',saved
        assert len(events(client,'steering.applied'))==1
        assert not events(client,'steering.held')
        assert len(events(client,'generation.started'))==1
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        assert sum(u.get('content',{}).get('text')==args['text'] for u in history['result']['updates'])==1
    finally:await client.close()
    config=tmp_path/'config.json';value=json.loads(config.read_text());value['workerCommand']=['/does-not-exist'];config.write_text(json.dumps(value))
    passive=await Client.launch(config)
    try:
        await passive.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        assert (await receipt(passive,sid,cwd,args['inputId']))==saved
    finally:await passive.close()


def test_receipt_pending_query_uses_index_for_large_session(tmp_path):
    store=Receipts(tmp_path,'fixture')
    with store.db(create=True) as db:
        db.executemany('INSERT INTO receipts VALUES (?,?,?)',[(str(i),'signature',json.dumps({'inputId':str(i),'disposition':'applied','generationId':'old'})) for i in range(25000)])
        plans=db.execute("EXPLAIN QUERY PLAN SELECT id,body FROM receipts WHERE json_extract(body,'$.disposition') IN ('reserved','queued') LIMIT 2000").fetchall()
    assert any('receipts_disposition' in row[-1] for row in plans),plans
    assert store.uncertain()==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('injection',[False,True])
async def test_cancel_preserves_held_vs_uncertain_context_effect(tmp_path,injection):
    client,sid,cwd=await opened(tmp_path,**({'injectionGate':'edit1'} if injection else {}))
    try:
        prompt=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}],'_meta':{'amplifier.dev/command':{'commandId':'turn1'}}}))
        await entered(tmp_path,'tool')
        args={'inputId':'edit1','activeInputId':'turn1','text':'Possible partial context effect'}
        assert (await steer(client,sid,args))['result']['accepted']
        if injection:
            release(tmp_path,'tool');await entered(tmp_path,'injection')
        await client.request('session/cancel',{'sessionId':sid})
        assert (await prompt)['result']['stopReason']=='cancelled'
        saved=await receipt(client,sid,cwd,'edit1')
        assert saved['disposition']==('unknown' if injection else 'held'),saved
        assert (await steer(client,sid,args))['result']['disposition']==saved['disposition']
        assert len(events(client,'generation.started'))==1
    finally:await client.close()

@pytest.mark.asyncio
async def test_mounted_runtime_without_anchored_contract_refuses_no_fallback(tmp_path):
    client,sid,cwd=await opened(tmp_path,steeringUnavailable=True)
    try:
        prompt=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}],'_meta':{'amplifier.dev/command':{'commandId':'turn1'}}}))
        await entered(tmp_path,'tool')
        result=(await steer(client,sid,{'inputId':'edit1','activeInputId':'turn1','text':'No fallback input'}))['result']
        assert result['accepted'] is False and result['executed'] is False
        release(tmp_path,'tool');await prompt
        assert len(events(client,'generation.started'))==1
        assert not events(client,'steering.applied')
    finally:await client.close()


@pytest.mark.asyncio
async def test_rich_steering_same_turn_preserves_original_quote_and_attachment(tmp_path):
    import base64
    from amplifier_acp.public_message import META
    from amplifier_acp.history import message_id
    client,sid,cwd=await opened(tmp_path,principal=True,promptAuditFile=str(tmp_path/'prompts.jsonl'))
    try:
        first=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'Previous **answer context**'}]})
        assert 'result' in first,first
        history=(await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50}))['result']
        ref=next(u['_meta']['amplifier.dev/history']['messageReference'] for u in history['updates'] if u['sessionUpdate']=='agent_message_chunk')
        before=len(events(client,'generation.started'))
        running=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}],'_meta':{'amplifier.dev/command':{'commandId':'rich-turn'}}}))
        await entered(tmp_path,'tool')
        assert events(client,'generation.started')[-1]['activeSteering']['content']['attachments'] is True
        text='Use **this** correction'
        original={'version':1,'inputId':'rich-edit','capture':'host-standard','blocks':[{'type':'text','text':text},{'type':'attachment','reference':{'owner':'resources','id':'amplifier-attachment://12345678-1234-1234-1234-123456789abc/body?session=ahp-session%3A%2Frich'},'name':'notes.txt','mimeType':'text/plain'}]}
        args={'inputId':'rich-edit','activeInputId':'rich-turn','prompt':[{'type':'text','text':text},{'type':'resource','resource':{'uri':'fixture:/notes','mimeType':'text/plain','blob':base64.b64encode(b'Exact steering attachment').decode()}}],'reply':{**ref,'sessionId':sid}}
        request={'sessionId':sid,'operation':'steer.submit','args':args,'_meta':{META:original}}
        accepted=await client.request('_amplifier/native',request)
        assert accepted.get('result',{}).get('accepted'),accepted
        assert (await client.request('_amplifier/native',request))['result']['replayed'] is False
        release(tmp_path,'tool')
        assert (await running)['result']['stopReason']=='end_turn'
        assert len(events(client,'generation.started'))==before+1
        assert (await receipt(client,sid,cwd,'rich-edit'))['disposition']=='applied'
        history=(await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50,'includePublicRows':True}))['result']
        rows=[r for r in history['publicRows'] if r['publicMessage']['inputId']=='rich-edit']
        assert len(rows)==1 and rows[0]['publicMessage']==original
        update=next(u for u in history['updates'] if u.get('_meta',{}).get('amplifier.dev/history',{}).get('nativeInput',{}).get('id')=='rich-edit')
        quote=update['_meta']['amplifier.dev/history']['replyTo']
        assert quote['messageId']==ref['messageId'] and 'Previous **answer context**' in quote['excerpt']
        audit=json.loads((tmp_path/'prompts.jsonl').read_text().splitlines()[-1])
        model_input=json.dumps(audit,ensure_ascii=False)
        assert text in model_input and 'Exact steering attachment' in model_input and 'Quoted earlier message' in model_input
        assert sum(m['role']=='user' and text in json.dumps(m) for m in audit)==1
    finally:await client.close()

@pytest.mark.asyncio
async def test_bad_rich_content_never_admitted_or_replayed(tmp_path):
    client,sid,cwd=await opened(tmp_path)
    try:
        running=asyncio.create_task(client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'steering-apply'}],'_meta':{'amplifier.dev/command':{'commandId':'turn1'}}}))
        await entered(tmp_path,'tool')
        args={'inputId':'invalid-rich','activeInputId':'turn1','prompt':[{'type':'text','text':'Correction'},{'type':'image','mimeType':'image/png','data':'not base64!'}]}
        assert 'error' in await steer(client,sid,args)
        assert await receipt(client,sid,cwd,'invalid-rich') is None
        release(tmp_path,'tool');await running
        assert not events(client,'steering.applied')
    finally:await client.close()
