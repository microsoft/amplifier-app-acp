# Optional application bridge contract

Baseline ACP clients have no `app_control`, application guidance, automatic UI
context reads or implicit Unified shell/imagegen behavior. Native configured
Foundation bundles, explicit shared `bundle.app`, Core modules and durable
history are unchanged. The generic application identity is `Amplifier ACP`.

The AHP host can opt into implemented capabilities in initialize:

```json
{"clientCapabilities":{"_meta":{"amplifier.dev/host":{
  "version":1,"name":"Amplifier Unified",
  "appControl":{"operations":["get_state","list_actions","dispatch","history"],
    "guidance":"Discover each enabled action's exact schema before calling it."},
  "features":{"operations":true,"questions":true}
}}}}
```

Metadata is bounded to 32 KiB; guidance to 16,000 characters. Omit features that
are not implemented. Supported feature keys: `context`, `memory`, `outputs`,
`visual`, `capacity`, `operations`, `questions`, `observation`, `unifiedShell`,
`imageGeneration`. `unifiedShell`/`imageGeneration` only select implicit default
behaviors when community settings have no explicit `bundle.app` list. For a
consistent settings catalog, prefer explicit trusted `bundle.app` configuration.

The native supervisor emits JSON-RPC `_amplifier/host` requests:

```json
{"sessionId":"native-root-id","operation":"dispatch","args":{
  "action":"canvas.show","args":{"kind":"markdown","title":"Result","content":"…"},
  "id":"stable-effect-id"
}}
```

Host callback context is the admission-bound native session, AHP session URI,
workspace and initiating command/client. Never trust `clientId`, native paths,
`_inputClients`, `_inputBindings` or `_contextBindings` from payloads as authority.
Those are selectors/lineage hints; verify them against host admissions. A child
inherits its assigned input IDs rather than a newly focused unrelated client.

| Operation | Arguments / result expectation |
| --- | --- |
| `get_state` | `{path?,offset?,limit?,revision?}`; bounded shared/session/requested-resource representation only, never whole app state/private drafts |
| `list_actions` | `{prefix?}`; only implemented, authorized named actions and exact argument schemas |
| `dispatch` | `{action,args?,expectedRevision?,id?}`; same action owner/admission as UI; return receipt/result, explicit unknown on uncertain effects |
| `history` | `{action:list\|search\|read,session_id?,query?,scope?,include_children?,include_internal?,offset?,limit?,text_offset?,text_limit?}`; scoped catalog/history owner, no runtime startup/replay |
| `context.read` | `{surfaceId,representation:summary\|state\|view\|image,fields?,revision?,viewId?}`; bounded selected authorized surface. Images are `_image` typed payloads with revision evidence |
| `context.focus` | `{surfaceId,requests:0..3}`; adapter retains brief explicit interest; uses context reads, not generic session state subscription |
| `context.manifest` | `{}` plus input bindings; only shared/selected surfaces of admitted input. This automatic callback requires feature `context` |
| `memory.context` | `{expected?}`; bounded saved reference items; automatic callback only with feature `memory` |
| `outputs.image.read` | `{id,sha256}`; exact immutable attachment payload; feature `outputs` |
| `outputs.images.read` | `{images:[{id,sha256}]}`; ordered exact comparison; feature `outputs` |
| `voice.visual.read` / `computer.visual.read` | `{captureId}`; previously authorized capture, no new capture side effect; feature `visual` |
| `capacity.admit` | `{call:<native call evidence>}` -> `{allowed,budgetRevision,admittedAt?,reasons?}`; only installed with feature `capacity`; never silently bypass enabled saved budget |
| `operations.observe` | `{runtimeSessionId,event:<native operation lifecycle>}`; only feature `operations`; associate trusted child/root lineage |
| `questions.admit` | `{questionIds:[...]}`; only feature `questions`; bind questions to admitted session/input |
| `observation.admit` | native observation envelope from explicit `observation.submit`; requires a host decision, otherwise fails |

Native controls are separate `_amplifier/native` requests, not private Python
objects forwarded through the application bridge. Provider/context transforms
never contact omitted host features. `app_control` tool schema only lists the
advertised operation subset. Generic discovery guidance promises no unavailable
voice, canvas, publishing, drafts or global catalogs. The host may add short
capability-specific guidance for its actual shipped owners.
