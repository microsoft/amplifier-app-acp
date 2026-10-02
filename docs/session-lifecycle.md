# Native session lifecycle

The adapter advertises `amplifier.dev/native.sessionLifecycle` version 1,
`passive:true`, `deletion:"preserve-history"`. Standard ACP `session/delete` is
supported. ACP's standard deletion defines removal from discovery and leaves
storage policy to the agent; this adapter retains canonical files. Standard
`session/fork` remains unstable upstream, so the versioned native extension
provides the required precise native boundary and receipt contract.

`_amplifier/session` accepts `{sessionId,cwd,operation,args}`. `cwd` is the immutable
native history workspace, not a caller-selected destination. The host must bind
it to the authorized session. No operation starts a runtime or calls a model.
The native owner acquires Foundation's OS writer lock for fork/export/removal.
An active owner or unresolved relocation refuses before a mutation receipt.
Hosts fence admission while making these requests. Read-only inspect and export
page reads need no model/worker. No operation scans projects or `events.jsonl`.

- `inspect {}` returns `sessionId`, `historyRevision`, bounded metadata,
  diagnostics, execution `location`, visibility `state`, and `preserved` categories.
- `fork {commandId,targetSessionId,expectedHistoryRevision,throughMessageId?}`
  copies actual native context, tool receipts, and native module/configuration
  choices into an independent UUID reserved by the host. A boundary is a stable
  native user message ID or exact recorded input ID; the whole user-anchored turn
  is retained. Omission means all history. The response includes `sessionId`,
  `creationConfirmed:true`, `historyCwd`, `executionDirectory`, `historyRevision`,
  source identity/revision/boundary, `workReplayed:false`, and a receipt. Native
  source files remain unchanged. Task/goal/job/approval/runtime ownership and
  event logs are not copied. Incomplete historical tool exchanges are retained
  as interrupted evidence, never rerun. A partial fork is fenced against startup.
- `export {commandId,expectedHistoryRevision,format,scope?,fromMessageId?,
  throughMessageId?,minimal?}` freezes public native conversation text and
  references as Markdown or JSON. Scope is `all`, `from`, or inclusive `range`.
  It returns `snapshotId`, `filename`, `mimeType`, `summary`, `capturedAt`,
  `historyRevision`, `contentHash`, and receipt. Snapshot bodies remain in native
  owned storage. Hidden reasoning, tool receipts, and attachment/artifact payloads
  are excluded with explicit omissions; this is a readable export, not a portable
  execution archive. Browser clipboard/download actions stay client-local.
- `export.read {snapshotId,offset?,limit?}` reads at most 65,536 bytes and returns
  `data`, `encoding:"base64"`, `offset`, `nextOffset`, and snapshot metadata.
  Offsets are bytes, so callers concatenate bytes before UTF-8 decoding. The saved
  immutable file stamp is checked. The host exposes an authorized resource URI,
  never an arbitrary local file path.
- `delete.preview {}` returns a ten-minute `confirmationToken`, exact history
  revision, `summary.behavior:"remove-from-discovery"`,
  `summary.canonicalFilesDeleted:0`, and preserved categories.
- `delete {commandId,expectedHistoryRevision,confirmationToken}` commits a
  native visibility tombstone. Canonical transcript, event logs, workspace files,
  operation/tool receipts, and child histories remain. Resume refuses until
  restored; passive history/export remains possible. This is not permanent
  managed-folder deletion.
- `restore {commandId,expectedHistoryRevision}` clears that visibility tombstone.
- `receipt {commandId}` reads the exact original command outcome. All mutation
  command IDs are 1–200 characters. Changed arguments under an existing ID fail.
  Exact duplicates return saved results without re-execution. Running/interrupted
  mutations remain `unknown`; never change the ID to retry uncertain work.

Standard `session/delete {sessionId}` uses the loaded session's history workspace.
A negotiated client may provide `_meta["amplifier.dev/native"].historyCwd` to remove
an unloaded known session without scanning global history. An optional `commandId`
provides exact receipt recovery. Repeated removal of a deleted session succeeds.
A client owns confirmation UI for this standard method. Archive is host-owned
shared organization, not native deletion or storage destruction.

Explicit limits: selected fork/export materializes at most 64 MiB of canonical
history; larger sources fail without mutation. Backup recovery, malformed
history, unsafe files, stale boundaries, and missing native bundle identity fail
explicitly. Managed-chat hard deletion, full portability archives, shared artifact
copying, and reference-only recovery copies are not claimed by these methods.
Live model/account acceptance is separate from fixture validation.
