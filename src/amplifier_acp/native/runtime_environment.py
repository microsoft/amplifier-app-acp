"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import hashlib


import json


import re


from pathlib import Path


import tomllib


from urllib.parse import parse_qs, urlsplit, urlunsplit


def manifest_path():
    return Path(__file__).with_name('runtime_deps') / 'pyproject.toml'


def receipt_directory(home, generation=None):
    root = Path(home) / 'updates'
    return root / 'releases' / generation if generation else root / 'baseline-runtime'


def manifest_content(home, generation=None):
    recorded = receipt_directory(home, generation) / 'runtime.toml'
    if recorded.exists():
        return recorded.read_bytes()
    return manifest_path().read_bytes()


def project_path(home, generation=None, *, content=None):
    alias = receipt_directory(home, generation) / 'runtime-project.json'
    if alias.exists():
        if content is not None and content != manifest_content(home, generation):
            raise ValueError('Qualified project manifest changed')
        value = json.loads(alias.read_text())
        name = value.get('project', '')
        if not isinstance(name, str) or not re.fullmatch(r'q-[a-f0-9]{32}(?:-[a-f0-9]{8})?', name):
            raise ValueError('Invalid qualified worker project identity')
        target = Path(home) / 'runtime' / name
        if target.is_symlink() or not target.resolve().is_relative_to((Path(home) / 'runtime').resolve()):
            raise ValueError('Qualified worker project is outside its owner')
        return target
    digest = hashlib.sha256(manifest_content(home, generation) if content is None else content)
    if generation:
        digest.update(generation.encode())
    return Path(home) / 'runtime' / digest.hexdigest()[:16]


def locked_sources(project):
    project = Path(project)
    path = project if project.is_file() else project / 'uv.lock'
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {row['name']: urlsplit(row['source']['git']) for row in data.get('package', [])
            if row.get('source', {}).get('git')}


def package_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def toml_value(value):
    """Serialize the app-owned generated manifest's basic TOML values."""
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(key) + ' = ' + toml_value(item) for key, item in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('Unsupported generated runtime manifest value')
