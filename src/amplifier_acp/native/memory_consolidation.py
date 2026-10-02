"""Extracted native application runtime; provenance in extraction.json."""
import asyncio


async def generate(controls, args):
    """One tool-free call through the worker's configured and observed provider."""
    from amplifier_core.message_models import ChatRequest, Message
    from .execution_events import CALL_PURPOSE
    controls.require_idle()
    prompt = args.get('prompt')
    if not isinstance(prompt, str) or len(prompt) > 31000:
        raise ValueError('Invalid bounded memory prompt')
    coordinator = controls.coordinator
    loop = coordinator.get('orchestrator')
    providers = coordinator.get('providers') or {}
    provider = getattr(loop, 'root_provider', None) or loop._select_provider(providers)
    from .host.session import SelectedProvider
    if isinstance(provider, SelectedProvider):
        # Keep the chosen model/effort/account while constraining a larger
        # conversation output override for this auxiliary request only.
        provider = SelectedProvider(provider.original, {**provider.selection,
            'max_output_tokens': min(provider.selection.get('max_output_tokens') or 4096, 4096)},
            execution_adapter=provider.execution_adapter)
    observe = coordinator.get_capability('web.provider_observe')
    if observe:
        provider = observe(provider)
    request = ChatRequest(messages=[Message(role='user', content=prompt)], tools=[],
                          max_output_tokens=4096, timeout=60)
    token = CALL_PURPOSE.set({'label': 'Memory consolidation', 'lifecycle': 'background'})
    try:
        reply = await asyncio.wait_for(provider.complete(request), 60)
        info = provider.get_info()
        import inspect
        if inspect.isawaitable(info):
            info = await info
        text = ''.join(block.text for block in reply.content if getattr(block, 'type', None) == 'text')
        return {'text': text, 'provider': getattr(info, 'id', None),
                'model': getattr(reply, 'model', None) or getattr(info, 'defaults', {}).get('model')}
    finally:
        CALL_PURPOSE.reset(token)
