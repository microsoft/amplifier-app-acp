# Trusted launch source closure

`sourceResolutionManifest` is a trusted JSON launcher field with `path` (an
existing absolute manifest file) and `sha256` (its exact SHA256). ACP requests
cannot set it. The adapter inherits the reference into worker and passive probe
processes as `AMPLIFIER_ACP_SOURCE_RESOLUTION_MANIFEST`. An inherited reference
cannot be replaced by another launcher value. Absence preserves the earlier
installation-only policy; `runtimeImmutable` alone does not claim source closure.
A launch claiming qualified source closure must supply this manifest.

The adapter requires the Foundation public `source_resolution_policy` and
`SourceResolutionDenied` and `SOURCE_RESOLUTION_POLICY_VERSION == 1` APIs before launch effects. It installs one cached
admission callback per manifest/process around preparation, root/include loads,
and governed passive queries. Nested scopes reuse the same callback. Ordinary
source declarations, settings URIs, and exported mount plans retain their source
intent; admitted `ResolvedSource` objects select the qualified local paths.

Manifest version 1 has `sources`, a nonempty array (at most 512 rows). Each row
has exactly these fields:

- `requestedUri`: exact credential-free source URI, including any fragment.
- `basePath`: exact existing absolute base for relative resolution, or `null`
  for a source whose binding applies independently of the base.
- `activePath`, `sourceRoot`: existing canonical absolute paths; active path is
  within the source root. Git sources require `resolvedCommit`, a full 40/64
  character lowercase commit hash; local sources may use `null`.
- `approval`: bounded review/exception reference, or `null`. This records review
  provenance; the trusted launcher remains the authority approving this launch.
- `files`: mapping of relative POSIX file paths to lowercase SHA256 digests.
- `admissionFiles`: 1–128 file names from `files` whose bytes must match before
  this entry is returned to Foundation. Include the actual bundle definition,
  module entrypoint, and package/project metadata consumed at admission.
- `packages`: up to 128 top-level Python import names whose effective origins
  must belong to this root and to its file manifest.

Include aliases for original requested URIs and effective local URIs emitted by
`moduleSources`, plus all root, include, resource, and lazy module sources.
Effective canonical absolute paths and local file URIs are admitted when they
belong to a declared root and their file/directory membership exists in the
manifest. Relative URI bindings must include their exact base. Unbound resolution refuses
without fallback. Rows sharing a physical root may declare disjoint file subsets. Local member
admission searches those subsets independent of row order; conflicting hashes
for the same file refuse. The document is bounded to 16 MiB and 65,536 file records.

## Qualification and runtime checks

Candidate preparation must independently verify the complete closure file
membership/digests, resolved commits, reviewed exceptions, and all configured
provider contracts. It should cover unused configured providers and record
actual loaded origins. The adapter does not run Git, modify settings/caches,
install packages, contact providers, or reproduce unknown creation effects.

Runtime checks read only the digest-bound manifest, needed admission files, and
actual importable/loaded package origins. Unchanged file checks are cached by
file identity, size, mtime and ctime. The adapter does not scan the entire forest
on each metadata request. Unsupported Foundation protection, changed bytes,
outside origins, unbound URIs and mismatched relative bases refuse. It does not
unload/reimport a conflicting package. The private mounted report contains
`sourceClosure` origin attestations and the manifest digest.

Native also checks every path in the Foundation public `prepared_sources()`
readonly snapshot after preparation. Foundation must enforce admission on prepared-path/cache reuse and before any
activated package code executes. Native checks before scope entry and after
preparation do not alone prove that internal activation inspected no wrong code.
The Foundation candidate must qualify that boundary; a source resolver callback
alone cannot prove it.

These checks assume qualified source trees remain sealed between candidate
qualification and load. They do not provide an OS sandbox or atomic protection
against a hostile concurrent filesystem writer, same-metadata replacement,
post-check swaps, or malicious provider code changing process state. No full
forest verification or memory-limit guarantee is claimed at runtime.

Only an explicitly verified separate mutable staging environment drops an
inherited reference. A readonly candidate retains it until its trusted launcher
supplies the candidate's qualified reference.

## Worker readiness

When the launcher binds a manifest, `NativeWorker.start` additionally requires
an exact typed `sourceClosure` readiness marker: version 1, the bound
`manifestSha256`, and `sourceResolution: qualified-only`. Missing, partial,
extra-field, wrong-version, wrong-digest or wrong-type reports refuse and join
the child through existing cleanup. The current worker projects this compact
marker from its private attested mounted report. This confirms protocol support;
it is not a sandbox against a dishonest worker. Without a manifest the existing
dependency readiness behavior is unchanged.
