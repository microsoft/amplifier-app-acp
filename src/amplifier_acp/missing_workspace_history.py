"""Exact passive history access when an ordinary workspace has disappeared.

This proves only a read scope. Session loading, effects and lifecycle writes keep
their existing-directory requirement. A colliding project slug is not identity.
"""
import os
import stat
from pathlib import Path
from .managed_session import read
from .maintenance_gate import checked

# Native metadata embeds bundle configuration, including for child sessions.
# Match the catalog's metadata admission; ownership receipts retain their own
# smaller bound in managed_session.read.
MAX_METADATA = 8 * 1024 * 1024


def validate(server, cwd, identity):
    path = Path(cwd)
    if not path.is_absolute() or str(path) != cwd or str(path.resolve()) != cwd:
        raise ValueError('Exact canonical history workspace required')
    checked(path)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('Missing-workspace history requires an absent directory')
    if any(path.is_relative_to(Path(root).resolve()) for root in server.config.get('managedSessionRoots', [])):
        raise ValueError('Managed history requires its existing allocation authority')
    directory = server.directory(cwd, identity)
    metadata = read(directory / 'metadata.json', MAX_METADATA)
    if not isinstance(metadata, dict):
        raise ValueError('Native metadata must be an object')
    if metadata.get('session_id') != identity or metadata.get('working_dir') != cwd or metadata.get('location', {}).get('kind') == 'managed':
        raise ValueError('Native metadata does not prove this exact history identity')
    # Only native history storage is opened, never the disappeared workspace.
    transcript = checked(directory / 'transcript.jsonl')
    fd = os.open(transcript, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Canonical transcript must be a regular file')
    finally:
        os.close(fd)
    return directory
