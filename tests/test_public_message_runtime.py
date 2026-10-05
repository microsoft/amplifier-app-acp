"""Packaged ACP and real worker/loop; fixture provider performs no network calls."""
import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml

from client import Client
from native_setup import configuration
from amplifier_acp.public_message import META, ENVELOPE, COPY_SOURCE


def original(identity,text):
    return {'version':1,'inputId':identity,'capture':'host-standard','blocks':[
        {'type':'text','text':text}, {'type':'attachment','reference':{
            'owner':'resources','id':'resource://original-owner/opaque?unchanged=1'},
         'name':'Literal attachment label','mimeType':'image/png'}]}


async def open_fixture(tmp_path,*,boundary=False,gates=False,legacy=False):
    config,cwd=configuration(tmp_path)
    bundle=tmp_path/'fixture.yaml';value=yaml.safe_load(bundle.read_text())
    value['providers'][0]['config']={'promptAuditFile':str(tmp_path/'provider-prompts.jsonl')}
    if boundary:
        import amplifier_module_context_managed
        value['session']['context']={'module':'context-managed','source':str(Path(amplifier_module_context_managed.__file__).parent),
                                    'config':{'engine':'boundary','token_meter':'actual','summarize_trigger':0.70,'summary_target_tokens':1500}}
    if gates:value['providers'][0]['config']['gateDirectory']=str(tmp_path/'gates')
    if legacy:
        legacy_python=Path(os.environ.get('PUBLIC_MESSAGE_LEGACY_PYTHON') or Path(__file__).resolve().parents[3]/'outputs/native-public-originals-source-20261004-r01/qualification/legacy-runtime/bin/python')
        if not legacy_python.exists():
            pytest.skip('Optional separately installed legacy context runtime is unavailable')
        old=legacy_python.parent.parent/'lib/python3.13/site-packages/amplifier_module_context_simple'
        assert old.is_dir()
        value['session']['context']={'module':'context-simple','source':str(old)}
        settings=json.loads(config.read_text());settings['workerCommand']=[str(legacy_python),'-I','-B','-m','amplifier_acp.native.runtime_worker']
        config.write_text(json.dumps(settings))
    bundle.write_text(yaml.safe_dump(value))
    client=await Client.launch(config)
    init=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert init['result']['agentCapabilities']['_meta']['amplifier.dev/native']['publicMessagePreservation']['runtimeNegotiated'] is True
    made=await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]})
    assert 'result' in made,made
    mounted=made['result']['_meta']['amplifier.dev/native']['publicMessagePreservation']
    assert mounted['supported'] is (not legacy)
    assert any(e.get('params',{}).get('event',{}).get('report',{}).get('publicMessagePreservation')==mounted for e in client.events)
    return client,made['result']['sessionId'],cwd,config


async def prompt(client,sid,identity,text,public):
    return await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':text}],
        '_meta':{'amplifier.dev/command':{'commandId':identity},META:public}})


async def read(client,sid,cwd):
    response=await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'includePublicRows':True})
    assert 'result' in response,response
    return response['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary',[False,True])
