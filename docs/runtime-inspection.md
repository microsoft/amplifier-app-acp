# Native runtime source evidence

Three separate statements have different authorities: the configured update
policy, a recorded qualified installation, and the process currently executing a
session. Neither a registry entry nor a lock file alone proves worker adoption.
These operations do not start workers, contact a provider, resolve remote Git
branches, or read historical conversation content.

Launcher-authorized `_amplifier/admin` operation `generations.current` accepts
`{generation?,cursor?,limit?,expectedRevision?}`. It requires `adminGenerations`
and the existing trusted admin workspace boundary. A missing generation returns
`available:false`. A selected generation reads only its bounded qualification and
installed-distribution receipts plus the current manifest; it does not scan
projects, sessions, or the live environment. Pages contain 1–100 distribution
rows. The source-policy hash, recorded baseline hash, last explicit source check,
qualification receipt hash and pointer remain separate. `matchesRecordedBaseline`
reports policy equality, not remote currency. `verification.installedGraph` and
`verification.loadedCode` explicitly remain `not_checked` on this read.

Negotiated native `runtimeInspection:{version:1,residentOnly:true}` enables
`_amplifier/native runtime.inspect {surface?:'distributions'|'mounted',refresh?,cursor?,limit?,expectedRevision?}`.
It reads the original resident worker only. The passive `_amplifier/runtime {sessionId,cwd,...pageArgs}` form can return
`available:false,workerStarted:false` without loading a session; its negative
status describes only this ACP connection, never other processes. Standard
session/resume remains an explicit mount. An already retired handle refuses
inspection without remounting. A host should use `nativeControlExisting` so its
own transport also cannot create a process to satisfy this inspection.

Installed distributions and mounted implementations have independent bounded
pages selected by surface; a distribution page never resends all module mounts.
The first request captures a selected snapshot; `refresh:true` replaces it.
Cursor pages are tied to its revision and original worker instance. The result
contains the actual interpreter path/version, captured generation identity,
installed distribution versions and PEP 610 provenance, and mounted provider,
tool, context and orchestrator implementation origins. Known application wrapper
chains are shown separately from their underlying implementations. Hook
registration and delegated sessions are explicitly outside this root snapshot;
a package appearing in the installed list does not establish that it is mounted.
Loaded module origins do not prove that a subsequently changed file equals the
bytes already executing (`loadedCodeBytesVerified:false`). Generation policy,
source checks, a fresh worker mount and functional acceptance remain distinct.

URLs remove userinfo, query strings and fragments; config objects, model keys,
provider credentials, prompts and reports are never included. Snapshots allow at
most 5000 installed distributions and 8 MiB internally; each public page is
bounded. Runtime source receipts never become authority for execution, mounting,
installation, or accepting client-supplied interpreter paths.

The distribution owns its active-worker inventory and aggregate currency UI.
It can compare the host's resident worker lifetime/generation to
`generations.current`, inspect selected workers, and invalidate after confirmed
lifecycle changes. Absent worker authority must stay unavailable. A distribution
self-updater still needs its own installed release manifest, command receipt,
quiescence/restart authority and fresh-process adoption proof.

Trusted launcher source overrides are reported separately as bounded
`moduleSourceOverrides`; see [the launcher source contract](launcher-module-sources.md).
Those selections do not replace mounted file-origin or sealed-byte evidence.
