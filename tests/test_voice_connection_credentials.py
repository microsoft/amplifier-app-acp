import os
import pytest
from amplifier_acp.native.voice_admin import configuration, perform

KEY='synthetic-owned-openai-key'
class Store:
    def __init__(self,path,settings):self.shared_home=path;self.settings=settings
    def read(self,*args):return self.settings
    def update(self,workspace,scope,mutator):mutator(self.settings)
class Setup:
    def __init__(self,store):self.store=store
    def _keys(self,values):os.environ.update(values)
def connection(reference='${AMPLIFIER_OPENAI_API_KEY}',**config):
    return {'id':'openai','module':'provider-openai','config':{'api_key':reference,**config}}
@pytest.fixture
def store(tmp_path,monkeypatch):
    for key in ['OPENAI_API_KEY','AMPLIFIER_VOICE_API_KEY','AMPLIFIER_OPENAI_API_KEY','OTHER_OPENAI_KEY']:monkeypatch.delenv(key,raising=False)
    return Store(tmp_path,{'config':{'providers':[connection()]}})
def view(store):return configuration(store,'.',credential_access=True)
def credential(store):return perform(Setup(store),'.','voice.credential',{})
def test_saved_connection_reference_matches_passive_and_explicit_selection(store,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY)
    assert view(store)['available'] is True
    assert view(store)['environmentAvailable'] is True
    assert view(store)['environmentVariable']=='AMPLIFIER_OPENAI_API_KEY'
    assert credential(store)['apiKey']==KEY
    assert KEY not in str(view(store))
    saved=perform(Setup(store),'.','voice.configure',{'source':'environment'})
    assert saved['available'] and saved['environmentVariable']=='AMPLIFIER_OPENAI_API_KEY'
def test_canonical_environment_precedes_saved_connection(store,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY);monkeypatch.setenv('OPENAI_API_KEY','synthetic-canonical')
    assert credential(store)['apiKey']=='synthetic-canonical'
    assert view(store)['environmentVariable']=='OPENAI_API_KEY'
def test_private_selection_never_falls_back_to_openai_connection(store,monkeypatch):
    store.settings['voice']={'credential_source':'private'};monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY)
    assert not view(store)['available'] and view(store)['environmentAvailable']
    assert credential(store)['apiKey'] is None
    monkeypatch.setenv('AMPLIFIER_VOICE_API_KEY','synthetic-private')
    assert view(store)['available'] and credential(store)['apiKey']=='synthetic-private'
@pytest.mark.parametrize('change',[
    lambda s:s['config']['providers'][0].update(enabled=False),
    lambda s:s.update(configurator={'disabled':{'providers':['openai']}}),
    lambda s:s.update(overrides={'openai':{'enabled':False}}),
    lambda s:s['config']['providers'][0]['config'].update(base_url='https://private.example/v1'),
    lambda s:s['config']['providers'][0].update(module='provider-openai-chatgpt'),
    lambda s:s['config']['providers'][0].update(module='provider-azure-openai'),
    lambda s:s['config']['providers'][0]['config'].update(api_key='literal-value-not-a-reference'),
    lambda s:s['config']['providers'][0]['config'].update(api_key='${AMPLIFIER_OPENAI_API_KEY}suffix'),
])
def test_ineligible_connections_cannot_supply_voice(store,monkeypatch,change):
    monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY);change(store.settings)
    assert not view(store)['available'] and credential(store)['apiKey'] is None
def test_ambiguous_references_are_not_selected_by_presence_or_order(store,monkeypatch):
    store.settings['config']['providers'].append({**connection('${OTHER_OPENAI_KEY}'),'id':'other'})
    monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY)
    assert not view(store)['available'] and credential(store)['apiKey'] is None
def test_duplicate_reference_is_one_binding_and_unreferenced_env_is_not_scanned(store,monkeypatch):
    monkeypatch.setenv('OTHER_OPENAI_KEY',KEY)
    assert not view(store)['available']
    store.settings['config']['providers'].append({**connection(),'id':'same-binding'})
    monkeypatch.setenv('AMPLIFIER_OPENAI_API_KEY',KEY)
    assert view(store)['available'] and credential(store)['apiKey']==KEY
def test_disabled_grant_does_not_read_key_file_or_resolve_reference(store,monkeypatch):
    import amplifier_acp.native.voice_admin as module
    monkeypatch.setattr(module,'_load_keys',lambda *args:pytest.fail('disabled metadata accessed keys'))
    result=configuration(store,'.',credential_access=False)
    assert not result['available'] and result['environmentAvailable'] is None
    assert result.get('environmentVariable') is None
def test_saved_keyfile_edits_and_connection_removal_refresh_presence(store):
    keyfile=store.shared_home/'keys.env';keyfile.write_text('AMPLIFIER_OPENAI_API_KEY=synthetic-first\n')
    assert view(store)['available'] and credential(store)['apiKey']=='synthetic-first'
    keyfile.write_text('AMPLIFIER_OPENAI_API_KEY=synthetic-edited\n')
    assert view(store)['available'] and credential(store)['apiKey']=='synthetic-edited'
    store.settings['config']['providers']=[]
    assert not view(store)['available'] and credential(store)['apiKey'] is None
