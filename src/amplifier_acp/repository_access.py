"""Trusted, process-local download policy; never edit personal Git settings."""
import os


def repository_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    if result.get('AMPLIFIER_GITHUB_ACCESS') != 'anonymous':
        return result
    for key in list(result):
        if key.startswith('GIT_CONFIG_') or key in {
            'GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN',
            'GIT_ASKPASS', 'SSH_ASKPASS',
        }:
            result.pop(key, None)
    result.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    settings = [('credential.helper', ''), ('http.extraHeader', ''),
                ('http.cookieFile', ''), ('core.askPass', '')]
    result['GIT_CONFIG_COUNT'] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        result[f'GIT_CONFIG_KEY_{index}'] = key
        result[f'GIT_CONFIG_VALUE_{index}'] = value
    return result
