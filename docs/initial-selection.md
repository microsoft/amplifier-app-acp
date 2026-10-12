# Initial bundle and provider selection

A peer advertising `agentCapabilities._meta["amplifier.dev/native"].initialSelection`
with `{version:1, typedSelection:true, passive:true, resolveMethod:"_amplifier/initial-selection"}` supports a typed selection on
standard ACP `session/new`. This additive capability is enabled with the existing
launcher `managedSessionRoots` or `adminWorkspaceRoots` configuration. Managed
allocation remains separately gated by `managedSessionRoots`. It does not change the ACP protocol requirements.

The advertised resolver accepts exact `{cwd, selection?}` parameters, where the
optional selection is a partial typed `{instance?,model?,effort?}` draft. It uses
the actual cwd and current selected bundle, rather than the global-only managed
creation API. Its response is `{version:1,schema,values}`: a flat JSON object
schema with string enums for `instance`, `model`, and supported `effort`,
`additionalProperties:false`, and required `instance`/`model`. Bounds are 64
provider instances (200 characters), 1,000 models (500 characters), and 32 effort
choices (100 characters). No provider-specific reasoning logic belongs in the
Host or client.

Each enum descriptor includes a human-readable `title`: `AI connection`,
`Model`, or `Reasoning`. Hosts can preserve these descriptors when projecting
the standard AHP configuration schema; titles do not change selection values.

Explicit invalid choices refuse. Omitted provider/model use configured metadata
defaults when advertised; if no model default is advertised, draft values omit
model and require the client to choose one. Omitted effort stays omitted, even
when the provider has a default. The draft is never stored. Full selection
validation delegates to this same resolver and requires both instance and model.

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

Peers advertising `bundleSelection:true` additionally accept a bounded `bundle`
string (4,096 characters) in the same typed selection. The exact registered ID,
local file, or source reference is resolved through Native's ordinary bundle
loader. The Host and Web client must check this capability before forwarding a
bundle choice; older engines must refuse it rather than start the saved default.

Bundle-only selection returns only a `bundle` enum and value, with no implicit
provider/model pin or model enumeration. When combined with a provider pin, that
pin is validated against the chosen bundle. The worker starts with the validated
root; success additionally requires its reported `root_bundle` to match the
resolved root identity. The existing canonical session metadata retains the
bundle for later loads; the provider control file stores only provider fields.
No post-creation bundle mutation or prompt replay is used.

The metadata shapes are defined in [initial-selection.v1.json](../schemas/initial-selection.v1.json).
