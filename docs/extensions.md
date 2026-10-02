# ACP extension contract — version 1

Use standard ACP methods first. The client requests extensions in
`initialize.clientCapabilities._meta`. The agent advertises the same versioned
keys in `agentCapabilities._meta`. Neither side assumes an unnegotiated key.

```json
{"amplifier.dev/native":{"version":1},"amplifier.dev/host":{"version":1}}
```

## Native execution and administration

Client request `_amplifier/native`:

```json
{"sessionId":"native-id","operation":"capabilities","args":{}}
```

`capabilities` returns supported operation names and the mounted report. Other
operations use the existing native runtime argument/result contracts. Standard
`configOptions` covers configured model choices and modes. The extension retains
composition/configuration, goals, budgets, provider inspection, native provider
controls, child messaging/steering/stopping, root steering, naming, scheduling,
observations, memory, kernels, history editing and generic tool invocation.
`server.NATIVE_CONTROLS` is the exact allowlist; unknown operations fail.

`steer` arguments are `{text,inputId?}`. Worker controls use
`{worker_id,text?,input_id?}`. No execution occurs merely to list these names;
calling the capability report mounts only the explicitly selected session.

Optional `session/prompt._meta["amplifier.dev/command"]` contains
`{commandId:string}` (1–128 characters). The native Input uses that identity.
Previously admitted IDs return error `-32001` with
`{outcome:"already-admitted",commandId,replayed:false}`. Reconcile by reading
history, not by changing an uncertain command's ID and resending it.

Agent notification `_amplifier/event` is `{sessionId,event}`. Advanced lifecycle
information is opt-in. It never includes provider reasoning. After a durable
native write, `event` is:

```json
{
  "type":"history.persisted",
  "sessionId":"actual-root-or-child-id",
  "cwd":"/workspace",
  "parentId":null,
  "metadataPath":"/native/projects/slug/sessions/id/metadata.json"
}
```

The host consumes this internally, validates the locator against its configured
native home, and hints only that catalog row. It must not broadcast the local
path to browser clients or treat the path as caller-provided read authority.

## Passive history

Client request `_amplifier/history`:

```json
{"sessionId":"native-id","cwd":"/absolute/workspace","limit":50,"cursor":null}
```

This does not require `session/resume`, import an engine, initialize a worker,
read `events.jsonl`, or run a model. `limit` counts complete user-anchored turns
(1–200). Initial reads return the latest window; opaque `nextCursor` reads older
turns. The response is:

```json
{
  "updates":[],
  "turnGroups":[{"id":"native-input-id","updates":[],"state":"unknown"}],
  "nextCursor":null,
  "revision":"native-file-stamp-hash",
  "diagnostics":[]
}
```

Each update is an ordinary ACP `SessionUpdate` with additive
`_meta["amplifier.dev/history"]:{position,turnId,messageId}`. Modern native input
IDs survive paging. Legacy IDs use record content plus byte position; rewritten
legacy history can change those IDs. Turn completion is `unknown` unless an
independent execution receipt establishes it. The host must not fabricate a
completed turn from a user-only record.

The reader seeks backwards and reads at most 2 MB per page. An oversized whole
turn fails explicitly, rather than truncating it. A changed file rejects stale
cursors. Missing-primary backup use and unmappable content are reported in
`diagnostics`; corruption and unsafe paths fail without modifying history.
Hidden reasoning, system/developer rows and ephemeral/service instruction rows
are excluded. Full standard load fails explicitly if visible content has no
faithful ACP mapping, rather than silently discarding it.

## Host callbacks

Only after `amplifier.dev/host` negotiation can the agent request
`_amplifier/host` with `{sessionId,operation,args}`. Native application capabilities
use this callback for bounded context/surface reads, action discovery/dispatch,
usage admission, operation observation, explicit question admission and related
host-owned functions. This is an optional host capability, not a second native
execution API.

The host binds authenticated origin/session authority itself. Client IDs or
surface IDs inside args are selectors, never permission. It must enforce its
scoped schemas and return unsupported for absent capabilities. In particular,
`capacity.admit` returns `{allowed,budgetRevision,admittedAt?,reasons?}`;
`operations.observe` receives `{runtimeSessionId,event}`;
`questions.admit` receives `{questionIds}`. No callback should materialize a global
application snapshot to fulfill a narrow read.

Without this extension the native agent does not install `app_control`. A saved
enabled host-owned lifetime budget fails closed when its admission authority is
unavailable. Ordinary native turns remain usable without host callbacks.

## Public ownership

The public package/repository/CLI name is **amplifier-acp**. The Python import is
`amplifier_acp`. This independent Amplifier adapter owns `amplifier.dev/native`,
`amplifier.dev/host`, the `_amplifier/*` ACP methods above and their public
[schemas](../schemas) / [fixtures](../fixtures). The `microsoft/amplifier-agent`
lightweight building block is a different project and is not a dependency.
Unified-specific capability manifests, records and action envelopes remain owned
by the Unified protocol-extension project; this adapter does not redefine them.
Schema `$id` values express intended public identity, not a claim of deployment.
