"""Packaged shell guidance must resolve independently of a source checkout."""
import dataclasses
from pathlib import Path
import re

import pytest


@pytest.mark.asyncio
async def test_packaged_shell_namespace_and_companions(tmp_path):
    from amplifier_foundation import BundleRegistry
    from amplifier_foundation.mentions import BaseMentionResolver
    from amplifier_acp.native.builtin_behaviors import (
        SHELL_BEHAVIOR_URI, resource_root, resolve_builtin_behavior,
    )

    skills_uri = ('git+https://github.com/microsoft/amplifier-bundle-skills@main'
                  '#subdirectory=behaviors/skills-tool.yaml')
    include = tmp_path / 'skills.yaml'
    include.write_text('bundle:\n  name: skills\n  version: 1.0.0\n')
    # The external skills implementation is irrelevant to packaged namespace
    # resolution. Resolve that include locally without contacting a provider.
    registry = BundleRegistry(home=tmp_path / 'registry', strict=True,
                              include_source_resolver=lambda uri: str(include) if uri == skills_uri else None)
    bundle = await registry.load(resolve_builtin_behavior(SHELL_BEHAVIOR_URI))
    bundle.resolve_pending_context()
    assert bundle.context and all(Path(p).is_file() for p in bundle.context.values())
    resolver = BaseMentionResolver(bundles={
        ns: dataclasses.replace(bundle, base_path=path)
        for ns, path in bundle.source_base_paths.items()
    })
    assert resolver.resolve('@unified:skills').resolve() == (resource_root() / 'skills').resolve()
    assert any('shell-skills.md' in str(p) for p in bundle.context.values())
    checked = []
    for document in (resource_root() / 'skills').rglob('*.md'):
        for link in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            if '://' in link or link.startswith('#'):
                continue
            target = (document.parent / link.split('#', 1)[0]).resolve()
            assert target.is_relative_to(resource_root().resolve())
            assert target.is_file(), (document, link)
            checked.append(target)
    assert len(checked) >= 10
