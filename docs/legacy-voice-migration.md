# Legacy voice-only history migration

Current Unified main can persist an entire spoken conversation in `app.sqlite3`
without creating a native transcript. Native catalog discovery cannot list that
conversation until native history exists. `amplifier_acp.legacy_voice` supplies
an offline, reviewed conversion into ordinary Foundation history. It is not an
automatic startup migration or a complete installation upgrade.

The compatibility reader follows main `7cef7cc0336575ef4841e23c6bf88a4dc77fb7ce`:
`state_records.load`, `cold_display.load`, `state_storage.resource`, and
`resource_files.resolve`. It overlays the latest scoped session records,
honors deletions, and verifies content-addressed cold message bodies. No legacy
service is initialized. The existing Native voice writer and passive history
reader define the resulting voice rows; the catalog needs no alternate history
authority. History import remains the separate path for arbitrary user files.

## Use on an inactive, retained snapshot

1. Stop new admission and quiesce the source installation using its owning update
   path. Capture its application database, artifacts and native history together.
   A live online database backup alone does not prove cross-owner consistency.
2. Run the installed candidate interpreter with
   `-I -B -m amplifier_acp.legacy_voice --database <copied-app.sqlite3>
   --native-home <copied-native-home>`. The bounded report has statuses and a
   `planHash`; it does not print conversation text or credentials.
3. Review every status. `ready` means this narrow voice conversion is supported.
   `native-history-owned` means existing primary/backup/checkpoint history wins.
   `review-required` and `empty` stay in the original snapshot and are not silently
   dropped from the switch accounting. An unreadable snapshot fails the plan.
4. Repeat with `--output <new-private-staging-directory>
   --expected-plan <reviewed-planHash>`. The output directory must be new and
   separate from both source roots. A changed plan is refused before writes.
5. Read `receipt.json`. Only `completed` is eligible for subsequent owner-managed
   staging. A `reserved` receipt/partial directory is unconfirmed and must not be
   activated or overwritten. On a lost acknowledgement, inspect that same path;
   do not run another migration into a different catalog root.
6. During distribution migration, combine the converted `native/projects` entries
   with the exact existing native-history copy using exclusive creates, then
   discover the final canonical home. Do not index both temporary and final roots:
   catalog URIs include native-home identity. Private-client mapping must use the
   final catalog native-ID resolver. Keep the old installation intact for rollback.

Original native session IDs and recorded workspaces/bundles remain unchanged.
Rows have stable call/item IDs, `recordedOnly` voice provenance, and no newly
admitted user-input or grant authority. Original session records are retained
privately under `originals/`. Conversion does not launch a runtime, model, tool,
schedule or continuation. Cold resume remains passive; only an explicit later
input starts work. No current working directory is substituted for an absent
recorded workspace.

## Explicit boundaries

This converts only idle voice-only chats with no existing native transcript or
legacy checkpoint. Mixed histories, missing/corrupt blobs, duplicate voice IDs,
attachments/replies/reactions, managed locations, parent relationships, retained
selection/configuration, workers and uncertain inputs require their own reviewed
reconciliation. Existing native transcripts are never merged by text matching.
Settings, credentials, client drafts, outputs and other capability-owner data are
outside this converter and remain separate release gates.

Source tests cover scoped-record replacement/deletion, frozen blob integrity,
unchanged originals, native-backup precedence, stale-plan refusal, exclusive
output reservation and metadata-only catalog discovery. The installed Native
integration exercises passive resume, an independent fork and one explicitly
submitted new turn, with retained settings/key bytes and an offline provider.
These tests do not establish whole-installation migration or physical voice
acceptance.
