"""Passive optional Native image setup contract; no runtime or provider imports."""
CAPABILITY = {'version': 1, 'catalog': 'provider-image-models', 'preservesChat': True, 'exclusive': True}
OPERATIONS = {'providers.imageModels', 'providers.configureImages'}

def validate(operation, args):
    allowed = {'id', 'scope', 'workspace', 'refresh'} if operation == 'providers.imageModels' else {'id', 'scope', 'workspace', 'refresh', 'enabled', 'model', 'exclusive'}
    if set(args) - allowed:
        raise ValueError('Unexpected image setup selector.')
    if not isinstance(args.get('id'), str) or not 1 <= len(args['id']) <= 200:
        raise ValueError('A saved image connection is required.')
    if args.get('scope', 'global') not in ('global', 'project', 'local'):
        raise ValueError('Invalid image settings scope.')
    for key in ('refresh', 'exclusive'):
        if key in args and not isinstance(args[key], bool):
            raise ValueError('Invalid image setup option.')
    if operation == 'providers.configureImages':
        if not isinstance(args.get('enabled'), bool):
            raise ValueError('Choose whether to enable image generation.')
        if 'model' in args and (not isinstance(args['model'], str) or len(args['model']) > 400):
            raise ValueError('Choose a valid image model.')
