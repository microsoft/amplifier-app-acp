"""Reviewed removal of shared conversation history, never working directories.

Only the held recovery coordinator can supply the product-owner census. Stable
writer locks and original command receipts survive removal. An interrupted
attempt retains its tombstone and quarantines; neither inspection nor another
command repeats the effect.
"""
from __future__ import annotations
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import time
import uuid
from .maintenance_gate import Gate, checked
from .workspace_cleanup import digest, inventory, open_directory, path, require_admin
from .session_lifecycle import regular, guard_copy_source
from .configuration_clone import location
from .relocation import write

FIELDS = {
    'maintenance.history.census': {'selection'},
    'maintenance.history.preview': {'selection', 'ownerCensus', 'reviewed'},
    'maintenance.history.remove': {'commandId', 'previewHash', 'ownerCensus', 'confirmationId'},
}
CAPABILITY = {'version': 1, 'previewRequired': True, 'receipt': True,
              'requiresRecoveryAdminLease': True, 'trustedOwnerCensus': True,
              'workspacePreserved': True, 'stableLocksPreserved': True,
              'automaticReplay': False, 'maxSessions': 1000}


class Refused(ValueError):
    data = {'executed': False, 'replayed': False, 'reason': 'history-cleanup-refused'}


class HistoryCleanup:
    def __init__(self, owner):
        self.owner = owner

    def check(self, command=None):
        owner = self.owner
        authority = require_admin(owner)
        if owner.config.get('maintenanceExternalWriters') != 'stopped':
            raise Refused('Saved history removal requires stopped external writers')
        if owner.lease and not owner.lease['released'] or getattr(owner, 'capture_scope', None) and owner.capture_scope.active():
            raise Refused('An archive capture still owns saved history')
        with owner.db() as db:
            pending = db.execute("SELECT id FROM commands WHERE id!=? AND json_extract(body,'$.state') IN ('running','unknown') AND json_extract(body,'$.operation')='maintenance.history.remove' LIMIT 1", (command or '',)).fetchone()
        if pending:
            raise Refused('An original history removal is unresolved; inspect it without replay')
        deadline = time.monotonic() + 120
        def check():
            owner.ensure_open()
            if time.monotonic() > deadline or require_admin(owner) != authority:
                raise Refused('History review expired or maintenance ownership changed')
        return check

    def discover(self, selection, check):
        from .native.session_files import project_slug, validate_id
        if not isinstance(selection, dict) or set(selection) not in ({'project'}, {'project', 'nativeSessionId'}):
            raise Refused('Choose an exact saved project or conversation')
        project = selection['project']
        if not isinstance(project, str) or not 1 <= len(project) <= 4096 or project in {'.', '..'} or any(c in project for c in '/\\') or any(ord(c) < 32 or ord(c) == 127 for c in project):
            raise Refused('Invalid saved project identity')
        directory = checked(self.owner.home / 'projects' / project / 'sessions')
        fd = open_directory(directory)
        records = {}
        try:
            with os.scandir(fd) as entries:
                for index, entry in enumerate(entries):
                    check()
                    if index >= 10000:
                        raise Refused('Project exceeds the bounded saved history census')
                    if entry.name.startswith('.'):
                        continue  # Stable lifecycle lock files are not histories.
                    sid = validate_id(entry.name)
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        raise Refused('Unexpected entry in shared history; files preserved')
                    raw = regular(directory / sid / 'metadata.json')
                    metadata = json.loads(raw)
                    if not isinstance(metadata, dict):
                        raise Refused('Saved history metadata is not an object')
                    cwd = metadata.get('working_dir') or metadata.get('workspace')
                    if metadata.get('session_id', sid) != sid or not isinstance(cwd, str) or str(path(cwd)) != cwd or project_slug(cwd) != project:
                        raise Refused('Saved history has an unverified project identity')
                    parent = metadata.get('parent_id')
                    if parent is not None:
                        validate_id(parent)
                    records[sid] = {'cwd': cwd, 'parent': parent, 'metadataHash': digest(metadata), 'title': metadata.get('name') or metadata.get('title')}
        finally:
            os.close(fd)
        chosen = selection.get('nativeSessionId')
        if chosen is not None:
            validate_id(chosen)
            if chosen not in records:
                raise Refused('The selected saved conversation is no longer available')
            selected = {chosen}
            while True:
                children = {sid for sid, row in records.items() if row['parent'] in selected}
                if children <= selected:
                    break
                selected |= children
        else:
            selected = set(records)
        if not selected or len(selected) > 1000:
            raise Refused('Select between one and 1000 saved conversations')
        workspaces = {records[sid]['cwd'] for sid in selected}
        if len(workspaces) != 1:
            raise Refused('Several workspace paths share this project name; choose an individual conversation')
        cwd = workspaces.pop()
        self.owner.admin.workspace(cwd)
        # Session-local control receipts are keyed by native ID. A second native
        # namespace sharing an ID makes ownership ambiguous, even if unindexed.
        projects = checked(self.owner.home / 'projects')
        examined = 0
        with os.scandir(projects) as scopes:
            for index, entry in enumerate(scopes):
                check()
                if index >= 10000 or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    raise Refused('Native identity census is incomplete')
                other = checked(Path(entry.path) / 'sessions')
                if entry.name == project or not other.exists():
                    continue
                with os.scandir(other) as sessions:
                    for item in sessions:
                        check()
                        examined += 1
                        if examined > 10000 or item.is_symlink() or item.name in selected:
                            raise Refused('Native history identity is ambiguous or the census exceeds capacity')
        rows = []
        for sid in sorted(selected):
            check()
            guard_copy_source(self.owner, sid)
            app = location(self.owner.app, sid)
            relocation = app / 'execution-relocation.json'
            if relocation.exists() and json.loads(regular(relocation)).get('status') != 'applied':
                raise Refused('Unresolved execution relocation protects saved history')
            rows.append({'nativeSessionId': sid, 'cwd': cwd, 'parentId': records[sid]['parent'], 'metadataHash': records[sid]['metadataHash']})
        title = records[chosen]['title'] if chosen else 'All saved conversations'
        if not isinstance(title, str) or not title or len(title) > 500:
            title = chosen or 'All saved conversations'
        result = {'version': 1, 'selection': selection, 'workspace': cwd, 'sessions': rows, 'title': title}
        return {**result, 'revision': digest(result)}

    def census(self, workspace, args):
        check = self.check()
        with Gate(self.owner.config, exclusive=True):
            return self.discover(args.get('selection'), check)

    def owners(self, value, census):
        if not isinstance(value, dict) or set(value) != {'version', 'coverage', 'revision', 'nativeRevision'} or value.get('version') != 1 or value.get('coverage') != 'all-configured-owners-held' or value.get('nativeRevision') != census['revision']:
            raise Refused('Exact held product owner census required')
        from .maintenance import token
        token(value['revision'])
        return value

    def stores(self, census):
        from amplifier_foundation.session.shared_state import SharedSessionStore
        return [SharedSessionStore.from_identity({'version': 1, 'workspace': row['cwd'], 'sessionId': row['nativeSessionId']}) for row in census['sessions']]

    def roots(self, census, stores):
        from .native.session_files import capture_sessions_dir
        roots = []
        for row, store in zip(census['sessions'], stores):
            sid = row['nativeSessionId']
            native = checked(self.owner.home / 'projects' / census['selection']['project'] / 'sessions' / sid)
            roots.append((native, True))
            capture = checked(capture_sessions_dir(row['cwd']) / sid)
            if capture != native:
                roots.append((capture, True))
            roots.append((checked(location(self.owner.app, sid) / 'history-revisions'), True))
            roots.append((checked(store.checkpoint_path), False))
        # No selected root may contain a working folder, native/admin root, or
        # another selection. Captures are launcher-configured, not user paths.
        paths = [root for root, _ in roots]
        protected = [path(census['workspace']), self.owner.home, self.owner.app]
        for i, root in enumerate(paths):
            if any(root == other or root in other.parents for other in protected) or any(root == other or root in other.parents or other in root.parents for other in paths[i+1:]):
                raise Refused('History storage overlaps a protected or selected folder')
        return roots

    def snapshot(self, roots, check):
        from .maintenance import file_stamp
        rows = []
        count = 0
        for root, directory in roots:
            check()
            if not root.exists():
                rows.append({'path': str(root), 'directory': directory, 'missing': True})
                continue
            if directory:
                fd = open_directory(root.parent)
                try:
                    observed = inventory(fd, root.name, check)
                finally:
                    os.close(fd)
            else:
                stamp = file_stamp(root)
                observed = {'stamp': stamp, 'bytes': stamp[2], 'entries': 1}
            count += observed['entries']
            if count > 200000:
                raise Refused('Combined history inventory exceeds its bound')
            rows.append({'path': str(root), 'directory': directory, 'inventory': observed})
        return rows

    def preview(self, workspace, args):
        if args.get('reviewed') is not True:
            raise Refused('Review permanent shared-history removal first')
        check = self.check()
        with ExitStack() as stack:
            stack.enter_context(Gate(self.owner.config, exclusive=True))
            census = self.discover(args.get('selection'), check)
            owners = self.owners(args.get('ownerCensus'), census)
            stores = self.stores(census)
            for store in stores:
                lease = store.acquire(app='amplifier-acp-history-review', pid=os.getpid())
                stack.callback(lease.release)
                if store.checkpoint_path.exists():
                    store.read()
            rows = self.snapshot(self.roots(census, stores), check)
            body = {'kind': 'shared-history-removal', 'workspace': workspace, 'census': census,
                    'ownerCensus': owners, 'roots': rows, 'expiresAt': time.time() + 600,
                    'nonce': uuid.uuid4().hex}
            if len(json.dumps(body).encode()) > 1024 * 1024:
                raise Refused('History review exceeds its bound')
            identity = digest(body)
            write(checked(self.owner.root / 'previews' / (identity + '.json')), body)
            return {'kind': body['kind'], 'previewHash': identity, 'confirmationId': census['revision'],
                    'selection': census['selection'], 'title': census['title'], 'sessionCount': len(census['sessions']),
                    'inventory': {'bytes': sum(row.get('inventory', {}).get('bytes', 0) for row in rows)},
                    'expiresAt': body['expiresAt'], 'permanent': True, 'workspacePreserved': True,
                    'stableLocksPreserved': True, 'receiptsPreserved': True, 'replayed': False}

    def remove(self, workspace, args):
        changed = False
        try:
            from .maintenance import token
            check = self.check(args['commandId'])
            identity = token(args.get('previewHash'))
            body = json.loads(regular(checked(self.owner.root / 'previews' / (identity + '.json'))))
            if digest(body) != identity or body.get('kind') != 'shared-history-removal' or body['workspace'] != workspace or time.time() >= body['expiresAt'] or args.get('confirmationId') != body['census']['revision']:
                raise Refused('Exact unexpired history review and confirmation required')
            with ExitStack() as stack:
                stack.enter_context(Gate(self.owner.config, exclusive=True))
                census = self.discover(body['census']['selection'], check)
                if census != body['census'] or self.owners(args.get('ownerCensus'), census) != body['ownerCensus']:
                    raise Refused('Saved history or owner references changed after review')
                stores = self.stores(census)
                leases = []
                for store in stores:
                    lease = store.acquire(app='amplifier-acp-history-removal', pid=os.getpid())
                    stack.callback(lease.release)
                    leases.append(lease)
                roots = self.roots(census, stores)
                if self.snapshot(roots, check) != body['roots']:
                    raise Refused('Saved history files changed after review')
                # The outer maintenance journal already owns this exact command.
                # Record permanent execution tombstones before the first effect.
                changed = True
                for row in census['sessions']:
                    check()
                    app = checked(location(self.owner.app, row['nativeSessionId']))
                    write(app / 'history-removal.json',
                          {'commandId': args['commandId'], 'previewHash': identity, 'historyCwd': row['cwd'], 'permanent': True})
                    # Discovery already understands this durable visibility marker.
                    # The separate permanent marker prevents a later restore from
                    # reviving an identity whose canonical files were removed.
                    write(app / 'lifecycle.json',
                          {'sessionId': row['nativeSessionId'], 'historyCwd': row['cwd'], 'deleted': True,
                           'commandId': args['commandId'], 'updatedAt': time.time()})
                    fd = open_directory(app)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                for lease in leases:
                    check()
                    lease.delete_checkpoint()  # Retains the Foundation stable lock.
                for row in body['roots']:
                    if row.get('missing') or not row['directory']:
                        continue
                    check()
                    root = Path(row['path'])
                    fd = open_directory(root.parent)
                    try:
                        quarantine = '.history-cleanup-' + uuid.uuid4().hex
                        os.rename(root.name, quarantine, src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                        if inventory(fd, quarantine, check) != row['inventory']:
                            raise RuntimeError('Quarantined history changed; retained for inspection')
                        shutil.rmtree(quarantine, dir_fd=fd)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            return {'removed': True, 'previewHash': identity, 'confirmationId': census['revision'],
                    'selection': census['selection'], 'sessionCount': len(census['sessions']),
                    'removedBytes': sum(row.get('inventory', {}).get('bytes', 0) for row in body['roots']),
                    'workspacePreserved': True, 'stableLocksPreserved': True, 'receiptsPreserved': True, 'replayed': False}
        except Exception as error:
            if changed:
                raise RuntimeError('History removal outcome needs inspection; never replay') from error
            if isinstance(error, Refused):
                raise
            raise Refused(str(error)) from None
