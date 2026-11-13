"""Canonical kernel dependency through the native bootstrap and tool boundary."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_acp.native import computation, runtime_bootstrap


def test_isolated_bootstrap_resolves_canonical_kernels(tmp_path):
    # Match the worker's explicit package bootstrap, without source PYTHONPATH or cwd.
    script = """
import importlib.metadata, json, runpy, sys
from pathlib import Path
runpy.run_path(sys.argv[1])["bootstrap_app_package"]()
from amplifier_acp.native.computation import Kernels
from amplifier_kernels import Kernels as canonical
import amplifier_kernels
assert Kernels is canonical
root = Path(amplifier_kernels.__file__).parent
assert (root / "python_runner.py").is_file()
assert (root / "node_runner.cjs").is_file()
print(json.dumps({"distribution": importlib.metadata.version("amplifier-kernels"),
                  "implementation": Kernels.__module__}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, runtime_bootstrap.__file__],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["implementation"] == "amplifier_kernels.manager"


@pytest.mark.asyncio
@pytest.mark.parametrize("observer_available", [True, False])
async def test_shared_kernel_preserves_admission_and_observation(observer_available):
    events, calls, tools = [], [], {}

    async def observe(event):
        events.append(event)

    class Coordinator:
        def get_capability(self, name):
            assert name == "operations.observe"
            return observe if observer_available else None

        async def mount(self, kind, tool, *, name):
            assert kind == "tools" and name == "compute"
            tools[name] = tool

    async def invoke(request, **options):
        assert computation.KERNEL_TRANSPORT.get() is True
        calls.append((request, options))
        return {"success": False, "error": {"message": "Fixture process permission denied"}}

    manager = await computation.install(SimpleNamespace(coordinator=Coordinator(), invoke=invoke))
    try:
        result = await tools["compute"].execute({"action": "create", "language": "python"})
        assert result.success is False
        assert manager.records == {}
        assert computation.KERNEL_TRANSPORT.get() is False
        if observer_available:
            assert [r["phase"] for r in events] == ["started", "finished"]
            assert len(calls) == 1
            assert calls[0] == ({"name": "bash", "arguments": {"action": "list"}},
                                {"bound_arguments": {"action": "list"}, "checkpoint": False,
                                 "provenance": {"source": "kernels.transport"}})
            assert events[-1]["status"]["state"] == "failed"
        else:
            assert calls == []
            assert "observation" in str(result.error).lower()
    finally:
        await manager.shutdown()
