"""Canonical conflict detection stays lazy until native execution admission."""
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_acp.native import runtime_qualification
from amplifier_acp.native.host import session as host
from amplifier_acp.native.runtime_worker import Worker


MODULE = 'amplifier_foundation.session.transcript_guard'


def test_selected_guard_is_the_canonical_foundation_class():
    from amplifier_foundation.session import NativeTranscriptGuard

    assert runtime_qualification.require_session_api() is NativeTranscriptGuard
    assert NativeTranscriptGuard.__module__ == MODULE


@pytest.mark.parametrize('method', [None, lambda self, **kwargs: None])
async def test_old_acquisition_api_refuses_before_ownership_or_history(tmp_path, monkeypatch, method):
    class OldStore:
        def __init__(self, *args, **kwargs):
            raise AssertionError('API admission must precede ownership')

    OldStore.acquire_async = method
    monkeypatch.setattr('amplifier_foundation.session.shared_state.SharedSessionStore', OldStore)
    monkeypatch.setattr('amplifier_acp.native.runtime_bootstrap.bootstrap_app_package', lambda: None)
    prepare, recover = AsyncMock(), Mock()
    monkeypatch.setattr(host, 'prepare_manager', prepare)
    monkeypatch.setattr('amplifier_acp.native.history_revision.recover_pending', recover)
    events = []
    monkeypatch.setattr('amplifier_acp.native.runtime_worker.publish', events.append)
    worker = Worker()
    try:
        await worker.start({'id': 'old-acquisition', 'workspace': str(tmp_path)}, recover_bundle=False)
        assert worker.shutdown.is_set() and worker.shared_handle is None
        assert worker.runtime is None and worker.execution is None
        prepare.assert_not_awaited()
        recover.assert_not_called()
        errors = [event for event in events if event['type'] == 'runtime.error']
        assert len(errors) == 1
        assert errors[0]['error'].startswith('RuntimeDependencyError: ')
        assert 'cancellation-safe writer acquisition' in errors[0]['error']
    finally:
        if worker.maintenance_gate:
            worker.maintenance_gate.release()


@pytest.mark.parametrize('missing', ['module', 'guard', 'conflict'])
async def test_missing_guard_refuses_start_before_native_writer_or_recovery(
        tmp_path, monkeypatch, missing):
    from amplifier_foundation.session import NativeTranscriptConflict, NativeTranscriptGuard

    surface = SimpleNamespace(NativeTranscriptGuard=NativeTranscriptGuard,
                              NativeTranscriptConflict=NativeTranscriptConflict)
    if missing == 'guard':
        surface.NativeTranscriptGuard = None
    elif missing == 'conflict':
        surface.NativeTranscriptConflict = None
    monkeypatch.setitem(sys.modules, MODULE, None if missing == 'module' else surface)
    monkeypatch.setattr('amplifier_acp.native.runtime_bootstrap.bootstrap_app_package', lambda: None)
    store = Mock(side_effect=AssertionError('A missing API must not acquire native ownership'))
    monkeypatch.setattr('amplifier_foundation.session.shared_state.SharedSessionStore', store)
    prepare, recover = AsyncMock(), Mock()
    monkeypatch.setattr(host, 'prepare_manager', prepare)
    monkeypatch.setattr('amplifier_acp.native.history_revision.recover_pending', recover)
    events = []
    monkeypatch.setattr('amplifier_acp.native.runtime_worker.publish', events.append)
    worker = Worker()
    try:
        await worker.start({'id': 'missing-guard', 'workspace': str(tmp_path)}, recover_bundle=False)
        assert worker.shutdown.is_set() and worker.shared_handle is None
        assert worker.runtime is None and worker.execution is None
        store.assert_not_called()
        prepare.assert_not_awaited()
        recover.assert_not_called()
        errors = [event for event in events if event['type'] == 'runtime.error']
        assert len(errors) == 1
        assert errors[0]['error'].startswith('RuntimeDependencyError: ')
        assert 'Update the adapter dependencies' in errors[0]['error']
        assert not (tmp_path / 'native-home' / 'projects').exists()
    finally:
        if worker.maintenance_gate:
            worker.maintenance_gate.release()


def test_unrelated_import_failure_is_not_reported_as_an_old_foundation(monkeypatch):
    error = ModuleNotFoundError('broken unrelated dependency', name='unrelated_native_dependency')
    monkeypatch.setattr(importlib, 'import_module', Mock(side_effect=error))
    with pytest.raises(ModuleNotFoundError) as raised:
        runtime_qualification.require_session_api()
    assert raised.value is error


async def test_dependency_preparation_can_import_and_enter_without_new_guard(tmp_path, monkeypatch):
    import amplifier_foundation.session as foundation_session
    from amplifier_foundation.session.shared_state import SharedSessionStore

    class PreparationReached(Exception):
        pass

    def load(*args, **kwargs):
        raise PreparationReached

    # Model the prior Foundation surface while retaining its existing dependency
    # APIs: preparing that graph must remain possible before the guard is added.
    with monkeypatch.context() as old:
        old.setitem(sys.modules, MODULE, None)
        old.delattr(foundation_session, 'NativeTranscriptGuard')
        old.delattr(foundation_session, 'NativeTranscriptConflict')
        old.delattr(SharedSessionStore, 'acquire_async')
        importlib.reload(host)
        old.setattr(host, 'load_config', load)
        try:
            with pytest.raises(PreparationReached):
                await host.prepare_dependencies(tmp_path)
        finally:
            # The module has no cached new Foundation class after this import.
            assert not hasattr(host, 'NativeTranscriptGuard')
    importlib.reload(host)
