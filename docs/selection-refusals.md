# Pre-effect selection and launcher refusals

Optional native operations still require their initialized capability and
launcher authority. These errors refine the existing JSON-RPC `-32000` response;
they do not grant new operations, change models, or recover old unknown commands.

For `_amplifier/native` `provider.select`, invalid provider/model input is
rejected before changing the selected provider, saved selection or settings:

```json
{"executed":false,"replayed":false,"reason":"provider-selection-invalid"}
```

A busy selection is refused with the same flags and
`reason:"provider-selection-busy"`. This covers the selected worker's generation,
queued/delegated work, and the session admission lock before worker startup.
Standard ACP `session/set_config_option` with `configId:"model"` uses this
same busy refusal. Standard unknown option/value remains invalid params.

These proofs come from `ProviderSelectionRefusal`, which is distinct from a
general `ProviderSelectionError` during startup. Errors after selector mutation,
including persistence failure, are not declared `executed:false`. There is no
error-message inference, automatic replay, or implicit provider fallback.

Optional administration disabled by the trusted launcher is refused before
owner initialization, lifecycle admission, file writes or provider work:

```json
{"executed":false,"replayed":false,"reason":"native-admin-launcher-disabled",
 "operation":"generations.check","launcherOption":"adminGenerations"}
```

`launcherOption` is one of `adminGenerations`, `adminMaintenance`,
`adminHistoryImport`, `adminPermissions`, `adminVoicePreferences`, or
`adminVoiceCredentials`. The final option governs `voice.credential` and
`voice.configure`; sanitized `voice.configuration` remains a passive metadata
read. Workspace authorization and actual initialized capability metadata still
apply. Consumers must not infer an enablement grant from the error.

The affected implementation class is native application configuration admission:
standard ACP config-option, optional selected worker control, and optional
Administration. The current local ecosystem catalog reviewed is
`microsoft/amplifier docs/MODULES.md` at `33b75749920472257d1d85d20a5699ecf9d1ece7`.
Core, Foundation and provider/module implementations are unchanged. Tests cover
real installed Core/Foundation selection, synthetic persistence failure after
selection, disabled policy across all six launcher flags, and selected idle
admission without any model/account calls. This does not qualify every provider
account or constitute browser or live deployment acceptance.
