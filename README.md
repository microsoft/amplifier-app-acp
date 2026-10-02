# Amplifier ACP agent

The ACP v1 boundary for the **full modular Amplifier application runtime**:
Foundation prepares bundles and creates Core `AmplifierSession` instances;
`loop-live` retains providers, tools, hooks, context, delegation, scheduling,
steering, naming, approvals, native storage and execution ownership.

This package does **not** use `microsoft/amplifier-agent`, import Unified's
`AppService`, or substitute an echo engine. The fixture provider is test-only.

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
  "maxSessions": 32
}
```

`home` owns the existing community settings/native history. `appHome` owns private
configuration/controls/attachments/reports. `registryHome` can override the
Foundation cache without moving native history. Omitted homes use existing
`AMPLIFIER_HOME` / `AMPLIFIER_WEB_HOME` conventions. Credentials remain in the
native configuration/environment; they are never ACP authentication payloads.
Do not point an experiment at another owner's state.

Each loaded native session uses a separate worker subprocess so cwd, provider
credentials, module imports and native ownership never mix across sessions.
Settled workers retire after `idleSeconds`; active turns, children, approvals,
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
