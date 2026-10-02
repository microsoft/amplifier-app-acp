"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import os


from pathlib import Path


import tempfile


def write_file(path: Path, contents: str | bytes, *, mode: int = 0o600) -> None:
    """Atomically write an app-owned file with its intended permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents.encode() if isinstance(contents, str) else contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_private(path: Path, text: str | bytes) -> None:
    """Atomically write private configuration, never briefly exposing it."""
    write_file(path, text)
