# Retired private copies

This optional selected-session authority removes **proven duplicate files**, not
directories, native history, or arbitrary old app state. It is distinct from
retention hiding and permanent managed-chat deletion.

The trusted launcher may configure up to16 `retiredSessionCopyRoots`, each exactly
`{id,directory,format:'unified-session-store-v1',writerState:'retired'}`. Paths must
be existing absolute directories, without symlink ancestors, outside current
native home, app home, and the selected workspace. Roots cannot overlap. The old
application must have been stopped permanently by its operator. Legacy writers
do not implement a cooperative lease: **writerState is an explicit deployment
assertion, not independent proof that an old process is absent**. Do not configure
a shared or still-used root. Configured roots must also exclude all other current
owner storage roots; the adapter can automatically exclude only its own roots.

When configured, initialize advertises
`native.sessionLifecycle.retiredCopies:{version:1,duplicateFilesOnly:true,retirementAuthority:'launcher-assertion'}`.
Requests use the negotiated `_amplifier/session` method with exact `sessionId`
and original `cwd` and these operations:

- `legacyCopy.inventory {}` returns configured root IDs only, limits and the
  explicit coverage. It does not open canonical history or start a worker.
- `legacyCopy.preview {rootId}` reads only this exact private session directory
  and its canonical source. It returns `previewId`, `previewHash`, `expiresAt`
  (Unix seconds), `historyRevision`, removable file names/bytes/hashes and retained
  reasons. It acquires the canonical Foundation writer lease. At most256 entries
  are enumerated; oversized directories refuse instead of silently omitting files.
- `legacyCopy.purge {commandId,previewId,previewHash,expectedHistoryRevision}`
  requires the unchanged ten-minute review, holds canonical ownership, checks
  root/directory/file inode and content identities again, and journals admission
  before staging any effect. Result has `removed`, `retained`,
  `canonicalFilesDeleted:0`, `eventsDeleted:0` and its durable `receipt`.
- `legacyCopy.receipt {commandId}` (also ordinary lifecycle `receipt`) is an
  exact passive lookup. Repeated purge IDs return their original result or unknown
  receipt; changed arguments refuse. No uncertain removal is automatically retried.

Only byte-identical `transcript.jsonl` and `metadata.json` qualify. A version1
`checkpoint.json` qualifies only when its entire ordered messages equal canonical
rows and every metadata value (including matching session_id) exists unchanged
in canonical metadata; no other checkpoint keys are accepted. Each large file is
bounded64MiB, metadata1MiB, and explicit selected capture remains bounded. This
intentionally refuses private-only metadata, truncated/reformatted transcripts,
unmapped legacy IDs and otherwise unproven copies. It never silently drops them.
Malformed, oversized, symlinked or hard-linked copies are retained/refused.
`events.jsonl`, live-job records, arbitrary files and every directory stay intact.

Removal stages exact reviewed files in a private directory inside the old session
directory, verifies descriptor/inode/hash again, then unlinks only those files.
An interrupted stage leaves its evidence and an unknown receipt; another command
cannot bypass that unresolved outcome. A later exact receipt read may confirm a
previous completed write, but does not resume cleanup. This version deliberately
has no force, recursive purge, unknown cleanup, or external-writer takeover path.
No root path, transcript bytes or credentials appear in public previews/receipts.
