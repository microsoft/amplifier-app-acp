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
| Full private backup / selective reset / destructive cleanup | Not implemented in this owner; original behavior remains a parity gap. |
| Repair / rollback to an unqualified legacy baseline | Not implemented. Qualified-generation rollback is supported; active environments are never repaired in place. |
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
- Repair should reconstruct a separately qualified environment from recorded
  evidence; the legacy in-place uv reinstall is intentionally not imported.
  Activation remains an explicit generation CAS; unknown work is not rerun.
- Worker retention policy is host admission/lifecycle policy; native graceful
  retirement already refuses owned background work. Generation reclamation needs
  an authoritative live/configuration reference index and explicit preservation
  of uncertain references. No automatic deletion or global settings scan is added.
- Selective reset/destructive cleanup remains an explicit parity gap. Implementing
  it requires cross-owner quiescence and retained originals; catalog hiding or
  deleting one owner's database must not be labeled a full reset.
