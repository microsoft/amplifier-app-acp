"""Read-only source admission from a digest-bound trusted launcher manifest.

No peer request can install or relax this policy. Settings retain their original
URIs; the manifest binds one launch to qualified local bytes.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

ENV = 'AMPLIFIER_ACP_SOURCE_RESOLUTION_MANIFEST'
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 65536
_callbacks = {}

class SourceClosureDenied(RuntimeError):
    def __init__(self):
        super().__init__('Source is outside the qualified launch closure; preserve its requested URI and qualify a new launch manifest.')

def _deny():
    raise SourceClosureDenied()

def _read(path, limit):
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): _deny()
        value = stream.read(limit + 1)
    if len(value) > limit: _deny()
    return value

def _absolute(value):
    if not isinstance(value, str) or len(value) > 4096: _deny()
    path = Path(value)
    if not path.is_absolute() or not path.exists() or path.resolve() != path: _deny()
    return path

def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None

def _uri(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value): _deny()
    from urllib.parse import urlsplit
    parsed = urlsplit(value[4:] if value.startswith('git+') else value)
    if parsed.username or parsed.password or parsed.query: _deny()
    return value

def _api():
    try:
        from amplifier_foundation.sources import source_resolution_policy, SourceResolutionDenied, SOURCE_RESOLUTION_POLICY_VERSION
        from amplifier_foundation.paths.resolution import ResolvedSource
    except ImportError:
        raise RuntimeError('Qualified source closure requires Foundation source_resolution_policy support.') from None
    if SOURCE_RESOLUTION_POLICY_VERSION != 1 or not callable(source_resolution_policy) or not issubclass(SourceResolutionDenied, Exception):
        raise RuntimeError('Unsupported Foundation source resolution policy.')
    return source_resolution_policy, ResolvedSource

class Admission:
    def __init__(self, reference):
        if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'} or not _sha(reference['sha256']): _deny()
        self.reference = dict(reference)
        raw = _read(_absolute(reference['path']), MAX_BYTES)
        if hashlib.sha256(raw).hexdigest() != reference['sha256']: _deny()
        try: document = json.loads(raw)
        except (ValueError, TypeError): _deny()
        if not isinstance(document, dict) or set(document) != {'version', 'sources'} or type(document['version']) is not int or document['version'] != 1: _deny()
        rows = document['sources']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 512: _deny()
        self.rows = {}; self.packages = {}; self.origins = {}; self.checked_files = {}; self.file_digests = {}; files_count = 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'requestedUri', 'basePath', 'activePath', 'sourceRoot', 'resolvedCommit', 'approval', 'files', 'admissionFiles', 'packages'}: _deny()
            uri = _uri(row['requestedUri'])
            base = None if row['basePath'] is None else str(_absolute(row['basePath']))
            root, active = _absolute(row['sourceRoot']), _absolute(row['activePath'])
            if not root.is_dir() or not active.is_relative_to(root): _deny()
            commit = row['resolvedCommit']
            if commit is not None and (not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', commit)): _deny()
            approval = row['approval']
            if approval is not None and (not isinstance(approval, str) or not 1 <= len(approval) <= 512 or any(ord(c) < 32 for c in approval)): _deny()
            if uri.startswith('git+') and commit is None: _deny()
            files = row['files']
            if not isinstance(files, dict) or not files: _deny()
            files_count += len(files)
            if files_count > MAX_FILES: _deny()
            for name, digest in files.items():
                relative = PurePosixPath(name)
                if not isinstance(name, str) or len(name) > 1024 or relative.is_absolute() or '..' in relative.parts or str(relative) != name or not _sha(digest): _deny()
                file_key = (str(root), name)
                if file_key in self.file_digests and self.file_digests[file_key] != digest: _deny()
                self.file_digests[file_key] = digest
            required = row['admissionFiles']
            if not isinstance(required, list) or not 1 <= len(required) <= 128 or any(name not in files for name in required): _deny()
            key = (uri, base)
            if key in self.rows: _deny()
            self.rows[key] = {**row, 'root': root, 'active': active}
            packages = row['packages']
            if not isinstance(packages, list) or len(packages) > 128: _deny()
            for package in packages:
                if not isinstance(package, str) or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', package): _deny()
                if package in self.packages and self.packages[package] != root: _deny()
                self.packages[package] = root
        self.verify()

    def verify(self):
        # Reference digest is bound at configuration. Runtime file verification
        # is lazy and stat-cached; full membership is a candidate qualification.
        path = _absolute(self.reference['path'])
        info = path.stat()
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if getattr(self, '_manifest_stat', None) != signature:
            if hashlib.sha256(_read(path, MAX_BYTES)).hexdigest() != self.reference['sha256']: _deny()
            self._manifest_stat = signature

    def check_file(self, root, relative, digest):
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink(): _deny()
        info = path.stat()
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        key = (str(path), digest)
        if self.checked_files.get(key) != signature:
            if hashlib.sha256(_read(path, 64 * 1024 * 1024)).hexdigest() != digest: _deny()
            self.checked_files[key] = signature

    def check_origin(self, root, location):
        path = Path(location).resolve()
        if not path.is_relative_to(root): _deny()
        if path.is_dir(): return
        relative = path.relative_to(root).as_posix()
        rows = [row for row in self.rows.values() if row['root'] == root and relative in row['files']]
        if not rows: _deny()
        self.check_file(root, relative, rows[0]['files'][relative])

    def __call__(self, uri, *, base_path):
        self.verify()
        key = (uri, str(Path(base_path).resolve()))
        row = self.rows.get(key) or self.rows.get((uri, None))
        active = row['active'] if row is not None else None
        if row is None:
            from urllib.parse import unquote, urlsplit
            parsed = urlsplit(uri)
            if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
                path = Path(unquote(parsed.path))
            elif not parsed.scheme:
                path = Path(uri)
            else:
                _deny()
            if not path.is_absolute(): _deny()
            active = _absolute(str(path))
            candidates = [value for value in self.rows.values() if active.is_relative_to(value['root'])]
            if not candidates: _deny()
            members = []
            for value in candidates:
                relative = active.relative_to(value['root']).as_posix()
                if active.is_file():
                    if relative in value['files']: members.append(value)
                elif any(name.startswith(relative + '/') or relative == '.' for name in value['files']):
                    members.append(value)
            if not members: _deny()
            row = members[0]
            if active.is_file():
                relative = active.relative_to(row['root']).as_posix()
                self.check_file(row['root'], relative, row['files'][relative])
        _absolute(str(row['root'])); _absolute(str(row['active']))
        for name in row['admissionFiles']:
            self.check_file(row['root'], name, row['files'][name])
        _, resolved = _api()
        return resolved(active_path=active, source_root=row['root'])

    def attest(self):
        """Check configured package origins without importing provider code."""
        self.verify()
        self.origins = {}
        for package, root in self.packages.items():
            loaded = sys.modules.get(package)
            spec = getattr(loaded, '__spec__', None) if loaded is not None else importlib.util.find_spec(package)
            if spec is None:
                if loaded is not None: _deny()
                continue  # Foundation has not activated it yet.
            locations = list(spec.submodule_search_locations or [])
            if spec.origin and spec.origin not in {'built-in', 'frozen'}: locations.append(spec.origin)
            if not locations: _deny()
            for location in locations:
                self.check_origin(root, location)
            for name, module in list(sys.modules.items()):
                if name == package or name.startswith(package + '.'):
                    origin = getattr(module, '__file__', None)
                    if origin: self.check_origin(root, origin)
            self.origins[package] = {'root': str(root), 'origin': spec.origin, 'verified': True, 'loaded': loaded is not None}
        return {'version': 1, 'manifestSha256': self.reference['sha256'], 'origins': dict(self.origins),
                'sourceResolution': 'qualified-only', 'declaredPackages': len(self.packages),
                'loadedPackages': sum(row['loaded'] for row in self.origins.values()),
                'loadedOriginsVerified': any(row['loaded'] for row in self.origins.values())}

def _reference(environment=None):
    raw = (os.environ if environment is None else environment).get(ENV)
    if raw is None: return None
    if len(raw.encode()) > 8192: _deny()
    try: return json.loads(raw)
    except (ValueError, TypeError): _deny()

def admission(reference=None):
    reference = _reference() if reference is None else reference
    if reference is None: return None
    key = json.dumps(reference, sort_keys=True)
    if key not in _callbacks: _callbacks[key] = Admission(reference)
    return _callbacks[key]

def configure(config, environment=None):
    env = os.environ if environment is None else environment
    supplied = config.get('sourceResolutionManifest')
    inherited = _reference(env)
    if supplied is not None and inherited is not None and supplied != inherited: _deny()
    reference = inherited if supplied is None else supplied
    if reference is not None:
        _api()  # Refuse unsupported Foundation before startup/files/settings effects.
        callback = admission(reference); callback.attest()
        env[ENV] = json.dumps(reference, sort_keys=True, separators=(',', ':'))
    return reference

@contextmanager
def scope():
    callback = admission()
    if callback is None:
        yield
        return
    policy, _ = _api()
    callback.attest()
    with policy(callback):
        yield
        callback.attest()

def attest_prepared(prepared):
    callback = admission()
    if callback is None: return {}
    resolver = getattr(prepared, 'resolver', None)
    snapshot = getattr(resolver, 'prepared_sources', None)
    if not callable(snapshot):
        raise RuntimeError('Qualified source closure requires Foundation prepared_sources readback.')
    rows = snapshot()
    if not isinstance(rows, tuple) or len(rows) > 4096: _deny()
    for module, source, path in rows:
        # Foundation guards original hint/path correspondence. Native separately
        # confirms local file membership and records effective imported origins.
        callback(str(path), base_path=Path.cwd())
    return callback.attest()

def attest():
    callback = admission()
    return callback.attest() if callback is not None else {}


def readiness(environment=None):
    reference = _reference(environment)
    if reference is None: return None
    return {'version': 1, 'manifestSha256': reference['sha256'],
            'sourceResolution': 'qualified-only'}

def ready_report(report):
    # Keep paths and imported-origin inventories in the private mounted receipt.
    value = report.get('sourceClosure')
    if not value: return None
    return {key: value.get(key) for key in ('version', 'manifestSha256', 'sourceResolution')}
