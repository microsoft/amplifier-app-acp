"""Rebind copied provider credentials inside a newly restored, inactive home.

Only archive-owned files can be selected. Original configuration bytes remain
beside the restore receipt; unresolved references never cause source reads.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath

from .maintenance_gate import checked
from .relocation import write


def is_configuration(name):
    p = PurePosixPath(name)
    if p.parts[0] == 'native-home':
        return (p.parts[1:] in [('settings.yaml',), ('settings.local.yaml',)]
                or len(p.parts) == 6 and p.parts[1] == 'projects'
                and p.parts[3] == 'sessions' and p.name == 'settings.yaml')
    if p.parts[0] == 'app-home':
        return (len(p.parts) == 4 and p.parts[1] == 'sessions'
                and p.name in {'configuration.json', 'effective-configuration.json'})
    return (p.parts[0] == 'workspace-settings' and len(p.parts) == 4
            and p.parts[2] == '.amplifier'
            and p.name in {'settings.yaml', 'settings.local.yaml'})


def rebind(target, roots, included):
    """Caller exclusively owns this new destination and verified archive inventory."""
    import yaml
    target = checked(Path(target).absolute())
    names = set(included)
    files = sorted(name for name in names if is_configuration(name))
    if len(files) > 10000:
        raise ValueError('Restored configuration inventory exceeds migration bound')
    mapping = sorted([(row['path'], target / row['id']) for row in roots
                      if row['kind'] == 'directory'], key=lambda row: len(row[0]), reverse=True)
    native = next(row['path'] for row in roots if row['id'] == 'native-home')
    changes, unresolved, prepared, total = [], [], [], 0

    def destination(value):
        # Tilde and environment references need explicit source-user context;
        # never expand them using the operator's account on the restore machine.
        if not isinstance(value, str) or not value.startswith('/') or '..' in Path(value).parts:
            return None
        for source, dest in mapping:
            if value.startswith(source.rstrip('/') + '/'):
                relative = value[len(source.rstrip('/')) + 1:]
                candidate = dest / relative
                name = str(candidate.relative_to(target))
                if name in names and checked(candidate).is_file():
                    return candidate
                return None
        return None

    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts:
            raise ValueError('Unsafe restored configuration inventory')
        path = checked(target / name)
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Restored configuration exceeds migration bound')
        raw = path.read_bytes(); total += len(raw)
        if total > 32 * 1024 * 1024:
            raise ValueError('Restored configuration set exceeds migration bound')
        data = json.loads(raw) if p.suffix == '.json' else yaml.safe_load(raw)
        if data is None:
            continue
        if not isinstance(data, dict):
            raise ValueError('Restored configuration must be a mapping')
        graph = data.get('config', data)
        providers = graph.get('providers', []) if isinstance(graph, dict) else []
        if not isinstance(providers, list):
            raise ValueError('Restored providers must be a list')
        changed = False
        for index, row in enumerate(providers):
            if not isinstance(row, dict) or not isinstance(row.get('config', {}), dict):
                raise ValueError('Restored provider configuration is malformed')
            config = row.get('config', {})
            refs = {key: config[key] for key in ('token_file_path', 'host_file_path') if key in config}
            if row.get('module') == 'provider-openai-chatgpt' and not config.get('token_file_path'):
                mode = config.get('auth_mode', 'chatgpt_codex')
                if mode in {'chatgpt_codex', 'oauth', 'chatgpt_plan'}:
                    refs['token_file_path'] = native + ('/chatgpt-plan/default.json' if mode == 'chatgpt_plan' else '/openai-chatgpt-oauth.json')
                    if mode == 'chatgpt_plan' and not config.get('host_file_path'):
                        refs['host_file_path'] = native + '/chatgpt-plan/host.json'
            for key, value in refs.items():
                dest = destination(value)
                if dest is None:
                    unresolved.append({'file': name, 'providerIndex': index, 'field': key,
                                       'reason': 'Credential reference requires explicit source-context or external-file review'})
                    continue
                config[key] = str(dest); row['config'] = config; changed = True
                changes.append({'file': name, 'providerIndex': index, 'field': key,
                                'destination': str(dest.relative_to(target))})
        if changed:
            encoded = (json.dumps(data, ensure_ascii=False, indent=2) + '\n' if p.suffix == '.json'
                       else yaml.safe_dump(data, sort_keys=False, allow_unicode=True)).encode()
            prepared.append((name, path, raw, encoded))

    originals = target / 'configuration-originals'
    if prepared:
        originals.mkdir(mode=0o700)
    for name, path, raw, encoded in prepared:
        original = originals / name
        original.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with original.open('xb') as stream:
            original.chmod(0o600); stream.write(raw)
        path.write_bytes(encoded); path.chmod(0o600)
    result = {'version': 1, 'examinedFiles': len(files), 'changedFiles': len(prepared),
              'bindings': changes, 'unresolved': unresolved, 'sourceFilesRead': False,
              'startsWorker': False, 'originalConfigurations': [
                  {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                   'restoredSha256': hashlib.sha256(encoded).hexdigest()}
                  for name, _, raw, encoded in prepared]}
    write(target / 'credential-relocation.json', result)
    return result
