# Exact terminal input failures

Initialize advertises `_meta['amplifier.dev/native'].inputOutcome`:
`{version:1,method:'_amplifier/input/receipt',passive:true,durable:true}`.
Negotiated failed prompt errors may carry `data['amplifier.dev/inputOutcome']`.
The [versioned schema](../schemas/input-outcome-receipt.v1.json) defines its exact
nine keys and the passive request/result. The proof is at most 4096 UTF-8 bytes;
error text is a fixed public category summary, never the provider exception body.

`_amplifier/input/receipt {sessionId,cwd,inputId}` returns `{receipt: proof|null}`.
Use the original native history cwd even after execution relocation. The private
ACP launcher's native home plus exact original session/cwd scope authorizes this
read; a browser-supplied session ID alone is not authority. It initializes no
Core worker, creates no database, scans no transcript and is available under a
held native maintenance gate. One indexed lookup reads one bounded original
receipt. It never reconstructs an outcome from current history or settings.

`status:'failed',terminal:true,inputPersisted:true,noReplay:true` proves that the
root execution task settled and the owned checkpoint retained that exact input.
It does **not** prove absence of effects. Version 1 conservatively reports
`effectBoundary:'unknown'`, including provider failures after earlier tool work.
Failure category or HTTP status cannot establish a no-effect boundary. Consumers
must not translate this into `executed:false`, automatic retry, or queue draining.
A fresh explicitly authorized input with a new identity is separate work.

The loop's `generation.failed` arrives before its final cleanup/checkpoint.
Native retains that event until execution joins, checkpoints under the original
writer/activation, confirms the exact input in the checkpointed context, and
commits the immutable per-input receipt. The ACP failed response is delivered
after the worker closes. A checkpoint/receipt failure or interrupted execution
without proof stays unknown. Cancellation and success are not manufactured as
failed receipts. Unknown/missing/malformed proof must retain the consumer's
existing uncertain outcome without replay.

A lost reply or process death after receipt commit can be inspected after
restart. Reusing a known failed input ID cannot start a new prompt, even when its
text differs. The existing one-slot `lifecycle-failure.json` remains a presentation
summary; it is not substituted for the exact receipt. Historical unknown turns
are not upgraded or replayed by installing this capability.

Records live in an additional indexed table of the existing selected session's
`input-provenance.sqlite3`, not another transcript or a global history service.
The `session-state` archive retains the existing store; full-native archive
captures a SQLite backup image. Reversible configuration reset preserves it.
No receipt is pruned merely because active context was cleared or rewritten.

Qualification uses actual Core/Foundation/loop-live and an offline deterministic
provider: provider/tool failures, delayed and failed checkpoints, lost reply,
worker death before/after commit, original-scope cold reads with an impossible
worker, duplicate refusal, corrected new input, and full-native backup coverage.
It does not establish live-account or alternative-loop terminal-proof coverage.
