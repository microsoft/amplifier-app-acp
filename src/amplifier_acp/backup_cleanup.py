"""Reviewed retirement of completed private archives, never recovery originals."""
from __future__ import annotations
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from .maintenance_gate import Gate, checked
from .workspace_cleanup import digest, inventory, open_directory, require_admin
from .session_lifecycle import regular
from .relocation import write

FIELDS = {
    'maintenance.backups.inspect': set(),
    'maintenance.backups.preview': {'artifactId', 'reviewed'},
    'maintenance.backups.remove': {'commandId', 'previewHash', 'confirmationId'},
}
CAPABILITY = {'version': 1, 'previewRequired': True, 'receipt': True,
              'requiresRecoveryAdminLease': True, 'newestProtected': True,
              'recoveryReferencesProtected': True, 'automaticReplay': False}

class Refused(ValueError):
    data = {'executed': False, 'replayed': False, 'reason': 'backup-cleanup-refused'}

class Backups:
    def __init__(self, owner):
        self.owner = owner

    def check(self):
        owner = self.owner
        authority = require_admin(owner)
        if owner.config.get('maintenanceExternalWriters') != 'stopped':
            raise Refused('Backup cleanup requires stopped external writers')
        if owner.lease and not owner.lease['released'] or getattr(owner, 'capture_scope', None) and owner.capture_scope.active():
            raise Refused('An archive capture is still active')
        deadline = time.monotonic() + 120
        def check():
            owner.ensure_open()
            if time.monotonic() > deadline or require_admin(owner) != authority:
                raise Refused('Backup review expired or maintenance ownership changed')
        return check

    def catalog(self, workspace, check, command=None):
        from .maintenance import file_stamp, identity
        owner = self.owner
        # Project only bounded receipt facts. No transcript or archive bodies.
        with owner.db() as db:
            pending = db.execute("SELECT id FROM commands WHERE id!=? AND json_extract(body,'$.state') IN ('running','unknown') AND (json_extract(body,'$.operation') LIKE 'maintenance.backups.%' OR json_extract(body,'$.operation') IN ('maintenance.snapshot','maintenance.restore.apply','maintenance.restore.reconcile','maintenance.reset.apply','maintenance.reset.restore')) LIMIT 1", (command or '',)).fetchone()
            rows = db.execute("SELECT workspace,json_extract(body,'$.result.artifactId'),json_extract(body,'$.result.sha256'),json_extract(body,'$.result.bytes'),json_extract(body,'$.settledAt') FROM commands WHERE json_extract(body,'$.operation')='maintenance.snapshot' AND json_extract(body,'$.state')='succeeded' ORDER BY json_extract(body,'$.settledAt') DESC,id DESC LIMIT 1001").fetchall()
        if pending:
            raise Refused('An earlier recovery operation is unresolved; inspect its original receipt')
        if len(rows) > 1000:
            raise Refused('Backup catalog exceeds the bounded review')
        references = set()
        plans = checked(owner.root / 'restore-plans')
        if plans.exists():
            with os.scandir(plans) as entries:
                for index, entry in enumerate(entries):
                    check()
                    if index >= 10000 or entry.is_symlink():
                        raise Refused('Recovery references cannot be completely inspected')
                    if entry.name.endswith('.json'):
                        plan = json.loads(regular(checked(Path(entry.path)), 1024 * 1024))
                        if not isinstance(plan.get('artifactId'), str):
                            raise Refused('Recovery reference identity is unavailable')
                        references.add(plan['artifactId'])
        available = []
        newest = None
        for scope, artifact, sha, size, created in rows:
            check()
            artifact = identity(artifact)
            directory = checked(owner.root / 'artifacts' / artifact)
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise Refused('Backup directory identity changed')
            description = json.loads(regular(directory / 'description.json', 1024 * 1024))
            if any(description.get(key) != value for key, value in [('artifactId', artifact), ('sha256', sha), ('bytes', size), ('workspace', scope)]) or file_stamp(directory / 'snapshot.tar')[2] != size:
                raise Refused('A saved backup no longer matches its completed receipt')
            if newest is None:
                newest = artifact
            if scope != workspace:
                continue
            reason = 'Newest completed backup is kept' if artifact == newest else 'Referenced by a saved restore review' if artifact in references else None
            available.append({'artifactId': artifact, 'sha256': sha, 'bytes': size,
                              'createdAt': created, 'removable': reason is None,
                              **({'reason': reason} if reason else {})})
        return {'version': 1, 'items': available, 'newestProtected': True,
                'recoveryReferencesProtected': True, 'canonicalFilesChanged': 0,
                'coverage': 'completed-native-private-archives-only'}

    def inspect(self, workspace, args):
        check = self.check()
        with Gate(self.owner.config, exclusive=True):
            return self.catalog(workspace, check)

    def preview(self, workspace, args):
        if args.get('reviewed') is not True:
            raise Refused('Review permanent backup removal first')
        check = self.check()
        with Gate(self.owner.config, exclusive=True):
            catalog = self.catalog(workspace, check)
            selected = next((r for r in catalog['items'] if r['artifactId'] == args.get('artifactId')), None)
            if not selected or not selected['removable']:
                raise Refused('Only an older unreferenced completed backup can be removed')
            fd = open_directory(self.owner.root / 'artifacts')
            try:
                observed = inventory(fd, selected['artifactId'], check)
            finally:
                os.close(fd)
            body = {'kind': 'private-backup-removal', 'workspace': workspace,
                    'artifact': selected, 'inventory': observed, 'expiresAt': time.time() + 600,
                    'nonce': uuid.uuid4().hex, 'coverage': catalog['coverage']}
            preview = digest(body)
            write(checked(self.owner.root / 'previews' / (preview + '.json')), body)
            return {k: v for k, v in {**body, 'previewHash': preview, 'permanent': True}.items() if k not in {'workspace', 'nonce'}}

    def remove(self, workspace, args):
        changed = False
        try:
            from .maintenance import token
            check = self.check()
            preview = token(args.get('previewHash'))
            body = json.loads(regular(checked(self.owner.root / 'previews' / (preview + '.json')), 1024 * 1024))
            if digest(body) != preview or body.get('kind') != 'private-backup-removal' or body.get('workspace') != workspace or time.time() >= body['expiresAt']:
                raise Refused('Exact unexpired account-owned backup review required')
            selected = body['artifact']
            if args.get('confirmationId') != selected['artifactId']:
                raise Refused('Confirm the exact reviewed backup identity')
            with Gate(self.owner.config, exclusive=True):
                current = next((r for r in self.catalog(workspace, check, args['commandId'])['items'] if r['artifactId'] == selected['artifactId']), None)
                if current != selected or not current['removable']:
                    raise Refused('Backup identity or recovery references changed after review')
                fd = open_directory(self.owner.root / 'artifacts')
                try:
                    if inventory(fd, selected['artifactId'], check) != body['inventory']:
                        raise Refused('Backup files changed after review')
                    # The enclosing maintenance journal already durably claimed
                    # this exact command. Failures after rename remain unknown.
                    quarantine = '.backup-cleanup-' + uuid.uuid4().hex
                    check()
                    changed = True
                    os.rename(selected['artifactId'], quarantine, src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                    if inventory(fd, quarantine, check) != body['inventory']:
                        raise RuntimeError('Quarantined backup changed; retained for inspection')
                    shutil.rmtree(quarantine, dir_fd=fd)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return {'artifactId': selected['artifactId'], 'previewHash': preview,
                    'removed': True, 'removedBytes': body['inventory']['bytes'],
                    'canonicalFilesChanged': 0, 'newestProtected': True,
                    'recoveryReferencesProtected': True, 'replayed': False,
                    'coverage': body['coverage']}
        except Exception as error:
            if changed:
                raise RuntimeError('Backup cleanup outcome needs inspection; never replay') from error
            if isinstance(error, Refused):
                raise
            raise Refused(str(error)) from None
