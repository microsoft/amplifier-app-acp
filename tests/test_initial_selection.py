"""Actual ACP/Core/Foundation pre-start selection, with inert local providers."""
import json
from pathlib import Path
import pytest
from client import Client
from test_standard_provider_config import fixture, NATIVE


def setup(tmp_path, *, supported=True):
    config,cwd,audit,discovery,catalogs=fixture(tmp_path)
    values=json.loads(config.read_text())
    values['runtimeImmutable']=True
    if supported:
        root=tmp_path/'managed';root.mkdir()
        values['managedSessionRoots']=[str(root)]
    config.write_text(json.dumps(values))
    rows=json.loads(catalogs['work'].read_text())
    rows[1]['defaults']['supported_reasoning_efforts']=['low','high']
    catalogs['work'].write_text(json.dumps(rows))
    return config,cwd,audit,discovery,catalogs


async def initialize(client, negotiated=True):
    return (await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{
        '_meta':{NATIVE:{'version':1}}} if negotiated else {}}))['result']


def args(cwd, selection):
    return {'cwd':str(cwd),'mcpServers':[],'_meta':{NATIVE:{'selection':selection}}}


@pytest.mark.asyncio
async def test_selected_before_first_prompt_and_persisted_on_resume(tmp_path):
    config,cwd,audit,discovery,_=setup(tmp_path);client=await Client.launch(config)
    selected={'instance':'work','model':'work-new','effort':'low'}
    try:
        caps=await initialize(client)
        assert caps['agentCapabilities']['_meta'][NATIVE]['initialSelection']=={'version':1,'typedSelection':True,'passive':True}
        result=await client.request('session/new',args(cwd,selected))
        assert 'result' in result,result
        reply=result['result'];sid=reply['sessionId']
        assert reply['_meta'][NATIVE]['initialSelection']=={'version':1,'selection':selected}
        control=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.providers','args':{}})
        assert control['result']['selection']==selected
        assert json.loads((tmp_path/'app/sessions'/sid/'control-state.json').read_text())['selection']==selected
        assert not audit.exists(), 'Validation and starting must never complete a model request'
        assert discovery.read_text().splitlines()==['work']
        assert next(r for r in reply['configOptions'] if r['category']=='model')['_meta'][NATIVE]['effective']==selected
        assert next(r for r in reply['configOptions'] if r['category']=='thought_level')['currentValue']!='__default__'
        prompt=await client.request('session/prompt',{'sessionId':sid,'prompt':[{'type':'text','text':'one explicit offline request'}]})
        assert prompt['result']['stopReason']=='end_turn',prompt
        assert json.loads(audit.read_text().splitlines()[0])==selected
    finally:await client.close()
    before=audit.read_bytes();client=await Client.launch(config)
    try:
        await initialize(client)
        rejected=await client.request('session/load',{'sessionId':sid,**args(cwd,selected)})
        assert rejected['error']['data']['executed'] is False
        loaded=await client.request('session/load',{'sessionId':sid,'cwd':str(cwd),'mcpServers':[]})
        assert 'result' in loaded,loaded
        current=await client.request('_amplifier/native',{'sessionId':sid,'operation':'configuration.providers','args':{}})
        assert current['result']['selection']==selected and audit.read_bytes()==before
    finally:await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('selected',[
    {'instance':'absent','model':'work-new'},
    {'instance':'work','model':'absent'},
    {'instance':'work','model':'work-new','effort':'unadvertised'},
    {'instance':'work','model':'work-default','effort':'low'},
    {'instance':'work','model':'work-new','effort':None},
    {'instance':'work','model':'work-new','extra':True},
    {'model':'work-new'},[],False,
])
async def test_invalid_choice_has_no_session_or_worker_effects(tmp_path,selected):
    config,cwd,audit,_,_=setup(tmp_path)
    values=json.loads(config.read_text());values['workerCommand']=['/must-not-start-a-worker'];config.write_text(json.dumps(values))
    client=await Client.launch(config)
    try:
        await initialize(client)
        response=await client.request('session/new',args(cwd,selected))
        assert response['error']['data']['executed'] is False,response
        assert response['error']['data']['runtimeStarted'] is False
        assert not list((tmp_path/'app/sessions').glob('*'))
        assert not list((tmp_path/'home/projects').glob('*/sessions/*'))
        assert not audit.exists()
    finally:await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('supported,negotiated',[(False,True),(True,False)])
