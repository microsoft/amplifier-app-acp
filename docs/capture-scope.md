# Continuous native capture exclusion

A full native archive alone is not proof that native writers stayed excluded
while a separate product archive was sealed. The optional `captureScope` native
maintenance capability holds the **same connection-owned native home OS lock**
from before source copying until the trusted offline coordinator finishes or
abandons its product archive. The native admin writer lock must already be held
by that connection's exact recovery administration fence.

Negotiate `admin.maintenance.captureScope.version: 1`. The marker declares
`connectionScoped: true`, `beforePrepare: true`, `retainsNativeHome: true`,
`maxArtifacts: 16`, `expiry: "connection-lifetime"`. No Core/session is started.

All methods use private `_amplifier/admin`; see
`schemas/native-capture-scope.v1.json`. They are not generic agent/browser actions.

1. The offline coordinator verifies the real stopped-service receipt and complete
   owner census against its frozen supervisor authority. It computes
   `stopReceiptSha256` over that exact evidence and retains it. Native treats this
   digest as opaque provenance; it does not authenticate another owner.
2. Acquire the native admin recovery fence with the coordinator's exact
   `instanceId` and `dataScope`. In the matching `withMaintenanceFence`, invoke
   `maintenance.capture.acquire` with `{commandId, nonce, instanceId, dataScope,
   stopReceiptSha256}`. Nonce is 32–128 characters. The result includes
   `captureId`, `adminInstanceId`, all those context fields, `active` and
   `artifacts`. Acquisition requires full-native launcher authority and explicit
   stopped-external-writers policy. Different coordinator scope is refused.
3. Create/prepare the full-native plan, acquire the ordinary archive lease and
   snapshot it. These operations borrow the retained capture gate. Releasing or
   expiring the ordinary archive lease cannot release capture exclusion. Plans
   prepared before capture have no capture evidence and cannot be upgraded by
   reading them inside a later capture.
4. `maintenance.capture.inspect {captureId,nonce}` reads live original-connection
   authority. Each artifact binding includes `artifactId`, `sha256`,
   `manifestDigest`, `manifestDigestKind`, `bytes`, `previewHash`.
   `manifestDigest` is SHA256 of exact `native-manifest.sqlite3` bytes, not JSON.
   `maintenance.artifact.inspect` verifies artifact bytes and returns the same
   immutable capture context/binding. Its `liveAuthority` field requires a live
   capture inspection; immutable metadata alone never proves a current lease.
5. Stream the artifact with the existing bounded artifact reader. The product
   coordinator checks live capture identity and exact bindings before each
   product file and before/after its final fsync/seal. A failed request, changed
   process, inactive lease or mismatched binding prevents a complete-product
   claim. A prebuilt artifact remains partial product evidence.
6. Release the ordinary archive lease, then invoke
   `maintenance.capture.release` with `{commandId,captureId,nonce,outcome:"sealed",
   artifactId,artifactSha256,manifestDigest,productSha256}`. The native owner checks
   that exact artifact belongs to this live capture, binds the external product
   digest as coordinator provenance, and records the conclusive release receipt.
   To abandon the product archive, use `outcome:"aborted"` without artifact or
   product seal fields. The original native artifact is retained. Finally release
   the admin fence using the authenticated coordinator outcome.

`maintenance.receipt` gives exact durable acquire/release receipts. Repeating a
command never acquires a second gate or repeats file effects. An old acquisition
receipt is explicitly reported inactive on a replacement connection. A process
that still holds the admin lease makes other-process administration requests
refuse as busy; after its death a new process can inspect receipts but cannot
recover its live capture identity. Only a new explicitly admitted capture can
start new copying.

There is no between-operation TTL on capture exclusion. The ordinary snapshot's
bounded runtime/idle lease still applies, but its borrowed gate cannot free the
wider lock. Transport cancellation and close join actual copy threads before
releasing exclusion. Process death releases OS locks and invalidates the product
proof. Native admin release refuses while a capture lease is active.

Native exclusion covers cooperating native workers/admin writers. The configured
stopped policy for noncooperating/legacy writers remains an explicit operator
assumption. Independent product owners and supervisor/installer state remain the
product coordinator's responsibility; native never sets completeProductBackup.
