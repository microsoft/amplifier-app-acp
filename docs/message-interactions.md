# Native message adjuncts — v1 integration candidate

This module implements the approved CS1–4/7 and AP1/3–5 message boundary. It is
an adjunct to canonical native transcript/events, never a resume store or draft
buffer. The native dispatcher and explicit-send admission are wired when a trusted principal provider is injected. Default CLI composition has no provider and does not advertise these methods; bridge/account binding remains a separate integration requirement.

Reply selection and Clear are client-local actions. They retain a native
reference beside the private per-conversation draft, never synchronize the draft
buffer and never send, enqueue or start a worker. Only explicit Send calls
`freeze_quote(directory, native_session_id, reference, input_id=..., epoch=...)`.
The owner supplies the trusted transcript directory and current context epoch.
The reference has exactly `sessionId`, `messageId`, `position`, `historyEpoch`
and optional `sourceDigest`; the client cannot supply an excerpt or path.
The helper verifies one complete public user/assistant row (maximum 256 KiB),
its byte boundary and actual `history.message_id` identity. Hidden/service/tool
rows, archive identities, missing primary history and stale epochs refuse.
Unchanged messages remain valid after append; rewritten records refuse.

The returned immutable quote contains the exact input identity, session/message,
role, source digest, history epoch, up to 4000 characters, truncation flag and
quote digest. Freeze it with the original durable input before dispatch. A lost
reply must inspect that original input; it must not create another quote or turn.
`native.message_interactions.quoted_text` treats the excerpt as historical data;
`prepare_input` expands mentions only in the new explicit input. Admission must
hold the existing native history/admin gate across reference validation and
freezing/admission so a concurrent reset cannot invalidate the reviewed source.
The server/worker integration must preserve this frozen quote on the accepted
message and fork/edit/export views. This module alone does not establish that.

## Proposed negotiated native methods

`handle_message_metadata(store, method, args, directory=..., epoch=...)` is the
minimal handler seam. The server negotiates and derives the authenticated principal before calling it:

| Method | Exact arguments | Result |
| --- | --- | --- |
| `_amplifier/message/reaction` | `sessionId`, `reference`, `emoji`, `present`, `commandId` | Selected message aggregate, actor's own choices, revision, changed flag, exact command ID, `sent:false` |
| `_amplifier/message/read` | `sessionId`, `messageIds` (1–50 distinct) | Selected metadata items, `coverage: selected`, `sent:false` |
| `_amplifier/message/receipt` | `sessionId`, original `commandId` | Original result or null, `replayed:false`; absence grants no retry authority |

`Server(config, output, message_principal=trusted_provider)` accepts an injected
synchronous callable `trusted_provider(native_session_id) -> authenticated account
principal | None`. It must authorize the native session in its existing trusted
connection/account context; browser identity and method arguments cannot populate
this provider. No new launcher identity config field is introduced.

`MessageMetadata(app_home, native_session_id, actor_id=trusted_actor)` takes
**authenticated actor identity from host/owner context**, never protocol args.
An authorized agent acts through the same principal and scope as its user; origin
is not another actor or proof of user consent. The native bridge owns authenticated
AHP/native ID mapping and canonical history workspace/epoch binding. It must reject
wrong conversation and forged actor/path/epoch claims before calling the module.

Eight supported emoji use desired presence per actor. Removing one actor's emoji
does not remove another's. Result `reactions` is the aggregate, `counts` counts
actors and `mine` is the authenticated actor's selection. At most 100 actors may
hold reactions on one selected message. Exact command/payload/actor retries return
the immutable original result, even if history later disappears; changed payload
or actor reuse refuses. Reactions/read/select start no worker or turn.

The selected-session `message-interactions.sqlite3` is created only on the first
explicit valid reaction. Annotation change, selected revision and original
receipt commit in one transaction. Connections are short lived; untouched passive
reads create no directory/database and existing reads use SQLite `mode=ro`.
No global startup enumeration or full conversation object is maintained.

Publish only the affected session/message metadata and revision to its interested
subscribers. Reconnect calls selected `read`; do not replace drafts or hydrate
unrelated histories. Host capability actions `message.reaction/read/receipt` are
advertised only after actual native negotiation and dispatcher wiring.

## Copy, retention and export obligation

`export_metadata(message_ids)` is a trusted selected export hook (1–50 IDs), with
up to 100 actor/emoji entries per message; overflow refuses and preserves originals.
It returns version/session/selected annotations and `executionAuthority:none`,
never drafts or command receipts. A copy owner must explicitly preserve/remap the
selected annotations against independently verified retained target message IDs;
source receipts and source execution authority never become target authority.
Retained native stores/archive plans must include this adjunct database and its
SQLite-consistent contents. Until those consumers are wired, fork/copy/export
acceptance remains unqualified; silent omission is not an accepted fallback.

## Implemented native wiring and remaining integration handoff

ACP Send accepts `_meta["amplifier.dev/messageReply"]` containing only the native
reference and `_meta["amplifier.dev/command"]: {commandId: originalInputId}`.
Before worker startup, the existing session admission lock and native home gate
cover validation/freezing and a durable exact-input reservation. Interrupted
reservation remains unknown and never replays. A confirmed worker acknowledgement
marks admission accepted, without claiming model/task completion. The existing
context wrapper adds the frozen `replyTo` only to the exact accepted native user
row; public history projection and native copied-message projection retain it.
`runtime_worker` is unchanged; its existing `reply_context` handling remains in use.

Reaction dispatcher paths accept no native path or actor arguments. Selected
canonical scope comes from a loaded session or a prior bounded history read.
Passive metadata/receipt reads need no transcript access, so original receipts
remain inspectable after history becomes unavailable. Updates use
`_amplifier/message/update` for only the changed selected message; exact committed
retries do not republish. The host must scope forwarding to interested clients.


1. Native bridge/composition: bind the trusted principal provider, negotiate these three methods, derive trusted session,
   transcript directory/current epoch and authenticated principal, and call the
   handler under existing native writer/history gates. No caller actor/path.
2. Native prompt admission is implemented; qualify its existing worker route in
   the combined distribution. Public dispatcher tests use an admitted-worker
   boundary fixture and actual canonical transcript/context files; they are not
   paid-model or full loop-live execution acceptance.
3. Host/native bridge: route negotiated scoped actions, retain original command
   receipts, emit narrow subscriber updates and expose selected reads.
4. Web/client: private reply preparation/clear and persisted locator beside draft;
   retain actual native message ID/position/epoch, not a response-part ID. Preserve
   newer drafts, share metadata only, and carry reference on explicit Send.
5. Copy/archive owners: consume the bounded export hook and preserve adjuncts;
   qualify two clients, agent parity, interrupted delivery and retained copies.

The source tests exercise cold real native files, SQLite transactions/concurrent
connections, restarts, exact receipts, public history IDs and negative references.
They do not qualify the unwired host/browser/worker route or live deployment.
