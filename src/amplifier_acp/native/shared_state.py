"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


from pathlib import Path


def configuration_paths(workspace: Path, session_id: str, home: Path) -> tuple[Path, ...]:
    """Only stat inputs this host actually consumes during a manager mount."""

    from .shared_settings import settings_paths, routing_dirs
    from .session_files import amplifier_home
    paths = settings_paths(workspace, session_id=session_id)
    matrices = [path for directory in routing_dirs(workspace) for path in sorted(directory.glob("*.yaml"))]
    return (
        *paths.values(),
        amplifier_home() / "keys.env",
        *routing_dirs(workspace),
        *matrices,
        home / "sessions" / session_id / "configuration.json",
        home / "sessions" / session_id / "control-state.json",
        home / "updates" / "active.json",
        home / "diagnostics" / "config.json",
    )


def configuration_stamp(workspace: Path, session_id: str, home: Path, stamp, *, extra_paths=()) -> tuple[tuple[str, object], ...]:
    """Return metadata-only invalidation data; no config file is parsed."""

    paths = [*configuration_paths(workspace, session_id, home)]
    for path in extra_paths:
        path = Path(path)
        if path not in paths:
            paths.append(path)
    return tuple((str(path), stamp(path)) for path in paths)
