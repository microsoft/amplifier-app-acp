"""Bounded current-source assistant references; never mounts or admits a turn."""
import json
import os
from pathlib import Path
import stat

from .history import HistoryError, content_updates, message_id, source, stamp, visible

CAPABILITY = {'version': 1, 'maxMessages': 12, 'maxScanBytes': 2097152,
              'maxRows': 512, 'previewChars': 512, 'execution': False}


def recent(directory, *, app_home=None, history_cwd=None):
    from .native.history_revision import context_head, guard_context_clear
    directory = Path(directory)
    if app_home is not None:
        guard_context_clear(app_home, directory.name)
    epoch = context_head(app_home, directory.name, history_cwd) if app_home is not None else None
    path, diagnostics = source(directory)
    if diagnostics:
        raise HistoryError('Recovered backup is not a current saved-message source')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise HistoryError('Saved-message source cannot traverse symbolic links')
    revision, size = stamp(path)
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    messages = []
    consumed = size
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise HistoryError('Saved-message source changed during open')
        start = max(0, size - CAPABILITY['maxScanBytes'])
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            stream.seek(start)
            data = stream.read(size - start)
        if len(data) != size - start:
            raise HistoryError('Saved-message source changed during read')
        # A trailing partial write has not yet committed a JSONL record.
        if data and not data.endswith(b'\n'):
            raise HistoryError('Saved-message source changed; a record is still being written')
        if start:
            split = data.find(b'\n')
            start += split + 1 if split >= 0 else len(data)
            data = data[split + 1:] if split >= 0 else b''
        position = size
        for count, line in enumerate(reversed(data.splitlines(keepends=True))):
            if count >= CAPABILITY['maxRows'] or len(messages) >= CAPABILITY['maxMessages']:
                break
            position -= len(line)
            consumed = position
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise HistoryError('Malformed saved-message source; originals preserved') from exc
            if not isinstance(row, dict) or not isinstance(row.get('role'), str):
                raise HistoryError('Malformed saved-message source; originals preserved')
            if row['role'] != 'assistant' or not visible(row) or (row.get('metadata') or {}).get('ephemeral') is True:
                continue
            updates, issues = content_updates(row, position)
            text = ''.join(update['content']['text'] for update in updates
                           if update.get('content', {}).get('type') == 'text')
            if issues or not text.strip():
                continue
            messages.append({'messageId': message_id(row, position, epoch), 'role': 'assistant',
                             'text': text[:CAPABILITY['previewChars']],
                             'truncated': len(text) > CAPABILITY['previewChars'], 'saved': True})
        if not data:
            consumed = start
    finally:
        os.close(fd)
    if stamp(path)[0] != revision or (app_home is not None and context_head(app_home, directory.name, history_cwd) != epoch):
        raise HistoryError('Saved-message source changed during capture')
    if app_home is not None:
        guard_context_clear(app_home, directory.name)
    return {'version': 1, 'selection': 'recent-saved-assistant', 'revision': revision,
            'messages': list(reversed(messages)), 'hasOlder': consumed > 0 or epoch is not None,
            'currentSegmentOnly': True, 'execution': False, 'mutationAuthority': 'none'}
