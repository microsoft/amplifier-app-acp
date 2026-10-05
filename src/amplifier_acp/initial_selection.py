"""Validate a typed initial pin before allocating or starting a conversation."""
from __future__ import annotations

import json

CAPABILITY = {'version': 1, 'typedSelection': True, 'passive': True, 'resolveMethod': '_amplifier/initial-selection'}


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


def partial(value):
    from .managed_session import bounded, Refused as Invalid
    if not isinstance(value,dict) or set(value)-{'instance','model','effort'}:
        raise Refused('Initial selection draft requires only typed instance, model and effort fields.')
    try:
        return {key:bounded(item,key,{'instance':200,'model':500,'effort':100}[key])
                for key,item in value.items()}
    except Invalid as exc:raise Refused(str(exc)) from None


def options(draft, providers, catalog):
    """Native alone interprets model-specific and provider-conditional choices."""
    from .configuration_options import model_rows, effort_choices, text
    if not isinstance(providers,list) or not 1<=len(providers)<=64:
        raise Refused('Initial provider configuration is unavailable.')
    identities=[row.get('id') for row in providers]
    if any(not text(value,200) for value in identities) or len(set(identities))!=len(identities):
        raise Refused('Initial provider configuration is unavailable.')
    instance=draft.get('instance',identities[0])
    if instance not in identities or catalog['instance']!=instance:
        raise Refused('Initial provider instance is not configured.')
    provider=next(row for row in providers if row['id']==instance)
    models=catalog['models']
    if not catalog['modelsSupported'] or not models:
        raise Refused('Initial provider model discovery is unavailable.')
    model_ids=[row['id'] for row in models]
    defaults=provider.get('info',{}).get('defaults') or {}
    model=draft.get('model',defaults.get('model') or defaults.get('default_model'))
    if 'model' in draft and model not in model_ids:
        raise Refused('Initial model is not advertised by this provider.')
    values={'instance':instance}
    schema={'type':'object','additionalProperties':False,'required':['instance','model'],
            'properties':{'instance':{'type':'string','enum':identities},
                          'model':{'type':'string','enum':model_ids}}}
    efforts=[]
    if model in model_ids:
        values['model']=model
        row=model_rows([next(row for row in models if row['id']==model)])[model]
        efforts,_=effort_choices(provider,row)
        if any(not text(value,100) for value in efforts):raise Refused('Initial reasoning metadata exceeds the typed selection bound.')
        if efforts:schema['properties']['effort']={'type':'string','enum':efforts}
    if 'effort' in draft:
        if draft['effort'] not in efforts:
            raise Refused('Initial reasoning choice is not advertised for this model.')
        values['effort']=draft['effort']
    return {'version':1,'schema':schema,'values':values}


async def resolve(config, cwd, draft):
    from pathlib import Path
    from .managed_session import app_home
    from .native.draft_defaults import resolve_defaults
    from .worker import selected_bundle
    from .native.managed_models import CatalogUnavailable
    draft=partial(draft)
    if not isinstance(cwd,str) or not 1<=len(cwd)<=4096:
        raise Refused('Initial selection cwd must be an existing absolute directory.')
    workspace=Path(cwd).expanduser()
    if not workspace.is_absolute() or not workspace.is_dir():
        raise Refused('Initial selection cwd must be an existing absolute directory.')
    try:
        bundle=selected_bundle(config)
        result=await resolve_defaults(app_home(config),str(workspace.resolve()),bundle,
                                      initial_selection=draft,
                                      registry_home=config.get('registryHome'))
    except CatalogUnavailable:
        raise
    except Exception:
        raise Refused('Initial selection could not be validated; no session was started.') from None
    if result.get('selectionRefused'):
        raise Refused('Initial provider, model or reasoning choice is unavailable.')
    return result['initialOptions'],result['bundle']


async def validate(config, cwd, selection):
    response,bundle=await resolve(config,cwd,selection)
    if response['values']!=selection:
        raise Refused('Initial model or reasoning choice is not advertised by this provider.')
    return bundle


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
