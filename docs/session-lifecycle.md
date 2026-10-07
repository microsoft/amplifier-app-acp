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

The passive `_amplifier/history/message` source lookup does not materialize a
lifecycle snapshot. It scans current canonical records, retains only its exact
match, and checks the remaining records for duplicate identities. Total history
size does not share the 64 MiB fork/edit limit; each record is still bounded to
64 MiB and the complete response to 64 KiB. A changed file or context epoch,
ambiguous identity, unsafe file, recovered backup, or non-user source is refused.
The read cannot start a worker, admit input, or grant execution authority.

## Edit one native history boundary and continue

`native.historyEdits` advertises
`{version:1,completion:"settled",active:false,originalContextVersion:1}`.
The admitted `_amplifier/native` operation `history.revise` accepts exactly
`{inputId,operationId,targetMessageId,expectedHistoryRevision,text}`. The host
reserves the replacement input identity under ordinary turn admission first.
Both identities are 1–200 characters. Text has the native 200,000-character bound.
A target is a native visible user-message ID or its recorded original input ID;
steering corrections are not independent edit anchors. No client transcript is
accepted. The legacy projection-driven `history.edit` is refused over ACP.

The native writer validates the whole selected source revision, resolves the
canonical index itself, and uses the retained native rewind/checkpoint journal.
It clears the old execution goal and preserves original event logs and revision
receipts. After the canonical rewrite commits, `history.revised` contains
`operationId,inputId,targetMessageId,beforeMessageId,beforeTurnId,retainedThroughTurnId,previousRevision,
historyRevision,eventsPreserved:true`. Only this proof authorizes the host to
truncate its displayed projection. The replacement then uses the same single
ACP prompt collector and returns `accepted:true,inputId,operationId,
historyRevision,stopReason`. Events may precede the control response.

Stale/busy preflight is `accepted:false,executed:false`. Confirmed rollback is a
known no-admission outcome. An ambiguous rewrite or input effect retains an
execution fence and original journal. `_amplifier/history/edit`
`{sessionId,cwd,operationId}` reads its small passive receipt; interrupted
`preparing` is reported `unknown`. No automatic replay or uncertain-fence clearing
is implemented. An explicit independent fork can preserve readable evidence for
continued work. Successful admission receipts remain `admitted`; this is not a
fabricated model-completion receipt. The host owns terminal turn accounting.

Before admitting a history edit, `_amplifier/history/before` accepts
`{sessionId,cwd,beforeTurnId,expectedRevision,limit<=200}` and returns the
standard bounded complete-turn history page immediately before that exact native
message/input ID. It never starts a worker. The revision must still match at
edit admission. The resulting proof's `retainedThroughTurnId` is null for an empty
prefix, otherwise the last retained canonical turn ID. Clients can capture this
page before the edit and replace their displayed prefix only after commit proof.

With `originalContextVersion:1`, the passive before-read also accepts
`includeEditContext:true` and returns `editContext:{version:1,targetMessageId,
publicMessage}` for the selected canonical row. This is an opaque public original,
not attachment access authority. The Host resolves its saved resource references
through their owning capability and sends the resulting ordinary ACP content as
optional `args.prompt` on `history.revise`. Its first block must be the replacement
execution text. Literal public text may differ from that expanded execution text;
capture provenance and attachment descriptors must remain unchanged. Missing
context, substituted descriptors, unsupported legacy rich inputs, or malformed
content are refused before rewriting history. Older callers can still edit plain
text but cannot silently drop the attachments of a preserved rich input.

Native rechecks the source revision, rebinds the original row's frozen quote to
the replacement input identity, and submits through the existing input decoder
and runtime. It does not resolve the quote again, replay the original input, or
copy any client transcript. Quote metadata uses the existing bounded input
provenance store; an oversized record is refused before the history journal is
written. Both context-simple and context-managed use this same admission path.
Host/Resources grant retention remains separate from canonical descriptor
preservation, including when editing a copied row in a fork.

## Guarded readable recovery and configured bundle fork

A clean cold resume preserves the original canonical transcript, metadata and
reviewed history revision through preparation and idle parking. Context modules
may normalize their in-memory representation during hydration; that alone is not
new conversation activity. The adapter compares the hydrated context exactly and
still checks the native file stamps and Foundation writer lease before skipping
an unchanged checkpoint. Explicit edits, new inputs, voice recording, context
clear, interrupted-receipt recovery, damaged-primary recovery, explicit bundle
switches and failure checkpoints retain their existing persistence paths. External
canonical changes still revoke the original revision; clients must not silently
retry an edit with a replacement revision. Focused acceptance is in
`tests/test_cold_resume_history.py`.

The optional `sessionLifecycle.recoveryCopy.version: 1` supports `fork` with
`mode: "recovery"`. It creates an independent root with readable public history
and labeled tool-result references, removing prior native tool calls, image and
private-provider payloads, and system/developer instructions. Source history,
events, safety-stop evidence and unknown delivery receipts are unchanged. The
copy does not start a worker, deliver an input or grant old operational authority.
A new explicit input is required to continue. Corrupt or incomplete canonical
history and pending native rewrites refuse; this is not a repair operation.

`bundleFork.version: 1` accepts only a key from trusted launcher `forkBundles`
(`{publicId: configuredRootReference}`). `inspect.forkBundles` returns bounded
`{id,name,revision}` choices. A fork selecting `bundle` must supply the exact
`bundleRevision`; old module plans/toggles are discarded, instructions rebuild
from the selected root at the next explicit execution. The model pin is retained
unless `resetModel: true`. This does not copy tasks, goals, approvals or jobs and
does not assert that an unmounted bundle/provider graph is ready to execute.

`descendantGuard.version: 1` accepts up to 100 selected catalog facts in
`descendants: [{sessionId,cwd,parentSessionId}]`. The native adapter verifies actual
metadata identity and parent links, includes its persisted worker relationships,
and holds Foundation writer ownership for the known family through the copy.
An independently owned historical child or grandchild refuses even when saved
metadata says idle. Child transcripts/events are not read. Missing identity,
cycles, conflicting parents or limits refuse before effect. The caller must supply
complete, fresh bounded indexed facts; this does not establish that undiscovered
or noncooperating external writers are absent. Host admission locks and native
ownership are both required; an index snapshot alone is never an idle proof.

`inspect.failure` is optional bounded recorded native failure guidance, with
`current`, category, summary, guidance, native generation/input identity and time.
It contains no arbitrary provider exception payload. Starting a newer generation
marks an older failure non-current; it is retained as evidence. Older sources
without a recorded descriptor report no inferred diagnosis.


### Guarded age retention hide

`sessionLifecycle.retention:{version:1,preservesCanonical:true}` negotiates
`retention.hide {commandId,expectedHistoryRevision,modifiedBefore,descendants}`.
The cutoff is an explicit past Unix timestamp. The existing durable lifecycle
receipt binds the whole request. Under the real source and known-descendant
Foundation writer leases, the owner refuses new metadata/transcript/event-file
activity at or after the cutoff, unresolved native creation/rewrite journals,
changed history, or active native ownership. No transcript/event body is loaded
for this age check. It then writes the existing preserve-history discovery
tombstone only. The result records the cutoff, observed latest activity, held
family coverage, and `canonicalFilesDeleted:0`. Exact receipt recovery never
repeats an uncertain effect. This is neither legacy presentation-file purge nor
permanent managed-chat deletion. Host selection/dependent-owner guards are
separate required authority; the native method never claims those by itself.
