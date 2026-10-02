import asyncio,json
import pytest
from test_session_lifecycle import prepared,prompt,action

@pytest.mark.asyncio
async def test_exact_native_edit_rewrites_then_streams_once_and_preserves_events(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        await prompt(client,sid,'First original','turn1');await prompt(client,sid,'Second original','turn2');await prompt(client,sid,'Third original','turn3')
        revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision']
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));(native/'events.jsonl').write_text('unchanged full event log\n')
        args={'operationId':'edit1','inputId':'replacement1','targetMessageId':'turn2','expectedHistoryRevision':revision,'text':'Second revised'}
        params={'sessionId':sid,'operation':'history.revise','args':args}
        result=await client.request('_amplifier/native',params);assert result.get('result',{}).get('stopReason')=='end_turn',result
        proofs=[row['params']['event'] for row in client.events if row.get('params',{}).get('event',{}).get('type')=='history.revised']
        assert len(proofs)==1 and proofs[0]['beforeTurnId']=='turn2' and proofs[0]['targetMessageId']=='turn2' and proofs[0]['inputId']=='replacement1'
        text=(native/'transcript.jsonl').read_text();assert 'First original' in text and 'Second revised' in text and 'Third original' not in text and 'Second original' not in text
        assert 'history-edit' in text and (native/'events.jsonl').read_text()=='unchanged full event log\n'
        duplicate=await client.request('_amplifier/native',params);assert duplicate['error']['data']['outcome']=='already-admitted'
        receipt=await client.request('_amplifier/history/edit',{'sessionId':sid,'cwd':str(cwd),'operationId':'edit1'});assert receipt['result']['receipt']['status']=='admitted'
        stale=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':{**args,'operationId':'edit2','inputId':'replacement2'}})
        assert stale['result']['accepted'] is False and stale['result']['executed'] is False
        assert (native/'events.jsonl').read_text()=='unchanged full event log\n'
    finally:await client.close()

@pytest.mark.asyncio
async def test_edit_busy_and_legacy_projection_bypass_refused(tmp_path):
    client,sid,cwd,config=await prepared(tmp_path)
    try:
        await prompt(client,sid,'original','turn1');revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision']
        running=asyncio.create_task(prompt(client,sid,'slow','active'))
        async with asyncio.timeout(10):
            while not any(row.get('params',{}).get('event',{}).get('activeInputId')=='active' for row in client.events):await asyncio.sleep(.01)
        args={'operationId':'edit1','inputId':'replacement1','targetMessageId':'turn1','expectedHistoryRevision':revision,'text':'changed'}
        result=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':args});assert result['result']['executed'] is False
        legacy=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.edit','args':{'source':{'messages':[]}}});assert 'error' in legacy
        await client.request('session/cancel',{'sessionId':sid});await running
    finally:await client.close()


@pytest.mark.asyncio
async def test_worker_loss_during_context_revision_fences_resume_and_never_replays(tmp_path,monkeypatch):
    import os,signal
    from test_steering_protocol import opened,entered
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'writers'))
    client,sid,cwd=await opened(tmp_path,rewindGate=True)
    try:
        await prompt(client,sid,'Original durable history','turn1')
        revision=(await action(client,sid,cwd,'inspect'))['result']['historyRevision']
        params={'sessionId':sid,'operation':'history.revise','args':{'operationId':'edit1','inputId':'replacement1','targetMessageId':'turn1','expectedHistoryRevision':revision,'text':'Never automatically replay this'}}
        pending=asyncio.create_task(client.request('_amplifier/native',params))
        await entered(tmp_path,'rewind')
        owner=json.loads(next((tmp_path/'writers').glob('v1/*/'+sid+'/owner.json')).read_text())
        os.kill(owner['pid'],signal.SIGKILL)
        response=await pending;assert 'error' in response,response
        saved=await client.request('_amplifier/history/edit',{'sessionId':sid,'cwd':str(cwd),'operationId':'edit1'});assert saved['result']['receipt']['status']=='unknown'
        blocked=await prompt(client,sid,'New input must not cross the unresolved revision','other');assert 'unresolved' in blocked['error']['message']
        history=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'limit':50})
        assert not any('Never automatically replay this' in u.get('content',{}).get('text','') for u in history['result']['updates'])
    finally:await client.close()
