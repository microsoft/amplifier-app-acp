"""Actual inert-provider worker exit and exact retirement acknowledgement races."""
import asyncio,io,json
from pathlib import Path
import pytest
from native_setup import configuration
from amplifier_acp.server import Server
from amplifier_acp.worker import NativeError

async def settled(tmp_path):
    config,cwd=configuration(tmp_path,idleSeconds=60)
    wire=io.StringIO();server=Server(json.loads(config.read_text()),wire)
    await server.dispatch('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    made=await server.dispatch('session/new',{'cwd':str(cwd),'mcpServers':[]})
    sid=made['sessionId'];session=server.sessions[sid]
    result=await server.dispatch('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'One owned explicit offline turn'}]})
    assert result['stopReason']=='end_turn'
    return server,session,wire,cwd

async def test_actual_worker_retirement_ack_lost_after_transport_receipt_reconciles_without_replay(tmp_path):
    server,session,wire,cwd=await settled(tmp_path);worker=session.worker;original=worker.command;answers=[]
    transcript=server.directory(str(cwd),session.identity)/'transcript.jsonl';before=transcript.read_bytes()
    async def lose_reply(op,**args):
        result=await original(op,**args)
        if op=='retire' and result.get('retired'):
            answers.append(result)
            raise NativeError('Injected caller-side loss after actual worker retirement reply')
        return result
    worker.command=lose_reply
    try:
        for _ in range(100):
            first=await session.retire()
            if first.get('reason')!='owned_work':break
            await asyncio.sleep(.02)
        assert first=={'retired':False,'reason':'retirement_unknown'}
        await asyncio.wait_for(worker.process.wait(),5)
        await worker.reader
        assert answers==[{'retired':True}]
        assert 'runtime.closed' in wire.getvalue()
        # Reconcile the same handle only from the exact original retirement
        # acknowledgement plus joined worker exit. Never invoke a second retire.
        recovered=await session.retire()
        assert recovered=={'retired':True},recovered
        status=await server.dispatch('_amplifier/retire/status',{'sessionId':session.identity})
        assert status=={'sessionId':session.identity,'retired':True}
        assert len(answers)==1 and transcript.read_bytes()==before
        count=wire.getvalue().count('runtime.ready')
        with pytest.raises(Exception,match='retired'):
            await server.dispatch('session/prompt',{'sessionId':session.identity,'prompt':[{'type':'text','text':'Must not reopen a retired handle'}]})
        assert wire.getvalue().count('runtime.ready')==count
    finally:await session.close()

@pytest.mark.parametrize('ack,exit_code,replaced',[(False,0,False),(True,None,False),(True,1,False),(True,-15,False),(True,0,True)])
async def test_unknown_retirement_requires_same_acknowledged_cleanly_exited_worker(tmp_path,ack,exit_code,replaced):
    from types import SimpleNamespace
    config,cwd=configuration(tmp_path)
    server=Server(json.loads(config.read_text()),io.StringIO())
    from amplifier_acp.server import Session
    session=Session(server,'unknown',str(cwd));session.retiring=True
    original=SimpleNamespace(retirement_confirmed=ack,process=SimpleNamespace(returncode=exit_code))
    session.retiring_worker=original
    session.worker=SimpleNamespace(retirement_confirmed=True,process=SimpleNamespace(returncode=0)) if replaced else original
    assert await session.retire()=={'retired':False,'reason':'retirement_unknown'}
    assert session.retiring and not session.retired
    assert session.retirement_status()=={'sessionId':'unknown','retired':False,'reason':'retirement_unknown'}

async def test_actual_idle_worker_close_is_not_whole_session_retirement(tmp_path):
    server,session,wire,cwd=await settled(tmp_path)
    worker=session.worker;before=(server.directory(str(cwd),session.identity)/'transcript.jsonl').read_bytes()
    try:
        for _ in range(100):
            proof=await worker.command('retire')
            if proof.get('retired'):break
            await asyncio.sleep(.02)
        assert proof=={'retired':True}
        await asyncio.wait_for(worker.process.wait(),5);await worker.reader
        assert not session.retiring and not session.retired
        assert await server.dispatch('_amplifier/retire/status',{'sessionId':session.identity})=={'sessionId':session.identity,'retired':False,'reason':'not_retiring'}
        count=wire.getvalue().count('runtime.ready')
        second=await server.dispatch('session/prompt',{'sessionId':session.identity,'prompt':[{'type':'text','text':'A separate NEW owned offline turn'}]})
        assert second['stopReason']=='end_turn' and wire.getvalue().count('runtime.ready')==count+1
        after=(server.directory(str(cwd),session.identity)/'transcript.jsonl').read_bytes()
        assert after.startswith(before)
        rows=[json.loads(line) for line in after.splitlines()]
        assert sum(row.get('role')=='user' and isinstance(row.get('content'),str) and row['content'].endswith('One owned explicit offline turn') for row in rows)==1
    finally:await session.close()

async def test_actual_retirement_proof_recheck_while_original_outer_reply_is_delayed(tmp_path):
    server,session,wire,cwd=await settled(tmp_path)
    worker=session.worker;original_command=worker.command;retire_calls=[]
    async def counted(op,**args):
        if op=='retire':retire_calls.append(op)
        return await original_command(op,**args)
    worker.command=counted
    # First wait only on definite owned-work refusal; no uncertain work is retried.
    for _ in range(100):
        result=await worker.command('park')
        if result.get('parked'):break
        await asyncio.sleep(.02)
    delayed=asyncio.Event();release=asyncio.Event();original_send=server.send
    async def delay_outer(row):
        if row.get('id')=='original-retirement' and row.get('result')=={'retired':True}:
            delayed.set();await release.wait()
        await original_send(row)
    server.send=delay_outer
    original=None
    try:
        for _ in range(100):
            original=asyncio.create_task(server.handle({'jsonrpc':'2.0','id':'original-retirement','method':'_amplifier/retire','params':{'sessionId':session.identity}}))
            finished=asyncio.create_task(delayed.wait())
            done,_=await asyncio.wait({original,finished},return_when=asyncio.FIRST_COMPLETED)
            if finished in done:break
            finished.cancel();await asyncio.gather(finished,return_exceptions=True)
            await original
            assert not session.retiring
            await asyncio.sleep(.02)
        assert delayed.is_set() and not original.done()
        before=len(retire_calls);history=(server.directory(str(cwd),session.identity)/'transcript.jsonl').read_bytes()
        proof=await server.dispatch('_amplifier/retire/status',{'sessionId':session.identity})
        assert proof=={'sessionId':session.identity,'retired':True} and len(retire_calls)==before
        assert (server.directory(str(cwd),session.identity)/'transcript.jsonl').read_bytes()==history
        release.set();await original
        replies=[json.loads(line) for line in wire.getvalue().splitlines() if 'original-retirement' in line]
        assert replies[-1]['result']=={'retired':True}
    finally:
        release.set()
        if original:await original
        await session.close()


@pytest.mark.parametrize('value,reply_id,error',[(1,'retirement',False),(True,'different-command',False),(True,'retirement',True)])
async def test_worker_reader_does_not_accept_nonliteral_uncorrelated_or_error_reply(value,reply_id,error):
    from types import SimpleNamespace
    from amplifier_acp.worker import NativeWorker
    rows=[]
    async def observe(row):rows.append(row)
    async def bridge(*args):raise AssertionError('No bridge calls permitted')
    worker=NativeWorker('fixture','/',{},observe,bridge)
    reader=asyncio.StreamReader();row={'op':'reply','id':reply_id,'result':{'retired':value}}
    if error:row['error']='uncertain'
    reader.feed_data((json.dumps(row)+'\n').encode());reader.feed_eof()
    worker.process=SimpleNamespace(stdout=reader,returncode=0)
    future=asyncio.get_running_loop().create_future();worker.pending['retirement']=future;worker.retirement_commands.add('retirement')
    await worker.read();await asyncio.gather(future,return_exceptions=True)
    assert worker.retirement_confirmed is False

async def test_actual_cold_peer_advertises_passive_status_and_never_warms_unknown_handle(tmp_path):
    from client import Client
    config,cwd=configuration(tmp_path,workerCommand=['/must/not/start'])
    peer=await Client.launch(config)
    try:
        initialized=await peer.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        marker=initialized['result']['agentCapabilities']['_meta']['amplifier.dev/native']['lifecycle']['retirementStatus']
        assert marker=={'version':1,'method':'_amplifier/retire/status','passive':True}
        missing=await peer.request('_amplifier/retire/status',{'sessionId':'not-loaded'})
        assert 'error' in missing and not peer.events
    finally:await peer.close()
