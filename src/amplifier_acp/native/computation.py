"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import contextvars


import os


import shutil


import sys


from pathlib import Path


from amplifier_kernels import Kernels


KERNEL_TRANSPORT = contextvars.ContextVar("kernel_transport", default=False)


ACTIONS = ("create", "execute", "interrupt", "reset", "close")


def tool_schema():
    return {
        "type": "object",
        "properties": {
            "action": {"enum": list(ACTIONS)},
            "language": {"enum": ["python", "node"]},
            "kernelId": {"type": "string", "maxLength": 200},
            "cellId": {"type": "string", "maxLength": 200},
            "generation": {"type": "integer", "minimum": 1},
            "code": {"type": "string", "minLength": 1, "maxLength": 16000},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
            "questionIds": {
                "type": "array",
                "items": {"type": "string", "maxLength": 200},
                "maxItems": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }


def runtimes():
    # No relative PATH/current-directory lookup and no untrusted request override.
    paths = os.pathsep.join(
        part
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if Path(part).is_absolute()
    )
    node = shutil.which("node", path=paths)
    result = {"python": str(Path(sys.executable).absolute())}
    if node:
        result["node"] = str(Path(node).absolute())
    return result


async def install(controls):
    from amplifier_core.models import ToolResult

    coordinator = controls.coordinator

    async def transport(action, arguments):
        token = KERNEL_TRANSPORT.set(True)
        try:
            response = await controls.invoke(
                {"name": "bash", "arguments": {"action": action, **arguments}},
                bound_arguments={"action": action, **arguments},
                checkpoint=False,
                provenance={"source": "kernels.transport"},
            )
        finally:
            KERNEL_TRANSPORT.reset(token)
        result = response.get("result", response)
        if not result.get("success"):
            raise ValueError(
                result.get("error", {}).get("message", "Process transport denied")
            )
        return result["output"]

    async def observe(event):
        callback = coordinator.get_capability("operations.observe")
        if not callable(callback):
            raise TypeError("Durable computation observation is unavailable")
        return await callback(event)

    manager = Kernels(transport, observe, runtimes())

    class Computation:
        name = "compute"
        description = (
            "Persistent Python/Node computation in an owned child process. Code can read/write files "
            "and use the network with the same host authority as approved shell execution; this is not a sandbox. "
            "Create, execute exact code in a known generation, interrupt, reset or close. Interrupt destroys "
            "variable memory; reset creates a fresh generation only after confirmed termination. Cells never replay."
        )
        input_schema = tool_schema()

        async def execute(self, data):
            try:
                action = data["action"]
                if action == "create":
                    result = await manager.create(data["language"])
                elif action == "execute":
                    result = await manager.execute(
                        data["kernelId"],
                        data["generation"],
                        data["code"],
                        data.get("timeout", 30),
                        data.get("questionIds", ()),
                    )
                elif action == "interrupt":
                    result = await manager.interrupt(
                        data["kernelId"], data["generation"], cell_id=data.get("cellId")
                    )
                else:
                    result = await getattr(manager, action)(
                        data["kernelId"], data["generation"]
                    )
                return ToolResult(success=True, output=result)
            except Exception as exc:  # noqa: BLE001 - Tool protocol returns failures instead of leaking exceptions.
                return ToolResult(success=False, error={"message": str(exc)})

    await coordinator.mount("tools", Computation(), name="compute")
    return manager
