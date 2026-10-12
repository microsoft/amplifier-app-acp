"""Passive optional Native image setup contract; no runtime or provider imports."""
CAPABILITY = {'version': 1, 'catalog': 'provider-image-models', 'preservesChat': True, 'exclusive': True}
PREFERENCES_CAPABILITY = {'version': 1, 'modes': ['automatic', 'selected', 'off'], 'application': 'new-sessions'}
OPERATIONS = {'providers.imageModels', 'providers.configureImages'}

def validate(operation, args):
    allowed = {'id', 'scope', 'workspace', 'refresh'} if operation == 'providers.imageModels' else {'id', 'scope', 'workspace', 'refresh', 'enabled', 'model', 'exclusive', 'mode'}
    if set(args) - allowed:
        raise ValueError('Unexpected image setup selector.')
    preference = operation == 'providers.configureImages' and 'mode' in args
    if preference and (args['mode'] not in ('automatic', 'off') or any(key in args for key in ('id', 'enabled', 'model', 'exclusive', 'refresh'))):
        raise ValueError('Choose an automatic image preference or a specific connection, not both.')
    if not preference and (not isinstance(args.get('id'), str) or not 1 <= len(args['id']) <= 200):
        raise ValueError('A saved image connection is required.')
    if args.get('scope', 'global') not in ('global', 'project', 'local'):
        raise ValueError('Invalid image settings scope.')
    for key in ('refresh', 'exclusive'):
        if key in args and not isinstance(args[key], bool):
            raise ValueError('Invalid image setup option.')
    if operation == 'providers.configureImages' and not preference:
        if not isinstance(args.get('enabled'), bool):
            raise ValueError('Choose whether to enable image generation.')
        if 'model' in args and (not isinstance(args['model'], str) or len(args['model']) > 400):
            raise ValueError('Choose a valid image model.')
