"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import os


import re


from .host.config import credential_environment_source, expand_environment, load_config, write_private


ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


PROVIDER_ENV = {
    'provider-openai': ('OPENAI_API_KEY',),
    'provider-azure-openai': ('AZURE_OPENAI_API_KEY',),
    'provider-chat-completions': ('CHAT_COMPLETIONS_API_KEY',),
    'provider-anthropic': ('ANTHROPIC_API_KEY',),
    'provider-gemini': ('GOOGLE_API_KEY','GEMINI_API_KEY'),
    'provider-github-copilot': ('GITHUB_TOKEN','COPILOT_AGENT_TOKEN','COPILOT_GITHUB_TOKEN','GH_TOKEN'),
}


def credential_field(module):
    return 'github_token' if module=='provider-github-copilot' else 'api_key'


def key_preview(module, value, env_name, *, configured=False):
    """Identify the service's configured key, never serialize a complete secret.

    This is configuration for a new provider, not a claim about an already
    mounted conversation or the validity of the credential at its issuer.
    """
    if module == 'provider-openai-chatgpt':
        return None  # OAuth access/refresh tokens are not API keys.
    # Display the mounted provider's fallback order, which can differ from its
    # preferred setup variable. Explicit Copilot config is promoted by Unified
    # into COPILOT_AGENT_TOKEN before mounting. Explicit API keys take
    # precedence over ambient defaults, including Compatible API.
    if configured:
        if not value:
            defaults = {
                'provider-github-copilot': ('COPILOT_AGENT_TOKEN', 'COPILOT_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN'),
                'provider-azure-openai': ('AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_KEY'),
                'provider-ollama': ('OLLAMA_API_KEY',),
                'provider-vllm': ('VLLM_API_KEY',),
            }.get(module, PROVIDER_ENV.get(module, ()))
            env_name = next((name for name in defaults if os.environ.get(name)), env_name)
    source = 'configuration'
    reference = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', value) if isinstance(value, str) else None
    if reference:
        env_name = reference.group(1)
    elif value:
        env_name = ''
    if env_name:
        source = credential_environment_source(env_name)
        value = os.environ.get(env_name)
    elif isinstance(value, str):
        try:
            value = expand_environment(value)
        except ValueError:
            value = None
    elif not value:
        return {'status': 'provider-managed', 'source': 'provider-managed', 'masked': None, 'envVar': ''}
    available = isinstance(value, str) and bool(value)
    # Short or unusual credentials get no characters exposed. Never include
    # length, a reusable digest, or a reveal/copy-full-key path in public state.
    masked = None
    if available:
        masked = value[:6] + '…' + value[-4:] if len(value) >= 16 and value.isascii() and value.isprintable() else '••••'
    return {'status': 'available' if available else 'missing', 'source': source,
            'masked': masked, 'envVar': env_name}


def environment_credential(module,raw=None,env_var=None):
    configured=raw is not None and env_var is None
    raw=raw or {}
    defaults=PROVIDER_ENV.get(module,())
    value=raw.get(credential_field(module))
    reference=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value) if isinstance(value,str) else None
    if env_var is not None and (not isinstance(env_var,str) or not ENV_NAME.fullmatch(env_var)):
        raise ValueError('Use an environment variable name containing letters, numbers and underscores, starting with a letter or underscore.')
    chosen=env_var or (reference.group(1) if reference else next((name for name in defaults if os.environ.get(name)),defaults[0] if defaults else ''))
    return {'module':module,'field':credential_field(module),'defaultEnvVar':defaults[0] if defaults else '',
            'alternatives':list(defaults[1:]),'envVar':chosen,'available':bool(chosen and os.environ.get(chosen)),
            'explicit':bool(reference),'hasStoredKey':bool(value and not reference),
            'supported':module!='provider-openai-chatgpt',
            'preview':key_preview(module,value,chosen,configured=configured)}
