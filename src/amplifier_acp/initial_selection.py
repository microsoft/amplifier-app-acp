"""Validate a typed initial pin before allocating or starting a conversation."""
from __future__ import annotations

import json

CAPABILITY = {'version': 1, 'typedSelection': True, 'passive': True}


class Refused(ValueError):
    def __init__(self, message):
        self.data = {'executed': False, 'runtimeStarted': False,
                     'reason': 'initial-selection-refused'}
        super().__init__(message)


def requested(value, *, supported, negotiated, new):
    if value is None or value == {}:
        return None
    if not supported or not negotiated or not new:
        raise Refused('Initial selection requires negotiated support on session/new.')
    from .managed_session import selection, Refused as Invalid
    try:return dict(selection(value))
    except Invalid as exc:raise Refused(str(exc)) from None


def validate_catalog(selection, catalog, schema):
    """One provider metadata observation; no completion, login or session mount."""
    from .configuration_options import model_rows, effort_choices
    rows = [row for row in catalog['models'] if row['id'] == selection['model']]
    if not catalog['modelsSupported'] or len(rows) != 1:
        return {'selectionRefused': True}
    model = model_rows(rows)[selection['model']]
    efforts, _ = effort_choices({'configSchema': schema}, model)
    if 'effort' in selection and selection['effort'] not in efforts:
        return {'selectionRefused': True}
    return {'validatedSelection': dict(selection)}


async def validate(config, cwd, selection):
    from .managed_session import app_home
    from .native.draft_defaults import resolve_defaults
    from .worker import selected_bundle
    from .native.managed_models import CatalogUnavailable
    try:
        bundle = selected_bundle(config)
        result = await resolve_defaults(app_home(config), cwd, bundle,
                                        model_instance=selection['instance'],
                                        initial_selection=selection,
                                        registry_home=config.get('registryHome'))
    except CatalogUnavailable:
        raise
    except Exception:
        # No conversation identity or state exists at this point. Keep internal
        # preparation details private while preserving the no-start boundary.
        raise Refused('Initial selection could not be validated; no session was started.') from None
    if result.get('validatedSelection') != selection:
        raise Refused('Initial model or reasoning choice is not advertised by this provider.')
    return result['bundle']


def persist(config, identity, selection):
    from .managed_session import app_home, checked, write
    directory = checked(app_home(config) / 'sessions' / identity)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(directory / 'control-state.json', {'selection': selection})


def confirmed(config, session, selection):
    from .managed_session import app_home
    saved = json.loads((app_home(config) / 'sessions' / session.identity / 'control-state.json').read_text())
    if saved.get('selection') != selection or session.provider_state.get('selection') != selection:
        # Starting may already have written canonical state. Never classify this
        # post-start mismatch as a pre-effect refusal or retry it automatically.
        raise ValueError('Initial selection was not confirmed by the mounted runtime; inspect original session state.')
    return {'version': 1, 'selection': dict(selection)}
