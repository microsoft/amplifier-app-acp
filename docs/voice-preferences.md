# Shared voice preferences

Native voice preferences are non-secret shared defaults. They are separately
launcher-enabled with `adminVoicePreferences: true`, require an authorized existing
admin workspace, and do not require `adminVoiceCredentials`. They never resolve an
API key, probe an account, mount Core, start a worker, or change an existing call.
The media owner continues to own call admission, active-call policy and transport.

Initialize advertises `amplifier.dev/native.admin.voice.preferences` as
`{version:1,cas:true,receipt:true,fields:["preferredModel","fallbackModel","voice","interruptions"]}`
only when enabled. `voice.configuration` always returns sanitized saved defaults:

- `preferredModel`, `fallbackModel`, `voice`, `interruptions`;
- `preferencesRevision`: SHA256 of these four canonical values;
- `preferenceFields`: the writable field names, or an empty list when disabled;
- `models`: bounded catalog entries `{id,name,description,voices:[{id,name,description}],...}`;
- `fallbackBehavior: "stored-only"`.

The model/voice catalog preserves the existing native application's
`amplifier_web/voice_options.json` (catalog date 2026-09-23, inspected source
`amplifier-unified` revision `1bbdd828d8c7f5e749c079f7231c3bf1fe5de816`). It records
supported transport choices, not current account access or external availability.
No model call was used to qualify these settings.

`_amplifier/admin` operation `voice.preferences.update` takes
`{commandId,expectedRevision,patch}`. Patch accepts only the advertised four fields;
credential fields remain exclusive to the separate private `voice.configure`
operation. Model and voice compatibility and a strict boolean for interruptions
are validated. Changing the preferred model without explicitly choosing a voice
retains a compatible current voice, or uses `marin`, matching retained behavior.

The shared `SettingsStore.update` delegates to Foundation's per-file locked
`update_settings`, the same authority used by the native/CLI settings ecosystem.
CAS and patch validation occur inside that lock. Other settings, unknown future
voice fields, and credential source are preserved. Revision intentionally covers
only voice preferences, so independent configuration edits do not invalidate them.

Each stable command reserves a private indexed receipt before writing. Success is
`{commandId,operation:"voice.preferences.update",status:"succeeded",replayed:false,result:<preference projection>}`.
A stale draft is conclusively refused with
`{status:"failed",executed:false,reason:"preferences_changed",currentRevision,error,...}`.
Invalid patches use `reason:"invalid_preferences"`. Only these known pre-write
validation errors are classified as no effect; transport/filesystem exceptions
retain an unknown receipt. Reusing an ID with other arguments is refused.

`voice.preferences.receipt {commandId}` returns that exact receipt or null.
This is an explicit passive operation usable under a held admin fence. A single
bounded, non-secret `voice._amplifier_acp_preferences` marker is written atomically
with shared defaults; it binds command, signature and result revision. A lost
acknowledgement can be proved by inspecting that marker without repeating work.
A later native preference write persists any proven prior completion before
replacing the marker. An external edit that invalidates unresolved marker evidence
leaves the original outcome unknown; it never authorizes replay. The original
receipt store is independent owner data, not implicitly included in a selected
native-session archive.

Both the old application's connect path and the extracted media worker choose the
preferred model only. The legacy fallback field was saved but not consumed.
Preserving `fallbackModel` therefore does **not** implement automatic fallback.
Clients must label that limitation or leave the fallback control read-only. A
future transport implementation needs its own safe admission/error policy and
account qualification before claiming automatic fallback.

The product composition must expose the separate operation only after negotiation,
retain exact command IDs through loss/recovery, project the authoritative returned
revision and choices, and invalidate the scoped voice metadata after a confirmed
save. Client draft typing remains local. Existing credential configuration semantics
and authorization do not change.
