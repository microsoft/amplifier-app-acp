"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


from contextvars import ContextVar


from dataclasses import dataclass


from pathlib import Path


@dataclass(frozen=True)
class Activation:
    """An opaque, per-acquisition authority token."""

    number: int


class ActivationGate:
    """Reject writes from a released or superseded worker activation."""

    def __init__(self):
        self._number = 0
        self._active: Activation | None = None
        self._current: ContextVar[Activation | None] = ContextVar("amplifier_web_activation", default=None)

    def activate(self) -> Activation:
        self._number += 1
        self._active = Activation(self._number)
        # Mount-time checkpoints run before the first Input exists.  A spawned
        # turn still binds its own captured token explicitly.
        self._current.set(self._active)
        return self._active

    def release(self, activation: Activation) -> None:
        self.check(activation)
        self._active = None

    def check(self, activation: Activation | None) -> None:
        if activation is None or activation is not self._active:
            raise RuntimeError("This session activation has been released or superseded.")

    def bind(self, activation: Activation):
        self.check(activation)
        return self._current.set(activation)

    def reset(self, token) -> None:
        self._current.reset(token)

    def check_current(self) -> None:
        self.check(self._current.get())

    def current(self) -> Activation | None:
        """Capture the calling task's token, never the latest owner's token."""
        return self._current.get()

    @property
    def current_valid(self) -> bool:
        return self._active is not None and self._current.get() is self._active


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
