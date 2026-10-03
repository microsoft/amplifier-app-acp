# App-local root bundle reset v1

The explicitly enabled admin maintenance peer advertises `appReset` v1 with the
single part `native.app-bundle-default`, retained undo and a required recovery
admin lease. Metadata initialization reads no private app settings and starts no
Core session. Four `_amplifier/admin` operations are available:

- `maintenance.appReset.prepare`: commandId, parts:[native.app-bundle-default],
  privateContentReviewed:true, optional restoreCommandId.
- `maintenance.appReset.apply`: commandId, preparedId, reviewHash.
- `maintenance.appReset.restore`: commandId, preparedId, reviewHash,
  resetCommandId, expectedPostResetRevision.
- `maintenance.appReset.inspect`: exact commandId OR preparedId + reviewHash.

Only the launcher-selected appHome/admin-settings.yaml bundle.active key is
cleared. Unrelated app keys, shared Amplifier settings/keys.env, workspace
settings, sign-in profiles, native history/events and module/source/generation
state remain preserved. Originals stay privately in maintenance/receipts.sqlite3
(app_reset_previews); public revisions are random opaque IDs, never private
content hashes. Public reviews are redacted, immutable and expire after600s.
This database is already included in native-maintenance-records/full-native
archives; adding these tables introduces no undeclared authority root.

Mutation runs under the exact held recovery admin context, native-home writer
exclusion and the ordinary Foundation per-settings-file lock. At most64KiB is
read. Unknown structures, symlinks, hard links, changed/expired reviews and newer
post-reset state refuse. The maintenance command is durable before the first
write. A lost result can be inspected cross-process without a session start;
unknown writes remain unknown and cannot be replayed or followed by a new reset.
Undo restores exact original bytes only after a fresh review and full-file CAS.
External noncooperating filesystem writes cannot be frozen by advisory locks.

Review preparation and prepared-review inspection take shared native-home
admission and the same exclusive per-settings-file lock used by Foundation's
settings writer. They can coexist with unrelated passive metadata reads; they
cannot read across a cooperating settings mutation. They write only private
review/revision records, not app settings. Preparation still requires its exact
recovery admin lease. Apply and restore retain exclusive native-home admission
and recheck the review against current settings under the file lock.

File-lock acquisition contention returns structured
`{reason:'native-app-reset-settings-busy',executed:false,replayed:false}` before
effects. This classification applies only to acquisition, not errors after work
begins. An admitted command refused for contention retains that exact refused
receipt; its identity is not automatically retried when the lock becomes free.
Exact command receipt inspection remains available without taking either lock.

No global credential-reset authority is added. The product recovery owner must
still authenticate explicit account-level reset and retain its all-owner hold
until exact owner receipts prove conclusive completion. This operation does not
claim a complete product settings reset.
