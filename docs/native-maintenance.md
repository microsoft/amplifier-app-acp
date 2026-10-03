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
