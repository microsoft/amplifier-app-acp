# Selected native maintenance

This private ACP administration owner provides a writer-fenced snapshot of an
explicit selection and a reversible reset of selected noncanonical configuration.
It does not provide a full-home or full-product backup. Every archive has an exact
inclusion manifest and omissions. No native runtime or model starts for any of
these operations, and no history is discovered by a global scan.

Enable `adminMaintenance:true` and `adminWorkspaceRoots` in the trusted launcher.
The negotiated `amplifier.dev/native.admin.maintenance` capability is
`{version:1,selectedOnly:true,privateArtifacts:true}`. Calls use `_amplifier/admin`
with `{cwd:<authorized coordinator workspace>,operation,args}`. Keep this
connection private: these are coordinator primitives, not public agent actions or
arbitrary browser filesystem access. The product owns user approval, its own
intake fence, every other owner's quiescence/export, and archive presentation.

The launcher must explicitly configure `maintenanceExternalWriters` as either
`foundation-cooperative` or `stopped` before selecting any native session. This is
deployment evidence asserted by the trusted launcher, not inferred from a PID or
supplied by the caller. Verify that all potential external CLI/host writers use
Foundation's shared session lock, or stop them. Old noncooperating CLI writers
cannot be protected by this adapter; unverified selections are refused. File
stamps and hashes additionally detect changing sources but cannot replace writer
cooperation. Native settings use the existing Foundation settings file locks.

## Snapshot flow

1. Close product intake with the product's durable maintenance fence. Retire owned
   native agents using negotiated `_amplifier/retire`. Busy/background/delegated
   or uncertain work must refuse quiescence; do not force-kill or replay it.
2. `maintenance.preview {sessions:[{nativeSessionId,cwd}],parts,
   privateContentReviewed:true,includeCredentials?:false,credentialsReviewed?:false}`
   returns `previewHash`, the explicit selection, file hashes/sizes, missing paths,
   exclusions and omissions. Session IDs remain canonical native IDs. Up to 32
   existing sessions and 2,048 directory/file entries are permitted. `parts` is a
   nonempty subset of `session-history`, `session-state`, `native-settings`.
3. `maintenance.acquire {commandId,previewHash,ttlSeconds?:60}` acquires a native
   home exclusive OS gate and all selected Foundation writer leases without
   waiting. It verifies the exact preview again. It returns `leaseId`, `active`,
   `expiresAt` and its durable receipt. TTL is 1–300 seconds. A known acquisition
   refusal has `executed:false`; exact command inspection retains that refusal.
4. `maintenance.snapshot {commandId,leaseId,previewHash}` streams a private TAR
   archive and returns `artifactId`, `sha256`, `bytes`, `contentType`, `manifest`
   and receipt. No caller chooses an output path. The default source limit is
   256 MiB; trusted `maintenanceMaxBytes` can raise it to at most 4 GiB. The
   inclusion manifest is limited to 512 KiB. Bounds cause explicit refusal, never
   silent omission or truncation. Retained partial artifacts are not published.
5. `maintenance.artifact.read {artifactId,sha256,offset?:0,maxBytes?:262144}` returns
   base64 `data`, `offset`, `nextOffset`, total `bytes`, whole-artifact `sha256` and
   `chunkSha256`. Verify chunks and the final whole hash. The artifact remains
   readable after lease release and coordinator restart. Its authority is the
   configured native home plus original authorized coordinator workspace. Adapt
   this private interface to an authenticated product resource if needed; never
   put archive bytes into a generic topic.
6. `maintenance.release {leaseId}` releases only the exact connection-owned lease.
   `maintenance.lease {}` reports current connection lease state. Connection
   close, expiry, and process death release OS locks. Expiry aborts an in-flight
   operation at a bounded file chunk and joins its file thread before releasing
   locks. A still-running operation is never allowed to outlive its writer fence.

Native workers hold shared OS gates for their whole lifetime, including parked
and background work. ACP native requests and administration hold shared gates
around their effects. Interactive admin sign-in retains its own shared gate
until its background task and cleanup finish. Gates are lazy per configured native home, with no hot
catalog, historical session inventory, polling process scanner, or install-global
lock. They use POSIX `flock`, consistent with Foundation's writer leases. Lock
files remain as stable inodes; process death releases ownership without deleting
them. An expired or disconnected lease is never automatically reacquired under
an old command. The product must retain its own durable intake fence until its
whole maintenance operation is conclusively resolved.

### Coverage and private content

- `session-history` captures all regular files beneath each explicitly selected
  canonical native directory, including transcript, metadata, events and local
  context-intelligence files. Child histories are separate explicit selections.
- `session-state` captures all regular files beneath each selected adapter session
  directory, including configuration, receipts and completed/uncertain markers.
  SQLite database and WAL files are copied while all adapter requests/writers are
  fenced. Transient `.lock` and SQLite shared-memory files are explicitly excluded.
