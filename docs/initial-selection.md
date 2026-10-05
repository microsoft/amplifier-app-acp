# Initial provider selection

A peer advertising `agentCapabilities._meta["amplifier.dev/native"].initialSelection`
with `{version:1, typedSelection:true, passive:true}` supports a typed selection on
standard ACP `session/new`. This additive capability is enabled with the existing
launcher `managedSessionRoots` grant, which also enables passive draft defaults
and model discovery. It does not change the ACP protocol requirements.

A client negotiating `amplifier.dev/native` may supply:

```json
{"cwd":"/owned/workspace","mcpServers":[],"_meta":{"amplifier.dev/native":{"selection":{"instance":"configured-instance","model":"advertised-model","effort":"low"}}}}
```

The instance is an exact configured provider instance, not a provider brand.
Model and optional effort must be advertised by that provider's public metadata.
Model-specific reasoning choices (including an empty list) override the provider
schema. There is no universal effort list, inferred entitlement, automatic login,
or inference probe. The existing provider-only preparation path resolves the same
bundle and workspace configuration as the worker; it can prepare source caches
and read configured credentials. `passive` describes discovery, not session creation.

Validation finishes before a session identity, session-owned state, or execution
worker is allocated. Shape/capability/choice refusals carry `executed:false`,
`runtimeStarted:false`, and `reason:"initial-selection-refused"`; discovery errors
retain their existing bounded reason. No public history or model call is made.

The validated selection is written to the existing `control-state.json` before
`Session.start`. The selected root is retained for that start. The successful
response includes `_meta["amplifier.dev/native"].initialSelection` with
`{version:1, selection:{instance,model,effort?}}` only after the persisted selection
and actual mounted provider selection match. Omitted effort stays omitted in this
proof; effective provider defaults are presented separately in standard
`configOptions`. The first prompt therefore observes the accepted selection.

An absent or empty selection preserves legacy behavior. A nonempty selection
without support or negotiation, or on load/resume, refuses before starting.
Load/resume continues to use the saved selection. Failure after persistence or
startup is not a no-effect refusal: retain original state and uncertainty; do not
retry creation or apply a delayed replacement selection automatically. This
extension adds no command receipt, replay mechanism, or new history authority.

The metadata shapes are defined in [initial-selection.v1.json](../schemas/initial-selection.v1.json).
