"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import json


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


def foundation_home(home):
    home = Path(home)
    identity = active_release(home).get('current')
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'
