"""Extracted native application runtime; provenance in extraction.json."""
from importlib import metadata


import platform


import sys


import time


from .host_identity import local_host_identity


def environment():
    try:
        version = metadata.version("amplifier-module-tool-computer-use")
    except metadata.PackageNotFoundError:
        version = None
    return {"host": local_host_identity(), "python": {"path": sys.executable,
        "version": platform.python_version()}, "platform": platform.system(),
        "computerUsePackage": {"status": "installed" if version else "missing",
            "version": version, "importVerified": False}}


def worker_report(controls):
    """Read public configuration/catalog metadata, never execute a tool."""
    config = controls.configuration()
    tools = controls.coordinator.get("tools") or {}
    desktop = tools.get("desktop")
    schema = getattr(desktop, "input_schema", {}) if desktop else {}
    actions = schema.get("properties", {}).get("action", {}).get("enum", [])
    unavailable = tools.get("computer_use_unavailable")
    return {**environment(), "status": "available", "observedAt": time.time(),
        "catalogRevision": controls.catalog_revision,
        "modules": [{key: row.get(key) for key in ("module", "id", "enabled")}
            for row in config["modules"] if row["section"] == "tools"],
        "mountedTools": sorted(tools),
        "computerControl": {"status": "unavailable" if "computer_use_unavailable" in tools
            else "mounted" if desktop else "not_mounted", "doctorSupported": "doctor" in actions,
            "detail": str(getattr(unavailable, "description", ""))[:1600] if unavailable else None},
        "browserContext": {"status": "not_exposed",
            "reason": "A tool name or installed package does not identify a browser, tab or signed-in account. Inspect the selected adapter through its own published actions."}}
