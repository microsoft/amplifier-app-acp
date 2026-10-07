# Restoring native configuration and credentials

Full-native capture and inactive restore use the same source-configuration
validator. Archives can retain managed-allocation requirements and launcher
module-source overrides without enabling either on the destination. The archive
schema describes both fields. An unavailable original module directory remains
evidence; it is not read during restore. Invalid metadata that requests automatic
activation is refused.

Credential review covers `keys.env`, Codex `*-oauth.json` files, the
`chatgpt-plan` directory and app-owned `config/chatgpt-sign-in` profiles. When
credentials are excluded, these entries count as authoritative omissions.
Other inline secrets may remain in reviewed configuration files, as before.
This is native-owner coverage, not complete product coverage.

After verifying and copying the archive into a new, inactive destination, restore
rebinds recognized provider `token_file_path` and `host_file_path` references to
captured files within the new native/app homes. Default ChatGPT paths are made
explicit when the corresponding account files were captured. Settings covered
are global, native session, staged project/local settings and app session mount
plans. All other settings and provider fields are retained.

The source archive and original installation are unchanged. Every edited copied
configuration is retained byte-for-byte under `configuration-originals/`.
`credential-relocation.json` records original and resulting hashes, changed fields
and unresolved references. Account files themselves remain byte-identical.
The original archive manifest describes the pre-relocation bytes; compare edited
settings with the relocation receipt and the retained originals.

Relative paths, tilde/environment paths without explicit source-user context,
uncaptured external files and missing credentials remain unchanged and are listed
for review. Restore never probes an old credential path or falls back to a live
account. `native-configuration-review.json` also retains external managed-file
and module-source requirements. Restore still requires configuration review and
runtime qualification before activation; these receipts do not verify an account
with its issuer. No workers or turns start during restore.

Full-native manifests retain each captured file and directory's owner permission
bits. Inactive restoration applies those digest-verified permissions after
configuration rebinding and child creation, preserving executable scripts and
read-only shared source objects. Group/other and privileged bits are not restored;
the new destination remains private. TAR member permissions cannot override the
verified manifest. Older manifests without these bits retain the previous private
defaults (files 0600, directories 0700), so their executable source trees need
separate qualification before activation.

This repair covers full-native capture and its matching restore. Reviewed
selected-history archives contain data rather than executable source roots and
keep their private defaults. Product installation archives already preserve file
modes separately. Foundation's shared-source verifier continues to reject modified
tracked files and executable-bit changes; its integrity rules are unchanged.

The affected implementations reviewed are the full-native capture and restore
owners, the launcher module-source validator, native provider account setup,
the OpenAI ChatGPT provider's Codex and plan layouts, and Foundation's scoped
settings reader. The product backup coordinator remains the owner of cross-owner
capture, and this change does not migrate unrelated capability databases. Legacy
voice-only conversion is described separately in `legacy-voice-migration.md`.

Installed tests exercise successful capture/restore, explicit credential
exclusion, every supported settings scope, changed source selectors, absent
original source directories, rejected automatic activation, and interrupted
restore receipt recovery. A retained global settings/account snapshot was also
read through installed Native after relocation with network and subprocess
execution blocked. Whole legacy-installation cutover and rollback remain a
separate acceptance requirement.
