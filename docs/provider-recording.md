# Provider request recording policy

The trusted launcher boolean `adminProviderRecording` enables the optional
administration descriptor `providerRecording` with version 1, CAS and receipts,
and application `next-runtime-preparation`. Without the grant, recording-policy
actions refuse before execution and the descriptor is absent.

`provider.recording.get` takes no arguments. It returns `enabled`, a SHA-256
`revision`, and the application boundary. `provider.recording.configure` takes
exactly `commandId`, `expectedRevision`, and boolean `enabled`.
`provider.recording.receipt` takes the original `commandId`. Reads are passive
and permitted under held intake; mutations use the existing administration gate.

Policy remains in `appHome/diagnostics/config.json` as `providerRequests`, so
the existing application can still read it. Unrelated fields are retained and
included in CAS. Explicit provider opt-outs remain authoritative. The existing
runtime preparation helper applies the default to each supported provider; this
does not interrupt or reconfigure an already running worker. Earlier requests
cannot be reconstructed.

A bounded receipt journal and an atomically published file marker preserve
original results after acknowledgement loss. Unknown commands are inspected,
never repeated. A later policy change first retains the prior marker's receipt.
Malformed existing policy is preserved and refuses writes. Receipt capacity is
explicitly bounded; old receipts are not silently evicted.

Full native authority archives preserve both policy and receipts. The explicit
`native-recording-receipts` part captures an owner-made SQLite image under a
held recovery lease. It is additive selected coverage, not a complete backup.
Private store revisions bind identity, size, mtime and bytes; ctime-only changes
between stable reads do not reject a read-only SQLite backup.

`test_provider_recording_policy.py` exercises installed adapter policy, every
supported provider flag, opt-outs, lost acknowledgements, malformed files,
launcher gating and held intake. `test_private_store_snapshot.py` verifies
original receipt images and preservation of committed WAL bytes. These checks
perform no real account inference.
