"""Passive, declared provider authentication observations.

An observation is not admission authority: an expired token may be refreshable
when the user deliberately invokes the provider. Never infer fallback or alter
the configured provider graph from this projection.
"""
from __future__ import annotations

import inspect


AUTH_CAPABILITIES = frozenset({"auth:oauth_device_code", "auth:oauth_pkce"})
AUTH_STATES = frozenset({"authenticated", "expired", "unauthenticated"})


def authentication_status(provider, info):
    """Return (public observation or None, supported) without auth effects.

Only the existing declared synchronous auth_status contract is invoked. Raw
return values and exception messages are never projected: implementations may
accidentally include private account or credential material in either.
"""
    capabilities = info.get("capabilities", []) if isinstance(info, dict) else getattr(info, "capabilities", [])
    method = getattr(provider, "auth_status", None)
    if not isinstance(capabilities, (list, tuple, set, frozenset)) or not any(item in capabilities for item in AUTH_CAPABILITIES) or not callable(method):
        return None, False
    result = {"status": "unknown", "source": "provider.auth_status", "observational": True}
    if inspect.iscoroutinefunction(method):
        return {**result, "reason": "async-status-unsupported"}, False
    try:
        status = method()
    except Exception:
        return {**result, "reason": "status-read-failed"}, True
    if type(status) is str and status in AUTH_STATES:
        return {**result, "status": status}, True
    if inspect.iscoroutine(status):
        status.close()
    return {**result, "reason": "unsupported-status"}, True
