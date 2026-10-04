# Provider sources in native setup

`providers.save` uses the same explicit source selector as `modules.save` and
`sources.save`: a credential-free HTTPS Git source or an existing explicit local
path. Absolute paths, `./` and `../` paths relative to the authorized workspace,
home-relative paths and local `file://` paths resolve to a canonical local path.
Saving a selection does not import code, install dependencies, test credentials,
or start a conversation. Existing providers retain their source when an edit
omits that field. Provider preparation and mounting still belong to Foundation
and Core, including their import-origin and immutable-runtime checks.

The administration launcher authorizes the configuration **workspace** through
`adminWorkspaceRoots`; out-of-root workspaces and escaped workspace symlinks
refuse before effects. The module editor already allows an explicitly selected
source checkout outside that workspace (for example, a sibling provider repo).
This fix adopts that existing selector contract; it does not add a source-root
sandbox or change tool filesystem permissions. Launcher `moduleSources` remains
a separate, stronger deployment override and retains its own validation and
precedence. A registered bundle name is not a module-source selector.

## Implementation audit

The reported F04 browser failure submitted an owned absolute provider directory.
Native setup reused `bundles.validate_uri`, which only accepted bundle names and
remote Git URIs. Native registry already exposes `source_uri` for providers in
the module editor. Setup now calls that existing helper, imported at mutation
time because RegistryManager also uses SetupManager's private-key writer.

The affected class is application-side source selection, not provider-specific
model or authentication code. Reviewed implementations:

| Implementation | Reviewed revision | Existing contract |
| --- | --- | --- |
| Current `microsoft/amplifier` catalog | `33b75749920472257d1d85d20a5699ecf9d1ece7` | Official and community providers share the module mount contract. |
| Native ACP setup/registry/admin | `04572bb4e3e788e84dbaa4cd9b30cc1ae64d3ae4` | Registry accepts local sources; setup incorrectly used bundle validation; admin bounds settings workspace before effects. |
| Extracted legacy Unified setup/registry | `cec95deae70b67f659ce4f55737cd6fbb84211b9` | Same provider/module validator discrepancy; legacy code is not modified by this native patch. |
| App CLI module/source commands and provider loader | `5aaafb478d02cee8c396967b954299a08e8a1efd` | Documents local provider overrides, persists explicit paths, activates their exact source and verifies import origin. |
| Foundation file resolver/module activator | `467f870abb4487184789386ecdb460410f64bd9f` | Resolves local paths and activates module code; preparation policy remains separate from saving configuration. |
| Qualified Foundation dependency-policy successor | `64e9efd90c4b52cd5f62eb7e0416894b3e12dede` | Used for actual source/installed qualification; immutable dependency-installation denial remains active. |
| Agent CLI provider catalog/admin report | `44a83adeb49fc1cfb55eb0c1bf38aa125ff0d705` | Static module sources and credential reporting; no competing provider-source editor. |

The accompanying qualification packet retains the current upstream catalog and
revision/entrypoint inventory of all 14 available official/community provider
checkouts. Native embedded providers in the test and browser fixtures use the
same source contract. Those provider implementations are unchanged; no live
account, remote Git fetch, Windows-path, or physical browser acceptance is claimed
by the isolated tests. Existing local `file://` spelling follows RegistryManager
as-is; this patch does not introduce URL decoding or change its path grammar.

Regression coverage includes scoped save/edit, supported local spellings, HTTPS
sources, malformed and missing sources with unchanged settings/private keys,
unauthorized workspace and symlink/traversal rejection, and real ACP save/restart
followed by Foundation/Core loading the selected provider file. The installed
wheel runs the same tests; no provider request is made in the mount fixture.
