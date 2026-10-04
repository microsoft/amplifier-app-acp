# Amplifier ACP agent

The ACP v1 boundary for the **full modular Amplifier application runtime**:
Foundation prepares bundles and creates Core `AmplifierSession` instances;
`loop-live` retains providers, tools, hooks, context, delegation, scheduling,
steering, naming, approvals, native storage and execution ownership.

This package does **not** use `microsoft/amplifier-agent`, import Unified's
`AppService`, or substitute an echo engine. The fixture provider is test-only.

Start with the [native agent vision](docs/VISION.md) and
[version 1 boundary contract](contracts/native-agent.v1.md) for ownership,
consumer obligations, executable checks and current limitations. These link
the upstream protocol and approved family contracts rather than redefining them.
Mounted provider reads can report [passive authentication observations](docs/provider-authentication.md)
without changing selection or request-time authentication behavior.

## Run

```sh
uv sync --no-dev
uv run --no-dev amplifier-acp --config /absolute/agent.json
```

`agent.json` is process configuration, supplied by the trusted launcher:

```json
{
  "home": "/owned/amplifier",
  "appHome": "/owned/amplifier-acp",
  "bundle": "work",
  "startupTimeout": 180,
  "idleSeconds": 60,
  "maxSessions": 32,
  "adminWorkspaceRoots": ["/owned/workspace"]
}
```

`home` owns the existing community settings/native history. `appHome` owns private
configuration/controls/attachments/reports. `registryHome` can override the
Foundation cache without moving native history. Trusted launchers may set the bounded
[`moduleSources` override map](docs/launcher-module-sources.md) to select an exact
reviewed module source without editing shared settings. Omitted homes use existing
`AMPLIFIER_HOME` / `AMPLIFIER_WEB_HOME` conventions. Credentials remain in the
native configuration/environment; they are never ACP authentication payloads.
Do not point an experiment at another owner's state.

Each loaded native session uses a separate worker subprocess so cwd, provider
credentials, module imports and native ownership never mix across sessions.
Standalone settled workers retire after `idleSeconds`; active turns, children, approvals,
bridges and operations prevent retirement. A new explicit prompt remounts from
native history, without replay. Connection metadata is bounded by `maxSessions`.
An adapter connection ending cancels its workers; the AHP host must retain its
ACP connection independently of browser connections.

## Wire boundary

- Stable ACP protocol version 1, JSON-RPC JSON lines over stdio. stdout is reserved
  for the wire; dependency/import/provider logs use stderr.
- `initialize`, `session/new`, `session/prompt`, `session/cancel`, `session/load`,
  `session/resume`, `session/close`, standard modes and config options.
- Text, image, embedded text/binary resources and resource-link input. Audio is
  not advertised; full-duplex voice belongs to a separate negotiated capability.
- `session/update` streams public model output, tools and progress. Permissions
  use `session/request_permission`; lost/invalid permission never authorizes work.
  A reply resolves only its live approval ID and an offered choice. It does not
  acquire a new execution admission or queue behind the waiting tool control;
  that original operation retains its native ownership until it settles.
- MCP stdio servers use `amplifier-module-tool-mcp`; server startup failure fails
  session setup. Client MCP names cannot silently replace configured tools.
- `session/prompt` returns at native completion, not input admission. Background
  child work keeps the prompt open until the native manager finishes it.
- `session/load` replays all visible retained history; `resume` does not replay.
  Both initialize the native runtime as required by ACP. Browsing uses the
  optional passive history extension described below.

Every native input has its native ID. `transcript.jsonl` and `metadata.json`
remain authoritative. Existing event logs are preserved. The extracted runtime
uses Foundation writer ownership, detects noncooperating transcript changes,
and retains interrupted child/job evidence. Missing/corrupt/owned history fails
explicitly rather than becoming a replacement conversation.

