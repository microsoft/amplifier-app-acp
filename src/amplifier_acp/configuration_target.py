"""Explicit administration settings owner; cwd remains launcher-authorized."""
CAPABILITY = {'version': 1, 'globalOnly': True, 'workspaceBound': True, 'isolatedResults': True}
PREFIXES = ('providers.', 'routing.', 'modules.', 'sources.')

def validate(operation, args):
    if 'configurationTarget' not in args:
        return None
    target = args['configurationTarget']
    if not operation.startswith(PREFIXES) or not isinstance(target, dict):
        raise ValueError('Explicit configuration owner is not supported for this operation.')
    kind = target.get('kind')
    if kind == 'global' and set(target) == {'kind'}:
        scopes, default = {'global'}, 'global'
    elif kind == 'workspace' and set(target) == {'kind', 'workspaceId'} and isinstance(target['workspaceId'], str) and 1 <= len(target['workspaceId']) <= 200 and not any(ord(c) < 32 for c in target['workspaceId']):
        scopes, default = {'local', 'project'}, 'local'
    else:
        raise ValueError('Choose one explicit configuration owner.')
    if any(key in args for key in ('workspace', 'location', 'nativeProject', 'sessionId', 'session_id')):
        raise ValueError('Explicit configuration owner cannot be mixed with other selectors.')
    if args.get('scope', default) not in scopes:
        raise ValueError('Configuration scope does not belong to the selected owner.')
    return dict(target)
