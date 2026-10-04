"""Standard ACP choices from owner-projected provider metadata, never a client draft."""
from __future__ import annotations

import hashlib
import json
import re

META = 'amplifier.dev/native'
DEFAULT = '__default__'
MAX_PROVIDERS = 64
MAX_MODELS = 512
MAX_OPTIONS = 4096


def token(kind, *parts):
    return kind + ':' + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def text(value, limit=512):
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def model_rows(value):
    """Bound projection; never silently truncate an advertised catalog."""
    if not isinstance(value, list) or len(value) > MAX_MODELS:
        raise ValueError('Provider model catalog exceeds the standard configuration bound')
    rows = {}
    for row in value:
        if isinstance(row, str):row = {'id': row}
        if not isinstance(row, dict) or not text(row.get('id')) or row['id'] in rows:
            raise ValueError('Provider model catalog requires unique bounded model identities')
        result = {'id': row['id'], 'display_name': text(row.get('display_name')) or row['id']}
        for key in ('context_window', 'max_output_tokens'):
            if type(row.get(key)) is int and 0 < row[key] <= 2**53:result[key] = row[key]
        capabilities = row.get('capabilities', [])
        if isinstance(capabilities, list) and len(capabilities) <= 64 and all(text(v) for v in capabilities):
            result['capabilities'] = list(capabilities)
        defaults = row.get('defaults') or {}
        if isinstance(defaults, dict):
            result['defaults'] = {key: defaults[key] for key in ('reasoning_effort',)
                                  if text(defaults.get(key), 128)}
            levels = defaults.get('supported_reasoning_efforts')
            if isinstance(levels, list) and len(levels) <= 32 and len(set(v for v in levels if isinstance(v,str))) == len(levels) and all(text(v,128) for v in levels):
                result['defaults']['supported_reasoning_efforts'] = list(levels)
            elif 'supported_reasoning_efforts' in defaults:
                raise ValueError('Provider model reasoning choices are invalid')
        rows[row['id']] = result
    return rows


def effort_choices(provider, model):
    """Model-specific public choices override provider ConfigField declarations."""
    defaults = model.get('defaults') or {}
    if 'supported_reasoning_efforts' in defaults:
        return defaults['supported_reasoning_efforts'], 'model.defaults'
    fields = (provider.get('configSchema') or {}).get('fields')
    if not isinstance(fields, list):fields = (provider.get('info') or {}).get('config_fields', [])
    if not isinstance(fields, list):return [], None
    for field in fields[:128]:
        if not isinstance(field, dict) or field.get('id') != 'reasoning_effort' or field.get('field_type') != 'choice':continue
        conditions = field.get('show_when') or {}
        if not isinstance(conditions, dict) or len(conditions) > 8:continue
        matches = True
        for key, expected in conditions.items():
            # Unknown non-model requirements cannot be proven from public metadata.
            if key not in {'model', 'default_model'} or not text(expected):matches=False;break
            actual, value = model['id'].lower(), expected.lower()
            if value.startswith('matches:'):
                try:matched = re.search(expected[8:], model['id'], re.IGNORECASE) is not None
                except re.error:matched = False
            else:
                matched = actual == value
                for prefix, test in (('not_contains:', lambda v: v not in actual), ('contains:', lambda v: v in actual),
                                     ('not_startswith:', lambda v: not actual.startswith(v)), ('startswith:', actual.startswith)):
                    if value.startswith(prefix):matched=test(value[len(prefix):]);break
            if not matched:matches=False;break
        choices=field.get('choices')
        if matches and isinstance(choices,list) and len(choices)<=32 and all(text(v,128) for v in choices) and len(set(choices))==len(choices):
            return choices, 'provider.configSchema'
    return [], None


