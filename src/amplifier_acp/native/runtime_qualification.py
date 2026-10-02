"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import importlib.metadata


import hashlib


import json


from pathlib import Path


import subprocess


import tomllib


from urllib.parse import unquote, urlsplit, urlunsplit


from . import runtime_environment as environments


def installed_graph(project):
    """Read complete PEP 610 provenance, including Foundation's editable caches."""
    paths = list((Path(project) / '.venv').glob('lib/python*/site-packages'))
    paths += [Path(project) / '.venv/Lib/site-packages']
    rows = []
    roots = {}  # One fresh evidence snapshot per real checkout, per call.
    for dist in importlib.metadata.distributions(path=[str(path) for path in paths if path.is_dir()]):
        row = {'name': environments.package_name(dist.metadata['Name']), 'version': dist.version}
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        if direct:
            row['directUrl'] = direct
            parsed = urlsplit(direct.get('url', ''))
            if parsed.scheme == 'file' and direct.get('dir_info') is not None:
                path = Path(unquote(parsed.path)).resolve()
                row['path'] = str(path)
                for parent in (path, *path.parents):
                    meta = parent / '.amplifier_cache_meta.json'
                    if meta.is_file() and (parent / '.git').exists():
                        if parent not in roots:
                            data = json.loads(meta.read_text())
                            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=parent, text=True).strip()
                            dirty = subprocess.check_output(['git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no'], cwd=parent, text=True).strip()
                            tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=parent).split(b'\0')
                            tree = hashlib.sha256()
                            for raw in sorted(item for item in tracked if item):
                                file = parent / raw.decode()
                                tree.update(raw + b'\0')
                                tree.update(file.read_bytes() if file.is_file() and not file.is_symlink() else b'<non-regular>')
                            roots[parent] = {'url': data['git_url'], 'ref': data.get('ref') or 'HEAD',
                                             'revision': revision, 'dirty': bool(dirty), 'trackedContentSha256': tree.hexdigest()}
                        row['cacheSource'] = {**roots[parent], 'subdirectory': str(path.relative_to(parent))}
                        break
        rows.append(row)
    names = [row['name'] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('The worker contains ambiguous duplicate installed distributions.')
    return sorted(rows, key=lambda row: row['name'])


def override_content(project):
    """Hold the already checked resolver graph while new module deps refresh."""
    data = tomllib.loads((Path(project) / 'uv.lock').read_text())
    lines = []
    for row in data.get('package', []):
        source = row.get('source') or {}
        if source.get('git'):
            parsed = urlsplit(source['git'])
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            value = row['name'] + ' @ git+' + urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')) + '@' + parsed.fragment
            if query.get('subdirectory'):
                value += '#subdirectory=' + query['subdirectory'][0]
            lines.append(value)
        elif source.get('registry'):
            lines.append(row['name'] + '==' + row['version'])
        elif source.get('editable') or source.get('directory'):
            path = (Path(project) / (source.get('editable') or source['directory'])).resolve()
            # A direct file requirement loses editable mode during a later
            # uv pip install. Preserve both the path and PEP 610 editability.
            lines.append(('-e ' if source.get('editable') else row['name'] + ' @ ') + path.as_uri())
    return '\n'.join(sorted(lines)) + '\n'


def verify_install_overrides(project, target):
    """Verify a recorded policy without re-exporting or rewriting its receipt."""
    target = Path(target)
    if not target.is_file():
        raise ValueError('The recorded worker installation policy is missing; its generation was preserved.')
    policy_file = target.with_name('runtime-install-policy.json')
    if policy_file.exists():
        policy = json.loads(policy_file.read_text())
        valid = (policy.get('version') == 2
                 and policy.get('lockSha256') == hashlib.sha256((Path(project) / 'uv.lock').read_bytes()).hexdigest()
                 and policy.get('overridesSha256') == hashlib.sha256(target.read_bytes()).hexdigest())
    else:
        # 0.20 receipts used the original universal-lock formatter. Do not
        # silently replace those exact policies with a new interpretation.
        valid = target.read_text() == override_content(project)
    if not valid:
        raise ValueError('The recorded worker installation policy changed; its generation was preserved.')
    return target


def _local_directory_identity(row):
    """Compare PEP 610's absent/false editable default without changing evidence.

    https://packaging.python.org/en/latest/specifications/direct-url-data-structure/#local-directories
    Only the literal boolean False is optional; malformed values stay distinct.
    """
    direct = row.get('directUrl')
    if isinstance(direct, dict) and isinstance(direct.get('url'), str):
        info = direct.get('dir_info')
        if (urlsplit(direct['url']).scheme == 'file' and isinstance(info, dict)
                and info.get('editable') is False):
            return {**row, 'directUrl': {**direct, 'dir_info': {
                key: value for key, value in info.items() if key != 'editable'}}}
    return row


def verify_recorded(project, receipt, *, allow_additions=False):
    evidence = Path(receipt) / 'runtime-installed.json'
    if not evidence.exists():
        return
    expected = json.loads(evidence.read_text())
    actual = installed_graph(project)
    if allow_additions:
        names = {row['name'] for row in expected}
        actual = [row for row in actual if row['name'] in names]
    if ([_local_directory_identity(row) for row in actual]
            != [_local_directory_identity(row) for row in expected]):
        raise ValueError('The worker graph changed after qualification; its recorded generation was preserved.')


def active_install_overrides(home, current_override=None):
    """Use a recorded generation's ordinary installer policy, never refresh.

    An explicit user override remains authoritative. Legacy generations have no
    new policy. Validate an existing graph before mounting another conversation.
    """
    from .updates import selected_release
    generation = selected_release(home)
    receipt = environments.receipt_directory(home, generation)
    if not (receipt / 'runtime-installed.json').exists():
        return None
    target = receipt / 'runtime-install-overrides.txt'
    compatibility = Path(__file__).parent / 'runtime_deps/compatibility.txt'
    if current_override and current_override not in {str(target), str(compatibility)}:
        return None
    project = environments.project_path(home, generation)
    if (project / 'uv.lock').read_bytes() != (receipt / 'runtime.lock').read_bytes():
        raise ValueError('The recorded worker installation policy changed; its generation was preserved.')
    verify_install_overrides(project, target)
    verify_recorded(project, receipt, allow_additions=True)
    return target
