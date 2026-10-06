"""Trusted launcher source selection; never sourced from an ACP request."""
from __future__ import annotations
import json, os, re
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ENV = 'AMPLIFIER_ACP_MODULE_SOURCES'
MAX_BYTES = 65536
MAX_MODULES = 64

def validate(value, *, require_local_exists=True):
    error = 'moduleSources must contain at most64 module IDs and existing absolute directories or credential-free exact-commit git+https sources (64KiB maximum).'
    if not isinstance(value, dict) or len(value) > MAX_MODULES:
        raise ValueError(error)
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError(error)
    result = {}
    for module, source in value.items():
        if not isinstance(module, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,127}', module):
            raise ValueError(error)
        if not isinstance(source, str) or not source or len(source) > 4096 or any(ord(c) < 32 for c in source):
            raise ValueError(error)
        if source.startswith('git+https://'):
            try:
                parsed = urlsplit(source[4:])
                _, at, revision = parsed.path.rpartition('@')
                fields = parse_qsl(parsed.fragment, keep_blank_values=True)
                if (not parsed.hostname or parsed.username or parsed.password or parsed.query or not at
                        or not re.fullmatch(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})', revision)
                        or len(fields) > 1 or any(k != 'subdirectory' or not v or PurePosixPath(v).is_absolute()
                        or '..' in PurePosixPath(v).parts for k, v in fields)
                        or parsed.fragment and not fields):
                    raise ValueError(error)
            except ValueError:
                raise ValueError(error) from None
            result[module] = source
        else:
            try:
                path = Path(source)
                if not path.is_absolute():raise ValueError(error)
                if require_local_exists:
                    if not path.is_dir():raise ValueError(error)
                    result[module] = str(path.resolve(strict=True))
                else:
                    result[module] = str(path)
            except (OSError, ValueError):
                raise ValueError(error) from None
    return result

def configure(config, environment=None):
    environment = os.environ if environment is None else environment
    # Launcher absence explicitly removes inherited private worker policy.
    value = validate(config.get('moduleSources', {}))
    environment[ENV] = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return value

def from_environment():
    raw = os.environ.get(ENV, '{}')
    if len(raw.encode()) > MAX_BYTES:raise ValueError('Launcher moduleSources exceeds64KiB.')
    try:value = json.loads(raw)
    except (ValueError, TypeError):raise ValueError('Malformed launcher moduleSources.') from None
    return validate(value)

def public_source(source):
    """Never publish credentials/query fields from original configured sources."""
    if source is None:return None
    if not isinstance(source, str) or len(source) > 4096:return '[unavailable]'
    try:
        prefix = 'git+' if source.startswith('git+') else ''
        parsed = urlsplit(source[len(prefix):])
        if not parsed.scheme:
            return str(Path(source)) if Path(source).is_absolute() else '[relative-source]'
        if parsed.scheme not in {'https','http','ssh','git','file'}:return '[unsupported-source]'
        host = parsed.hostname or ''
        if parsed.port:host += ':' + str(parsed.port)
        fragment = ''
        for key, value in parse_qsl(parsed.fragment):
            if key == 'subdirectory' and re.fullmatch(r'[A-Za-z0-9_./-]{1,512}', value):
                fragment = 'subdirectory=' + value
        return prefix + urlunsplit((parsed.scheme, host, parsed.path, '', fragment))
    except (ValueError, OSError):return '[unavailable]'

def record(config, module, original, effective):
    rows = config.source_resolutions
    row = {'module':module, 'originalSource':public_source(original),
           'effectiveSource':public_source(effective), 'authority':'trusted-launcher', 'override':True}
    key = json.dumps(row, sort_keys=True)
    if key in rows:return
    if len(rows) >= 128 or sum(len(k.encode()) for k in rows) + len(key.encode()) > MAX_BYTES:
        config.source_resolution_truncated = True
    else:rows[key] = row

def provenance(config):
    return {'version':1, 'authority':'trusted-launcher', 'configuredOverrides':len(config.launcher_module_sources),
            'resolutions':list(config.source_resolutions.values()), 'truncated':config.source_resolution_truncated,
            'loadedCodeBytesVerified':False}
