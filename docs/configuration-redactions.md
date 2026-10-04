# Preserving credentials when editing a mount plan

`configuration.inspect` returns a redacted plan. Applying a draft restores the
existing private values before validating and remounting. A context-only change
previously failed when an unrelated provider contained credentials inside a list
without module IDs, because every list was treated as a list of module instances.

List restoration now finds one unambiguous prior counterpart. Module instance
IDs retain priority; a nested entry may instead have a unique name. A list with
no identities may match by unique public structure, which masks secrets and
preserves distinctions such as booleans versus numbers. Order never assigns
credential ownership. Duplicate identities or structures, missing identities,
identity-kind changes and changed anonymous endpoints refuse restoration. Lists
are not extended; omitted entries remain omitted and explicit new values stay
explicit. Nested arrays follow the same matching rule.

Changing an anonymous endpoint while retaining its redacted credential remains
ambiguous and requires explicit credential management. Entries with established
stable identities can change non-secret fields. The fix does not guess which key
belongs to a new anonymous endpoint. Scope permissions, redaction
field names, configuration revisions and the existing restore-on-failure path
remain unchanged.

## Class audit and evidence

- Native ACP `04572bb4e3e788e84dbaa4cd9b30cc1ae64d3ae4` and the extracted
  legacy Unified implementation at `cec95deae70b67f659ce4f55737cd6fbb84211b9`
  both map every list by module identity. Existing native tests cover top-level
  provider reordering, required modules, CAS, remount and cancellation.
- App CLI `5aaafb478d02cee8c396967b954299a08e8a1efd`,
  `runtime/config.py:restore_redacted_secret_values`, already matches nested
  lists by unique id/name or unique non-secret structure and never by position.
  Native adapts this approach while retaining its existing refusal for unmatched
  redactions and its module instance identity semantics. Duplicate submitted
  structures also refuse rather than duplicating an existing credential.
- Foundation configurator inspection at
  `467f870abb4487184789386ecdb460410f64bd9f` redacts its projections; it is not
  the owner of the native full-plan apply/inverse-redaction operation.
- Current web `b31addde6b2c99353f145aea768b05456b73015b` already exposes
  module filters, enabled/source fields, scalar controls, structured summaries,
  credential indicators and private per-module JSON drafts. Those UI paths still
  need their own browser acceptance; this native fix does not qualify them.

The audit used the current `microsoft/amplifier` module catalog at
`33b75749920472257d1d85d20a5699ecf9d1ece7` and the existing 14-provider inventory
recorded with the F04 source-selector packet. Provider modules remain unchanged.
The actual ACP fixture demonstrates a context edit and reordered nested endpoints
through Foundation/Core remount, with exact private key-to-endpoint assertions
and no secrets in public responses. A changed anonymous endpoint is refused and
the prior configuration remains intact. No model/account request or live service
change is part of this qualification.
