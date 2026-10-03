"""Bounded public provider discovery for an exact, uncreated managed draft."""
from __future__ import annotations
import base64
import inspect
import json
from pathlib import Path

MAX_MODELS = 1000
MAX_BYTES = 524288


class CatalogUnavailable(ValueError):
    def __init__(self, reason):
        allowed = {'provider-discovery-failed', 'provider-instance-unavailable',
                   'model-catalog-oversized', 'model-catalog-invalid',
                   'model-catalog-changed', 'model-cursor-invalid'}
        self.reason = reason if reason in allowed else 'provider-discovery-failed'
        self.data = {'executed': False, 'reason': self.reason, 'runtimeStarted': False}
        super().__init__('Selected bundle model discovery is unavailable: ' + self.reason)


async def discover(provider, instance, module):
    """Call only the standard metadata API, never a completion or session mount."""
    from .provider_probe import public
    method = getattr(provider, 'list_models', None)
    if not callable(method):
        return {'instance': instance, 'module': module, 'modelsSupported': False, 'models': []}
    rows = method()
    if inspect.isawaitable(rows):
        rows = await rows
    if not isinstance(rows, (list, tuple)):
        raise CatalogUnavailable('model-catalog-invalid')
    if len(rows) > MAX_MODELS:
        raise CatalogUnavailable('model-catalog-oversized')
    models, seen, size = [], set(), 0
    for raw in rows:
        row = public(raw)
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not 1 <= len(row['id']) <= 500 or row['id'] in seen:
            raise CatalogUnavailable('model-catalog-invalid')
        # Preserve public ModelInfo fields only. Provider config and arbitrary
        # vendor objects never enter this response or its cursor identity.
        row = {key: value for key, value in row.items() if key in {
            'id', 'display_name', 'context_window', 'max_output_tokens', 'capabilities', 'defaults'}}
        # Model defaults are extensible in Core; presentation needs this small
        # configuration subset, not arbitrary provider/private metadata.
        if 'defaults' in row:
            if not isinstance(row['defaults'], dict):
                raise CatalogUnavailable('model-catalog-invalid')
            row['defaults'] = {key: value for key, value in row['defaults'].items() if key in {
                'model', 'max_tokens', 'temperature', 'reasoning_effort', 'thinking_budget'}}
        try:
            size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
        except (TypeError, ValueError):
            raise CatalogUnavailable('model-catalog-invalid') from None
        if size > MAX_BYTES - 8192:
            raise CatalogUnavailable('model-catalog-oversized')
        models.append(row)
        seen.add(row['id'])
    models.sort(key=lambda row: row['id'])
    return {'instance': instance, 'module': module, 'modelsSupported': True, 'models': models}


def page(result, scope, limit, cursor=None):
    from ..managed_session import digest, encoded
    revision = digest([scope, result['modelsSupported'], result['models']])
    offset = 0
    if cursor is not None:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')))
            if set(value) != {'version', 'scope', 'revision', 'offset'} or value['version'] != 1 or value['scope'] != digest(scope) or type(value['offset']) is not int or not 0 < value['offset'] < len(result['models']):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CatalogUnavailable('model-cursor-invalid') from None
        if value['revision'] != revision:
            raise CatalogUnavailable('model-catalog-changed')
        offset = value['offset']
    end = min(offset + limit, len(result['models']))
    answer = {**result, 'models': result['models'][offset:end], 'revision': revision,
              'total': len(result['models']), 'limit': limit}
    if end < len(result['models']):
        answer['nextCursor'] = base64.urlsafe_b64encode(encoded({
            'version': 1, 'scope': digest(scope), 'revision': revision, 'offset': end})).decode('ascii')
    return answer


async def resolve(config, params):
    from ..managed_session import Refused, app_home, bounded, bundle, digest
    from .draft_defaults import resolve_defaults
    if set(params) - {'operation', 'bundle', 'bundleRevision', 'instance', 'limit', 'cursor'}:
        raise Refused('Selected managed model discovery accepts only exact choices and bounded pagination')
    selected = bundle(config, params)
    instance = bounded(params.get('instance'), 'provider instance', 200)
    limit = params.get('limit', 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise Refused('Model page limit must be 1 through 100')
    cursor = params.get('cursor')
    if cursor is not None:
        bounded(cursor, 'model page cursor', 1024)
    result = await resolve_defaults(app_home(config), str(Path(config['home']).resolve()),
                                    selected, global_only=True, model_instance=instance)
    for key in ('bundle', 'modelInstance', 'globalOnly'):
        result.pop(key, None)
    choice = ({'kind': 'configured', 'id': params['bundle'], 'revision': params['bundleRevision']}
              if 'bundle' in params else {'kind': 'engine-default'})
    answer = page(result, [choice, digest(selected), instance], limit, cursor)
    return {**answer, 'bundleSelection': choice, 'globalOnly': True,
            'configurationScope': 'global-and-session', 'runtimeStarted': False}
