import copy
import json
from pathlib import Path
import pytest
from amplifier_acp.configuration_options import project, model_rows, effort_choices, token, DEFAULT


def state():
    return {'pinned':True,'selection':{'instance':'personal','model':'alpha','effort':'deep'},'providers':[
        {'id':'personal','info':{'display_name':'Same brand','defaults':{'model':'alpha'}},'modelCatalog':{'state':'available','fresh':True,'models':[
            {'id':'alpha','display_name':'Alpha','context_window':1000,'capabilities':['thinking'],'defaults':{'supported_reasoning_efforts':['brief','deep'],'api_key':'PRIVATE'}},
            {'id':'beta','defaults':{'supported_reasoning_efforts':['light']}}]}},
        {'id':'work','info':{'display_name':'Same brand','defaults':{'model':'alpha'}}}]}


def test_standard_groups_preserve_instance_metadata_and_stable_values():
    data=state(); options,mapping,efforts=project(data)
    model=options[0]
    assert model['category']=='model' and options[1]['category']=='thought_level'
    assert [g['group'] for g in model['options']]==['','personal','work']
    assert mapping[token('model','personal','alpha')]!=mapping[token('model','work','alpha')]
    assert model['currentValue']==token('model','personal','alpha')
    assert options[1]['currentValue']==token('effort','personal','alpha','deep')
    assert set(e['effort'] for e in efforts.values())=={'brief','deep'}
    assert 'PRIVATE' not in json.dumps(options)
    data['providers'].reverse();data['providers'][1]['modelCatalog']['models'].reverse()
    assert project(data)[1]==mapping and project(data)[2]==efforts
    import jsonschema
    schema=json.loads((Path(__file__).parent/'schema/acp-v1.json').read_text())
    for option in options:jsonschema.validate(option,{'$ref':'#/$defs/SessionConfigOption','$defs':schema['$defs']})


def test_failed_or_replaced_catalog_keeps_effective_pin_without_invented_choices():
    data=state();data['providers'][0]['modelCatalog']={'state':'unavailable','models':[]}
    model=project(data)[0][0];row=model['options'][1]['options'][0]
    assert row['_meta']['amplifier.dev/native']['source']=='effective'
    assert model['currentValue']==row['value']
    data['providers'][0]['modelCatalog']={'state':'available','models':[{'id':'replacement'}]}
    assert {row['name'] for row in project(data)[0][0]['options'][1]['options']}=={'replacement','alpha'}


@pytest.mark.parametrize('value',[None,{},[{'id':'same'},{'id':'same'}],[{'id':'x'*513}],[{'id':'bad','defaults':{'supported_reasoning_efforts':[1,'high']}}],[{'id':str(i)} for i in range(513)]])
def test_invalid_catalog_is_not_silently_partial(value):
    with pytest.raises(ValueError):model_rows(value)


@pytest.mark.parametrize('condition,model,expected',[
    ({'default_model':'not_contains:haiku-3'},'haiku-3',[]),
    ({'model':'startswith:gpt'},'gpt-fixture',['low','high']),
    ({'model':'matches:^gpt-[0-9]'},'gpt-6',['low','high']),
    ({'model':'matches:['},'gpt-6',[]),
    ({'credential_mode':'private'},'gpt-6',[]),
    ({},'custom',['low','high']),
])
def test_public_provider_field_conditions(condition,model,expected):
    provider={'configSchema':{'fields':[{'id':'reasoning_effort','field_type':'choice','choices':['low','high'],'show_when':condition}]}}
    assert effort_choices(provider,{'id':model})[0]==expected
    assert effort_choices(provider,{'id':model,'defaults':{'supported_reasoning_efforts':['specific']}})[0]==['specific']
    assert effort_choices(provider,{'id':model,'defaults':{'supported_reasoning_efforts':[]}})[0]==[]


def test_no_invented_effort_and_preserved_existing_nonadvertised_effort():
    data=state();data['providers'][0]['modelCatalog']['models'][0]['defaults']={}
    assert len(project(data)[0])==1
    data=state();data['selection']['effort']='old-effort'
    options,_,efforts=project(data)
    assert options[1]['currentValue']==token('effort','personal','alpha','old-effort')
    assert efforts[options[1]['currentValue']]['effort']=='old-effort'
