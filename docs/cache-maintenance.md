# Cold cache inventory and reviewed derived-file clearing

This native application extension inspects configured sources and retained cache
roots only after an explicit maintenance request. It does not initialize Core,
resolve bundles, fetch Git remotes, enumerate canonical histories, or add a
background cache watcher. Pages are read from an immutable SQLite scan index.

## Coverage and limits

This is partial coverage of F12.09, F12.21 and F17.02, not source-cache garbage
collection. Every source directory, shared source object, build view, binding,
HTTP/ZIP download, installed package, unrecognized format, and linked path remains
non-removable. Other clients and old runtimes may still use them: absence from the
current configuration does not prove retirement.

The only eligible files are untracked Python bytecode in an application-owned
legacy Foundation cache clone. Eligibility requires an unchanged source file,
current interpreter magic/tag, valid timestamp or source hash, and an exact match
between the bytecode payload and compiling the retained source without executing
it. The complete Git tree must have no modified tracked files or unrecognized
untracked/ignored content. The local upstream must contain HEAD; this is reported
as `none-local-proof`, not proof that a remote received a push. Missing upstream,
local commits, retained generation/runtime references, immutable shared-source
markers and incomplete reference inspection prevent clearing.

The reference inventory reads only explicit shared/project/local configuration,
launcher bundle/runtime manifest, native configuration records, registry,
active-generation pointer, retained generation receipt files and worker leases.
It never performs stale-lease cleanup or infers worker liveness from a PID. Roots
include the application's legacy cache, native shared cache, explicit registry
cache, configured shared object/build store and retained generation caches. Only
the first root can contain eligible bytecode. Unsupported nested formats are
visible as preserved unknown entries. The inventory is not a census of arbitrary
external source directories or every ecosystem cache.

## Wire contract

Negotiate `admin.maintenance.cacheInventory` under the native ACP initialize
metadata. Version 1 reports `cold: true`, `paged: true`,
`clearKinds: ["python-bytecode"]`, `sourceRetirement: false`, and
`requiresRecoveryAdminLease: true`. Requests use `_amplifier/admin`, with `cwd`
resolved by trusted launcher administration roots. The browser cannot provide a
path or native home as deletion authority.

| Operation | Arguments | Result |
| --- | --- | --- |
| `maintenance.cache.scan` | `commandId` | Completed scan identity, revision, expiry, counts, reference revision, coverage and durable receipt |
| `maintenance.cache.page` | `scanId`, `revision`, optional `cursor`, `limit` 1–50 | Stable indexed `items`, `nextCursor` and scan metadata |
| `maintenance.cache.preview` | `scanId`, `revision`, 1–50 unique item `ids`, `reviewed: true` | Exact `previewHash`, expiry, selected public items and byte count |
| `maintenance.cache.clear` | `commandId`, `previewHash` | Removed IDs/bytes, zero source directories/canonical files changed, exact receipt |
| `maintenance.receipt` | original `commandId` | Passive exact receipt; never repeats effects |

Schemas are in `schemas/native-cache-maintenance.v1.json`. Public items contain
`id`, `kind`, `label`, root-relative `location`, `usage`, `references`, `clearable`
and `reason`; optional source/Git facts distinguish configured, referenced,
dirty, unpushed and unknown entries. Source URLs omit userinfo, query and fragment.
Private file paths, stat identities and hashes remain inside owner storage.

A scan expires for new review after 15 minutes; a preview after at most five.
Expired index pages remain readable. Clearing requires the exact held **recovery**
admin lease, explicit trusted-launcher stopped-external-writers policy, and the
exclusive native maintenance writer gate. This is a cooperative lease plus an
operator assumption about other writers, not an OS proof that arbitrary processes
cannot write. Product coordinators must additionally hold their actual owner
intakes before entering the maintenance scope.

The whole selected set is validated before the first unlink, including the
reference census, exact source/bytecode hashes and stat identities, and Git
facts. Each file is checked again before unlink. A conclusive pre-effect refusal
sets `executed: false`; any partial effect or uncertain interruption stays
`unknown`. Reusing a command returns its exact saved receipt and never replays.
Cancellation joins the actual file thread while retaining exclusion. The scan
and preview indexes share the existing native maintenance receipt database and
are included by its SQLite backup API.

Memory/I/O bounds: pages and selections 50 entries; individual inspected files
and Git output 2 MiB; source extras 10,000; explicit disk inventory 100,000 entries;
retained generations 5,000; per-Git invocation 15 seconds; cold scan 300 seconds.
Limits refuse or classify unknown; they do not authorize deletion of omitted data.

## Source-class audit (2026-10-03)

No resolver class was changed. The adapter uses passive evidence instead of
calling a resolver's broad cache-reset behavior.

| Implementation | Audited source | Consequence |
| --- | --- | --- |
| Current module inventory | microsoft/amplifier `33b75749920472257d1d85d20a5699ecf9d1ece7`, `docs/MODULES.md` | Enumerated the relevant source implementations before adapter changes |
| Foundation Git/File/HTTP/ZIP and shared store | `467f870abb4487184789386ecdb460410f64bd9f`, `sources/*`, `cache/disk.py` | Shared objects, app ref bindings and build locks do not establish global reader retirement; downloads and local sources preserved |
| Module resolution File/Git/Package sources | `88276bfa385abf5cb27429e14cb40e2077886a3b`, `sources.py` | Legacy package-target caches are not assumed to be Foundation Git clones |
| Skills legacy/shared cache | `2f58408a879535df36da5c92d2b073f49271ade4`, `modules/tool-skills/.../sources.py` | Shared and legacy adoption preserve local authority; no skills-source clearing |
| Legacy CLI cache manager | `5aaafb478d02cee8c396967b954299a08e8a1efd`, `utils/cache_management.py` and reset flow | Broad recursive clearing was not transplanted |
| Native updater/generation owner | native baseline `fd568f08bd56df6108e04287eedf9a1ea9b7ebd4`, update sources/storage, runtime environment, generation leases | Retained baseline/rollback/worker references are inspected without mutating leases |

Tests exercise actual ACP subprocesses with an impossible worker launcher,
credential-redacted fixture URLs, dirty/untracked/unpushed/unknown/retained cases,
stable pages, expired/stale review, competing writer exclusion, exact receipt
recovery, partial-clear failure and cancellation while a file effect is running.
Fixture success is not account, browser, platform or production-cache acceptance.