async def test_nonempty_requires_capability_but_empty_remains_legacy(tmp_path,supported,negotiated):
    config,cwd,audit,discovery,_=setup(tmp_path,supported=supported);client=await Client.launch(config)
    try:
        init=await initialize(client,negotiated)
        assert ('initialSelection' in init['agentCapabilities']['_meta'][NATIVE])==supported
        rejected=await client.request('session/new',args(cwd,{'instance':'work','model':'work-new'}))
        assert rejected['error']['data']['executed'] is False
        assert not list((tmp_path/'app/sessions').glob('*'))
        accepted=await client.request('session/new',args(cwd,{}))
        assert 'result' in accepted,accepted
        assert 'initialSelection' not in accepted['result']['_meta'][NATIVE]
        assert not audit.exists() and not discovery.exists()
    finally:await client.close()


def test_model_effort_overrides_provider_defaults_and_empty_is_authoritative():
    from amplifier_acp.initial_selection import validate_catalog
    schema={'fields':[{'id':'reasoning_effort','field_type':'choice','choices':['low']}]}
    selection={'instance':'work','model':'m','effort':'low'}
    catalog={'modelsSupported':True,'models':[{'id':'m','defaults':{'supported_reasoning_efforts':[]}}]}
    assert validate_catalog(selection,catalog,schema)=={'selectionRefused':True}
    catalog['models'][0]['defaults']={}
    assert validate_catalog(selection,catalog,schema)=={'validatedSelection':selection}


@pytest.mark.asyncio
async def test_saved_app_bundle_is_validated_and_effort_omission_stays_omitted(tmp_path):
    import yaml
    config,cwd,audit,_,_=setup(tmp_path)
    values=json.loads(config.read_text());old=Path(values['bundle'])
    saved=tmp_path/'app-selected.yaml';saved.write_bytes(old.read_bytes())
    old_doc=yaml.safe_load(old.read_text());old_doc['providers']=old_doc['providers'][:1]
    old.write_text(yaml.safe_dump(old_doc))
    app=tmp_path/'app';app.mkdir()
    (app/'admin-settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':str(saved)}}))
    client=await Client.launch(config)
    try:
        await initialize(client)
        selected={'instance':'work','model':'work-new'}
        response=await client.request('session/new',args(cwd,selected))
        assert 'result' in response,response
        assert response['result']['_meta'][NATIVE]['initialSelection']=={'version':1,'selection':selected}
        assert not audit.exists()
    finally:await client.close()


@pytest.mark.parametrize('configured',['~/native-app','native-app'])
def test_worker_bundle_selection_normalizes_app_home_and_preserves_clone_precedence(tmp_path,monkeypatch,configured):
    from amplifier_acp.worker import selected_bundle
    monkeypatch.setenv('HOME',str(tmp_path));monkeypatch.chdir(tmp_path)
    app=tmp_path/'native-app';app.mkdir()
    (app/'admin-settings.yaml').write_text('bundle:\n  active: saved-root\n')
    config={'appHome':configured,'bundle':'configured-root'}
    assert selected_bundle(config)=='saved-root'
    assert selected_bundle({**config,'cloneBundle':'exact-clone'})=='exact-clone'


@pytest.mark.asyncio
async def test_preparation_timeout_is_a_bounded_prestart_refusal(monkeypatch):
    from amplifier_acp import initial_selection
    async def unavailable(*args,**kwargs):raise TimeoutError('private provider detail')
    monkeypatch.setattr('amplifier_acp.native.draft_defaults.resolve_defaults',unavailable)
    with pytest.raises(initial_selection.Refused) as error:
        await initial_selection.validate({},'/unused',{'instance':'work','model':'m'})
    assert error.value.data=={'executed':False,'runtimeStarted':False,'reason':'initial-selection-refused'}
    assert 'private' not in str(error.value)
