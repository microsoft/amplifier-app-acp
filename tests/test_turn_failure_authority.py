"""Root generation evidence owns failure; idle and successful tools do not."""
import asyncio
import json
import pytest
from amplifier_acp.server import Server, Session
from amplifier_acp.worker import NativeError
from amplifier_acp.lifecycle_failure import read
from native_setup import configuration

@pytest.mark.asyncio
async def test_idle_then_failed_root_stays_pending_until_failure_and_keeps_tool_success(tmp_path):
    config,cwd=configuration(tmp_path)
    server=Server(json.loads(config.read_text()),None)
    updates=[]
    async def notify(method,params): updates.append((method,params))
    server.notify=notify
    session=Session(server,'root-failure',str(cwd))
    session.prompt=asyncio.get_running_loop().create_future()
    session.input_id='input-one'
    await session.observe({'type':'execution.event','event':{
        'id':'tool-one','sessionId':session.identity,'kind':'tool','label':'read_file',
        'phase':'completed','output':{'text':'original tool result'}}})
    await session.observe({'type':'session.idle','sessionId':session.identity})
    assert not session.prompt.done()
    await session.observe({'type':'generation.failed','sessionId':session.identity,
        'rootSessionId':session.identity,'input_ids':['input-one'],
        'error_type':'ProviderError','error_category':'provider_unavailable'})
    with pytest.raises(NativeError,match='generation failed'): await session.prompt
    assert read(server,session.identity)['inputId']=='input-one'
    tool=[params['update'] for method,params in updates if method=='session/update']
    assert tool[0]['status']=='completed' and tool[0]['toolCallId']=='root-failure:tool-one'

@pytest.mark.asyncio
@pytest.mark.parametrize('identity,root',[('child','root-failure'),('root-failure','other-root')])
async def test_child_or_foreign_root_failure_never_finishes_or_records_parent_failure(tmp_path,identity,root):
    config,cwd=configuration(tmp_path)
    server=Server(json.loads(config.read_text()),None)
    async def notify(*args): pass
    server.notify=notify
    session=Session(server,'root-failure',str(cwd))
    session.prompt=asyncio.get_running_loop().create_future();session.input_id='input-one'
    await session.observe({'type':'generation.failed','sessionId':identity,'rootSessionId':root,
        'input_ids':['input-one'],'error_type':'ProviderError','error_category':'provider_unavailable'})
    assert not session.prompt.done() and read(server,session.identity) is None
    session.prompt.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected', [
    ('summary_output_limit', 'summary reached its output limit'),
    ('summary_empty', 'no usable conversation summary'),
    ('previous_failure', 'waiting after an earlier compaction failure'),
    ('native_input_oversized', 'too large for native compaction'),
    ('native_checkpoint_invalid', 'checkpoint cannot be used'),
    ('summary_failed', 'summary could not be prepared'),
    ('native_failed', 'could not compact'),
])
async def test_root_compaction_failure_retains_specific_guidance_without_replaying_history(tmp_path, code, expected):
    from amplifier_acp.input_outcome import Outcomes
    config, cwd = configuration(tmp_path)
    server = Server(json.loads(config.read_text()), None)
    updates = []
    async def notify(method, params): updates.append((method, params))
    server.notify = notify
    session = Session(server, 'root-summary', str(cwd))
    session.prompt = asyncio.get_running_loop().create_future()
    session.input_id = 'original-input'
    directory = server.directory(str(cwd), session.identity)
    directory.mkdir(parents=True, exist_ok=True)
    history = directory/'transcript.jsonl'
    original = b'{"role":"user","content":"Keep my original request"}\n'
    history.write_bytes(original)
    event = {'type':'generation.failed','sessionId':session.identity,
        'rootSessionId':session.identity,'input_ids':['original-input'],
        'error_type':'ContextPreparationError','error_category':'context_compaction',
        'error_stage':'context_preparation','error_code':code,
        'error': 'private-sdk-payload-must-not-appear'}
    await session.observe(event)
    with pytest.raises(NativeError): await session.prompt
    saved = read(server, session.identity)
    assert expected in saved['summary']
    assert saved['code'] == code and saved['stage'] == 'context_preparation'
    assert saved['replayed'] is False and saved['inputId'] == 'original-input'
    assert 'private-sdk' not in json.dumps(saved)
    assert history.read_bytes() == original
    outcomes = Outcomes(server.config['appHome'], session.identity, str(cwd))
    receipt = outcomes.failed('original-input', event)
    assert receipt['error']['message'] == saved['summary']
    assert receipt['noReplay'] and receipt['effectBoundary'] == 'unknown'
    assert outcomes.failed('original-input', event) == receipt
    assert history.read_bytes() == original


def test_counting_failure_keeps_bounded_retry_guidance_and_never_arbitrary_provider_fields():
    from amplifier_acp.lifecycle_failure import generation_failure
    safe = generation_failure({'error_category':'context_measurement','count_failure':{
        'category':'timeout','retryable':True,'httpStatus':503,'attempts':2,
        'requestId':'request-123','headers':{'Authorization':'secret'}}})
    assert safe['countFailure'] == {'category':'timeout','retryable':True,'httpStatus':503,'attempts':2,'requestId':'request-123'}
    assert safe['retryable'] and 'Continue conversation' in safe['guidance']
    assert 'secret' not in json.dumps(safe)
    unsafe = generation_failure({'error_category':'context_measurement','count_failure':{
        'category':'authentication','retryable':True,'httpStatus':True,'attempts':99,
        'requestId':'private\nheader'}})
    assert unsafe['retryable'] is False and unsafe['countFailure'] == {'category':'authentication','retryable':False}
    unknown = generation_failure({'error_category':'context_compaction','error_code':{'untrusted':'value'}})
    assert 'code' not in unknown
