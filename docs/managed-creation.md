# Managed creation v1

Launcher `managedSessionRoots` is a bounded list (at most 16) of explicit private
allocation roots, separate from administration/workspace browsing roots. The
adapter advertises `native.managedCreation:{version:1,passive:true,
configurationScope:'global-and-session',typedSelection:true}` only when enabled.

Negotiated `_amplifier/managed` accepts:

- Create: `{operation:'create',sessionId,cwd,commandId,allocationHash,title?,bundle?,bundleRevision?,selection?:{instance,model,effort?}}`.
- Receipt: `{operation:'receipt',sessionId,cwd,commandId,allocationHash}`.

The exact layout is `<root>/chats/<sessionId>/files`. The parent marker is
`{version:1,id:sessionId,workspace:cwd,creationReceipt:commandId,owner:'amplifier-unified',allocationHash}`;
its hash is SHA256 of recursively sorted compact UTF8 JSON excluding the hash.
No links, hard-linked marker, external root, extra field, conflicting command or
existing native identity is adopted. Bundle aliases/revisions use the existing
launcher `forkBundles` mapping; omission uses the configured default bundle.
A saved provider/model selection is typed and preserved, never guessed.

Creation reserves a native-owned `appHome/sessions/<id>/managed-creation.json`
before writing an empty Foundation canonical transcript/metadata under a real
Foundation writer lease. It writes selection controls and location metadata,
then commits the creation proof. No Core worker or model starts. Receipt result:
`{commandId,status:'completed'|'unknown',result?,replayed:false}`; completed result
contains exact sessionId/historyCwd/executionDirectory, executionRevision 0,
creationConfirmed true, location, title, bundle, configurationScope,
runtimeStarted false and workReplayed false. A partial reservation stays unknown;
repeating create cannot finish it or replay work. Cancellation joins native file
I/O before releasing the native home gate.

Every mount and remount verifies the native authoritative creation record,
marker and retained directory identities. A copied marker alone has no policy
or allocation authority. A normal ACP session/new cannot adopt the private
folder. Mounted policy uses global and session settings/routing; project/local
scoped writes are unavailable. Canonical checkpoints retain location metadata.
Scoped native administration can recognize a verified allocation without making
its root a general browsing grant.

Managed clone/fork now require an independently proved fresh target allocation.
Relocation/transfer still refuse until an explicit ownership transition exists. Permanent
managed deletion is a separate reviewed authority and is not implemented here.
The full-native backup already includes the native app session proof/control
files; host allocation journal and generated files are separate product roots.

## Qualification scope

This change uses Core/Foundation native worker APIs and the existing extracted
settings builder. It changes no provider/context/loop/tool implementations.
Reviewed boundaries: legacy Unified managed marker/settings behavior, native
configuration cloning and lifecycle fork, Foundation writer/history APIs,
current Amplifier MODULES catalog, host private directory owner, and catalog
metadata projection. Generic ACP clients remain valid; managed creation is an
explicit optional extension. No amplifier-agent dependency is introduced.

## Additive options, defaults and copies

`{operation:'options'}` returns at most 64 configured `{id,name,revision}` bundle
choices without exposing their paths. `{operation:'defaults',bundle?,bundleRevision?}`
resolves only the exact launcher choice and global settings, returning the existing
sanitized providers/effective metadata plus `bundleSelection`, `configurationScope`
and `runtimeStarted:false`. The isolated Foundation probe prepares provider metadata;
it never creates a Core session or mounts tools/hooks. It is bounded to 45 seconds,
64 providers and 256 KiB; failure is explicit rather than selecting another bundle.

Negotiated managedCreation adds `historyCopy:{version:1,freshAllocation:true}`,
`configurationCopy:{version:1,passive:true}`, and `defaults:{version:1,globalOnly:true}`.
For managed `sessionLifecycle fork`, the host supplies private `managedTarget`
`{cwd,commandId,allocationHash}` alongside a fresh targetSessionId. Native verifies
source creation authority and target launcher allocation, reserves a native-owned
target proof before writing, and uses separate source/target Foundation leases.
Result includes exact `location`, `configurationScope`, `runtimeStarted:false` and
`generatedFilesCopied:false`. The old source remains unchanged; text references
are not rewritten. File access policy paths within the old owned allocation are
rebound to the new owned allocation, preserving outside explicit policy.
Uncertain source or target receipts never replay or finish automatically.

`{operation:'configuration.snapshot',sessionId,cwd}` requires proved managed source
and its actual writer lease. It captures saved effective configuration only, and
refuses a source without that persisted configuration instead of mounting it.
`{operation:'clone',sessionId,cwd,commandId,allocationHash,title?,source:<snapshot>}`
creates empty canonical history and the exact source configuration under independent
source/target leases. Snapshot hash and execution reference must still match.
A subsequent explicit first prompt mounts the new runtime; no task/goal/jobs or
execution receipt is copied. Same-command receipt inspection is always separate
from creation. Copy-source identity/hash is retained by both native and host owners.

Full-native `sourceConfiguration.managedAllocations` explicitly records configured
external host owner roots as `included:false, requiredForManagedActivation:true`.
It is not native-owned omission: native app session proof/control files are captured,
while product backup must independently hold/capture the host ledger and managed
files. `completeProductBackup` remains false for a native-only archive.

### Selected bundle model discovery

`managedCreation.models` v1 advertises explicit passive model discovery. Call
`_amplifier/managed` with `operation: "models"`, the same exact configured
`bundle`/`bundleRevision` used for draft defaults, `instance`, optional `limit`
(1–100; default 50), and optional opaque `cursor`. An omitted bundle uses only
the launcher default. A caller cannot supply a native bundle path or workspace.

The response carries `instance`, `module`, `modelsSupported`, `models`, `total`,
`limit`, `revision`, optional `nextCursor`, exact `bundleSelection`,
`globalOnly: true`, `configurationScope: "global-and-session"`, and
`runtimeStarted: false`. It prepares only the selected provider source through
Foundation and calls its public `list_models` method. This can perform provider
metadata network I/O or dependency preparation; it never starts Core, tools,
hooks, a conversation, inference, or a managed allocation. Provider exceptions
are reduced to bounded reason codes without their private diagnostic text.

The provider's complete response must fit 1,000 models and 512 KiB. Oversized or
invalid responses fail explicitly; pages never silently truncate the catalog.
Cursors bind the selected alias/instance and the complete sanitized catalog
revision. Every explicitly requested page re-discovers that one provider; a
changed result rejects the old cursor. No background polling or global model
cache is introduced. `modelsSupported: false` distinguishes an absent public
method from a supported empty list. Public model fields are retained; arbitrary
vendor/config fields and credential defaults are excluded. A provider may itself
return a static or fallback catalog; discovery does not prove model readiness.

Compatibility review used the current local `amplifier/docs/MODULES.md` provider
inventory and Core `ModelInfo`/`list_models` contract. This is an adapter change,
with no provider implementation changes: sync and async public methods and
Pydantic/dict model rows are supported. Actual validation uses the offline public
fixture, not authenticated calls to every listed/community provider.
