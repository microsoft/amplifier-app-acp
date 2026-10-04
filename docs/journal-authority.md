# Native journal schema admission

Native receipt journals are authority for original commands. A missing table must
not turn an unknown command into a fresh command, an interrupted generation into
idle, or an enrolled quote/outcome namespace into an unused feature. This guard
changes journal admission only. Canonical transcripts, effect-specific ownership,
settings CAS, exact request bindings and native failure/unknown semantics remain
with their existing owners.

## Covered owners

`journal_schema.py` defines fixed layouts for bundle commands, maintenance,
lifecycle receipts, generations, staged history imports, voice preferences,
permissions, managed disposal, steering, coordination, message adjuncts and input
provenance/outcomes. The last two files have cooperating writers: message quote
and reaction paths share one layout; input provenance and terminal outcomes share
another. Every initializer of those files uses the same admission path.

Each existing file is checked through SQLite read-only mode, including committed
WAL state, **before any writable SQLite connection**. Main and sidecar paths must
be regular files without symbolic/hard links; directories and FIFOs refuse before
SQLite access. A file is genuinely new only if the main, `-wal`, `-shm` and
`-journal` paths are all absent, using `lstat` so dangling entries also refuse.
An existing empty file is not a new database. Passive reads of untouched journals
create no directories, database or schema lock.

A short per-journal `.schema.lock` serializes cooperating initializers, including
first creation. It is only an OS lock, never a replacement receipt or ownership
record. Schema publication, the supported import migration, namespace enrollment
and the `user_version` marker commit together. Ordinary record transactions and
their owner locks retain their existing boundaries. A damaged initializer does
not normalize or reconstruct command records. Maintenance/generation construction
and lifecycle receipt lookup validate existing journals without creating new ones.

Checks are bounded schema metadata: fixed table names, column order/types/defaults,
NOT NULL/primary-key and unique-index key metadata, plus the existing coordination
revision singleton. SQL projections bound individual metadata values before they
reach Python, and noninteger singleton values refuse without materializing their
content. SQLite internal schema parsing is not a global allocation/VFS limit.
There is no production record census, full-file hash/copy,
`integrity_check`, history scan or shadow mirror. Derived SQL indexes can still be
built from an admitted schema on a write; this existing index-building work is
separate from the bounded admission check.

## Legacy and optional namespaces

`tests/fixtures/native-journal-legacy.json` records DDL and exact source revisions
for the 14 historical base/optional profiles. The already-supported import
`uploads.target` addition remains valid only for the exact complete unversioned
pre-target layout. Arbitrary missing columns/tables are not a migration.

Unversioned reactions-only and provenance-only files remain valid. Their quote or
outcome tables are optional until actually enrolled. Existing optional tables are
enrolled on the next write. Explicitly adding an optional namespace atomically
publishes both its table and marker; all writer peers preserve that enrollment.
Once marked, removing the table refuses instead of returning an absent receipt.
An unknown family/version marker or a marker/layout disagreement refuses.

Unversioned absence can be indistinguishable from an older legitimate profile;
the guard does not invent historical enrollment. It also cannot detect a valid
record deletion, a coherent database replacement, or deletion of the main file
and every sidecar. Old writers that do not honor these markers are outside the
cooperative initialization guarantee. The guard is not a defense against an
adversarial path swap during file access, or a replacement for schema integrity
and storage recovery tools. SQLite read-only WAL inspection may create empty
WAL/SHM coordination files; existing main and WAL authority bytes are preserved.

## Qualification boundary

The focused tests use small owned journals and inert callbacks. They cover exact
unknown bundle no-reentry after damage, recorded legacy profiles, optional-table
loss and rollback, genuine-new versus orphan sidecars, fixed singleton loss,
concurrent initializer/namespace writers, stable nonregular-path refusal, and a
real killed writer retaining a damaged committed WAL. Hashes are used only on
these tiny fault fixtures to establish preservation. Healthy named-row owner controls also verify exact original failed/succeeded/unknown
receipts, duplicate identity binding and deterministic connection closure.
No model, provider, bundle,
import, reset, disposal, generation build, live service or user-history effect is
needed for these tests.
