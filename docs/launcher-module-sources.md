# Trusted launcher module sources v1

`moduleSources` is an optional operator-owned launcher configuration field. It is
not an ACP request field or a client/agent source-selection authority. The default
is empty and preserves the existing configured module policy. This permanently
supported mechanism can select reviewed development candidates without claiming
an unmerged commit is the configured moving branch.

```json
{
  "registryHome": "/owned/candidate/foundation",
  "moduleSources": {
    "provider-openai-chatgpt": "/owned/candidate/sources/reviewed-provider"
  }
}
```

The map allows at most64 module IDs and64KiB JSON total. IDs are lower-case
letters/digits/underscores/hyphens, starting with a letter, up to128characters.
Each value is an existing absolute local directory (canonicalized) or a
credential-free `git+https://...@<40-or-64-hex-commit>` reference with optional
relative `#subdirectory=...`. Moving branch aliases, relative local paths,
userinfo, query parameters, traversal fragments and missing directories refuse
before worker startup. Errors do not echo rejected values. Config omission clears
an inherited internal worker override variable. Shared settings, source aliases,
native history, bundle declarations and unlisted module sources are unchanged.

An exact module override takes precedence in Foundation's public `source_resolver`
for normal roots, snapshots, preparation and remounts. Explicit overrides are also
materialized into in-memory source-less declarations before preparation, because
Foundation invokes this resolver only for declarations containing a source. The
on-disk declaration stays unchanged. After activation, only the source fields
inserted by this preparation are restored to their exact prior presence/value in
the Bundle and prepared mount plan, before Core/configurator state is created.
The resolver retains the actual selected module path. Thus effective configuration,
configuration clones and history forks cannot turn the temporary launcher source
into a user-owned source declaration. Explicit source fields (including intentional
configuration edits) are preserved. Removing the launcher map for a fresh lifetime
restores the original source-selection policy. Existing explicitly saved source
paths are not inferred to be disposable overrides or rewritten retroactively. The same choice reaches
the prepared resolver for lazily resolved child modules; no repository-wide or
sibling-module override is inferred. Foundation's cache key resolves the actual
selected source; we do not bind a different commit beneath a claimed `@main`
cache record. The existing Core contract validation and canonical import identity
checks remain strict, including compatibility with native-required loop/naming
implementations. An override changes code selection, not provider availability,
model choice or credentials. There is no fallback after failure.

Fresh workers receive only the validated launcher map through the private worker
environment. Administration/probe subprocesses inherit the same policy. Provider catalog cache
keys and probe payloads use the effective source, matching the mounted catalog
identity; changing the override cannot reuse the previous source's model/schema
metadata. Provider setup rows retain their configured `source` and add redacted
`sourceSelection` provenance when overridden (originalSource, effectiveSource,
authority and override). Existing
workers keep their original map; changing launcher configuration requires a fresh
ACP/worker lifetime. A mutable local checkout is not an immutable byte attestation:
operators must retain their reviewed source digest and protect candidate files.
Removing the map restores ordinary configured moving-source policy for new workers.
The map is a deployment override, not a replacement standing pin policy.

Existing passive resident `runtime.inspect` / `_amplifier/runtime` results add
`moduleSourceOverrides` with version, configuredOverrides, bounded resolutions,
truncated and loadedCodeBytesVerified:false. Each observed root preparation
resolution has module, originalSource, effectiveSource, authority:'trusted-launcher'
and override:true. Original URLs omit credentials and query strings. Provenance
is capped at128rows/64KiB with explicit truncation; it does not enumerate cold
sessions or claim delegated runtime inspection. The mounted surface independently
reports actual implementation `loadedFrom`. Compare that file against the sealed
candidate source receipt; installed distribution metadata alone is insufficient.

Full-native archive sourceConfiguration records the validated override selectors
and marks them as launcher authority, source bodies only captured when within
declared native roots, automaticRestore:false. External source trees require
separate retained provenance; archive restore does not re-enable arbitrary code.

Qualification: actual ACP/Core/Foundation fresh worker and passive resume followed
by explicit configuration mount select two distinct sealed local provider paths
with the same unchanged original git@main/settings and isolated registry. Network
is denied, no prompt/provider request/login/refresh occurs, and the unavailable
provider remains mounted. Unit/regression checks cover invalid maps, credential
redaction, empty-map baseline, exact dynamic-child precedence and independent
unlisted module sources. Source, installed artifact, loaded-file origin and real
account acceptance remain separate evidence.

The effective-source regression checks cover both models and the schema metadata
shortcut, changes during discovery, mounted/admin key equality, unchanged default
policy, and declared/source-less providers in actual fresh Core/Foundation workers
and isolated defaults probes. These checks do not use authentication or inference.

The persistence regression captures real effective configuration, creates a
separate-process configuration clone, removes the operator override, and resumes
both identities in fresh actual Core/Foundation workers. They return to installed
provider source selection; no candidate path appears in the cloned declaration.
No prompt, model request, authentication or network is involved.