def project(state):
    providers=state.get('providers', [])
    if not isinstance(providers,list) or len(providers)>MAX_PROVIDERS:raise ValueError('Provider configuration exceeds the standard option bound')
    effective=state.get('selection') or state.get('effective') or {}
    selected_instance, selected_model=effective.get('instance'), effective.get('model')
    groups=[]; selections={}; effort_map={}; selected_row=None; selected_provider=None; instances=set()
    for provider in providers:
        instance=provider.get('id')
        if not text(instance) or instance in instances:raise ValueError('Provider configuration requires unique bounded instance identities')
        instances.add(instance)
        info=provider.get('info') or {}; catalog=provider.get('modelCatalog') or {}; models=model_rows(catalog.get('models',[]))
        sources={key:'catalog' for key in models}
        # Configured defaults remain useful before an explicit catalog read, but
        # are never described as proof of current provider/account availability.
        configured=(info.get('defaults') or {}).get('model') or (info.get('defaults') or {}).get('default_model')
        if catalog.get('state','not-loaded')=='not-loaded' and text(configured):
            models.setdefault(configured,{'id':configured,'display_name':configured});sources.setdefault(configured,'configured')
        if instance==selected_instance and text(selected_model):
            models.setdefault(selected_model,{'id':selected_model,'display_name':selected_model});sources.setdefault(selected_model,'effective')
        choices=[]
        for model_id,model in models.items():
            value=token('model',instance,model_id)
            selections[value]={'instance':instance,'model':model_id}
            source=sources[model_id]
            choices.append({'value':value,'name':model['display_name'],
                'description':{'catalog':'Advertised by this provider; availability is checked when used.','configured':'Configured default; model discovery has not checked this choice.','effective':'Current effective model; not present in the available catalog.'}[source],
                '_meta':{META:{'version':1,'instance':instance,'model':model_id,'source':source,'modelInfo':model}}})
            if instance==selected_instance and model_id==selected_model:selected_row=model;selected_provider=provider
        if len(selections)>MAX_OPTIONS:raise ValueError('Model configuration exceeds the standard option bound')
        groups.append({'group':instance,'name':text(info.get('display_name')) or instance,'options':choices,
            '_meta':{META:{'version':1,'instance':instance,'catalogState':catalog.get('state','not-loaded'),'catalogFresh':catalog.get('fresh') is True}}})
    if not selections:return [], {}, {}
    groups.insert(0,{'group':'','name':'Selection policy','options':[{'value':DEFAULT,'name':'Bundle default',
        'description':'Clear the explicit model and reasoning selection; use the bundle policy.'}]})
    current=token('model',selected_instance,selected_model) if state.get('pinned') else DEFAULT
    model_option={'id':'model','name':'Model','category':'model','type':'select','currentValue':current,'options':groups,
        'description':'Models are grouped by configured provider instance. Select one model to apply its provider and model together.',
        '_meta':{META:{'version':1,'groupedProviderModels':True,'effective':{k:effective[k] for k in ('instance','model','effort') if text(effective.get(k))}}}}
    options=[model_option]
    if selected_row and selected_provider:
        efforts, source=effort_choices(selected_provider,selected_row)
        if efforts:
            effort_map={token('effort',selected_instance,selected_model,v):{**selections[token('model',selected_instance,selected_model)],'effort':v} for v in efforts}
            chosen=effective.get('effort'); current=token('effort',selected_instance,selected_model,chosen) if text(chosen,128) else DEFAULT
            if text(chosen,128) and chosen not in efforts:
                effort_map[current]={**selections[token('model',selected_instance,selected_model)],'effort':chosen}
            options.append({'id':'reasoning_effort','name':'Reasoning effort','category':'thought_level','type':'select','currentValue':current,
                'description':'Provider-advertised reasoning choices for the effective model. Default removes only the explicit effort override.',
                'options':[{'value':DEFAULT,'name':'Provider default'}]+[{'value':value,'name':selection['effort'],**({'description':'Current effective effort; not advertised for a new selection.'} if selection['effort'] not in efforts else {})} for value,selection in effort_map.items()],
                '_meta':{META:{'version':1,'instance':selected_instance,'model':selected_model,'source':source,
                    **({'effectiveEffort':chosen} if text(chosen) else {})}}})
    return options,selections,effort_map
