import json
from pathlib import Path
import pytest
from amplifier_acp.managed_session import options, bundle, dispatch
from amplifier_acp.server import Server
from native_setup import configuration


def test_global_catalog_and_explicit_allowlists_keep_their_authority(tmp_path):
    file, workspace=configuration(tmp_path)
    config=json.loads(file.read_text())
    config['managedBundleCatalog']=True
    # A workspace-specific alias must not enter a workspace-free chat.
    (workspace/'.amplifier').mkdir()
    (workspace/'.amplifier/settings.yaml').write_text('bundle:\n  added:\n    private-project: /project-only\n')
    settings=Path(config['home'])/'settings.yaml'
    settings.write_text('''bundle:
  app: []
  added:
    mine: /custom-bundle.yaml
web_bundles:
  entries:
    - name: anchors
      uri: ignored-disabled-source
      role: standalone
      enabled: false
''')
    rows=options(config)['bundles']
    assert {r['id'] for r in rows}=={'mine','anchors-amp-dev','work','work-amp-dev'}
    selected=next(row for row in rows if row['id']=='work-amp-dev')
    assert selected['label']=='Work · Amplifier development'
    request={'bundle':selected['id'],'bundleRevision':selected['revision']}
    assert bundle(config,request).endswith('bundles/work-amp-dev.md')
    original=settings.read_bytes()
    assert options(config)['runtimeStarted'] is False
    assert settings.read_bytes()==original
    assert not (Path(config['home'])/'projects').exists()
    assert not (Path(config['appHome'])/'sessions').exists()
    assert options(config|{'forkBundles':{}})['bundles']==[]
    assert {row['id'] for row in options(config|{'forkBundles':{'only':'/approved'}})['bundles']}=={'only'}
    # A retained private selection cannot adopt a new underlying registration.
    settings.write_text('bundle:\n  added:\n    work-amp-dev: /different-source\n')
    with pytest.raises(ValueError,match='revision'):bundle(config,request)
    assert not (Path(config['home'])/'projects').exists()


@pytest.mark.asyncio
async def test_managed_metadata_probes_share_the_explicit_worker_registry(tmp_path,monkeypatch):
    file,_=configuration(tmp_path);config=json.loads(file.read_text())
    config['registryHome']=str(tmp_path/'owned-prepared-sources')
    seen=[]
    async def probe(home,workspace,selected,**kwargs):
        seen.append(kwargs)
        if 'model_instance' in kwargs:
            return {'instance':'fixture','module':'provider-fixture','modelsSupported':True,'models':[{'id':'real-advertised'}]}
        return {'providers':[],'globalOnly':True,'effective':{}}
    monkeypatch.setattr('amplifier_acp.native.draft_defaults.resolve_defaults',probe)
    server=Server(config,None)
    await dispatch(server,{'operation':'defaults'})
    result=await dispatch(server,{'operation':'models','instance':'fixture'})
    assert result['models']==[{'id':'real-advertised'}]
    assert len(seen)==2 and all(row['registry_home']==config['registryHome'] and row['global_only'] for row in seen)
    assert not server.sessions
