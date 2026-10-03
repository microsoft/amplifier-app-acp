# Reviewed native history import

This opt-in native administration capability creates a fresh, inert Core/Foundation
history. It does not start a worker, execute a model/tool/job, adopt an existing
native UUID, restore approval authority, or discover other histories. The launcher
must set `adminHistoryImport: true` and explicit `adminWorkspaceRoots`. Negotiation
is `amplifier.dev/native.admin.historyImport` version 1 with `staged: true`,
`maxBytes: 1048576`, and `chunkBytes: 16384`.

All requests use `_amplifier/admin` with `{cwd, operation, args}`. `cwd` must resolve
to an existing launcher-authorized workspace. Generic client `args.cwd`, native
homes, and native IDs are forbidden. The schema is
`schemas/native-history-import.v1.json`.

| Operation | Arguments beyond commandId | Result inside receipt |
| --- | --- | --- |
| history.import.begin | format json/jsonl, bytes, sha256, optional title/bundle | uploadId, receivedBytes |
| history.import.chunk | uploadId, offset, contentBase64 | uploadId, receivedBytes |
| history.import.preview | uploadId | previewHash, uploadId, sourceSha256, summary, workReplayed:false |
| history.import.commit | previewHash | native creation proof below |
| history.import.receipt | none | Exact original receipt or null (no outer receipt) |

Each mutation needs its own stable `commandId` (1–200 characters). A duplicate with
identical arguments returns recorded evidence without repeating work; conflicting
arguments are rejected. `chunk` accepts at most 16,384 raw bytes (21,848 base64
characters), with an exact contiguous byte offset. This fits a 32 KiB product
request without widening either the native 512 KiB admin request budget or ACP
framing. Upload bytes, chunks, command receipts, and normalization are transactional
private SQLite records. No source filesystem path comes from the browser.

Mutation receipts are `{commandId, operation, status, replayed:false, result?}`;
status is `succeeded`, `failed`, or `unknown`. Conclusive pre-effect validation
failures include `executed:false` and bounded `error`. Native file effects occur
only after a durable commit reservation. A crash leaves that command unknown and
its target fenced; another commit cannot consume that preview. An exact passive
receipt read can prove completion from the native committed marker after a lost
acknowledgement, without repeating or modifying the import. Otherwise it stays
unknown. No automatic retry, rollback, deletion, or repair is implied.

Preview summary is `{messageCount, sourceMessageCount, interruptedToolCalls,
roles, title, bundle, omissions}`. The preview hash binds workspace, upload,
original SHA256, normalized messages, and this summary. Committing that exact hash
is the review decision. Original UTF-8 JSON/JSONL bytes are retained privately.
JSON accepts a messages array or `{messages, metadata}`; JSONL accepts message
objects per nonempty line. Duplicate object keys, non-finite numbers, malformed
roles/content, orphan tool results, and event logs are rejected. Imports contain
1–10,000 original messages and normalized data cannot exceed 4 MiB.

Complete tool evidence is retained. An incomplete call receives an explicit
historical interrupted/unconfirmed tool result and is never replayed. Imported
system/developer content remains history. Original row metadata is preserved in
the private original file bytes, while canonical rows receive unverified import
provenance and new source-derived input IDs. No imported user row becomes human
consent. Executable parent/job/schedule/configuration/approval ownership is not
restored. An explicit bundle overrides recorded `bundle_name`/`bundle`, then the
launcher's default; absence of all three refuses import. This does not load or
validate a bundle until a later explicit continuation. JSON export rows with
`text` normalize to `content` and report that transformation.

Successful commit proof:

```json
{
  "nativeSessionId": "server-generated UUID",
  "historyCwd": "/authorized/existing/workspace",
  "executionDirectory": "/authorized/existing/workspace",
  "historyRevision": "native opaque revision",
  "title": "Reviewed title",
  "bundle": "reviewed bundle identity",
  "creationConfirmed": true,
  "workReplayed": false,
  "sourceSha256": "64 lowercase hex",
  "previewHash": "64 lowercase hex",
  "importCommandId": "exact commit commandId"
}
```

Foundation `SessionHistoryStore.save(..., preserve_system=True)` and
`SharedSessionStore.acquire` own canonical writes and the native writer lease.
Native home maintenance/admin gates cover the complete operation. Only exact
`history.import.receipt` is a passive read permitted during held admin intake.
It neither initializes SetupManager nor mounts Core. Cross-process queries use
the same trusted native/app homes and workspace. There is no native global list.

An independent host may create an AHP mapping only after querying the configured
engine's exact succeeded `history.import.commit` receipt and validating command,
workspace, creation proof, and identity collision guards. Browser-provided native
IDs are never authority. A later explicit resume/prompt uses this original native
history; it does not resubmit imported inputs. Imported source bytes are private
adapter evidence, outside the selected native-file archive's current coverage;
a full configured-product backup needs that owner store explicitly included.

## Legacy cleanup remains a separate product boundary

Retained Unified `management.py` history cleanup preview excluded the selected
conversation, non-idle work, and publishing-owned histories before applying a
presentation hide. Its optional purge removed `appData/sessions/<id>`. In the new
architecture that same namespace contains active native configuration, task state,
ownership and receipts: age or path alone does not prove obsolete app-only data.

A product cleanup owner must use a bounded authorized conversation index plus
explicit protection owners, present reviewed age/count selection, and separately
prove legacy presentation-file ownership with an exact migration manifest before
purge. Current migration has no such ownership manifest. Native import therefore
advertises no cleanup or hard-delete operation. Canonical transcripts, events,
workspace files and current app authority are never deleted by inferred age.

Validation covers actual ACP subprocess imports, full-size chunking, strict
rejections, cross-process receipt recovery, original-byte preservation,
interrupted-call evidence, held intake, lost completion, and actual Core/Foundation
continuation after a fresh explicit input. Offline fixture success is not a model
account test or browser acceptance.
