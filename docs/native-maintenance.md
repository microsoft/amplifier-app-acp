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
"native-preference-receipts","native-maintenance-records",
"native-retained-archives"],requiresRecoveryAdminLease:true}`. These additive
parts use the existing create/prepare/capture wire. They are never silently added
to a selected-session or configuration archive.

* `native-import-records` preserves the adapter's original imported upload bytes,
  prepared evidence, target reservations and exact command receipts.
* `native-preference-receipts` preserves shared voice-default command receipts.
  The settings and last-write proof are in `shared-configuration`, which must be
  selected separately for their preservation.

These import/preference selections cover **all records in the configured native app home**, not only
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

`ownerCoverage` has `included` store rows, typed authoritative `omitted` rows,
`authoritativeSelection`, and separate transient/derived exclusions. It always
reports `completeNativeBackup:false` and `completeProductBackup:false`: canonical
histories and session state cover explicit session IDs, workspace configuration
covers explicit directories, and shared configuration is an independent selection.
Runtime generation authority, workspace/module sources and independent product
stores remain outside this contract. An aggregator must prove its complete desired
authority set; an empty private-owner omission list is not proof of full backup.

### Maintenance records and retained archive bodies

`native-maintenance-records` includes the owner's receipt database, pre-existing
preview records, plan databases and seals, retained reset originals, and artifact
descriptions/manifests. `native-retained-archives` additionally includes prior
complete/partial archive bodies and prepared historical plan images, and requires
records to be selected. Omitted bodies are authoritative historical evidence, not
rebuildable cache. They are explicitly listed as unselected when excluded. Neither
selection enumerates arbitrary native homes, project histories or product stores.

Both use the same exact recovery-admin lease and exclusive native gate. Database
files become standalone SQLite backup images; records and optional payloads stream
into private plan storage under byte, entry and 120-second preparation limits.
The current plan is excluded before traversal. The source cutoff is recorded as
`{capturedAt,phase:'before-current-plan-seal',excludedPlanId,prepareCommandId,
currentPrepareReceipt:'running-is-uncertain-never-replay',artifactBodies}`.
At that cutoff, the current prepare receipt is still running and no current capture
artifact exists. Preserved running receipts are uncertain history, never executable
work. New receipts or plans created afterward are outside the reviewed image;
ongoing capture bookkeeping cannot invalidate its own snapshot. Original stores
and prior archive bytes remain unchanged.

The immutable prepared images are verified by the paged manifest during capture.
This is deliberately different from the import/preference images, whose source
revisions must still match review. Unknown entries in the maintenance owner's
versioned roots fail explicitly instead of silently disappearing. Existing images
and old archives may be large; optional inclusion fails its explicit byte budget
rather than recursively expanding current output or loading payloads in memory.
The cutoff excludes only this capture's plan, not previous uncertain evidence.

These archives do not authorize restoration or automatic continuation. A future
restore owner must validate original receipts, rebind filesystem-dependent plan
seals against verified image hashes, and preserve uncertain states; extracting
SQLite images into a running owner directory is unsupported.

`test_private_store_snapshot.py` qualifies actual ACP subprocesses with impossible
worker launchers: original upload and receipt fidelity, committed WAL inclusion,
source-byte preservation, exact repeat/restart reads, missing stores, stale review,
held lease authority, corrupted/foreign databases and unsafe paths. It makes no
provider, credential, model-account or live-service calls.

`test_maintenance_store_snapshot.py` adds actual Core/Foundation reset-original
preservation, cutoff receipt truth, prior archive-byte fidelity, no self-inclusion,
SQLite image integrity, explicit optional-body omissions, unknown/unsafe source
refusal and exact failed-command inspection without replay.

### Explicit full native authority and new-destination restore

The separate sole part `full-native-authority` is enabled only by trusted launcher
`maintenanceFullNative:true`. It requires the exact recovery admin lease, native
exclusive OS gate and `maintenanceExternalWriters:'stopped'`. No discovery runs at
startup or during normal chat. Explicit preparation streams an on-disk inventory
and private immutable copies under byte, entry, depth and elapsed-time limits.
`maintenanceMaxBytes` permits at most 1 TiB for this opt-in path; the default stays
256 MiB. `maintenanceMaxManifestEntries` defaults to 100,000 (maximum 1,000,000),
and `maintenanceFullTimeoutSeconds` defaults to 600 (maximum 7,200).

Native and adapter homes must be disjoint. Exact effective external authority roots
must be declared in `maintenanceFullNativeRoots`: Foundation `checkpoint`, relocated
`events`, bundle `registry`, module `sources`, and local `bundle` / `runtimeManifest`
files when outside the two homes. Effective checkpoint/event/source locations come
from their existing supported environment variables, never an arbitrary browser
path. A root already inside a captured home needs no duplicate declaration.
Unknown top-level home entries, symbolic/hard links, unresolved journals,
unqualified installed environments and unsupported checkpoint layouts refuse the
archive. This conservative refusal is visible; no unknown entry silently becomes
cache. New native owners must add their authority roots deliberately.

The archive includes every native project/child history, events, settings and
session-state record, even for absent historical workspaces, plus import uploads
and receipts, voice preference receipts, permission receipts, maintenance records,
old prepared images and retained archives, generation policy/lock/source receipts,
and configured native source/registry roots. Every SQLite database becomes a
standalone backup image with committed WAL contents. OS locks, SQLite coordination
sidecars, advisory worker leases and Python bytecode are explicit transient/derived
exclusions. A generation `.venv` is excluded only after its exact recorded graph
qualifies; lockfiles such as `runtime.lock` and `uv.lock` remain authoritative.

The cold metadata inventory collects known native workspaces into a temporary
SQLite index. Only existing workspaces authorized by configured admin roots can
supply `.amplifier` configuration. Their settings/routing/bundle trees and private
path mapping are included in `workspace-settings`; missing workspace directories
are recorded. Unreadable/ambiguous metadata or existing workspaces outside allowed
roots create authoritative omissions, making `completeNativeBackup:false`.
Unselected credential stores also make that flag false. Explicit credential review
permits native keys/OAuth records; histories and settings can still contain private
material regardless of that selection. No inherited environment is serialized.
`completeProductBackup` is always false. External workspace files, external
credential services and source dependencies outside native-owned roots remain
separate owner/installation requirements. The component cannot prove all CLI
writers stopped: the trusted launcher must establish that prerequisite.

`amplifier-native-authority` version 1 TAR starts with bounded `native-archive.json`,
then a hashed standalone `native-manifest.sqlite3`, then streamed file payloads.
The manifest is paged through existing archive APIs; whole history lists/bodies
never enter hot state or the public preview. Preparation records its pre-seal
cutoff, excludes its own plan before traversal and captures prior running receipts
as uncertain evidence. It never recursively captures the current artifact.
The format is described in `schemas/native-full-archive.v1.json`.

Restore is enabled only with configured `maintenanceRestoreRoots:{rootId:parent}`.
Each parent must already exist outside the source native/adapter homes. Operations:

- `maintenance.restore.preview`: `{artifactId,sha256,destination:{rootId,name},
  privateContentReviewed:true,credentialsReviewed?:true}` verifies this owner's
  exact private artifact and returns immutable `previewHash`, bounded inventory,
  coverage and review requirements.
- `maintenance.restore.manifest`: `{previewHash,cursor?,limit?:1..100}` reads its
  immutable indexed page. This exact passive read remains available while held.
- `maintenance.restore.apply`: `{commandId,previewHash}` requires the matching
  held recovery admin authority and creates a **new absent** destination only.
- `maintenance.restore.inspect`: `{restoreCommandId}` passively checks the exact
  original receipt and destination completion record. `finalized:true` requires
  a succeeded original, matching completion record, and absent pending marker.
  Missing/unknown evidence never becomes success and this read clears no fence.
- `maintenance.restore.reconcile`: `{commandId,restoreCommandId}` may finalize a
  leftover destination fence only if the original exact receipt is conclusively
  succeeded. It never repeats extraction or turns unknown into success.

Only this owner's opaque artifact IDs are accepted. There is no arbitrary input
path, overwrite mode, in-place restore, worker start, dependency install or model
call. Headers/versions, SQLite schema, member kinds/paths, duplicate and missing
members, per-file hashes, archive hash, byte/count/depth/time limits and source
stability are verified. Files stream to new private paths; TAR extraction helpers
and untrusted SQLite schemas are never executed. A persistent pending marker plus
OS writer gates protects the new home. Process loss during writing leaves it
fenced and the original command unknown. Exact repeats inspect receipts only.

After a conclusive durable write, generated `native-launcher.json` and
`native-environment.json` map captured roots to the new destination. No worker
commands, admin privileges or unrelated environment values are inherited.
Project settings remain staged in `workspace-settings` and do not overwrite
existing workspaces. Absolute references inside original settings, installed
source policies and old sealed previews remain original evidence: they require
configuration review/rebinding and fresh runtime qualification. Completion itself
does not authorize execution. Original archives/histories/receipts remain intact;
transfer fences, unknown inputs and interrupted work are never converted to work.

Actual Core/Foundation subprocess fixtures qualify full capture and a separately
requested later resume under the same native identity, with exactly one new input
and no history replay. Fault fixtures kill separate owner processes during write
and after receipt persistence, verifying retained fences, no-repeat unknowns and
exact conclusive reconciliation. Malformed TAR/version/schema/path/digest cases
refuse before a destination is created. These tests use only owned offline data;
product aggregation, UI restoration and real-account acceptance are separate.
