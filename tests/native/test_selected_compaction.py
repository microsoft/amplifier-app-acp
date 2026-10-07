"""Compaction follows root selection without changing provider or summary budgets."""
import asyncio
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest
from amplifier_acp.native.host.session import SelectedProvider


class CompactProvider:
    default_model = 'configured-model'

    def __init__(self):
        self.calls = []

    async def compact_context(self, request):
        self.calls.append(('compact', request))
        return {'compacted_for': request.model}

    def validate_compacted_context(self, message, *, model=None):
        self.calls.append(('validate', model))
        return message['compacted_for'] == (model or self.default_model)

    async def request_budget(self, request, *, request_options=None):
        self.calls.append(('budget', request, request_options))
        return request.max_output_tokens

    async def complete(self, request, **kwargs):
        self.calls.append(('complete', request, kwargs))
        return 'summary'


@pytest.mark.parametrize('effort', [None, 'low'])
async def test_selected_native_compaction_and_validation_preserve_independent_reserve(effort):
    provider = CompactProvider()
    wrapped = SelectedProvider(provider, {'model':'chosen-model','effort':'high','max_output_tokens':999})
    original = ChatRequest(messages=[], max_output_tokens=123, reasoning_effort=effort,
                           metadata={'purpose':'context-compaction'})
    assert wrapped.default_model == 'chosen-model'
    assert await wrapped.request_budget(original) == 123
    result = await wrapped.compact_context(original)
    assert wrapped.validate_compacted_context(result)
    assert not wrapped.validate_compacted_context({'compacted_for':'configured-model'})
    budget, compact = provider.calls[:2]
    assert budget[1] is not original and compact[1] is not original
    assert budget[1].model == compact[1].model == 'chosen-model'
    assert budget[1].reasoning_effort == compact[1].reasoning_effort == (effort or 'high')
    assert compact[1].max_output_tokens == 123
    assert original.model is None and original.reasoning_effort == effort
    assert provider.default_model == 'configured-model' and wrapped._selected_requests == []


async def test_summary_dispatch_shares_preflight_selection_and_answer_limit_is_unchanged():
    provider = CompactProvider()
    wrapped = SelectedProvider(provider, {'model':'chosen-model','effort':'high','max_output_tokens':999})
    summary = ChatRequest(messages=[], max_output_tokens=123, reasoning_effort='low',
                          metadata={'purpose':'context-compaction'})
    await wrapped.request_budget(summary)
    await wrapped.complete(summary)
    assert provider.calls[0][1] is provider.calls[1][1]
    assert provider.calls[1][1].max_output_tokens == 123
    assert provider.calls[1][2] == {'model':'chosen-model','reasoning_effort':'low'}
    await wrapped.complete(ChatRequest(messages=[], max_output_tokens=12))
    assert provider.calls[-1][1].max_output_tokens == 999


async def test_original_one_argument_validator_and_capability_detection_are_preserved():
    calls = []
    class Older(CompactProvider):
        def validate_compacted_context(self, message):
            calls.append(message)
            return True
    provider = Older(); wrapped = SelectedProvider(provider, {'effort':'high'})
    assert wrapped.default_model == 'configured-model'
    message = await wrapped.compact_context(ChatRequest(messages=[],max_output_tokens=123))
    assert wrapped.validate_compacted_context(message) and calls == [message]
    assert provider.calls[0][1].max_output_tokens == 123
    missing = SelectedProvider(SimpleNamespace(), {'model':'chosen'})
    assert not hasattr(missing, 'compact_context')
    assert not hasattr(missing, 'validate_compacted_context')


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_failed_compaction_is_not_retried_or_left_prepared(failure):
    class Broken(CompactProvider):
        async def compact_context(self, request):
            self.calls.append(('compact', request))
            raise failure()
    provider = Broken(); wrapped = SelectedProvider(provider, {'model':'chosen'})
    request = ChatRequest(messages=[],metadata={'purpose':'context-compaction'})
    await wrapped.request_budget(request)
    with pytest.raises(failure): await wrapped.compact_context(request)
    assert len([row for row in provider.calls if row[0]=='compact']) == 1
    assert wrapped._selected_requests == []


async def test_actual_openai_compactor_serializes_and_validates_selected_model(monkeypatch):
    from unittest.mock import AsyncMock
    module = pytest.importorskip('amplifier_module_provider_openai')
    async def forbidden(*args, **kwargs):
        raise AssertionError('This fixture must never make a network request')
    monkeypatch.setattr('httpx.AsyncClient.send', forbidden)
    provider = module.OpenAIProvider(api_key='offline-fixture', config={'default_model':'gpt-6-sol'})
    compact = AsyncMock(return_value=SimpleNamespace(
        output=[{'type':'compaction','encrypted_content':'offline-opaque-checkpoint'}],
        usage={'input_tokens':12,'output_tokens':4}))
    provider._client = SimpleNamespace(base_url='https://api.openai.com/v1',
                                       responses=SimpleNamespace(compact=compact))
    provider._guard_assembled_params_with_provider_count = AsyncMock(return_value=12)
    wrapped = SelectedProvider(provider, {'model':'gpt-6-astra','effort':'high','max_output_tokens':999})
    request = ChatRequest(messages=[], max_output_tokens=123, metadata={'purpose':'context-compaction'})
    result = await wrapped.compact_context(request)
    assert compact.await_count == 1 and compact.call_args.kwargs['model'] == 'gpt-6-astra'
    assert wrapped.validate_compacted_context(result['message'])
    with pytest.raises(ValueError, match='different model'):
        provider.validate_compacted_context(result['message'])
    assert provider.default_model == 'gpt-6-sol' and request.max_output_tokens == 123
