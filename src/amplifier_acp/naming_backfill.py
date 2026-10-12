"""Cold, bounded naming for discovered chats. Never mounts a conversation."""
import asyncio
import hashlib
import json
from pathlib import Path

from amplifier_foundation.session.metadata import SessionMetadataStore
from . import history
from .chat_title_preview import read as excerpt
from .native.naming import automatic_metadata, accept_generated, read as naming_metadata, initial_name
from .session_lifecycle import guard_start, lifecycle_state


def eligible(owner):
    if not Path(owner.cwd).is_dir() or lifecycle_state(owner.server, owner.identity, owner.cwd)['deleted']:
        return None
    guard_start(owner.server, owner.identity, owner.cwd)
    current = SessionMetadataStore(owner.directory).read()
    effective = naming_metadata(owner.directory)
    _, _, _, legacy_view = initial_name(owner.directory)
    legacy_auto_off = 'name_auto' not in current and legacy_view.get('autoName') is False
    if current.get('parent_id') or effective.get('name_source') in {'manual', 'generated'} or not automatic_metadata(effective) or legacy_auto_off:
        return None
    loaded = getattr(owner.server, 'sessions', {}).get(owner.identity)
    if loaded and (loaded.lock.locked() or loaded.config_cancel is not None or loaded.worker_retirement_pending):
        return None
    source = history.inspect(owner.directory)
    if not source['available'] or source['diagnostics']:
        return None
    value = [source['revision'], current.get('name_revision', 0), current.get('name_policy_revision', 0), effective, legacy_view.get('autoName')]
    revision = hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
    return current, revision


def provider(setup, workspace):
    """Select Foundation's first enabled configured provider; never fall back."""
    config = setup.config(workspace)
    disabled = set(config.settings.get('configurator', {}).get('disabled', {}).get('providers', []))
    rows = []
    for row in config.providers:
        identity = row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')
        if row.get('enabled', True) is False or identity in disabled:
            continue
        values = row.get('config') or {}
        priority = values.get('priority', 100)
        rows.append((priority if type(priority) in {int, float} else 100, len(rows), identity, values))
    if not rows:
        return None
    _, _, identity, values = min(rows)
    public = next((row for row in setup.provider_rows(workspace) if row['id'] == identity), None)
    # Providers with no declared key field may manage authentication themselves
    # or run locally. A declared but missing key or disconnected account is not
    # silently replaced by another provider.
    if not public or (not public['credentialsConfigured'] and
                      (public.get('account') or public.get('credential', {}).get('supported') is not False)):
        return None
    model = values.get('default_model') or values.get('model')
    return identity, model if isinstance(model, str) and model else None


async def perform(owner, commands):
    """At most one attempt per transcript/name-policy revision, across restarts."""
    from .naming_commands import projection, NamingUnknown
    from .native.setup import SetupManager
    from .relocation import home
    candidate = eligible(owner)
    if not candidate:
        return {'applied': False, 'status': 'ineligible'}
    current, revision = candidate
    command = 'automatic-backfill:' + revision
    previous = commands.receipt(command)
    if previous:
        if previous['status'] == 'succeeded':
            return previous['result']
        return {'applied': False, 'status': 'unknown', 'commandId': command, 'replayed': False}
    setup = SetupManager(home(owner.server))
    selected = provider(setup, owner.cwd)
    if selected is None:
        return {'applied': False, 'status': 'provider-unavailable'}
    # The bounded reader is shared with sidebar previews, but the model gets
    # a useful excerpt. Neither path hydrates or rewrites conversation history.
    text = await asyncio.to_thread(excerpt, owner.directory, {'title': 'Conversation ' + owner.identity[:8]}, max_title=1200)
    if not text['title']:
        return {'applied': False, 'status': 'empty'}
    import amplifier_module_hooks_session_naming as module
    hook = module.SessionNamingHook(None, module.SessionNamingConfig())
    context = hook._extract_naming_context([{'role': 'user', 'content': text['title']}], None, None)
    prompt = module.INITIAL_NAMING_PROMPT.format(context=context)
    if eligible(owner) != candidate:
        return {'applied': False, 'status': 'changed-before-admission'}

    async def invoke():
        result = await setup.probe('naming.complete', {'id': selected[0], 'model': selected[1], 'prompt': prompt}, owner.cwd)
        parsed = hook._parse_response(result.get('text') or '')
        if not parsed or parsed.get('action') == 'defer' or not parsed.get('name'):
            return {'applied': False, 'status': 'deferred', 'commandId': command}
        if eligible(owner) != candidate:
            return {'applied': False, 'status': 'changed-during-generation', 'commandId': command}
        value = {'name': str(parsed['name'])[:hook.config.max_name_length],
                 'name_revision': current.get('name_revision', 0),
                 'name_policy_revision': current.get('name_policy_revision', 0)}
        if isinstance(parsed.get('description'), str):
            value['description'] = parsed['description'][:hook.config.max_description_length]
        updated, applied = accept_generated(owner.directory, value)
        return {**projection(updated, applied), 'status': 'named' if applied else 'changed-during-generation', 'commandId': command}

    try:
        return await commands.perform('backfill', {'commandId': command}, invoke)
    except NamingUnknown:
        return {'applied': False, 'status': 'unknown', 'commandId': command, 'replayed': False}

