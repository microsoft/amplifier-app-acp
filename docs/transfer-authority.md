# Native transfer authority

This optional boundary requires launcher `transferAuthorityDirectory` (the same
private `TransferNode` journal as the trusted portability owner),
`transferWorkspaceRoots`, and the `portability` package extra. Its negotiated
capability is `amplifier.dev/native.transfer={version:1,passiveInspection:true}`.
The transport is `_amplifier/transfer {sessionId,cwd,operation,args:{transferId,...}}`.
Native IDs are preserved; cwd is the immutable native history workspace. Source
and destination are SHA-256 hexadecimal public-key fingerprints, transferId a UUID.
No request may choose the native home, journal, trust roots or probe executable.

Source operations: `source.fence`, `source.capture`, `source.read` (name,offset,
limit<=65536), `source.verify`, `source.commit`, `source.cancel`. Retire any resident
worker first. Fencing acquires Foundation's real ownership handle; it never kills
an unknown writer. Capture returns an immutable file manifest and portable intent;
window reads return base64 bytes. Source commit requires the exact signed journal
release phase and unchanged native snapshot. Original canonical files/events stay
in place, permanently fenced on the source. This deliberately differs from the
old Unified implementation's private relocation of source history files.

Destination operations: `destination.fence`, `destination.policy`,
`destination.check`, `destination.install`, `destination.activate`. Staged history
remains in the private authenticated capsule until a signed source release exists.
Destination installation preserves canonical bytes and native identity. Only
provider/model selection intent is mounted; task/goal/approval/job execution
rights and source executable configuration are never restored. The default bundle
and provider configuration must already exist independently on destination.

Readiness uses the retained single-attempt provider contract, fixed prompt, one
count request, one generation request, no tools/retries/continuations, and no
default elapsed timeout. Each staging/activation probe has a durable no-replay
receipt before launch. Custom native workers require an explicit matching
`transferProbeCommand`; the adapter cannot claim another environment is qualified.
A probe proves only that the selected model request was accepted with destination
configuration, never a human/account/organization identity. Real credentials are
not required by the offline fixture qualification.

`inspect` returns `{receipt,fence,transferPhase}` without a worker. Mutations
return a top-level proof with version, transferId, nativeSessionId, historyCwd,
executionDirectory, phase, source, destination, inputsReplayed:false, and
`authority:'foundation-transfer-fence'`. Source fence/commit include `fence`.
Destination activated additionally includes releaseHash and historyRevision.
Host adoption must fetch this persisted proof independently and compare every
identity/path with its configured destination fingerprint; caller booleans grant
nothing. Interrupted installation/activation remains explicit unknown and is
never retried automatically. Source cancellation clears only an unreleased exact
staged fence after a durable cancellation receipt.

Limits: native file32MiB, aggregate native64MiB, complete signed capsule64MiB.
External attachment payloads and live/native task authority are explicit reviewed
omissions. Signed owner evidence can preserve historical operational observations
without activating jobs, questions, schedules, secrets, or client drafts.
