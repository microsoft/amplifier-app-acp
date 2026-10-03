"""Every native test owns its complete storage/config namespace."""
import pytest
import sys
@pytest.fixture(autouse=True)
def isolated_native_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('UNIFIED_RUNTIME_PYTHON',sys.executable)
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native-home'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path/'app-home'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH',str(tmp_path/'events'))

    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(tmp_path/'foundation-state'))
    monkeypatch.setenv('AMPLIFIER_SOURCE_STORE',str(tmp_path/'source-store'))
