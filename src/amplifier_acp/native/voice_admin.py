"""Server-only voice key selection over the existing settings and key-file owner."""
import os
from .host.config import _load_keys
PRIVATE_KEY='AMPLIFIER_VOICE_API_KEY'

def perform(setup,workspace,operation,args,*,preferences_access=False):
    _load_keys(setup.store.shared_home/'keys.env')
    if operation=='voice.configure':
        if set(args)-{'source','apiKey'} or args.get('source') not in {'environment','private'}:raise ValueError('Choose environment or private voice credentials.')
        value=args.get('apiKey','')
        if not isinstance(value,str) or len(value)>4000 or any(c in value for c in '\n\r\0'):raise ValueError('A bounded single-line voice API key is required.')
        value=value.strip();source=args['source']
        if source=='environment' and not os.environ.get('OPENAI_API_KEY'):raise ValueError('No OPENAI_API_KEY is available to this host.')
        if source=='private' and not value and not os.environ.get(PRIVATE_KEY):raise ValueError('Enter an OpenAI API key.')
        if source=='private' and value:setup._keys({PRIVATE_KEY:value})
        setup.store.update(workspace,'global',lambda settings:settings.setdefault('voice',{}).update(credential_source=source))
    elif args:raise ValueError('Voice configuration and credential reads take no client arguments.')
    if operation=='voice.credential':
        source=setup.store.read(workspace).get('voice',{}).get('credential_source','environment')
        if source not in {'environment','private'}:raise ValueError('Invalid saved voice credential source; settings were preserved.')
        key=os.environ.get(PRIVATE_KEY if source=='private' else 'OPENAI_API_KEY')
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
    key=os.environ.get(PRIVATE_KEY if source=='private' else 'OPENAI_API_KEY')
    return {**result,'available':bool(key),'environmentAvailable':bool(os.environ.get('OPENAI_API_KEY')),'privateKeyAvailable':bool(os.environ.get(PRIVATE_KEY)),'reason':None if key else 'Add an OpenAI API key in Voice settings to enable calls and previews.'}
