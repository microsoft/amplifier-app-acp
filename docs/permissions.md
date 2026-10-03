# Scoped file-access settings

Launcher `adminPermissions: true` enables `native.admin.permissions` version 1.
The private initialized ACP administration connection uses the existing authorized
`cwd`; the request cannot choose a home, settings path, or session identity.
No read or save starts Core, a worker, a provider, or a tool.

| Operation | Arguments | Result |
| --- | --- | --- |
| `permissions.get` | `{scope?: 'global'|'project'|'local'}` | Bounded view below |
| `permissions.save` | `{commandId,scope?,expectedRevision,allowed,denied}` | Exact receipt |
| `permissions.receipt` | `{commandId}` | Original selected-workspace receipt or null |

Scopes retain the original shared settings layout: native-home `settings.yaml`,
workspace `.amplifier/settings.yaml`, and private `.amplifier/settings.local.yaml`.
Managed-chat workspaces expose their existing supported scopes only. Symlinked
settings authority is refused. Relative entries resolve against the selected
canonical workspace. Nonexistent directories may be authorized for subsequent
creation. Existing ordinary files are refused. There are at most 64 paths per
list, 4096 characters per path, and 256 KiB per request; oversize input is refused,
never truncated. Selected settings reads are limited to 2 MiB per file.

A view has `scope`, `workspace`, `allowed`, `denied`, `configured`, `revision`,
`scopes`, `limits`, `application: 'next-idle-execution'`, and `detail`.
`effective` describes only the module-default settings override: `allowed`,
`denied`, per-field `sources`, all-layer `revision`,
`coverage: 'module-default-settings-only'`, and `deniedTakesPrecedence: true`.
It is a preference projection, not a claim about a running sandbox. Bundle,
session, and instance policies may add restrictions; shell/network policy is
outside this editor. The actual execution directory is retained by native host
policy. If it differs from this canonical history workspace, the mounted tool
configuration is authoritative.

Save CAS covers the exact selected scope's permissions, not unrelated provider
secrets or another scope. It runs inside Foundation's existing settings-file
lock and atomic replacement. It preserves all unrelated settings. A small
settings marker and private `appHome/permissions/receipts.sqlite3` commands table
bind exact command/workspace/scope/signature. An interrupted attempt is unknown;
a passive receipt can prove completion from the matching atomic marker. No
receipt or duplicate request repeats a write. A later owner write seals a proven
previous unknown receipt before replacing its marker. The full native archive
must declare this receipt root; selected archives retain their explicit coverage.

Receipts are `{commandId,operation:'permissions.save',status,replayed:false}`.
Successful results include `result: view`. Known pre-effect revision/validation
refusals have `status:'failed',executed:false,reason,error` and optional
`currentRevision`; transport/post-write failures remain unknown. Reads and exact
receipts remain allowed under a held administration fence. Saves require open
intake and the native writer gate.

Native sessions preserve their mounted scoped-settings stat baseline. A save
during an active turn cannot be mistaken for policy already mounted when that
turn parks. A later input remounts if settings changed. No per-delta settings or
history reads were introduced.

The product bridge exposes a host-scoped change-metadata topic, not a shared
editor selection. `permissions.get/save/receipt` bind any `sessionId` selector
to trusted AHP context and strip it before native dispatch. Agents cannot change
global preferences or target another session; project/local changes use only
the current session's authorized workspace. Human shared/global changes remain
explicit actions. This does not grant authority to bypass file-tool denials.

## Qualification boundary

The inventory consulted `amplifier/docs/MODULES.md` at 33b7574 and the retained
native `_apply_settings/_apply_host_policy` implementation, including filesystem,
patch and image policy propagation. The reference filesystem tool at 8bd1eab is
exercised through actual Core/Foundation/loop-live with an offline provider.
Tests hold a live tool boundary, save preferences, confirm the mounted policy is
unchanged, then confirm next-input remount and actual file writes/denials. Custom
community tools with their own authority model are not claimed compatible.
Source, installed-wheel, and browser acceptance are separate receipts.
