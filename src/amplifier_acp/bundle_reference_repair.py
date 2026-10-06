"""Reviewed, metadata-only legacy Work repair. Never mount or submit input.

Lineage and reference rules follow Unified 0.20.51's bundle_reference_repair.
Native leases and exact metadata/configuration hashes replace its UI locks.
"""
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path

from .configuration_clone import fingerprint
from .maintenance_gate import Gate, checked
from .relocation import write
from .session_lifecycle import regular
from .native.session_files import project_slug, validate_id

FIELDS = {
    'maintenance.bundleReferences.preview': set(),
    'maintenance.bundleReferences.page': {'previewHash', 'reportHash', 'cursor', 'limit'},
    'maintenance.bundleReferences.apply': {'previewHash', 'commandId'},
}
CAPABILITY = {'version': 1, 'metadataOnly': True, 'previewRequired': True,
              'receipt': True, 'paged': True, 'workReplayed': False}
LEGACY = {'Work', 'bundle:Work'}
MAX_ROWS = 5000
MAX_PLAN = 8 * 1024 * 1024


class Refused(ValueError):
    data = {'executed': False, 'replayed': False, 'reason': 'bundle-reference-repair-refused'}


def root_session(identity, metadata):
    # Match native catalog/Unified lineage, including independent CLI forks.
    if metadata.get('_independent_fork') or (type(metadata.get('forked_from_turn')) is int
            and metadata['forked_from_turn'] >= 0 and metadata.get('forked_at')):
        return True
    if metadata.get('session_visibility') == 'internal':
        return False
    keys = [key for key in ('parent_id', 'parent_session_id') if key in metadata
            and (metadata[key] is None or isinstance(metadata[key], str))]
    if keys:
        return not any(metadata[key] for key in keys)
    return metadata.get('session_visibility') == 'chat' or '_' not in identity


def metadata(path):
    checked(path)
    if path.lstat().st_nlink != 1:
        raise ValueError('Linked metadata is not repairable')
    raw = regular(path, MAX_PLAN)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Invalid metadata object')
    for key in ('bundle_name', 'bundle', 'working_dir', 'workspace', 'session_id'):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError('Invalid metadata routing field')
    return value, hashlib.sha256(raw).hexdigest()