async def test_packaged_prompt_revise_fork_and_cold_resume_exact_originals(tmp_path,boundary):
    client,sid,cwd,config=await open_fixture(tmp_path,boundary=boundary)
    try:
        first=original('first','  literal @mention\n');second=original('second','Second literal')
        assert (await prompt(client,sid,'first','First expanded execution',first))['result']['stopReason']=='end_turn'
        assert (await prompt(client,sid,'second','Second expanded execution',second))['result']['stopReason']=='end_turn'
        result=await read(client,sid,cwd)
        assert [r['publicMessage'] for r in result['publicRows']]==[first,second]
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));rows=[json.loads(x) for x in (native/'transcript.jsonl').read_text().splitlines()]
        assert [r['content'] for r in rows if ENVELOPE in r.get('metadata',{})]==['First expanded execution','Second expanded execution']
        observed=(tmp_path/'provider-prompts.jsonl').read_text()
        assert ENVELOPE not in observed and COPY_SOURCE not in observed and 'Literal attachment label' not in observed
        assert 'First expanded execution' in observed and 'Second expanded execution' in observed
        selected=result['publicRows'][0]['locator']
        assert (await client.request('_amplifier/history',{'sessionId':sid,'cwd':str(cwd),'includePublicRows':True,'rowLocator':selected}))['result']['publicRows']==[result['publicRows'][0]]
        replacement=original('replacement','Revised literal')
        args={'inputId':'replacement','operationId':'revise','targetMessageId':'second','expectedHistoryRevision':result['revision'],'text':'Revised expanded execution'}
        before=(native/'transcript.jsonl').read_bytes()
        calls=(tmp_path/'provider-prompts.jsonl').read_bytes()
        # An older caller must not silently turn a rich input into text only.
        refused=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':args,'_meta':{META:replacement}})
        assert refused['result']['accepted'] is False and refused['result']['executed'] is False,refused
        assert (native/'transcript.jsonl').read_bytes()==before
        assert (tmp_path/'provider-prompts.jsonl').read_bytes()==calls
        context=[{'type':'text','text':args['text']},{'type':'resource_link','uri':replacement['blocks'][1]['reference']['id']}]
        changed=json.loads(json.dumps(replacement));changed['blocks'][1]['reference']['id']='resource://different'
        refused=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':{**args,'prompt':context},'_meta':{META:changed}})
        assert refused['result']['executed'] is False,refused
        assert (native/'transcript.jsonl').read_bytes()==before
        assert (tmp_path/'provider-prompts.jsonl').read_bytes()==calls
        refused=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':{**args,'prompt':[context[0],{'type':'unknown'}]},'_meta':{META:replacement}})
        assert refused['result']['executed'] is False,refused
        assert (native/'transcript.jsonl').read_bytes()==before
        assert (tmp_path/'provider-prompts.jsonl').read_bytes()==calls
        revised=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise',
            'args':{**args,'prompt':context},
            '_meta':{META:replacement}})
        assert revised.get('result',{}).get('stopReason')=='end_turn',revised
        current=await read(client,sid,cwd)
        # Existing archive chain retains the before-image; current epoch owns
        # replacement and remains the sole executable continuation.
        assert any(r['publicMessage']==replacement for r in current['publicRows'])
        primary=[json.loads(x) for x in (native/'transcript.jsonl').read_text().splitlines()]
        retained=[r for r in primary if ENVELOPE in r.get('metadata',{})]
        assert retained[-1]['content'].startswith('Revised expanded execution\nReferenced resource:') and retained[-1]['metadata'][ENVELOPE]==replacement
        assert second not in [r['metadata'][ENVELOPE] for r in retained]
        async with asyncio.timeout(10):
            while not (await client.request('_amplifier/retire',{'sessionId':sid}))['result']['retired']:
                await asyncio.sleep(.01)
        current=await read(client,sid,cwd)
        before=(native/'transcript.jsonl').read_bytes()
        import uuid
        target=str(uuid.uuid4())
        fork=await client.request('_amplifier/session',{'sessionId':sid,'cwd':str(cwd),'operation':'fork',
            'args':{'commandId':'fork','targetSessionId':target,'expectedHistoryRevision':current['revision']}})
        assert fork.get('result',{}).get('creationConfirmed') is True,fork
        copied=await read(client,target,cwd)
        assert copied['publicRows'] and all(r['copySource']['source']['sessionId']==sid for r in copied['publicRows'])
        assert (native/'transcript.jsonl').read_bytes()==before
        await client.request('session/close',{'sessionId':sid})
    finally:await client.close()
    cold=await Client.launch(config)
    try:
        await cold.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
        resumed=await cold.request('session/resume',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert resumed['result']['_meta']['amplifier.dev/native']['publicMessagePreservation']['supported'] is True
        assert any(r['publicMessage']==replacement for r in (await read(cold,sid,cwd))['publicRows'])
    finally:await cold.close()


@pytest.mark.asyncio
async def test_text_only_original_can_be_edited_by_older_caller(tmp_path):
    client,sid,cwd,_=await open_fixture(tmp_path)
    try:
        public=original('plain','Original literal');public['blocks']=public['blocks'][:1]
        assert (await prompt(client,sid,'plain','Original execution',public))['result']['stopReason']=='end_turn'
        source=await read(client,sid,cwd)
        before=await client.request('_amplifier/history/before',{'sessionId':sid,'cwd':str(cwd),'beforeTurnId':'plain','expectedRevision':source['revision'],'includeEditContext':True})
        assert before['result']['editContext']=={'version':1,'targetMessageId':'plain','publicMessage':public}
        result=await client.request('_amplifier/native',{'sessionId':sid,'operation':'history.revise','args':{
            'inputId':'plain-edit','operationId':'plain-edit','targetMessageId':'plain',
            'expectedHistoryRevision':source['revision'],'text':'Revised plain text'}})
        assert result['result']['stopReason']=='end_turn',result
        current=await read(client,sid,cwd)
        expected={**public,'inputId':'plain-edit','blocks':[{'type':'text','text':'Revised plain text'}]}
        assert any(row['publicMessage']==expected for row in current['publicRows'])
    finally:await client.close()


@pytest.mark.asyncio
async def test_packaged_steer_captures_same_admitted_row_without_new_turn(tmp_path):
    client,sid,cwd,_=await open_fixture(tmp_path,boundary=True,gates=True)
    try:
        active=asyncio.create_task(prompt(client,sid,'active','steering-apply',original('active','Literal active input')))
        async with asyncio.timeout(20):
            while not (tmp_path/'gates/tool.entered').exists():await asyncio.sleep(.01)
        correction=original('correction','  Literal correction\n')
        args={'inputId':'correction','activeInputId':'active','text':'Use corrected execution direction'}
        response=await client.request('_amplifier/native',{'sessionId':sid,'operation':'steer.submit','args':args,'_meta':{META:correction}})
        assert response.get('result',{}).get('accepted') is True,response
        (tmp_path/'gates/tool.release').touch()
        assert (await active)['result']['stopReason']=='end_turn'
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid));rows=[json.loads(x) for x in (native/'transcript.jsonl').read_text().splitlines()]
        matched=[r for r in rows if (r.get('metadata') or {}).get('amplifier_input',{}).get('id')=='correction']
        assert len(matched)==1 and matched[0]['metadata'][ENVELOPE]==correction
        assert 'Use corrected execution direction' in str(matched[0]['content'])
        assert sum(e.get('params',{}).get('event',{}).get('type')=='generation.started' for e in client.events)==1
        assert any(r['publicMessage']==correction for r in (await read(client,sid,cwd))['publicRows'])
    finally:await client.close()


@pytest.mark.asyncio
async def test_packaged_old_context_truthful_negotiation_and_no_canonical_duplication(tmp_path):
    client,sid,cwd,_=await open_fixture(tmp_path,legacy=True)
    try:
        result=await prompt(client,sid,'legacy','Legacy execution',original('legacy','Host retains this literal'))
        assert result['result']['stopReason']=='end_turn'
        native=next((tmp_path/'home/projects').glob('*/sessions/'+sid))
        rows=[json.loads(x) for x in (native/'transcript.jsonl').read_text().splitlines()]
        assert not any(ENVELOPE in r.get('metadata',{}) for r in rows)
        assert (await read(client,sid,cwd))['publicRows']==[]
        assert any(e.get('params',{}).get('event',{}).get('type')=='public.message.unavailable' for e in client.events)
    finally:await client.close()
