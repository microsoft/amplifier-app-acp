# Saved Work reference repair

This optional native maintenance operation carries the metadata-only repair in
Unified v0.20.51 forward through ACP. The comparison baseline is Unified main
`c7d57076280c944a1133ee85417e84e198c263ba` (v0.20.52), including
`amplifier_web/bundle_reference_repair.py` and its preservation/lease tests.
Startup resolution already canonicalizes the registered legacy alias; this
explicit repair updates retained root-chat metadata for future readers.

The launcher must enable `adminMaintenance`, `maintenanceFullNative`, authorized
workspace roots, and `maintenanceExternalWriters: foundation-cooperative` or
`stopped`. Only then does admin maintenance advertise `bundleReferences` version
1 with `metadataOnly`, `previewRequired`, `receipt`, `paged`, and
`workReplayed:false`. It does not mount bundles, start workers, submit input or
alter transcripts. Ordinary maintenance discovery does not scan saved chats.

| Operation | Arguments / result |
| --- | --- |
| `maintenance.bundleReferences.preview` | Empty arguments. Cold scan; returns an immutable `previewHash`, counts and the first result page. |
| `maintenance.bundleReferences.page` | Exactly one `previewHash` or `reportHash`; optional integer cursor and limit 1–100. |
| `maintenance.bundleReferences.apply` | Exact `previewHash` and durable `commandId`. Returns a retained `reportHash`, `sourcePreviewHash`, per-chat outcomes and completeness. |
| `maintenance.receipt` | Original `commandId`. Inspects the original command; never repeats the repair. |

Preview inspects native metadata independently of UI selection, including
unopened, zero-turn, archived roots and independent forks. It excludes child and
internal sessions. Only `Work` and `bundle:Work` referring unambiguously to the
registered `work` source are eligible. Local files, custom aliases, missing
workspaces, conflicting identity/reference fields, absent primary metadata,
links and unsupported Foundation lease identities remain unchanged and reported.
Inventory limits are 20,000 directories, 256 MiB metadata, 5,000 report rows,
8 MiB per metadata/plan file, and 100 rows per public page.

Apply rechecks the exact preview metadata and bundle-registration hashes while
holding the native execution lease and Foundation sibling metadata lock for each
chat. Busy writers are skipped. It changes only `bundle_name` and an existing
`bundle` field, using Foundation's metadata save and backup behavior. It does not
rewrite client views, configuration overrides, private drafts or pending inputs.
Cancellation joins the original write before releasing its lease. A crashed or
uncertain command remains inspectable and is never replayed under its identity.
`complete:false` identifies any preserved/skipped entry; a new explicit review
is required to consider those entries again.

The optional product capability requires the authenticated host maintenance
authority and a UI caller for this cross-chat operation. UI actions use the same
native owner; their saved request identity survives browser reload. This does not
grant an agent access to unrelated chat metadata or establish whole-product
backup, migration, release or physical-device acceptance.

Acceptance covers exact metadata preservation, backup bytes, busy writers,
changed metadata/registrations, custom/local references, missing metadata,
symlinks, unsupported identities, cancellation, paging, authority and unknown
receipts. The compiled browser is exercised through an independently installed
native wheel and public host, including a response lost after successful apply,
reload, receipt recovery, no worker startup, desktop and mobile layout.
