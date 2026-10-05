"""Server-only voice key selection over the existing settings and key-file owner."""
import os
import re
from .host.config import _load_keys
PRIVATE_KEY='AMPLIFIER_VOICE_API_KEY'

def environment_variable(settings):
    """Select only the canonical key or one configured global OpenAI reference."""
    if os.environ.get('OPENAI_API_KEY'):return 'OPENAI_API_KEY'
    disabled=settings.get('configurator',{}).get('disabled',{}).get('providers',[])
    overrides=settings.get('overrides',{})
    references=set()
    for row in settings.get('config',{}).get('providers',[]):
        if row.get('module')!='provider-openai':continue
        identity=row.get('id') or row.get('instance_id') or 'openai'
        if row.get('enabled') is False or identity in disabled or overrides.get(identity,{}).get('enabled') is False:continue
        config=row.get('config',{})
        if any(config.get(key) is not None and (not isinstance(config[key],str) or config[key].rstrip('/')!='https://api.openai.com/v1') for key in ('base_url','api_base','endpoint')):continue
        reference=config.get('api_key')
        match=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]{0,199})\}',reference) if isinstance(reference,str) else None
        if match:references.add(match[1])
    return next(iter(references)) if len(references)==1 else None

def perform(setup,workspace,operation,args,*,preferences_access=False):
    _load_keys(setup.store.shared_home/'keys.env')
    if operation=='voice.configure':
        if set(args)-{'source','apiKey'} or args.get('source') not in {'environment','private'}:raise ValueError('Choose environment or private voice credentials.')
        value=args.get('apiKey','')
        if not isinstance(value,str) or len(value)>4000 or any(c in value for c in '\n\r\0'):raise ValueError('A bounded single-line voice API key is required.')
        value=value.strip();source=args['source']
        if source=='environment' and not os.environ.get(environment_variable(setup.store.read(workspace)) or ''):raise ValueError('No unambiguous configured OpenAI key is available to this host.')
        if source=='private' and not value and not os.environ.get(PRIVATE_KEY):raise ValueError('Enter an OpenAI API key.')
        if source=='private' and value:setup._keys({PRIVATE_KEY:value})
        setup.store.update(workspace,'global',lambda settings:settings.setdefault('voice',{}).update(credential_source=source))
    elif args:raise ValueError('Voice configuration and credential reads take no client arguments.')
    if operation=='voice.credential':
        settings=setup.store.read(workspace)
        source=settings.get('voice',{}).get('credential_source','environment')
        if source not in {'environment','private'}:raise ValueError('Invalid saved voice credential source; settings were preserved.')
        key=os.environ.get(PRIVATE_KEY if source=='private' else environment_variable(settings) or '')
        return {'apiKey':key or None,'source':source,'available':bool(key)}
    return configuration(setup.store,workspace,credential_access=True,preferences_access=preferences_access)


def configuration(store,workspace,*,credential_access,preferences_access=False):
    """Passive settings metadata; unavailable authorization never inspects secrets."""
    settings=store.read(workspace)
    voice=settings.get('voice',{})
    source=voice.get('credential_source','environment')
    if source not in {'environment','private'}:raise ValueError('Invalid saved voice credential source; settings were preserved.')
    from ..voice_preferences import projection
    result={**projection(settings,preferences_access),'source':source,'credentialAccess':'enabled' if credential_access else 'disabled','configurable':credential_access}
    if not credential_access:
        return {**result,'available':False,'environmentAvailable':None,'privateKeyAvailable':None,'reason':'Voice credential access is disabled for this installation.'}
    _load_keys(store.shared_home/'keys.env')
    variable=environment_variable(settings)
    key=os.environ.get(PRIVATE_KEY if source=='private' else variable or '')
    return {**result,'available':bool(key),'environmentAvailable':bool(os.environ.get(variable or '')),'environmentVariable':variable,'privateKeyAvailable':bool(os.environ.get(PRIVATE_KEY)),'reason':None if key else 'Choose an existing OpenAI connection or add a private key in Voice settings to enable calls and previews.'}
