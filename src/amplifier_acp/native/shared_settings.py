"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


from pathlib import Path


from .session_files import amplifier_home, project_slug, validate_id


def read_yaml(path):
    from amplifier_foundation.settings import read_yaml as read
    return read(path)


def settings_paths(workspace, *, shared_home=None, session_id=None, global_only=False):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    paths = {
        "global": root / "settings.yaml",
        "project": workspace / ".amplifier" / "settings.yaml",
        "local": workspace / ".amplifier" / "settings.local.yaml",
    }
    from .managed_chats import metadata
    if global_only or metadata(workspace):
        paths = {"global": paths["global"]}
    if session_id:
        paths["session"] = root / "projects" / project_slug(workspace) / "sessions" / validate_id(session_id) / "settings.yaml"
    return paths


def read_settings(workspace, *, shared_home=None, session_id=None, global_only=False, cache=None):
    from amplifier_foundation.settings import read_settings as read_scoped_settings
    paths = settings_paths(workspace, shared_home=shared_home, session_id=session_id, global_only=global_only).values()
    return cache.read(paths) if cache is not None else read_scoped_settings(paths)


def routing_dirs(workspace, *, shared_home=None, global_only=False):
    root = Path(shared_home or amplifier_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    from .managed_chats import metadata
    return [root / "routing"] if global_only or metadata(workspace) else [workspace / ".amplifier" / "routing.local", workspace / ".amplifier" / "routing", root / "routing"]


def overlay(base, patch):
    from amplifier_foundation.settings import overlay as merge
    return merge(base, patch)


def atomic_write(path, contents, *, private=False):
    from amplifier_foundation.settings import atomic_write as write
    return write(path, contents, private=private)


def update_settings(path, mutator):
    from amplifier_foundation.settings import update_settings as update
    return update(path, mutator)
