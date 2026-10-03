# Reviewed context clearing — native extension v1

`agentCapabilities._meta["amplifier.dev/native"].contextClear.version === 1`
negotiates this contract. An older peer's `context.clear` is not a
history-preserving operation. This adapter uses the existing Core
`AmplifierSession`, the mounted context module's public `clear()` contract,
and Foundation checkpoint/writer ownership. It does not use amplifier-agent.

Schemas: [review/apply/results](../schemas/context-clear.v1.json) and
[passive receipt](../schemas/context-clear-receipt.v1.json). Public encoded
review/result/receipt descriptors are bounded to 16 KiB. No application-wide
history or state projection is required.

## Review and execution

Call `_amplifier/native` with the selected native `sessionId` and operation
`context.clear.review`, args `{}`. It returns the current canonical
`historyRevision`, the `controlRevision` of the explicit goal and saved task,
`canClear`, `goalPresent`, `goalAction:"clear"`, and the small task identity,
revision and status. A running saved task must be paused first: otherwise its
goal would be reinstated on resume. A paused task is retained.

Explicit execution calls `context.clear` with exactly:

```json
{"commandId":"original-command","expectedHistoryRevision":"64 lowercase hex characters","expectedControlRevision":"64 lowercase hex characters"}
```

An empty legacy request refuses before effects. The native command lock and
Foundation session writer govern this operation. Busy or changed state is not
permission to invent a replacement command. The owner retains a refused receipt
when the reviewed history/control revision or task precondition fails. Only
owner-proven `executed:false` means no clear effect occurred; an unrelated RPC
or transport failure is not such proof.

Before touching the mounted context, the existing per-session history revision
owner durably reserves an unknown command and fence, then streams exact
`transcript.jsonl` **and** `metadata.json` before-images into an immutable private
segment. Each file and its directory entry are flushed; the descriptor records
its digest and size. A streamed digest sidecar authenticates each touched 64 KiB
chunk during historical viewing, without scanning the whole transcript or
binding it to an inode that changes during an exact-byte restore. It then clears the mounted context, clears the explicit
goal, persists the empty active context through the existing checkpoint, and
commits the segment head and original result. Canonical `events.jsonl` is kept.
A context module can retain its own additional derived archives as before.

`transcript.jsonl` and `metadata.json` remain resume authority. Archived segments
are historical viewing evidence, never merged back into a model context on
resume. Failure after reservation remains unknown; the selected session refuses
new work and checkpoint rewrites until its outcome has been established.
Other sessions remain independent. This version provides no automatic repair,
rollback, replay or unknown-to-success inference.

## Exact recovery

`_amplifier/context/receipt {sessionId,cwd,commandId}` is passive and does not
start a worker. `cwd` is the original immutable **history** workspace, including
after execution relocation. A wrong scope refuses. The result contains `receipt`
or `null`; an unknown receipt has no invented result. A succeeded receipt returns
the original bounded result, and a duplicate matching apply returns that same
result without applying again. Reusing a command with different arguments
refuses. Clients should use the passive receipt after a lost reply.

## Viewing identity and mutation authority

`_amplifier/history` pages active history followed by committed older segments,
with at most one nonempty segment per page and a bounded empty-segment traversal.
Its revision remains the active canonical revision domain. Opaque continuation
cursors also bind session identity, the current archive head and the selected
segment's own revision. Explicit standard `session/load` streams the retained
visible history; it does not change what `session/resume` mounts.

A group's `id` remains its original projection/admission identity across reload.
Historical groups additionally carry `archiveId` and `historicalOnly:true`.
Their update metadata uses an **archive-qualified message reference**:
`archive:<archiveId>:<original-message-id>`, with `mutationAuthority:"none"`.
A group ID is not authorization to mutate its archived messages.

New synthetic active message/voice identities include the clear epoch, so
identical content at identical offsets after successive clears cannot reuse an
older identity. Explicit admitted input IDs stay unchanged and retain their
existing durable duplicate guard. New voice receipts use the same epoch while
old receipts remain immutable.

Canonical fork, edit, rewind and export continue to operate on the active
transcript only. They explicitly refuse archive-qualified boundaries. Old
unqualified synthetic IDs do not match the new active epoch. An export of
`scope:"all"` is therefore all **active canonical** messages; it does not claim
to export the entire historical viewing stream. Extending archived mutation or
export authority is outside this version.

## Preservation and coverage

Segments, command receipts, fences and the head are under the existing
`appHome/sessions/<id>/history-revisions` owner. Selected `session-history`
archives include this directory even when `session-state` is unselected.
Selecting both avoids duplicate archive entries. Full-native authority archives
already include the complete app session tree and preserve these files.

The affected context-class inventory follows `amplifier/docs/MODULES.md`:
`context-simple`, `context-managed`, and the catalogued `context-persistent`.
Native clearing uses their public context protocol, without modifying modules.
The actual Core/Foundation fixture exercises the qualified context-simple
implementation. Source inspection of context-managed confirms its own clear
archives before resetting state; it is not mounted in this fixture because its
current module factory derives a personal-home session directory. A local
context-persistent implementation was unavailable, so no installed evidence is
claimed for it. Generic failures after clear admission remain unknown and
preserved rather than being treated as successful clears.

`tests/test_context_clear.py` covers exact before-images, goal/task semantics,
stale reviews, lost response and cold receipt recovery, killed workers at three
write boundaries, affected-session fencing, repeated identical content, voice
identity, archived mutation refusal, bounded pages, standard replay versus
empty active context, and selected/full-native archive inclusion. These are
owned fixtures with zero provider invocations; no live/account acceptance is
claimed.
