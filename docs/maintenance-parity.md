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
| Prompt execution | Fresh qualified interpreter through normal native worker; Core/Foundation fixture passed ACP new/prompt after promotion. No account/model acceptance claimed. |
| Manual update wakeup | Calls execute immediately and serialize at owned mutation lock. A prepare requested during check waits and uses the completed check unless caller supplied an exact checkId. No timer poll. |
| Active worker currency | Optional public host callback; absent authority reports unavailable, never assumes all workers adopted. |
| Distribution self-update / feature installation | Not implemented or advertised here. Requires installed-distribution owner, qualified release manifest, restart/adoption receipts and genuine lifecycle authority. |
| Automatic background checking/install preferences, included/other application tiers | Not yet implemented in the optional product capability. Explicit native commands work. |
| Full private backup / selective reset / destructive cleanup | Not implemented in this owner; original behavior remains a parity gap. |
| Repair / rollback to an unqualified legacy baseline | Not implemented. Qualified-generation rollback is supported; active environments are never repaired in place. |
| Retention / reclamation | Existing worker generation leases remain; no automated generation deletion in this owner. |
| Browser support export | Native sanitized server diagnostics/check timing present; client-side support report composition is a separate remaining UI integration. |

The generic admin transport must be configured with `adminGenerations:true` and
launcher-owned `adminWorkspaceRoots`. Execution launchers opt into
`managedGenerations:true` independently. Neither capability accepts a command,
interpreter path, native home, or workspace authority from browser arguments.
