"""Reaction feedback reaches requests without scheduling an input."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_acp.history import message_id, page
from amplifier_acp.message_metadata import MessageMetadata
from amplifier_acp.native.passive_feedback import PassiveFeedback

@pytest.mark.asyncio
async def test_reaction_boundary_restart_and_checkpoint_retry(tmp_path, monkeypatch):
    home=tmp_path/'home'; directory=tmp_path/'native'; directory.mkdir()
    monkeypatch.setenv('AMPLIFIER_APP_HOME', str(home))
    monkeypatch.setattr('amplifier_acp.native.host.config.app_home', lambda: home)
    original={'role':'assistant','content':'The **answer**'}
    transcript=directory/'transcript.jsonl'; transcript.write_text(json.dumps(original)+'\n')
    store=MessageMetadata(home,'feedback-session',actor_id='user')
    ref={'sessionId':'feedback-session','messageId':message_id(original,0),'position':0,'historyEpoch':None}
    saved=store.reaction(directory,ref,emoji='❤️',present=True,command_id='heart')
    assert saved['sent'] is False
    assert store.read([ref['messageId']])['feedbackEvents']==[saved['feedbackEvent']]
    rows=[original]
    async def add(row):rows.append(row)
    context=SimpleNamespace(get_messages=AsyncMock(side_effect=lambda:list(rows)),add_message=AsyncMock(side_effect=add))
    async def checkpoint():transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    controls=SimpleNamespace(session=SimpleNamespace(session_id='feedback-session'),coordinator=SimpleNamespace(get=lambda _:context),checkpoint=AsyncMock(side_effect=[OSError('disk'),None]))
    delivery=PassiveFeedback(controls,store.path.parent)
    with pytest.raises(OSError):await delivery.boundary('provider:request',{})
    assert len(rows)==2
    controls.checkpoint.side_effect=checkpoint
    await delivery.boundary('provider:request',{})
    await PassiveFeedback(controls,store.path.parent).boundary('provider:request',{})
    assert len(rows)==2 and context.add_message.await_count==1
    assert rows[1]['role']=='user' and 'The **answer**' in rows[1]['content']
    assert rows[1]['metadata']['passive'] is True
    groups=page(directory)['turnGroups']
    assert groups[-1]['recordedOnly'] is True
    assert groups[-1]['updates'][0]['_meta']['amplifier.dev/history']['feedbackEvent']['id']=='reaction:heart'
    store.reaction(directory,ref,emoji='❤️',present=False,command_id='remove-heart')
    await delivery.boundary('provider:request',{})
    assert len(rows)==3 and 'removed ❤️' in rows[-1]['content']
    store.reaction(directory,ref,emoji='❤️',present=False,command_id='already-removed')
    await delivery.boundary('provider:request',{})
    assert len(rows)==3

@pytest.mark.asyncio
@pytest.mark.parametrize("boundary",[False,True])
async def test_feedback_visible_to_real_provider_without_extra_turn(tmp_path,boundary):
    from test_public_message_runtime import open_fixture
    from pathlib import Path
    client,sid,cwd,config=await open_fixture(tmp_path,boundary=boundary)
    try:
        async def prompt(text,identity):
            return await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':text}],'_meta':{'amplifier.dev/command':{'commandId':identity}}})
        assert 'result' in await prompt('Give a short answer','first')
        settings=json.loads(config.read_text())
        directory=next((Path(settings['home'])/'projects').glob('*/sessions/'+sid))
        located=[]; offset=0
        for line in (directory/'transcript.jsonl').read_bytes().splitlines(keepends=True):
            located.append((offset,json.loads(line))); offset+=len(line)
        position,row=next((position,row) for position,row in reversed(located) if row.get('role')=='assistant')
        store=MessageMetadata(Path(settings['appHome']),sid,actor_id='user')
        store.reaction(directory,{'sessionId':sid,'messageId':message_id(row,position),'position':position,'historyEpoch':None},emoji='❤️',present=True,command_id='feedback-real')
        before=len([e for e in client.events if e.get('params',{}).get('event',{}).get('type')=='generation.started'])
        assert before==1
        model_events=[e.get('params',{}).get('event',{}) for e in client.events]
        assert any(e.get('type')=='execution.event' and e.get('event',{}).get('kind')=='llm' for e in model_events)
        assert 'result' in await prompt('Continue','second')
        observed=(tmp_path/'provider-prompts.jsonl').read_text()
        requests=[json.loads(line) for line in observed.splitlines()]
        feedback=[row for row in requests[-1] if row.get('role')=='user' and 'added ❤️ reaction' in str(row.get('content',''))]
        assert len(feedback)==1 and 'Referenced message' in feedback[0]['content']  # Visible content, not metadata alone.
        assert len([e for e in client.events if e.get('params',{}).get('event',{}).get('type')=='generation.started'])==2
    finally:await client.close()