- `native-settings` captures shared `settings.yaml` and adapter
  `admin-settings.yaml`. `keys.env` requires both `includeCredentials:true` and
  `credentialsReviewed:true`. No credentials are returned in preview or receipts.
  Excluding keys.env does **not** sanitize inline secrets from selected transcripts
  or configuration; `privateContentReviewed:true` acknowledges private content.
- Unselected histories, independent product/client stores, workspace files,
  arbitrary artifacts, module source/cache/generations, Foundation live leases,
  transfer authority, external credential stores and shared routing/bundle trees
  are explicitly omitted. Environment variables and browser drafts are never
  copied. Symlinks, hard links and special files cause refusal.

The archive retains original native identity and byte content. It is neither a
portable transfer capsule nor an installation manifest. Importing/restoring a
whole archive into a running native home is not implemented. The product must
label a stopped aggregate snapshot according to its actual owner coverage, not
infer atomicity across independently running stores.

## Selected configuration reset

`maintenance.reset.preview {nativeSessionId,cwd,scope:'session-configuration',
privateContentReviewed:true}` returns the same exact preview structure plus reset
effects and preservation. Acquire its `previewHash` normally, then call
`maintenance.reset.apply {commandId,leaseId,previewHash}`.

This removes only the selected adapter's `configuration.json` and
`effective-configuration.json`, and clears `selection`, `configurator`, `mode`,
and `budget` from `control-state.json`. All original bytes are retained privately
before any change. Goal, task, task/capacity receipts, native history/events,
workspace files, bundle identity, execution location and transfer ownership are
preserved. Pending configuration, bundle, history-edit, relocation or reset
transactions refuse reset. No worker automatically resumes.

The result contains `retainedId`, `postResetHash`, `nativeSessionId` and
`canonicalFilesChanged:0`. For an explicit undo, obtain a fresh reset preview and
lease, then call `maintenance.reset.restore {commandId,leaseId,previewHash,
resetCommandId,expectedPostResetHash}`. Undo requires the exact completed reset
receipt and unchanged post-reset configuration. It restores original bytes and
retains the replaced post-reset bytes. Newer configuration is never overwritten.

Reset writes are fenced by a durable `maintenance-reset-pending.json` marker until
the completed command receipt is saved. Interrupted/uncertain writes keep that
marker and originals; native startup refuses them. Exact repeated commands
inspect their receipt and never reapply changes. Unknown partial writes require
explicit operator recovery; this owner does not guess that they succeeded or
automatically roll them back. If only fence removal was interrupted **after** a
completed reset receipt, obtain an ordinary selected snapshot preview/lease, then
`maintenance.reset.reconcile {commandId,leaseId,previewHash,resetCommandId}` proves
the completed post-reset hash and removes its exact leftover marker. It never
reexecutes the reset and refuses unknown original receipts.

`maintenance.receipt {commandId}` retrieves an exact original operation.
Receipts are `running`, `succeeded`, `refused`, or `unknown`; a saved running
operation observed after interruption is unknown. There is no mutation retry on
lost transport. Neither retention nor native generation deletion is implemented:
safe reclamation needs an authoritative live/configuration reference index.

## Paged native archive plans

The additive `admin.maintenance.archivePlans` capability advertises version1,
32-selection batches and paged manifests. This path extends native coverage;
it does not replace the existing small selected-preview/reset contract.

1. `maintenance.archive.create {commandId,parts,privateContentReviewed:true,
   includeCredentials?:false,credentialsReviewed?:false}` creates an opaque plan.
   Parts are `session-history`, `session-state`, `shared-configuration`, and
   `workspace-configuration`. The response is `{planId,revision:0,phase:'open'}`.
2. `maintenance.archive.add {commandId,planId,expectedRevision,sessions?:
   [{nativeSessionId,cwd}],workspaces?:[cwd]}` appends at most32 of each per batch.
   Every native identity and workspace is checked against launcher authority.
   Repeat exact command IDs inspect saved receipts; new batches require the
   returned revision. There is no discovery, directory listing or implicit child
   inclusion. Up to100,000 explicit selections fit in the private SQLite plan.
3. `maintenance.archive.prepare {commandId,planId,expectedRevision}` seals the
   selection and returns an immutable `previewHash`, compact inventory counts,
   selection/manifest hashes, omissions, exclusions and credential coverage.
   The inventory stays in SQLite, not in a response or hot application state.
   `maintenanceMaxManifestEntries` defaults to100,000 (maximum1,000,000); the
   existing configured source byte limit applies. Exceeding either fails
   explicitly, with private partial plan retained and no source changes.
