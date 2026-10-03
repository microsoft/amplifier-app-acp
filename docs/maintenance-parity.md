# Native runtime maintenance boundary

The native generation owner extracts the preparation, frozen graph verification,
source protection and lazy profile qualification contracts from Unified
`fc9db3e49a924f897849d1a45f7946804247f7d1` (v0.20.47). The independent Git staging
and bounded check timing changes from v0.20.48, PR328 candidate
`1c22ff71a16ad009be9b73f88589f719deeabe52`, merged as
`278a3abf69d77c14843884e66e67d4531a2a41b4`, are carried into the owner.
No AppService, global session catalog, host-private Python object or application
replacement implementation is imported.

| Behavior | Native implementation / acceptance |
|---|---|
| Branch policy versus immutable receipts | Future preparation uses the branch manifest; lock/installed source graph are qualification and rollback evidence only. Existing graphs never refreshed in place. |
| Preparation and profile mount | Foundation batched dependency preparation, one frozen resolver transaction, separate read-only profile probes. Real owned fixture passed. |
| Source protection | Exact checked revision/ref/dirty-state preflight; only reviewed generated artifacts restored, only inside staged copy. |
| Git staging | Four independent candidates concurrently; writes to one target serialize; failure cancels siblings before qualification. |
| Generation promotion | CAS on current pointer, verified native receipts, durable command identity, old workers retain captured generation and leases. |
| Unknown commands | Exact command lookup; interrupted running receipt becomes unknown on explicit repeated inspection/admission; no execution replay. |
| Lazy selected-session configuration | Native `runtime_profiles.ensure`, local input content signatures and saved full module plans; qualified child generation retained independently. |
| Passive reads | SQLite keyset inventory pages (1–100), 30 diagnostics, at most five check timings. No historical workspace or session inventory. |
| Prompt execution | Fresh qualified interpreter through normal native worker. Owned current-source qualification resolved Foundation c60c5fe and loop bdd76 with supported Core wheel2.0.1, then one configured local-provider user prompt settled successfully; normal naming retained. This is separate from offline multi-worker fixtures and does not prove UI/device acceptance. |
| Manual update wakeup | Calls execute immediately and serialize at owned mutation lock. A prepare requested during check waits and uses the completed check unless caller supplied an exact checkId. No timer poll. |
| Active worker currency | `generations.current` reports selected qualified receipts/policy; resident-only `runtime.inspect` reports actual interpreter, installed provenance and mounted root origins. Host inventory/aggregate adoption remains a public callback; absence is explicit. |
| Distribution self-update / feature installation | Not implemented or advertised here. Requires installed-distribution owner, qualified release manifest, restart/adoption receipts and genuine lifecycle authority. |
| Automatic background checking/install preferences, included/other application tiers | Not yet implemented in the optional product capability. Explicit native commands work. |
| Native private backup / restore / selected reset | Selected archive parts remain supported. Explicit full-native authority now streams all native canonical/configuration/receipt/generation roots and stages reviewed restore into a new owned destination, with process-loss fencing and no replay. Native project settings restore as staged evidence; configuration rebinding and runtime qualification remain explicit. Aggregate product backup/restore and destructive cleanup remain separate owner duties. |
| Environment repair | `generations.repair.preview` binds exact qualified receipts; `generations.repair` reconstructs a separate interpreter, checks provenance and read-only profile mounts, preserving original files and unknown work. Actual 98-distribution current graph passed without a model prompt or promotion. Missing/changed source inputs remain an explicit refusal. |
| Rollback to an unqualified legacy baseline | Unsupported; qualified-generation rollback remains available. |
| Retention / reclamation | Existing worker generation leases remain; no automated generation deletion in this owner. |
| Browser support export | Native sanitized server diagnostics/check timing present; client-side support report composition is a separate remaining UI integration. |

The generic admin transport must be configured with `adminGenerations:true` and
launcher-owned `adminWorkspaceRoots`. Execution launchers opt into
`managedGenerations:true` independently. Neither capability accepts a command,
interpreter path, native home, or workspace authority from browser arguments.


## Remaining owner duties

- Distribution self-update and optional component installation need the installed
  distribution's artifact manifest, explicit admission receipt, safe replacement
  and restart callback, and a fresh-process adoption proof. Native runtime
  generations cannot impersonate that authority.
- Update preferences and automatic checking/install scheduling belong to that
  product coordinator. Native explicit commands are event driven; a preference
  without a functioning background owner must not be advertised as active.
- Full private backup requires a consistent export from every independent owner,
  exact native-history writer fencing, and a declared inclusion/omission manifest.
  The legacy global database snapshot is not a substitute for the new graph.
- Browser support export composes local browser diagnostics with sanitized native
  reports. Credentials, drafts and full transcripts are not implied inputs.
- [Native environment repair](generation-repair.md) now reconstructs a separately
  qualified environment from recorded evidence. The product still owns exposing
  preview/approval, exact command recovery and the separate activation CAS. Repair
  does not recover deleted local source checkouts or invent missing credentials.
- Worker retention policy is host admission/lifecycle policy; native graceful
  retirement already refuses owned background work. Generation reclamation needs
  an authoritative live/configuration reference index and explicit preservation
  of uncertain references. No automatic deletion or global settings scan is added.
- [Selected native maintenance](native-maintenance.md) now implements a writer-fenced
  scoped archive and reversible noncanonical configuration reset. The product owns
  intake fencing, all other stores, approval and archive presentation. Whole-archive
  restoration now exists only for the versioned full-native archive into a new
  owned destination; it never restores over running stores. Product aggregation,
  automatic recovery of unknown effects, broad/destructive reset and generation
  reclamation remain separate duties. Native coverage never implies full-product
  backup.

The [retained recovery audit](recovery-parity-audit.md) records the exact legacy effects, current acceptance and remaining aggregate/destructive boundaries.

## Cold source/cache maintenance

The additive `cacheInventory` protocol provides explicit cold indexed source
inspection and reviewed removal of exactly reconstructible native-owned Python
bytecode. It preserves every source directory and all unknown, dirty, unpushed,
shared, retained-generation and unsupported cache roots. This partially covers
F12.09/F12.21/F17.02; complete source retirement remains unavailable without a
cross-consumer reference authority. See [the cache contract](cache-maintenance.md)
for audit revisions, bounds, no-replay receipts and the required held writer gate.