Optional administration is launcher-authorized and never opens an agent session;
see [the application bridge contract](docs/host-bridge.md) and
[extension schemas](docs/extensions.md). Omit `adminWorkspaceRoots` to disable it.
Generic ACP peers receive no implicit Unified UI guidance or app behavior.

## Extensions

[Native/host extension contract](docs/extensions.md) documents advanced modular
controls, passive bounded history and host callbacks. Extensions are negotiated
with versioned `_meta` capabilities; an ordinary ACP client requires none.
Private drafts, UI selection and layout never enter this package.

## Verification and source

```sh
uv sync
uv run pytest -q
```

The suite launches real ACP and native worker subprocesses, using actual Core,
Foundation, loop-live and tool-mcp with an offline fixture provider/MCP server.
It exercises completion, approvals, cancellation, writer conflicts, MCP tools,
resume, full history replay, passive complete-turn paging and stable input IDs.
Messages are checked against the upstream stable v1 JSON schema.

The native runtime is a symbol-level extraction from Unified revision
`828d2bff97a77fbdbd0f06429369f7ff1f4abf42`. `extraction.json` records exact source
hashes and selected symbols. The initial 169-module import closure was reduced
to 66 runtime modules without importing application state. Subsequent adapter
changes are described in [coverage](docs/coverage.md). `uv.lock` is a qualification
receipt; upstream module sources continue to track main, and upgrades require
rerunning qualification (`uv sync --upgrade`).

Live provider accounts, production history and external editor clients have not
been exercised by these fixture tests. Source extraction and passing regressions
do not by themselves establish full production feature acceptance.

Scheduled turns require negotiated `scheduledTurns: {version: 1, completion: "settled"}`.
`_amplifier/native` operation `schedule.submit` accepts `{inputId, taskId,
taskRevision, text, kind: "task" | "monitor"}`. It shares the prompt lock and
standard ACP streaming, returns `{accepted:false,reason}` before execution or
`{accepted:true,inputId,stopReason}` only after settlement. Uncertain transport
loss is never a rejection or permission to replay. Reused admitted input IDs are
rejected with `already-admitted`. Monitor execution keeps the native finite-run
continuation guard and saved task goal restoration.

Configuration inheritance is advertised as `configurationClone: {version:1}`.
`configuration.snapshot` returns the opaque native-owned reference `{version:1,
sourceSessionId,sourceCwd,configurationHash}`. The source must be idle. A separately
initialized process accepts `_amplifier/session/new` with `{sessionId:<reserved
UUID>,cwd,mcpServers:[],source:<reference>,commandId}`. It verifies the current
source configuration, exclusively reserves the identity and mounts the complete
native bundle/plan/provider/mode/budget/capacity settings. History, goals, tasks,
receipts and execution state are excluded. Private plan/credential values never
cross the wire. A confirmed response is standard NewSession plus native metadata
`creationConfirmed:true,configurationHash`. Duplicate identities or interrupted
creation require receipt inspection, never another create. The source host must
hold its configuration/admission lock through creation. Cross-workspace cloning
is deliberately refused until a workspace owner supplies an explicit contract.

Execution handoff advertises `relocation:{version:1,passiveInspection:true}`.
Native operation `session.relocate {commandId,target,expectedExecutionRevision}`
validates two worktrees share a Git common directory, requires the runtime's
acknowledged parked/retired boundary, then remounts the same native session with
unchanged history/settings workspace and the new execution directory. The native
revision here counts location changes, independently of any host admission
counter. Result `{applied:true,historyCwd,executionDirectory,executionRevision,
commandId,blocked:false}` is durable only after remount succeeds. No turn is
submitted or replayed. A failed/uncertain remount leaves a durable fence.

