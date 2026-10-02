"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import os


from pathlib import Path


import re


def amplifier_home():
    return Path(os.environ.get('AMPLIFIER_HOME') or Path.home() / '.amplifier').expanduser().resolve()


def project_slug(workspace):
    # Exact app-cli convention: spaces, periods and underscores are significant.
    slug = str(Path(workspace).expanduser().resolve()).replace('/', '-').replace('\\', '-').replace(':', '')
    return slug if slug.startswith('-') else '-' + slug


def validate_id(identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,220}', identity):
        raise ValueError('Invalid session identifier')
    return identity


def sessions_dir(workspace):
    return amplifier_home() / 'projects' / project_slug(workspace) / 'sessions'


def capture_sessions_dir(workspace):
    # Match CI's supported relocation setting; transcript paths do not relocate.
    root = Path(os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH') or amplifier_home() / 'projects').expanduser().resolve()
    return root / project_slug(workspace) / 'sessions'


def capture_dir(workspace, identity):
    return capture_sessions_dir(workspace) / validate_id(identity) / 'context-intelligence'
