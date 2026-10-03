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

Managed clone/fork/relocation currently refuse before effects: those paths need
an independent target allocation or an explicit ownership transition. Permanent
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
