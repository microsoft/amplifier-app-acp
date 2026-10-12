import json
from pathlib import Path

import pytest
import yaml

from amplifier_acp.restored_credentials import rebind


def test_rebinds_all_captured_scopes_preserves_originals_and_never_reads_source(tmp_path):
    target=tmp_path/'inactive';target.mkdir()
    native='/unavailable/original/.amplifier';app='/unavailable/original/.amplifier-unified'
    roots=[{'id':'native-home','kind':'directory','path':native}, {'id':'app-home','kind':'directory','path':app}]
    token='native-home/openai-chatgpt-oauth.json'
    plan='native-home/chatgpt-plan/default.json';host='native-home/chatgpt-plan/host.json'
    credentials={token:b'{"refresh_token":"fixture"}',plan:b'{"refresh_token":"plan-fixture"}',host:b'{"host_id":"unchanged"}'}
    originals={}
    names=['native-home/settings.yaml','native-home/projects/work/sessions/saved/settings.yaml',
           'workspace-settings/work/.amplifier/settings.yaml',
           'workspace-settings/work/.amplifier/settings.local.yaml',
           'app-home/sessions/saved/configuration.json','app-home/sessions/saved/effective-configuration.json']
    providers=[{'id':'explicit','module':'provider-openai-chatgpt','config':{'token_file_path':native+'/openai-chatgpt-oauth.json','reasoning_effort':'high'}},
               {'id':'default','module':'provider-openai-chatgpt'},
               {'id':'plan','module':'provider-openai-chatgpt','config':{'auth_mode':'chatgpt_plan'}},
               {'module':'provider-openai','config':{'api_key':'${KEEP_KEY}'}},
               {'module':'provider-custom','config':{'token_file_path':'/external/uncaptured.json'}},
               {'module':'provider-tilde','config':{'token_file_path':'~/.other/account.json'}},
               {'module':'provider-escape','config':{'token_file_path':native+'/../outside.json'}}]
    for name in names:
        graph={'providers':providers,'unknown_field':{'preserve':'value'}}
        data=graph if name.endswith('.json') else {'config':graph,'voice':{'voice':'cedar'}}
        originals[name]=(json.dumps(data) if name.endswith('.json') else yaml.safe_dump(data)).encode()
    for name,raw in {**originals,**credentials}.items():
        p=target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    report=rebind(target,roots,list(originals)+list(credentials))
    assert report['changedFiles']==6 and len(report['bindings'])==24
    assert len(report['unresolved'])==18 and report['sourceFilesRead'] is False
    for name,raw in originals.items():
        assert (target/'configuration-originals'/name).read_bytes()==raw
        data=json.loads((target/name).read_bytes()) if name.endswith('.json') else yaml.safe_load((target/name).read_bytes())
        graph=data.get('config',data);p=graph['providers']
        assert graph['unknown_field']=={'preserve':'value'}
        assert p[0]['config']=={'token_file_path':str(target/token),'reasoning_effort':'high'}
        assert p[1]['config']['token_file_path']==str(target/token)
        assert p[2]['config']=={'auth_mode':'chatgpt_plan','token_file_path':str(target/plan),'host_file_path':str(target/host)}
        assert p[3:]==providers[3:]
    assert all((target/name).read_bytes()==raw for name,raw in credentials.items())
    assert json.loads((target/'credential-relocation.json').read_text())==report


def test_missing_or_unselected_credentials_do_not_become_rebound_authority(tmp_path):
    home=tmp_path/'native-home';home.mkdir()
    settings=home/'settings.yaml'
    original=b'config:\n  providers:\n  - module: provider-openai-chatgpt\n'
    settings.write_bytes(original)
    # Presence alone is insufficient: the file must belong to the sealed archive.
    (home/'openai-chatgpt-oauth.json').write_text('{"refresh_token":"unselected"}')
    report=rebind(tmp_path,[{'id':'native-home','kind':'directory','path':'/original'}],['native-home/settings.yaml'])
    assert report['changedFiles']==0 and len(report['unresolved'])==1
    assert settings.read_bytes()==original and not (tmp_path/'configuration-originals').exists()
