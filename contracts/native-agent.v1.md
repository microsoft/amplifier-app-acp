# Native agent boundary — v1

This is the contract entrypoint for the existing native ACP adapter, its clients,
hosts and maintainers. It organizes existing promises and their owners; it does
not ratify new behavior or claim complete feature parity. Read the
[local vision](../docs/VISION.md) for purpose and ownership.

## Authority and compatibility

The [upstream ACP protocol](https://agentclientprotocol.com/protocol/overview)
owns standard initialization, prompt, tool, permission and cancellation
semantics. This implementation negotiates protocol version 1. The retained
[upstream schema fixture](../tests/schema/README.md) records test provenance;
it is not an independent protocol definition.

Use standard capabilities before [Amplifier extensions](../docs/extensions.md).
Optional behavior requires the advertised version and any operation-specific
runtime capability. Launcher authorization remains necessary even when a schema
exists. Missing support must not silently become a different execution path.
The public schema directory and linked owner documents define extension shapes;
this entrypoint does not duplicate those definitions.

The Unified family owns [AP](https://github.com/microsoft/amplifier-unified/blob/main/contracts/agent-protocol.v1.md),
[WS](https://github.com/microsoft/amplifier-unified/blob/main/contracts/working-set.v1.md)
and [CB](https://github.com/microsoft/amplifier-unified/blob/main/contracts/component-boundaries.v1.md).
The table below traces this repository's part of those approved clauses. A
generic ACP consumer need not implement Unified or its optional settings UI.

## Promises, consumer obligations and falsifiers

| Family clauses | Native boundary and consumer obligation | Falsifier / executable acceptance |
| --- | --- | --- |
| AP1–2; CB1–2 | Execute through ACP into Foundation-prepared Core sessions; preserve the supported modular runtime. Consumers do not import native private objects or require a Unified checkout. | A replacement engine or private host shortcut produces the result. [Boundary](../tests/test_boundary.py), [actual native graph](../tests/test_native_graph.py), [native controls](../tests/native/test_runtime_controls.py). |
| AP3, AP6; WS1 | Preserve native session identity, transcripts, metadata and retained events. Foundation ownership and conflict checks govern writes; a viewer does not become a second writer. Failed resume is not permission to create another conversation or replay effects. | Independent adapters both write, or resume changes identity/history. [Native graph](../tests/test_native_graph.py), [storage](../tests/native/test_host_storage.py), [session lifecycle](../tests/test_session_lifecycle.py). |
| AP4 | Completion, cancellation, tools and permission responses retain their meanings. Admission is not completion. A permission response resolves only its outstanding approval ID and offered choice within the original operation's lifetime. | A tool holds a command lock while its response waits for that lock, or an invalid/duplicate response authorizes another decision. [Approval scheduling](../tests/test_control_approval.py), [lifecycle](../tests/test_lifecycle.py). |
| AP4, AP6 | Consumers distinguish typed pre-effect refusal from an ambiguous outcome. Use the operation's exact receipt/inspection contract; retain uncertain identity and arguments without automatically repeating effects. | Transport loss or a message string becomes proof of no effect, or a changed command ID replays admitted work. [Steering receipts](../tests/test_steering_protocol.py), [transfer](../tests/test_transfer_protocol.py), [app reset](../tests/test_app_reset.py). |
| AP5; CB3 | Standard configuration first; advanced composition, host callbacks and administration are scoped optional interfaces. Saved, effective and mounted configuration are distinct facts. | A generic client needs Unified guidance, or a stale draft silently overwrites a newer configuration. [Host negotiation](../tests/test_host_negotiation.py), [configuration lifecycle](../tests/test_configuration_lifecycle.py). |
| WS2–3, WS5–6 | Passive selected history/worker/task views do not start an agent. Location hints follow durable writes; catalog discovery and AHP window recovery belong to their owners. No private draft synchronization is required here. | A cold browse starts Core, parses unrelated event logs, or transports a global application snapshot. [History](../tests/test_history.py), [passive task](../tests/test_passive_task.py), [coordination](../tests/test_coordination.py). |
| WS4; CB7 | Resident work and read payloads are bounded. Retirement and maintenance account for actual admitted work, children, unresolved decisions and joined asynchronous effects. Consumers require held owner proof, not a sampled idle flag. | Retirement or lease expiry releases exclusion while work still runs. [Lifecycle](../tests/test_lifecycle.py), [admin lifetime](../tests/test_admin_lifecycle.py), [archive lifetime](../tests/test_full_archive_lifetime.py), [capture scope](../tests/test_capture_scope.py). |
| CB4–6 | Independent fixtures and installed peer qualification identify exact tested versions and limits. Changes use isolated state and public seams. | Source availability is reported as installed-worker or live-account acceptance. [Verification entrypoint](../README.md#verification-and-source) and [acceptance inventory](../docs/coverage.md). |

## New work, admitted responses and uncertainty

A prompt, scheduled input, question turn or effectful control needs its own
applicable admission and scope checks. An answer to a currently outstanding ACP
permission request is a correlated response to already admitted work. It must
not acquire a second execution admission or wait behind the tool it unblocks.
Cancellation likewise follows its existing lifecycle path, not a fabricated
successful completion. These distinctions do not relax approval identity,
offered-choice validation, native ownership or host authentication.

`executed:false` is usable only where the operation supplies structured,
pre-effect evidence. An arbitrary error, timeout, disconnected process or absent
receipt is not that evidence. Result shapes vary by owned operation; there is no
new universal error envelope here. Exact duplicate inspection can return an
existing receipt without repeating the effect. Recovery never infers permission
to resend an uncertain input under another identity.

Questions currently negotiate
`questionTurns:{version:1,completion:'settled',active:false}`. The
[question-turn schema](../schemas/native-question-turn.v1.json) and
[protocol test](../tests/test_question_protocol.py) require idle admission and
the host's exact answered-question proof. Active-turn question delivery remains
unsupported. [Anchored steering](../schemas/native-steering.v1.json) is a
separate negotiated path; its queued/applied/held/unknown evidence does not grant
question authority or permit an undelivered correction to become a new turn.

## Find the owned wire contract

| Surface | Entry points |
| --- | --- |
| Native controls and optional application callbacks | [Native request](../schemas/native-request.v1.json), [host request](../schemas/host-request.v1.json), [host capability declarations](../schemas/host-capabilities.v1.json), [host bridge](../docs/host-bridge.md) |
| Passive history and canonical lifecycle | [History request](../schemas/native-history-request.v1.json), [history result](../schemas/native-history-result.v1.json), [session lifecycle](../schemas/native-session-lifecycle.v1.json), [lifecycle semantics](../docs/session-lifecycle.md) |
| Configuration and admitted special inputs | [Configuration CAS](../schemas/native-configuration-apply.v1.json), [scheduled turn](../schemas/native-scheduled-turn.v1.json), [question turn](../schemas/native-question-turn.v1.json), [steering](../schemas/native-steering.v1.json), [extensions](../docs/extensions.md) |
| Resident work and retirement | [Workers](../schemas/native-workers.v1.json), [runtime inspection](../schemas/native-runtime-inspection.v1.json), [graceful retirement](../schemas/native-retire.v1.json), [coordination](../docs/coordination.md) |
| Trusted administration and maintenance | [Admin request](../schemas/native-admin-request.v1.json), [admin lifecycle](../schemas/native-admin-lifecycle-request.v1.json), [held administration](../docs/native-admin-lifecycle.md), [maintenance](../docs/native-maintenance.md), [full archive](../schemas/native-full-archive.v1.json), [connection-held capture](../docs/capture-scope.md) |
| Other optional native surfaces | [Schema directory](../schemas/) and linked owner documents cover relocation, transfer, permissions, imports, managed files, cache maintenance, voice preferences and app-local reset. Each retains its own authorization, bounds and receipt rules. |

## Evidence and remaining acceptance

Capabilities state what the configured adapter offers; tests are executable
checks, not timeless pass claims. Record source revision and dependency graph,
then separately record the installed artifact and fresh worker graph used by a
test. A subprocess fixture with Core/Foundation demonstrates that boundary. It
does not establish every external client's behavior, live credentials, account
access, browser behavior, or an operator's deployed process. Cross-repository
and live acceptance belongs in the corresponding integration/deployment receipt.

The [coverage inventory](../docs/coverage.md),
[maintenance parity ledger](../docs/maintenance-parity.md) and
[recovery scope audit](../docs/recovery-parity-audit.md) distinguish retained
behavior from remaining work. Historical counts and receipts in those documents
are dated evidence, not a replacement for current qualification. No claim here
extends active question delivery, arbitrary source-cache deletion, cross-engine
history conversion, or native-only backup into complete product recovery.

Changes to this map follow changes approved by the owning upstream/family/local
contract. Update the affected schema, consumer obligations and falsifier together
when semantics change; incompatible extension changes require an explicit
version/negotiation decision. Documentation alone never upgrades capability or
acceptance status.