4. `maintenance.archive.manifest {previewHash,cursor?,limit?:50}` reads1–100
   entries by indexed keyset cursor. Entries have `path`, `status` (included,
   directory, missing), and included `bytes/sha256`; no absolute source path.
   It returns `items,nextCursor,inventory,coverage,omissions,exclusions`.
   Immutable-plan identity/authority checks are bounded metadata reads, including
   after restart. Paging does not rescan source files or hydrate the manifest.
5. Existing acquire/snapshot/release and chunked artifact reads consume the
   sealed hash. The snapshot verifies the complete explicit selection before
   and after capture. A TAR contains `manifest.json` with `{summary,entries}`;
   entries stream from SQLite into the private manifest file. Archive bytes and
   manifest entries are never assembled into one RPC result. Source changes,
   unsafe filesystem objects and lease expiry prevent publication of a completed
   artifact. A fresh review is required, never an implicit recapture.

`shared-configuration` includes shared `settings.yaml`, `routing/`, `bundles/`,
and adapter `admin-settings.yaml`. Reviewed credential inclusion additionally
captures `keys.env`; excluding it does not redact inline secrets elsewhere.
`workspace-configuration` includes only explicitly named workspaces' `.amplifier/`
`settings.yaml`, `settings.local.yaml`, `routing/`, `routing.local/`, and `bundles/`.
Missing named roots appear in the inventory. Unknown home entries, workspace
source files, module caches, generation stores, product/client stores and
external credential stores remain omitted. Transient `.lock`/`-shm` are excluded.
Native-history authority and original files are not modified.

Shared/workspace configuration trees require the trusted launcher assertion
`maintenanceExternalWriters:'stopped'`: current settings file locks do not cover
arbitrary routing/bundle directory changes by external tools. Plans selecting
more than32 sessions also require stopped external writers, avoiding thousands
of simultaneously live Foundation writer leases. Smaller cooperative plans
acquire actual Foundation session writer leases. The native-home exclusive OS
gate remains mandatory in both modes. This assertion is deployment authority,
not an untrusted browser checkbox or proof inferred from a momentary idle sample.

Plan create/add/prepare failures that finish their private file thread have a
`failed` receipt with `settled:true,canonicalFilesChanged:0`. Private partial
selection/manifest files can remain; this is not `executed:false`. Process loss
before a conclusive receipt remains `unknown`. No interrupted plan is resumed
or rebuilt automatically. Only exact receipt inspection can resolve it.

See [the retained-behavior audit](recovery-parity-audit.md) for full-product backup,
credential reset, managed deletion and retention boundaries.

### Explicit native private stores

`archivePlans.nativeStores` advertises `{version:1,parts:["native-import-records",
"native-preference-receipts"],requiresRecoveryAdminLease:true}`. These additive
parts use the existing create/prepare/capture wire. They are never silently added
to a selected-session or configuration archive.

* `native-import-records` preserves the adapter's original imported upload bytes,
  prepared evidence, target reservations and exact command receipts.
* `native-preference-receipts` preserves shared voice-default command receipts.
  The settings and last-write proof are in `shared-configuration`, which must be
  selected separately for their preservation.

Both selections cover **all records in the configured native app home**, not only
the current workspace. This scope is explicit in the returned `ownerCoverage`.
Prepare requires this connection's exact held recovery admin lease; service-stop
and update leases grant no maintenance rights. An exclusive native writer gate
also fences live native workers during image creation. Launcher-owned paths select
only these adapter stores; browser file paths and foreign databases are not read.

The owner uses SQLite's backup API in bounded page batches to create standalone
database images. Committed WAL contents are included in each image, but original
WAL/SHM files are never archived or checkpointed. Dormant stores are opened as
immutable only when the held owner gates prove no writer and no WAL exists.
Integrity, source revisions, sizes and deadlines are checked; missing stores are
reported without creating them. Source changes after review require a new plan.
Source contents remain private; previews include only store scope, table counts,
image hash/bytes and an opaque source revision. Manifest entries and artifact reads
retain existing pagination and 256 KiB chunk limits. No restore or execution is
authorized by these images; uncertain receipts stay uncertain.

`ownerCoverage` has `included` store rows and typed `omitted` owner rows, plus
`completeNativeBackup:false` and `completeProductBackup:false`. Maintenance command
receipts, retained archive artifacts/plans and reset originals remain an explicit
`native-maintenance` omission in this version. Independent product stores,
unselected histories/configuration, runtime generations and existing documented
exclusions are still outside coverage. This is a selected native archive, not a
full native or configured-product backup.

`test_private_store_snapshot.py` qualifies actual ACP subprocesses with impossible
worker launchers: original upload and receipt fidelity, committed WAL inclusion,
source-byte preservation, exact repeat/restart reads, missing stores, stale review,
held lease authority, corrupted/foreign databases and unsafe paths. It makes no
provider, credential, model-account or live-service calls.
