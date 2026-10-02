"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


class RuntimeStartupError(RuntimeError):
    """This attempt failed preparation before the worker submitted its input."""
