# Reviewed managed allocation file disposal

Disposal now acquires the native home's actual exclusive OS writer gate before
review validation, and retains it through the joined effect. An unrelated active
ACP worker or native request causes a known refusal; no worker is killed. The
selected managed project's canonical metadata is independently inventoried twice,
before and after per-session Foundation ownership acquisition. The complete
acyclic family must exactly equal the host-reviewed descendant identities.
Missing or contradictory metadata, unknown entries, links, changed identities,
and exceeded budgets refuse before effect. This cold operation reads at most101
session metadata files (32KiB each, 2MiB combined, five-second budget), plus their
bounded known Foundation metadata-lock entries. It never scans other projects or
reads transcript/event bodies to discover membership. Completion includes a
`familyGuard.selectedProject` bounded metadata digest and coverage declaration.
The OS gate excludes cooperating ACP workers/creation requests; independent
noncooperating filesystem writers remain outside the stated coverage.

This optional passive native lifecycle capability removes files only from a
launcher-authorized managed allocation with its exact native creation proof.
A working directory, marker copy, catalog row, or age does not confer ownership.
Canonical native transcript, metadata, events and private journals remain intact.
This is separate from hiding history, purging duplicate retired-app copies, and
any operation that would destroy canonical history.

The host must additionally hold its source/family admission fences and every
configured product reference-owner lease. The native capability alone cannot
prove absence of retained publications, artifacts, remote jobs or other product
references. The distinct product marker is `managedFiles:{version:1,
preservesCanonical:true}`, purpose `managed-files-disposal`.

Negotiation requires native v1, configured managed roots, and Foundation's public
`SharedSessionStore.identity/from_identity` APIs (merged Foundation 8462030).
The native marker is `sessionLifecycle.managedFiles:{version:1,
preservesCanonical:true,maxEntries:10000,maxBytes:268435456}`. Every call uses
`_amplifier/session` with the original sessionId/history cwd:

- `managedFiles.preview {}` returns reviewId, reviewHash, ten-minute expiresAt,
  historyRevision, allocationId/allocationHash, treeHash, entryCount/bytes,
  `manifestComplete:true`, `preservesCanonical:true`, zero canonicalFilesDeleted
  and eventsDeleted. The full path manifest is private to the native owner.
- `managedFiles.dispose {commandId,reviewId,reviewHash,
  expectedHistoryRevision,descendants}` consumes that exact review. Descendants
  are bounded native session/cwd/parent tuples checked against canonical metadata
  while holding the original Foundation ownership handles.
- `managedFiles.receipt {commandId}` reads the original receipt. Unknown never
  triggers another deletion. A completed native marker can prove a lost receipt
  acknowledgement without replay.

The complete manifest is limited to 10,000 entries, 256 MiB, depth 64, 30 seconds,
and 4 MiB of persisted metadata. No links, hardlinked files, special files, or
history-shaped events.jsonl/transcript.jsonl/metadata.json are removed. Any such
entry refuses the whole review. Applying a changed/expired review refuses before
file effects. Files/ancestors are opened with descriptor-relative no-follow
operations; a replaced ancestor cannot redirect deletion outside the allocation.

Before atomic staging, a durable unknown command and execution fence retain the
original creation-record digest and the public root/descendant checkpoint
identities. Only explicitly reviewed entries are removed from the private stage;
there is no recursive wildcard deletion. Observed changes, extra entries, I/O
failure, transport uncertainty or process death preserve the remaining stage and
unknown evidence. Directory removals are fsynced. Cancellation joins the bounded
file operation before releasing native ownership. These leases do not freeze
arbitrary non-cooperating same-account filesystem edits; no broader claim is made.

A completed response includes directoryRemoved:true, preservesCanonical:true,
canonicalFilesDeleted:0, eventsDeleted:0, original historyRevision and review,
exact allocation/tree counts, and familyGuard. The host must durably confirm this
native effect before repairing derived catalog visibility or releasing product
protection. Unknown and completed fences both prohibit ordinary execution, even
if somebody recreates the old directory. A completed disposal supports cold
history reading/export and a separately admitted coherent recovery fork into a
fresh allocation. It never recreates the removed cwd or repeats old inputs.
