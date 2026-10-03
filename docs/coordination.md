# Native worker coordination

Initialize advertises `native.workers` version1 with passiveInspection and
residentControls. `_amplifier/workers {sessionId,cwd,workerId?,cursor?,limit?,
afterSequence?,resultLimit?,commandId?}` reads only that parent's persisted
observation index. It never mounts a worker, reads a transcript, walks projects,
or follows events.jsonl. No index is explicit `available:false`, not proof that
an older native session never had children.

List pages have at most100 worker metadata rows, selected reports at most32
receipts,20KB text per receipt. SQL indexes support cursor and exact identity
queries. Reports retain stable native reportId and monotonically increasing
per-worker sequence. Retention keeps32 reports per worker and exposes the actual
latestSequence so a consumer can report gaps. Root lifecycle loss marks active
observations interrupted. A passive peer returns residency unverified and never
claims it can follow up just because a saved worker was active. A current ACP
connection may use `workers.inspect` via `_amplifier/native` without startup to
obtain resident status. `workers.changed` events invalidate bounded selected reads.

`_amplifier/native worker.followup {workerId,inputId,text}` and
`worker.interrupt {workerId,commandId}` operate only on the original resident
runtime. They refuse cold/retiring handles before startup and validate the exact
child run ID again in the runtime before admission. A durable receipt is reserved
before sending; identical IDs return the original acknowledgement and never
requeue work. Lost acknowledgement is unknown. Accepted means admitted, not
completed; reports and lifecycle determine later outcomes. Interruption does not
undo effects. Passive commandId reads recover exact receipts.

Persistent child messaging and finite/persistent cancellation retain the native
Children owner. Arbitrary child steering is unavailable on this wire until it
has the same anchored disposition guarantees as root steering. A product host
must preserve user-derived message authorization, limiting an agent to its own
conversation's workers. The adapter cannot infer user consent from message prose.

Tests cover actual Core/Foundation persistent child creation, report, follow-up,
exact duplicate, interruption and cold read with an impossible configured worker;
25k indexed rows; unknown admission no replay; cold refusal. This is fixture
provider acceptance, not a paid-provider delegated-agent qualification.

For a root follow-up, a negotiated prompt may carry
`_meta['amplifier.dev/input'] = {inputOrigin:'coordination'}` alongside the ordinary
command ID. The adapter reserves that origin durably before native admission and
annotates the exact canonical input. Arbitrary authority/provenance fields are
refused. Passive history retains `authorization:'unverified-native-history'`;
this marker never grants human consent.


A delegated tool's queue placeholder may share an exact call ID with its native
child. The index links only an unambiguous active job/child pair: the canonical
child remains visible, its `jobId` links the retained job, and the placeholder's
`supersededBy` points back. Exact-ID reads retain both records. Visible keyset
pages use a partial index, so hidden placeholders do not create historical scans.
Matching labels never establish identity; ambiguous call IDs remain visible.
