# Bundle command outcomes, version 1

This implements the existing AP4 outcome and CS4 exact-command recovery principle
at the native owner boundary. It adds no Core/Foundation semantics or host history
store. Save/export/default are native administration; activation is a separate
idle native session control. Standard ACP remains the upstream protocol authority;
these optional methods require `amplifier.dev/native` version 1 negotiation.

## Negotiation and wire

Initialization advertises `agentCapabilities._meta["amplifier.dev/native"].bundleCommands`:

```json
{"version":1,"commandId":"optional-legacy-required-for-receipt","adminOperations":["bundle.default","bundle.export","bundle.save"],"activation":"bundle.switch","adminReceipt":"bundle.receipt","activationReceipt":"_amplifier/bundle/receipt","passiveReceipt":true,"maxResultBytes":524288,"replayUnknown":false}
```

Administration additionally requires the existing `native.admin` negotiation and
launcher-authorized workspace roots. New consumers must read actual initialized
metadata, rather than infer support from a configured name. Existing clients that
omit `commandId` retain legacy behavior and have no receipt guarantee.

- `_amplifier/admin {cwd, operation: bundle.save|bundle.export|bundle.default,
  args: {...existingArgs, commandId}}` returns the existing public operation result.
- `_amplifier/native {sessionId, operation: "bundle.switch", args:
  {bundle, commandId, previewId?, resetModel?}}` returns its public activation result.
- `_amplifier/admin {cwd, operation: "bundle.receipt", args: {commandId}}`
  returns `{receipt: null|receipt}`. Workspace authorization still applies.
- `_amplifier/bundle/receipt {sessionId,cwd,commandId}` returns the same envelope.
  This is a separate passive method, not a `nativeControl` call: it neither mounts
  a worker nor reads canonical transcript/settings to derive a result. The trusted
  host supplies the authorized canonical history cwd and native identity.

[Request schema](../schemas/native-bundle-command-request.v1.json) and
[receipt schema](../schemas/native-bundle-command-receipt.v1.json) specify the DTOs.
A host should derive different stable child IDs for save and activation from its
outer command. It must preserve the original child ID and arguments on loss.
Receipt lookup never reissues that mutation or invents a completed result from a
current file, current mounted plan, or a host status-only receipt.

## Outcomes and durability

Every receipt-bearing command binds its ID to operation, canonical cwd, native
session identity when applicable, and sorted-key JSON arguments. A changed request
with the same ID is a typed pre-effect conflict; it never reveals another scope's
receipt. There is no fallback to a different ID. An unknown reservation is committed
before effects. The exact original result is committed before the response.

Receipts contain version, commandId, operation, status, createdAt (epoch seconds)
and replayed:false. `succeeded` adds completedAt and the original result. `refused`
adds completedAt, executed:false and reason `bundle-preflight-refused`. `unknown`
contains no result or claim that nothing happened. The JSON result limit is 512 KiB;
export validates that bound before writing a saved file. A post-effect encoding,
storage, cancellation or transport failure stays unknown. No error-message matching
or arbitrary current-state inspection can turn that uncertainty into permission
to execute again. Exact succeeded/refused duplicate calls return the original
outcome; an unknown duplicate refuses without execution.

For activation, a confirmed `succeeded` receipt means the original control
returned conclusively. Its result is either `applied:true` after successful remount,
or `requiresModelChoice:true, applied:false` with the original preview. The latter
is a confirmed unapplied choice, not an activated bundle. A later explicit model
choice is a new, independently admitted command. Save success survives either this
unapplied outcome or an unknown/failed activation. Existing rollback journals still
protect remount internals; a recovered journal is not substituted for a command
receipt, and an interrupted command remains unknown.

Managed-session preview and remount both use the authenticated managed
`global_only` configuration scope. Filtering the same settings files is not
enough: `ResolvedRoot` retains the complete configuration, selected bundle and
execution workspace identity, and is consumed only once. A genuine settings
change during remount still rejects activation and invokes rollback. Successful
rollback alone does not convert an existing unknown command into a refusal.
`tests/test_managed_bundle_switch.py` exercises managed and ordinary sessions,
stale previews, concurrent settings edits, retained history and cold exact
receipt recovery with actual Core/Foundation and no provider calls.

## Storage, backup and scope limits

The native owner stores indexed exact-ID receipts in
`appHome/bundles/command-receipts.sqlite3`, with WAL and FULL synchronous commits.
There is no in-memory all-command catalog or transcript copy. Private request
fingerprints are not exposed. Errors in receipts are bounded classifications rather
than raw exception messages. The store has no automatic eviction or replay.

Full-native authority archives already include the owned `bundles` root and make
consistent SQLite backup images under writer exclusion; the regression test checks
this receipt image directly. Selected-session or selected private-store archives
have narrower declared coverage and do not include this global bundle receipt
store. App-default reset preserves receipts and unrelated shared settings. These
receipts are not client draft state and do not grant a new setting scope.

Tests use actual installed Core/Foundation with an offline fixture provider and
zero provider calls. They drop transport replies, restart processes, retain
unknown after a process dies following a settings write, preserve native voice
history, and test concurrent duplicate, scope and held-gate boundaries. These are
local protocol/storage proofs, not live account or browser qualification.

## Affected implementation inventory

The baseline is native `a434719266e712f517d01ba45f4a904cf238581d`.
The audit covered native Administration default routing, BundleManager export/save
and registration writes, BundleTransaction/remount ownership, Worker control error
propagation, and the existing voice-preference, permission and maintenance receipt
implementations. Retained Unified counterparts (`bundles.py`, `bundle_selection.py`,
`service.py` at the extraction-reference checkout `cb348bed`) confirm that removing
the rollback journal was not a durable command receipt. The inspected ecosystem
catalog is `microsoft/amplifier` `docs/MODULES.md` at `33b75749`; this is an
application-control change, not a runtime module-class change. Core, Foundation,
provider, loop, context, hook and tool implementations are unchanged. The existing
settings mutator/lock, Foundation ownership, remount and rollback remain their
owners. Other registration/provider/module controls do not gain this receipt
contract implicitly.

## Private failure evidence

Version 1 receipts may add `diagnosticReceipt` only for `refused` or `unknown`
commands. It is an opaque private worker-log basename matching
`^startup-[a-f0-9]{32}\.log$` (44 characters). It contains no path, exception text
or credential. Consumers with closed receipt validators must accept this
additive field before adopting this native artifact; successful receipts retain
their original shape. Native error data and duplicate exact-command inspection
preserve the same reference. A diagnostic does not establish completion, restore
an earlier outcome, or authorize a retry. Existing historical unknowns remain
unknown and are not rewritten. Private diagnostics use the existing bounded,
redacted owner log writer; a log-write failure must not replace the command error.

Foundation shared-source objects identify a commit while their per-registry
bindings identify branches. The `context-simple` compatibility check verifies
both before reusing an installed package: selected object location, requested
repository and scoped branch binding, installed commit and import origin, clean
Git tree including dependency declarations, and every package file. Other refs,
local/custom sources, changed files, extra files and escaping symlinks continue
through their selected resolver and Core validation; no import cache is purged.
This repairs the Work boundary-context to Foundation simple-context transition
without weakening Core's source identity checks.
The scoped binding and source-root metadata are read only through a nonblocking,
no-follow regular-file descriptor with size and before/after identity checks.
Special files, replaced metadata, non-object JSON and platforms without those
safe-open flags retain the selected source.
