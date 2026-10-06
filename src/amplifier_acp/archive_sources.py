"""Shared archive source contract; evidence never grants execution authority."""
from pathlib import Path


def validate(value):
    allowed = {'bundle', 'registryHome', 'runtimeManifest', 'generationProfiles',
               'managedAllocations', 'moduleSourceOverrides'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unsupported native source configuration')
    for key in ('bundle', 'registryHome', 'runtimeManifest'):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 8192):
            raise ValueError('Unsupported native source selector')
    profiles = value.get('generationProfiles', [])
    if (not isinstance(profiles, list) or len(profiles) > 128
            or any(not isinstance(v, str) or len(v) > 8192 for v in profiles)):
        raise ValueError('Unsupported generation profile selection')
    if 'managedAllocations' in value:
        managed = value['managedAllocations']
        if (not isinstance(managed, dict)
                or set(managed) != {'roots', 'authority', 'included', 'requiredForManagedActivation'}
                or managed['authority'] != 'external-host-owner'
                or managed['included'] is not False
                or managed['requiredForManagedActivation'] is not True):
            raise ValueError('Unsupported managed allocation source references')
        roots = managed['roots']
        if (not isinstance(roots, list) or len(roots) > 16
                or any(not isinstance(p, str) or len(p) > 8192 or not Path(p).is_absolute() for p in roots)):
            raise ValueError('Unsupported managed allocation source references')
    if 'moduleSourceOverrides' in value:
        modules = value['moduleSourceOverrides']
        if (not isinstance(modules, dict)
                or set(modules) != {'sources', 'authority', 'sourceBodies', 'automaticRestore'}
                or modules['authority'] != 'trusted-launcher'
                or modules['sourceBodies'] != 'only-within-declared-native-roots'
                or modules['automaticRestore'] is not False):
            raise ValueError('Unsupported native module source evidence')
        from .module_sources import validate as validate_modules
        # A recorded source may be unavailable on the restore machine. Check its
        # syntax without probing it or turning the archived path into permission.
        validate_modules(modules['sources'], require_local_exists=False)
    return value
