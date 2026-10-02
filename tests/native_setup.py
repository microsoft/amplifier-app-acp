import importlib.util,json
from pathlib import Path

def configuration(tmp_path,**extra):
    home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir()
    provider=Path(__file__).parent/'fixtures/provider'
    context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
    bundle=tmp_path/'fixture.yaml'
    bundle.write_text('''bundle:\n  name: offline-native\n  version: 1.0.0+amplifier-unified.snapshot.1\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '''+str(context)+'''\nproviders:\n  - module: provider-fixture\n    source: '''+str(provider.resolve())+'''\n    config: {}\n''')
    (home/'settings.yaml').write_text('bundle:\n  app: []\n')
    config=tmp_path/'config.json';config.write_text(json.dumps({'home':str(home),'appHome':str(tmp_path/'app'),'bundle':str(bundle),'startupTimeout':90,**extra}))
    return config,workspace
