"""Trusted process policy for preparation in an immutable Python runtime.

This is dependency-installation policy, not source/cache or module-selection
policy. Configured modules remain configured; missing dependencies must be
prepared by an explicit, separate staging process.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import sys

VARIABLE = 'AMPLIFIER_RUNTIME_IMMUTABLE'


def immutable(environment=None):
    value = (os.environ if environment is None else environment).get(VARIABLE, '')
    if value not in {'', '0', '1'}:
        raise ValueError('Invalid trusted immutable-runtime environment policy')
    return value == '1'


def _foundation():
    try:
        from amplifier_foundation.modules import dependency_installation_policy, DependencyInstallationDenied
    except ImportError:
        raise RuntimeError('Immutable runtime requires Foundation dependency-installation policy support; prepare a compatible runtime outside this process.') from None
    if not callable(dependency_installation_policy) or not issubclass(DependencyInstallationDenied, Exception):
        raise RuntimeError('Unsupported Foundation dependency-installation policy')
    return dependency_installation_policy


def configure(config, environment=None):
    """Only the trusted launcher binds this; peer requests never select policy."""
    if 'runtimeImmutable' in config and type(config['runtimeImmutable']) is not bool:
        raise ValueError('runtimeImmutable must be a boolean launcher option')
    env = os.environ if environment is None else environment
    restricted = immutable(env) or config.get('runtimeImmutable') is True
    if restricted:
        _foundation()  # Refuse unsupported protection before startup effects.
        env[VARIABLE] = '1'
    return restricted


def metadata(environment=None):
    if not immutable(environment): return {}
    _foundation()
    return {'runtimePreparation': {'version': 1, 'immutable': True,
            'dependencyInstallation': False, 'policy': 'foundation-dependency-installation'}}


@contextmanager
def scope(*, readonly=False):
    if immutable() or readonly:
        with _foundation()(False): yield
    else: yield


def governed(function):
    """Keep the policy through child tasks, lazy activation and joined cleanup."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with scope(readonly=kwargs.get('qualification_readonly') is True):
            return await function(*args, **kwargs)
    return wrapped


async def prepare(bundle, *, readonly=False, **kwargs):
    """One entry for all native Foundation preparation, including probes."""
    restricted = immutable() or readonly
    with scope(readonly=readonly):
        return await bundle.prepare(install_deps=not restricted, **kwargs)


def require_mutable():
    if immutable():
        from amplifier_foundation.modules import DependencyInstallationDenied
        raise DependencyInstallationDenied('Dependency preparation requires a separate mutable staging environment.')


def staging_environment(environment, project, owned_root, *, prepared=True, readonly=False):
    """Remove inherited worker restriction only for a distinct owned candidate.

    Called by the existing trusted generation/profile builder, never a peer
    parameter or same-process context relaxation. The child uses a new prefix.
    """
    root = Path(owned_root).resolve(strict=True)
    project = Path(project).resolve(strict=True)
    candidate = (project/'.venv').resolve(strict=prepared)
    if (not project.is_relative_to(root) or project == root or candidate.parent != project
            or not readonly and candidate == Path(sys.prefix).resolve()
            or prepared and (not candidate.is_dir() or not (candidate/'pyvenv.cfg').is_file())):
        raise ValueError('Mutable preparation requires a separate owned staged Python environment')
    result = dict(environment)
    if readonly: result[VARIABLE] = '1'
    else: result.pop(VARIABLE, None)
    result.pop('VIRTUAL_ENV', None)
    # uv must use this verified candidate even if its parent shell selected an
    # unrelated UV_PROJECT_ENVIRONMENT. This also covers initial uv sync.
    result['UV_PROJECT_ENVIRONMENT'] = str(candidate)
    return result