Passive `_amplifier/relocation {sessionId,historyCwd}` reads location and receipt
without opening Core: `{sessionId,historyCwd,executionDirectory,
executionRevision,blocked,receipt?}`. Receipt has `{commandId,status:
"applied"|"remounting",target,from,expectedExecutionRevision,historyCwd,signature}`.
`_amplifier/relocation/reconcile` adds original `commandId` and confirms only an
already-written native location marker matching that receipt; absent proof it
returns `blocked:true,reconciled:false`, never repeats the effect. Standard
resume/load use execution `cwd` plus negotiated `_meta["amplifier.dev/native"]
={historyCwd:<original>}; returned native metadata includes both directories and
location revision. Passive history still uses original historyCwd. Configuration
snapshot references include executionDirectory/locationRevision so a fresh
configuration-only clone preserves the selected worktree while its own history
identity and location revision start fresh.

Historical operation controls may set native request metadata
`_meta["amplifier.dev/native"]={requireExisting:true}`. This checks the actual
native worker, independently of the ACP connection's lifetime, and refuses cold
or retired workers before startup. A warm ACP connection is not proof of a live
process owner. The host's `nativeControlExisting` must pass this metadata.

Native idle retirement is negotiated as
`_meta['amplifier.dev/native'].lifecycle:{version:1,gracefulRetire:true}`.
`_amplifier/retire {sessionId}` never mounts a worker. It atomically refuses
active requests, queued work, jobs, approvals, bridges, process controls, logins,
naming and auxiliary memory calls. `{retired:true}` means the native worker
committed retirement and exited; this connection's session handle permanently
rejects new work. The host may then close ACP. A false result or lost transport
is never permission to kill a possibly active worker. Hosts should hold their
session admission lock during the handshake. `session/cancel` and explicit
`session/close` remain user-directed termination paths, separate from idle cleanup.

A private spawning Host can offer
`clientCapabilities._meta['amplifier.dev/host'].runtimeRetention` as exactly
`{version:1,owner:'host',lifetime:'connection'}` during `initialize`, with Host
capability version 1. Native acknowledges the exact tuple plus
the informational `standaloneIdleSeconds` in
`agentCapabilities._meta['amplifier.dev/native'].lifecycle.runtimeRetention`.
Only this accepted agreement suppresses the Native autonomous idle timer. The
Host then owns bounded retention and requests the existing atomic retirement
handshake. The informational interval snapshots the existing scheduling expression
`max(1, idleSeconds)`, defaulting to 60 seconds. Zero or negative settings have a
one-second floor and never disable retirement. Existing standalone scheduling
and configuration remain effective for unnegotiated peers.
Missing, unsupported, malformed or extra-key offers receive no retention
acknowledgement and keep the standalone timer. The agreement is immutable for
that ACP connection, binds before sessions, and requires no heartbeat. Connection
loss still cancels workers and preserves the existing no-replay semantics.

Passive history inspection is negotiated by `history.passiveInspect:true`.
`_amplifier/history/inspect {sessionId,cwd}` returns
`{available,revision:string|null,diagnostics}` using regular-file stat only; it
never parses transcript/events or starts a native worker. Availability means the
file exists, not that its entire content was validated. `_amplifier/history`
accepts `expectedRevision` and rejects a changed source before returning rows.
Per-update history metadata preserves recorded provenance fields where present,
including native input ID/kind/source, timestamp, schedule/question and voice
identities. It always carries `authorization:'unverified-native-history'`;
missing input origin stays unknown. Imported/native transcript roles never prove
current human authorization; the host's exact admitted-message reader is the
consent authority. No input is replayed to enrich missing metadata.

`questionTurns:{version:1,completion:'settled',active:false}` advertises the
host-admitted `question.submit` native operation. It uses the same ACP streaming
collector and terminal settlement as scheduled turns. Active sessions return
`{accepted:false,executed:false,reason}`; a second prompt collector is never made.
The negotiated questions host feature must provide `questions.delivery.admit`
with the submitted exact arguments. Its proof requires `admitted:true`, matching
inputId/questionId/questionRevision and `answerHash` equal to SHA256 UTF-8 text.
Missing/stale proof refuses before input execution. A successful response is
`{accepted:true,inputId,stopReason}` only after settlement.

The adapter reserves the input's provenance in a scoped indexed private ledger
before admission. A transparent public context delegate places
`inputOrigin:'question'`, questionId/revision and bounded answerProvenance on the
canonical native input row. The reservation is not a replay queue: an uncertain
reserved ID cannot execute again. Resume and history preserve this provenance;
it never grants tool permission. Active steering or answering a dependency while
another host turn remains active requires a distinct admitted-input attribution
contract and is not claimed by this idle-only extension.

`observationTurns:{version:1,completion:"settled",active:false}` admits one idle
`observation.submit` through the same ACP stream. Args are deterministic `inputId`,
`watchId`, `taskId`, `taskRevision` and a bounded typed `outcome` object (64 KiB).
The existing host observation proof, task revision guard, finite one-iteration
explanation and tool denial apply. A busy or changed authority is refused before
execution; accepted observations settle with `stopReason`, never replay on retry.

### Native generation maintenance

Trusted launchers may enable `adminGenerations:true` alongside administration
roots. `_amplifier/admin` then accepts `generations.inspect`, `check`, `prepare`,
`promote`, `rollback`, `receipt`, `launcher`, and `ensure`. Reads are passive;
mutations require a durable `commandId` and serialize in the native owner.

- `inspect {limit?:1..100,cursor?:string}` returns paged source summaries, pointer,
  bounded sanitized diagnostics and five most recent check timings.
- `check {commandId}` records current native source availability without installs.
- `prepare {commandId,checkId?}` snapshots native sources and configuration,
  prepares/freeze-verifies dependencies, then mounts offered profiles in isolated
  read-only probes. Explicit `checkId` is a CAS; omission waits for an in-progress
  check and uses its completed result.
- `promote {commandId,generation,expectedCurrent:null|string}` and
  `rollback {commandId,expectedCurrent}` switch only future workers after verifying
  receipts. They preserve active workers and never replay conversation input.
- `receipt {commandId}` returns one exact durable result. Unknown means inspect;
  a repeated command never reruns an uncertain operation.
- `launcher {generation?}` returns the verified native-owned `workerCommand` and
  `runtimeGeneration` for a trusted compositor. These paths are server-only.
- `ensure {commandId,nativeSessionId,generation?,bundle?}` lazily qualifies one
  selected saved configuration in the authorized workspace.

`managedGenerations:true` lets execution processes resolve the selected native
launcher and lazily qualify a session before starting its worker. Running workers
retain their captured generation. Default launchers continue requiring explicit
`workerCommand` for preexisting generation pointers. `runtimeManifest` and
`generationProfiles` are optional trusted launcher inputs for distributions and
isolated fixtures, never public request fields.

Each mutation returns `{commandId,operation,state,result?,errorType?,detail?}`.
States are running, succeeded, failed or unknown. Inspecting native maintenance
while a mutation runs remains responsive and does not start another operation.
See [maintenance parity](docs/maintenance-parity.md) for retained behavior and
explicit remaining distribution, repair, retention and UI gaps.

### Passive saved task authority

`taskState:{version:1,passive:true}` advertises `_amplifier/task/inspect`
`{sessionId,cwd,expectedRevision?}`. The result is
`{available,revision,task,source:"persisted-native-controls"}`. It verifies the
native session's immutable history workspace and reads only its bounded persisted
control record. Missing state is distinct from corrupt, oversized or changed
state. This is saved task authority, not proof that a runtime is currently active;
execution still rechecks live task revision and host admission.

### Private scoped recovery

Trusted launchers may enable `adminMaintenance:true` for selected native snapshots
and reversible configuration reset. The coordinator must prove quiescence and
explicitly review private content; old noncooperative CLI writers are unsupported.
Archives use private opaque IDs and bounded chunk reads, with exact inclusion and
omission manifests. Nothing discovers all histories, resets canonical messages,
or deletes generations. See [native maintenance](docs/native-maintenance.md) and
[its private request schema](schemas/native-maintenance-request.v1.json).

Optional launcher-authorized [shared voice preferences](docs/voice-preferences.md)
provide locked compare-and-set updates and exact receipts without credential
access. [History import](docs/history-import.md) creates reviewed inert native
histories through bounded chunks; cleanup remains a separate product authority.

Native maintenance also supports [cold source inspection and reviewed derived
cache clearing](docs/cache-maintenance.md). It never treats unknown use as
permission to delete source directories; only exact, reconstructible bytecode in
an owned cache can qualify for a fenced clear operation.

### Reproducible optional portability installation

`uv sync --frozen --extra portability --no-editable --no-dev` installs the
standalone adapter plus the optional portability libraries. Until those libraries
are available from the package registry, the development source declarations use
`microsoft/amplifier-portability` main and Foundation's `libraries/worktrees` main.
The lock records exact resolved Git commits for qualification and rollback; the
source policy remains moving main. No editable sibling checkout is required.
Refresh all Foundation distributions together when updating their shared Git
source (`amplifier-foundation`, hooks-session-naming, and tool-delegate).

### Exact bundle command recovery

Negotiated `bundleCommands` v1 adds native-owned exact receipts for bundle save,
export, default selection and separate session activation. Receipt reads are
passive and preserve an independent successful save when activation needs a model
choice or has an unknown outcome. See [wire and outcome contract](docs/bundle-commands.md).

Reviewed, history-preserving context clearing is an additive native capability:
[contract and limitations](docs/context-clear.md). It keeps canonical history
before-images while resetting active model context, and uses passive exact
receipts after a lost reply. Archived viewing references never grant canonical
fork/edit/export authority.

Exact failed-input settlement and passive recovery are documented in [input outcomes](docs/input-outcomes.md); terminal failure never means no effects or replay permission.

Trusted launchers can bind [`runtimeImmutable: true`](docs/runtime-preparation-policy.md)
to prohibit dependency installation throughout native worker and probe lifetimes.
Configured modules stay configured; changed dependencies require a separate
owned staging environment. Initialize and resident inspection expose the actual
`runtimePreparation` binding only when the required public Foundation policy is available.

Native administration negotiates `admin.lifecycle.admissionAbort` version 1 with
durable original receipts. Distribution attempts retain pending/acquired/refused
evidence and intake in reserved rows of the existing maintenance journal. Process
exit drops the OS gate but does not erase unresolved intake. The private
`abortAdmission` operation requires separately authenticated, exactly bound
`distribution-admission-abort` proof. It settles the original attempt without
reacquiring it or starting a worker; only an original explicit refusal can return
`not-acquired`. An exact original acquired/unchanged released receipt may settle
as `released` with the new abort proof. Missing legacy evidence remains unknown.
Identical retries return the durable original owner receipt, including after a
lost reply and replacement process. Normal maintenance commands cannot use the
reserved admission namespace.

### Passive retirement status

Negotiated `lifecycle.retirementStatus` advertises
`{version:1,method:'_amplifier/retire/status',passive:true}`. On the same existing
ACP connection, `{sessionId}` returns `{sessionId,retired,reason?}` without
starting, retiring, closing, killing or waiting for a worker. True requires the
original captured retiring worker, its exact correlated literal `retired:true`
acknowledgement, and clean process exit. Session flags, worker closure, EOF and
exit alone are insufficient. False, errors and unavailable older peers preserve
retirement uncertainty. A Host can inspect this proof before closing the old
bridge and explicitly resuming the same Native identity for a **new** turn;
no original uncertain turn is replayed. Definite `active_request` / `owned_work`
retirement refusals keep the existing handle usable.