class BundleReferences:
    def __init__(self, owner):
        self.owner = owner
        self.root = owner.root / 'bundle-references'

    def authorize(self):
        if self.owner.config.get('maintenanceFullNative') is not True:
            raise Refused('Full native maintenance must be enabled to inspect saved bundle references')
        if self.owner.config.get('maintenanceExternalWriters') not in {'foundation-cooperative', 'stopped'}:
            raise Refused('Saved bundle repair requires cooperative or stopped external writers')

    def directory(self, row):
        validate_id(row['nativeSessionId'])
        project = row['nativeProject']
        if not isinstance(project, str) or '/' in project or '\\' in project or not project.startswith('-'):
            raise ValueError('Invalid native project')
        return checked(self.owner.home / 'projects' / project / 'sessions' / row['nativeSessionId'])

    def reference(self, row):
        from .native.host.config import read_config
        from .native.host.bundle_paths import canonical_bundle_reference, local_bundle_path
        cwd = row.get('workspace')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            return 'workspace-unavailable', None
        try:
            workspace = self.owner.admin.workspace(cwd)
        except ValueError:
            return 'workspace-not-authorized', None
        if workspace != cwd or project_slug(workspace) != row['nativeProject']:
            return 'identity-conflict', None
        config = read_config(workspace, home=self.owner.app, shared_home=self.owner.home,
                             session_id=row['nativeSessionId'], registry_home=self.owner.config.get('registryHome'))
        if local_bundle_path(config, 'Work') or local_bundle_path(config, 'work'):
            return 'local-bundle', None
        target = config.registrations.get('work')
        if not target:
            return 'target-unregistered', None
        if 'Work' in config.registrations:
            if config.registrations['Work'] != target:
                return 'alias-conflict', None
        elif canonical_bundle_reference(config, 'Work') != 'work':
            return 'alias-conflict', None
        return None, fingerprint({'work': target, 'Work': config.registrations.get('Work')})

    def save(self, body):
        if len(json.dumps(body).encode()) > MAX_PLAN:
            raise Refused('Saved bundle review exceeds its bounded inventory')
        digest = fingerprint(body)
        write(checked(self.root / (digest + '.json')), body)
        return digest

    def load(self, workspace, digest):
        from .maintenance import token
        body = json.loads(regular(checked(self.root / (token(digest) + '.json')), MAX_PLAN))
        if body.get('workspace') != workspace or fingerprint(body) != digest:
            raise Refused('Saved bundle review identity changed')
        return body

    def page(self, workspace, args):
        self.authorize()
        if bool(args.get('previewHash')) == bool(args.get('reportHash')):
            raise Refused('Choose one exact saved review or report')
        digest = args.get('previewHash') or args['reportHash']
        body = self.load(workspace, digest)
        if body['applied'] != bool(args.get('reportHash')):
            raise Refused('Saved review/report kind does not match')
        cursor, limit = args.get('cursor', 0), args.get('limit', 50)
        if type(cursor) is not int or type(limit) is not int or not 0 <= cursor <= len(body['sessions']) or not 1 <= limit <= 100:
            raise Refused('Bounded saved bundle page required')
        rows = [{k: v for k, v in row.items() if k not in {'metadataHash', 'referenceHash'}}
                for row in body['sessions'][cursor:cursor + limit]]
        return {k: v for k, v in body.items() if k not in {'workspace', 'sessions'}} | {
            'reportHash' if body['applied'] else 'previewHash': digest,
            'sessions': rows, 'nextCursor': cursor + limit if cursor + limit < len(body['sessions']) else None}

    def preview(self, workspace, args):
        self.authorize()
        rows, scanned, total_bytes = [], 0, 0
        projects = checked(self.owner.home / 'projects')
        with Gate(self.owner.config):
            for project in sorted(projects.iterdir()) if projects.exists() else []:
                checked(project)
                sessions = checked(project / 'sessions')
                if not sessions.is_dir():
                    continue
                for directory in sorted(sessions.iterdir()):
                    self.owner.ensure_open()
                    if directory.name.startswith('.'):
                        continue  # Foundation's sibling metadata locks.
                    scanned += 1
                    if scanned > 20000:
                        raise Refused('Saved bundle inventory exceeds 20000 native directories')
                    row = {'nativeProject': project.name, 'nativeSessionId': directory.name}
                    try:
                        checked(directory)
                        validate_id(directory.name)
                        path = directory / 'metadata.json'
                        missing = not path.exists()
                        value, digest = metadata(path.with_suffix('.json.backup') if missing else path)
                        total_bytes += path.with_suffix('.json.backup').stat().st_size if missing else path.stat().st_size
                        if total_bytes > 256 * 1024 * 1024:
                            raise Refused('Saved metadata inventory exceeds 256 MiB')
                        if not root_session(directory.name, value) or (value.get('bundle_name') or value.get('bundle')) not in LEGACY:
                            continue
                        row.update(workspace=value.get('working_dir') or value.get('workspace'),
                                   title=str(value.get('name') or value.get('title') or directory.name)[:200], metadataHash=digest)
                        if missing:
                            row['status'] = 'missing-primary'
                        elif not re.fullmatch(r'[A-Za-z0-9-]{1,128}', directory.name):
                            row['status'] = 'unsupported-lease-identity'
                        elif value.get('session_id', directory.name) != directory.name:
                            row['status'] = 'identity-conflict'
                        elif any(value.get(k) not in LEGACY | {'work', None, ''} for k in ('bundle_name', 'bundle')):
                            row['status'] = 'reference-conflict'
                        else:
                            reason, reference = self.reference(row)
                            row.update(status=reason or 'eligible', referenceHash=reference)
                    except Refused:
                        raise
                    except (OSError, ValueError, RuntimeError):
                        row['status'] = 'unreadable'
                    rows.append(row)
                    if len(rows) > MAX_ROWS:
                        raise Refused('Saved bundle review exceeds 5000 records')
        body = {'version': 1, 'workspace': workspace, 'applied': False, 'from': 'Work', 'to': 'work',
                'workReplayed': False, 'workerStarted': False, 'scanned': scanned,
                'counts': dict(Counter(row['status'] for row in rows)), 'sessions': rows, 'complete': False}
        digest = self.save(body)
        return self.page(workspace, {'previewHash': digest})

    def repair(self, row):
        from amplifier_foundation.session.shared_state import SharedSessionStore, SessionBusyError, SessionTransferFencedError
        from amplifier_foundation.session.history import SessionHistoryStore
        from filelock import FileLock, Timeout
        if row['status'] != 'eligible':
            return row['status']
        reason, reference = self.reference(row)
        if reason:
            return reason
        if reference != row['referenceHash']:
            return 'reference-changed'
        directory = self.directory(row)
        try:
            held = SharedSessionStore(row['workspace'], row['nativeSessionId']).acquire(app='amplifier-acp-bundle-repair', pid=os.getpid())
        except (SessionBusyError, SessionTransferFencedError):
            return 'busy'
        try:
            # Same sibling lock as Foundation, but skip busy writers promptly.
            lock = FileLock(str(checked(directory.parent / ('.' + directory.name + '.metadata.lock'))), timeout=0)
            with lock:
                value, digest = metadata(directory / 'metadata.json')
                if digest != row['metadataHash']:
                    return 'changed-since-preview'
                reason, reference = self.reference(row)
                if reason or reference != row['referenceHash']:
                    return reason or 'reference-changed'
                backup = directory / 'metadata.json.backup'
                if backup.exists():
                    metadata(backup)
                value['bundle_name'] = 'work'
                if 'bundle' in value:
                    value['bundle'] = 'work'
                SessionHistoryStore(directory)._save_metadata_unlocked(value)
                if metadata(directory / 'metadata.json')[0] != value:
                    raise RuntimeError('Saved metadata verification failed')
                return 'changed'
        except Timeout:
            return 'busy'
        finally:
            held.release()

    def apply(self, workspace, args):
        self.authorize()
        body = self.load(workspace, args.get('previewHash'))
        if body.get('applied') is not False:
            raise Refused('An exact preview is required')
        results = []
        with Gate(self.owner.config):
            for row in body['sessions']:
                self.owner.ensure_open()
                result = {k: v for k, v in row.items() if k not in {'metadataHash', 'referenceHash'}}
                try:
                    result['status'] = self.repair(row)
                except (OSError, ValueError, RuntimeError) as error:
                    result.update(status='failed', errorType=type(error).__name__)
                results.append(result)
        report = {**body, 'applied': True, 'sourcePreviewHash': args['previewHash'],
                  'sessions': results, 'counts': dict(Counter(row['status'] for row in results)),
                  'complete': all(row['status'] == 'changed' for row in results)}
        digest = self.save(report)
        return self.page(workspace, {'reportHash': digest})
