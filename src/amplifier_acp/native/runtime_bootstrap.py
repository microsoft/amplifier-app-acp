"""Extracted native application runtime; provenance in extraction.json."""
from importlib.util import module_from_spec, spec_from_file_location


from pathlib import Path


import sys


def bootstrap_app_package():
    package_dir = Path(__file__).resolve().parent
    # A qualified dependency environment intentionally does not install this
    # application. Bind only its explicit package, never the caller's venv.
    outer_dir=package_dir.parent;outer_init=outer_dir/'__init__.py'
    outer=sys.modules.get('amplifier_acp')
    if outer is not None:
        if Path(getattr(outer,'__file__','')).resolve()!=outer_init:
            raise ImportError('A different amplifier_acp application is already loaded')
    else:
        outer_spec=spec_from_file_location('amplifier_acp',outer_init,submodule_search_locations=[str(outer_dir)])
        if outer_spec is None or outer_spec.loader is None:raise ImportError('Cannot load the native ACP application')
        outer=module_from_spec(outer_spec);sys.modules['amplifier_acp']=outer
        try:outer_spec.loader.exec_module(outer)
        except BaseException:
            sys.modules.pop('amplifier_acp',None);raise

    init = package_dir / "__init__.py"
    existing = sys.modules.get("amplifier_acp.native")
    if existing is not None:
        if Path(getattr(existing, "__file__", "")).resolve() != init:
            raise ImportError("A different amplifier_acp.native package is already loaded")
        return existing
    # Adding package_dir.parent to sys.path would expose the outer tool venv's
    # site-packages, including distribution metadata absent from this runtime.
    spec = spec_from_file_location(
        "amplifier_acp.native", init, submodule_search_locations=[str(package_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the Amplifier Unified package")
    package = module_from_spec(spec)
    sys.modules["amplifier_acp.native"] = package
    try:
        spec.loader.exec_module(package)
    except BaseException:
        sys.modules.pop("amplifier_acp.native", None)
        raise
    return package