from types import SimpleNamespace
from unittest.mock import AsyncMock
import copy
import pytest
from amplifier_acp.image_artifacts import completed_artifact, deliver


def event():
    return {'kind':'tool','label':'image_generate','phase':'completed','sessionId':'session-one','toolCallId':'call-one',
        'output':{'success':True,'output':{'schema':'amplifier.image.receipt.v1','status':'completed','operation':'generate',
        'requestId':'image-one','requestHash':'a'*64,'artifact':{'path':'mug.png','mimeType':'image/png','sha256':'b'*64}}}}


def session(feature=True, done=False):
    return SimpleNamespace(identity='session-one',prompt=SimpleNamespace(done=lambda:done),
        server=SimpleNamespace(host_info={'features':{'imageResultArtifacts':feature}}),
        bridge=AsyncMock(return_value={'accepted':True}))


@pytest.mark.asyncio
async def test_live_root_result_uses_exact_hash_and_stable_command():
    s=session();row=event()
    assert 'saved' in await deliver(s,row)
    operation,request=s.bridge.call_args.args
    assert operation=='dispatch' and request['action']=='canvas.openFile'
    assert request['args']=={'path':'mug.png','kind':'image','title':'Generated image','expectedSha256':'b'*64}
    assert request==completed_artifact(copy.deepcopy(row))
    row['output']['output']['operation']='edit'
    assert completed_artifact(row)['args']['title']=='Edited image'


@pytest.mark.asyncio
async def test_no_replay_child_or_unnegotiated_delivery():
    for s,row in [(session(False),event()),(session(done=True),event()),(session(),dict(event(),sessionId='child'))]:
        assert await deliver(s,row) is None
        s.bridge.assert_not_called()


@pytest.mark.asyncio
async def test_failed_delivery_is_bounded_and_never_regenerates_or_retries():
    s=session();s.bridge.side_effect=RuntimeError('private details')
    result=await deliver(s,event())
    assert 'original file is preserved' in result and 'private details' not in result
    assert s.bridge.call_count==1


@pytest.mark.parametrize('change',[
    {'phase':'error'},{'label':'app_control'},{'output':'forged serialized receipt'},
    {'output':{'success':False,'output':event()['output']['output']}},
])
def test_invalid_tool_provenance_is_not_an_image(change):
    assert completed_artifact(dict(event(),**change)) is None


@pytest.mark.parametrize('field,value',[('requestHash','bad'),('requestId',''),('operation','unknown')])
def test_bad_receipt_refused(field,value):
    row=event();row['output']['output'][field]=value
    assert completed_artifact(row) is None


@pytest.mark.asyncio
async def test_actual_execution_hook_delivers_and_reports_only_in_tool_details():
    from amplifier_acp.server import Session
    from amplifier_acp.native.execution_events import ExecutionEvents
    s=Session.__new__(Session)
    s.identity='session-one';s.tools=set();s.prompt=SimpleNamespace(done=lambda:False)
    s.coordination=SimpleNamespace(observe=lambda row:False)
    s.server=SimpleNamespace(extensions=set(),host_info={'features':{'imageResultArtifacts':True}},notify=AsyncMock())
    s.bridge=AsyncMock(return_value={'accepted':True})
    events=[];observer=ExecutionEvents(s.identity,events.append)
    observer.hook(s.identity,'tool:post',{'tool_call_id':'call-one','tool_name':'image_generate','result':event()['output']})
    for row in events:await s.observe(row)
    s.bridge.assert_awaited_once()
    updates=[call.args[1]['update'] for call in s.server.notify.call_args_list]
    assert all(row['sessionUpdate'].startswith('tool_call') for row in updates)
    assert updates[-1]['rawOutput']==event()['output']
    assert 'saved' in updates[-1]['content'][-1]['content']['text']

@pytest.mark.asyncio
async def test_gallery_metadata_is_negotiated_and_uses_the_scoped_actual_tool_identity():
    s = session()
    s.server.host_info['features']['imageResultMetadata'] = 1
    row = event()
    await deliver(s, row)
    assert s.bridge.call_args.args[1]['args']['imageResult'] == {
        'version': 1, 'toolCallId': 'session-one:call-one',
        'requestId': 'image-one', 'operation': 'generate'}
    assert 'imageResult' not in completed_artifact(row)['args']

@pytest.mark.asyncio
@pytest.mark.parametrize('version', [True, False, '1', 2, None])
async def test_unknown_metadata_versions_use_legacy_publication(version):
    s = session()
    s.server.host_info['features']['imageResultMetadata'] = version
    await deliver(s, event())
    assert 'imageResult' not in s.bridge.call_args.args[1]['args']
