# Retained recovery behavior and current owner coverage

The reference is Unified v0.20.48 (`278a3abf69d77c14843884e66e67d4531a2a41b4`),
particularly `amplifier_web/recovery.py`, `managed_deletion.py`, `managed_chats.py`,
`management.py`, `runtime_retention.py`, `service.py` and `tests/test_recovery.py`.
This audit separates implemented effects from broader wording in the old UI.

| Retained behavior | What the old implementation did | Current boundary |
|---|---|---|
| Private backup | App-owned database, operations, Recall, publishing/captures, app configuration/artifacts, shared settings/routing/bundles, app-known workspace settings and app-used native chats/workers. Tests explicitly excluded untouched legacy CLI histories. | Selected native history/state and explicit shared/workspace config are implemented here. Independent product owners must provide their own fenced snapshot streams; there is no claim to back up an arbitrary home or all historical CLI sessions. |
| Settings reset | Moved app-owned `config`, `routing`, `bundles` directories while retaining originals. The broader UI description of credential reset did not make shared `keys.env` part of that reset. | Reversible selected noncanonical session reset/undo is implemented. Shared settings and credentials reset remain unavailable pending exact owner effects and review. |
| Conversation reset | Hid app presentation rows while retaining native canonical history. Optional legacy history cleanup affected app session files. | Native discovery tombstones preserve canonical events/history. Product row reset needs independent owner authority. |
| Managed hard delete | Required recorded managed allocations, same-workspace child ownership, idle execution, and questions/approvals/jobs/schedules/publishing cleanup; deleted managed native/workspace data. | Not equivalent to a tombstone, and not implemented. It requires coordinated deletion participants and exact preview/proofs; no inferred ownership from directory names or historical indexes. |
| Retention | Policy for idle worker processes (warm budget, idle timeout and prewarming), not age-based canonical history deletion. | Generic host capacity/idle graceful retirement supplies current process control. Policy UI/defaults are product-owned; historical disk deletion and native generation reclamation remain unavailable. No global prewarming is introduced. |
| Derived environment repair | Runtime-generation repair reconstructed missing/changed installed environments. | Public native generation repair reconstructs a separate qualified environment from exact receipts. Dirty/local module source is not disposable cache; blanket cache removal would be data loss. |

## Acceptance and limits

`test_archive_plan.py` drives actual ACP subprocesses with an offline Core/Foundation
session. It verifies a 2,200-file manifest stays paged, archive member hashes match,
unselected histories/workspace files stay excluded, file changes invalidate review,
selection revisions and command receipts do not replay, unauthorized workspaces and
symlinks fail, and changed sealed-manifest storage is rejected. The product recovery
owner's installed-host/native tests also hold the shared admin lifetime fence through
configuration capture and preserve canonical bytes. No personal credentials, model
account, external CLI process or live installation is used by these tests.

The stopped-writer assertion is a trusted deployment condition, not a test result
about every external CLI version. Whole-archive restore, credential reset, managed
hard delete and reference-aware generation reclamation remain visible gaps. Existing
limits fail explicitly; they never silently truncate the backup.

## Full configured-product snapshot seam

A distribution coordinator can compose independently installed owner participants:
`prepareSnapshot(heldContext, explicitSelection)` returns an immutable plan and
coverage; `manifestPage(planId,cursor,limit)` returns bounded entries and omissions;
`captureSnapshot(planId,reviewedHash,heldContext)` returns an opaque artifact identity
and exact receipt; `readSnapshot(artifactId,hash,offset,maxBytes)` streams chunks.
Every callback must bind authenticated account, instance/data scope and the same
held intake fence. Each owner retains unknown effects and originals itself. The
coordinator writes owner archives and their inclusion manifest to disk, never a
single in-memory aggregate. A configured owner without this contract is an explicit
omission and prevents a “full configured-product backup” claim. Independent running
stores are not an atomic snapshot merely because reads were initiated together.
