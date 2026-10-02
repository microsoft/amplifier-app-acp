"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import json
import os


from pathlib import Path


import re


def active_release(home):
    path = Path(home) / 'updates' / 'active.json'
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        value = {}
    for field in ('current','previous'):
        identity = value.get(field)
        if identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}', identity)):
            raise ValueError('Invalid ecosystem release identity')
    return value


def selected_release(home):
    # Workers capture their source generation at spawn; the serving host follows
    # the current pointer. Never change a running worker's resolver underneath it.
    selected = os.environ.get('AMPLIFIER_UNIFIED_RELEASE')
    identity = (selected or None) if selected is not None else active_release(home).get('current')
    if identity is not None and not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Invalid worker source generation')
    return identity

def foundation_home(home):
    home = Path(home)
    identity = selected_release(home)
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'
