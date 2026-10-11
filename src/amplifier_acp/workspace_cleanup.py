"""Exact reviewed workspace removal for the trusted maintenance coordinator.

This is not a browser path API. The product coordinator supplies a complete,
fresh owner census while holding all product writers. Native owns the filesystem
effect and its original receipt; canonical histories never live in the selection.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid
from contextlib import ExitStack
from .maintenance_gate import Gate, checked

def require_admin(owner):
    from .private_store_snapshot import require_admin as require
    return require(owner)

FIELDS = {
    'maintenance.workspace.preview': {'selection', 'reviewed'},
    'maintenance.workspace.remove': {'commandId', 'previewHash', 'selection', 'confirmationPath'},
}
CAPABILITY = {'version': 1, 'previewRequired': True, 'receipt': True,
              'requiresRecoveryAdminLease': True, 'trustedOwnerCensus': True,
              'historyPreserved': True, 'automaticReplay': False}


class Refused(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.data = {'executed': False, 'replayed': False, 'reason': 'workspace-cleanup-refused'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def text(value, name, maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise Refused('Bounded ' + name + ' required')
    return value


def path(value):
    value = Path(text(value, 'absolute path'))
    if not value.is_absolute() or '..' in value.parts:
        raise Refused('An absolute path without parent traversal is required')
    return checked(value)


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def open_directory(value):
    """Anchor every ancestor; O_NOFOLLOW on only the leaf is insufficient."""
    value = path(str(value))
    fd = os.open(value.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in value.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def inventory(parent, name, check):
    """Bounded metadata fingerprint including links, without reading file bodies."""
    rows = []
    total = 0
    def visit(fd, leaf, relative, depth=0):
        nonlocal total
        check()
        if depth > 128 or len(rows) >= 200_000:
            raise Refused('Folder exceeds the bounded cleanup inventory')
        before = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        mode = stat.S_IFMT(before.st_mode)
        if mode not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise Refused('Folder contains special files; cleanup is unavailable')
        if before.st_dev != root_device:
            raise Refused('Folder crosses a mounted filesystem; cleanup is unavailable')
        row = [relative, mode, before.st_dev, before.st_ino, before.st_size,
               before.st_mtime_ns, before.st_ctime_ns, before.st_nlink]
        # Renaming the selected root changes ctime but not its reviewed contents.
        if not relative:
            row[6] = None
        rows.append(row)
        if mode == stat.S_IFREG:
            total += before.st_size
        elif mode == stat.S_IFLNK:
            row.append(os.readlink(leaf, dir_fd=fd))
        else:
            child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise Refused('Folder changed during inventory')
                with os.scandir(child) as entries:
                    names = []
                    for entry in entries:
                        check()
                        if len(names) + len(rows) >= 200_000:
                            raise Refused('Folder exceeds the bounded cleanup inventory')
                        names.append(entry.name)
                for item in sorted(names):
                    visit(child, item, relative + '/' + item, depth + 1)
                after = os.fstat(child)
                if (opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                    raise Refused('Folder changed during inventory')
            finally:
                os.close(child)
        after = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Refused('Folder changed during inventory')
    root = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(root.st_mode):
        raise Refused('Only an existing registered folder can be removed')
    root_device = root.st_dev
    visit(parent, name, '')
    return {'sha256': digest(rows), 'entries': len(rows), 'bytes': total,
            'device': root.st_dev, 'inode': root.st_ino}


class WorkspaceCleanup:
    def __init__(self, owner):
        self.owner = owner
        with owner.db(namespace='workspace-cleanup'):
            pass

    def selection(self, value):
        owner = self.owner
        require_admin(owner)
        if owner.config.get('maintenanceExternalWriters') != 'stopped':
            raise Refused('Workspace removal requires launcher-confirmed stopped external writers')
        if not isinstance(value, dict) or set(value) != {'workspaceId', 'path', 'revision', 'coverage', 'registeredPaths', 'protectedPaths', 'sessions'}:
            raise Refused('Complete trusted owner cleanup selection required')
        text(value['workspaceId'], 'workspace identity', 128)
        text(value['revision'], 'owner census revision', 200)
        if value['coverage'] != 'all-configured-owners-held':
            raise Refused('All configured owners must supply held reference coverage')
        target = path(value['path'])
        owner.admin.workspace(str(target))
        # Descendants of the OS home are normal workspaces. Protect the home
        # itself/ancestors and the native history root in both directions.
        if overlaps(target, owner.home) or target == Path.home() or target in Path.home().parents:
            raise Refused('Native history and home directories are protected')
        app_workspaces = owner.app / 'workspaces'
        if overlaps(target, owner.app) and not (app_workspaces in target.parents):
            raise Refused('Application state is protected')
        if target == Path(target.anchor) or (target / '.amplifier').exists() or (target / '.amplifier').is_symlink():
            raise Refused('Workspace-local Amplifier state is protected')
        for key in ('registeredPaths', 'protectedPaths'):
            rows = value[key]
            if not isinstance(rows, list) or len(rows) > 10_000:
                raise Refused('Bounded complete owner references required')
            for item in rows:
                candidate = path(item)
                if key == 'registeredPaths' and candidate == target:
                    continue
                if overlaps(target, candidate):
                    raise Refused('Another workspace, conversation, or retained file overlaps this folder')
        if value['registeredPaths'].count(str(target)) != 1:
            raise Refused('Exactly one registered workspace must own this folder')
        sessions = value['sessions']
        if not isinstance(sessions, list) or len(sessions) > 10_000:
            raise Refused('Bounded complete native history selection required')
        from .native.session_files import validate_id, project_slug
        from .session_lifecycle import regular
        # The derived catalog may lag an idle CLI-created chat. Include every
        # physical history in this exact workspace namespace before taking its
        # writer leases. Discovery stays bounded and never reads transcript bodies.
        sessions = json.loads(json.dumps(sessions))
        namespace = checked(owner.home / 'projects' / project_slug(str(target)) / 'sessions')
        if namespace.exists():
            with os.scandir(namespace) as entries:
                examined = 0
                for entry in entries:
                    examined += 1
                    if examined > 10_000:
                        raise Refused('Selected native history namespace exceeds capacity')
                    if entry.is_symlink():
                        raise Refused('Linked native history cannot establish cleanup ownership')
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    sid = validate_id(entry.name)
                    row = {'nativeSessionId': sid, 'cwd': str(target)}
                    if row not in sessions:
                        sessions.append(row)
            if len(sessions) > 10_000:
                raise Refused('Combined native history selection exceeds capacity')
        seen = set()
        for row in sessions:
            if not isinstance(row, dict) or set(row) != {'nativeSessionId', 'cwd'}:
                raise Refused('Exact native history identity required')
            sid = validate_id(row['nativeSessionId'])
            cwd = path(row['cwd'])
            key = (str(cwd), sid)
            if key in seen:
                raise Refused('Duplicate native history identity')
            seen.add(key)
            directory = checked(owner.home / 'projects' / project_slug(str(cwd)) / 'sessions' / sid)
            if overlaps(target, directory) or not directory.is_dir():
                raise Refused('Every selected native history must exist outside the folder')
            metadata = json.loads(regular(directory / 'metadata.json'))
            if metadata.get('session_id', sid) != sid:
                raise Refused('Native history identity disagrees with the owner selection')
        return {**json.loads(json.dumps(value)), 'sessions': sorted(sessions, key=lambda row: (row['cwd'], row['nativeSessionId']))}

    def check(self):
        deadline = time.monotonic() + 120
        authority = require_admin(self.owner)
        def check():
            self.owner.ensure_open()
            if time.monotonic() > deadline or require_admin(self.owner) != authority:
                raise Refused('Cleanup review expired or maintenance ownership changed')
        return check

    def preview(self, workspace, args):
        if args.get('reviewed') is not True:
            raise Refused('Review permanent folder removal first')
        selection = self.selection(args.get('selection'))
        with self.owner.db() as db:
            pending = db.execute("SELECT command FROM workspace_cleanup_attempts WHERE workspace_id=? AND state!='completed'", (selection['workspaceId'],)).fetchone()
        if pending:
            raise Refused('An earlier cleanup is unresolved; inspect its original receipt without replay')
        with Gate(self.owner.config, exclusive=True):
            target = Path(selection['path'])
            fd = open_directory(target.parent)
            try:
                observed = inventory(fd, target.name, self.check())
            finally:
                os.close(fd)
        body = {'selection': selection, 'inventory': observed, 'createdAt': time.time(),
                'expiresAt': time.time() + 600, 'nonce': uuid.uuid4().hex}
        identity = digest(body)
        with self.owner.db() as db:
            db.execute('INSERT INTO workspace_cleanup_previews VALUES(?,?,?)', (identity, workspace, json.dumps(body)))
        return {'previewHash': identity, 'workspaceId': selection['workspaceId'],
                'confirmationPath': selection['path'], 'inventory': observed,
                'expiresAt': body['expiresAt'], 'historyPreserved': True,
                'permanent': True, 'includesUncommittedWork': True, 'replayed': False}

    def remove(self, workspace, args):
        changed = False
        try:
            selection = self.selection(args.get('selection'))
            identity = text(args.get('previewHash'), 'preview hash', 64)
            with self.owner.db() as db:
                row = db.execute('SELECT workspace,body FROM workspace_cleanup_previews WHERE hash=?', (identity,)).fetchone()
            if not row or row[0] != workspace:
                raise Refused('Exact account-owned cleanup review required')
            body = json.loads(row[1])
            if digest(body) != identity or body['selection'] != selection or time.time() >= body['expiresAt']:
                raise Refused('Folder review or owner references changed; review again')
            if args.get('confirmationPath') != selection['path']:
                raise Refused('Type the exact reviewed folder path to confirm removal')
            target = Path(selection['path'])
            command = text(args.get('commandId'), 'original command identity', 200)
            quarantine = '.unified-cleanup-' + uuid.uuid4().hex
            with ExitStack() as stack:
                stack.enter_context(Gate(self.owner.config, exclusive=True))
                from amplifier_foundation.session.shared_state import SharedSessionStore
                for session in sorted(selection['sessions'], key=lambda row: (row['cwd'], row['nativeSessionId'])):
                    lease = SharedSessionStore(session['cwd'], session['nativeSessionId']).acquire(app='amplifier-acp-workspace-cleanup', pid=os.getpid())
                    stack.callback(lease.release)
                check = self.check()
                fd = open_directory(target.parent)
                stack.callback(os.close, fd)
                if inventory(fd, target.name, check) != body['inventory']:
                    raise Refused('Folder contents changed after review')
                with self.owner.db() as db:
                    pending = db.execute("SELECT command FROM workspace_cleanup_attempts WHERE workspace_id=? AND state!='completed'", (selection['workspaceId'],)).fetchone()
                    if pending:
                        raise Refused('An original cleanup is unresolved; it cannot be retried')
                    db.execute('INSERT INTO workspace_cleanup_attempts VALUES(?,?,?,?,?)',
                               (command, selection['workspaceId'], identity, 'attempting', str(target.parent / quarantine)))
                # After this durable point all failures are uncertain. The product
                # retains its intake fence and inspects this command, never retries.
                changed = True
                check()
                os.rename(target.name, quarantine, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                if inventory(fd, quarantine, check) != body['inventory']:
                    raise RuntimeError('Quarantined folder differs from the reviewed inventory; preserved for inspection')
                shutil.rmtree(quarantine, dir_fd=fd)
                os.fsync(fd)
                with self.owner.db() as db:
                    db.execute("UPDATE workspace_cleanup_attempts SET state='completed' WHERE command=?", (command,))
            return {'workspaceId': selection['workspaceId'], 'previewHash': identity,
                    'removed': True, 'removedBytes': body['inventory']['bytes'],
                    'canonicalFilesChanged': 0, 'historyPreserved': True, 'replayed': False}
        except Exception as error:
            if changed:
                raise RuntimeError('Workspace cleanup outcome needs inspection; do not replay') from error
            if isinstance(error, Refused):
                raise
            raise Refused(str(error)) from None
