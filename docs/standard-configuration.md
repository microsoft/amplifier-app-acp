# Standard provider, model and reasoning selection

ACP `session/set_config_option` is the commit path. The agent returns the entire
current `configOptions` list and publishes `config_option_update` after an
accepted change. Clients replace their list; they must not synthesize values or
parse display labels. The standard ACP category is presentation metadata, not
authority. This adds no backend draft and never blocks prompts while a client
browses a provider.

`model` is a standard grouped select (category `model`). Each provider group ID
is the **configured provider instance ID**, not a provider brand or module name.
Two instances of the same module remain distinct. Each opaque model value binds
one instance/model pair and remains stable across ordering, catalog refresh and
reconnection. Choosing it applies the pair in one existing `provider.select`
operation. A different pair clears the previous explicit effort; selecting the
already pinned pair is a no-op. The separate Bundle default choice clears the
pin through `provider.reset`, preserving bundle routing behavior.

Model/group metadata uses `_meta["amplifier.dev/native"]`:

```json
{
  "group": "work-instance",
  "name": "Provider display name",
  "options": [{
    "value": "model:opaque-owner-value",
    "name": "Model display name",
    "_meta": {"amplifier.dev/native": {
      "version": 1, "instance": "work-instance", "model": "provider-model-id",
      "source": "catalog", "modelInfo": {
        "id": "provider-model-id", "display_name": "Model display name",
        "context_window": 8192, "capabilities": ["thinking"],
        "defaults": {"supported_reasoning_efforts": ["brief", "deep"]}
      }
    }}
  }],
  "_meta": {"amplifier.dev/native": {
    "version": 1, "instance": "work-instance",
    "catalogState": "available", "catalogFresh": true
  }}
}
```

`source` is `catalog`, `configured` (default before discovery) or `effective`
(the existing pin absent from the catalog). None promises account entitlement
or successful execution. Current pins remain represented when discovery fails
or excludes the model; the owner does not silently substitute another model.
Catalog state is `not-loaded`, `available` or `unavailable`; a failed refresh
can retain earlier catalog rows with `catalogFresh:false`.

A client with negotiated native controls can privately browse provider instances
from `configuration.providers`, then request
`configuration.providerModels {provider: <instance>}`. That read uses the
existing selected-provider, single-flight cache/120-second discovery timeout.
Success and failure republish the complete standard list from owner state.
There is no enumeration of all providers at initialization or option refresh,
no completion/test/login call, and no inferred login. Mode selectors are retained.
Standard option refresh also does not call provider authentication-status readers.

The runtime's existing provider projection adds
`modelCatalog:{state,models,fresh}`. This is a passive cache projection. The model
option advertises `_meta["amplifier.dev/native"].groupedProviderModels:true`
and the actual effective instance/model/effort. Generic ACP clients can use the
standard grouped choices already known to the agent. ACP has no group-expansion
callback; richer lazy discovery requires the negotiated native read. An empty
group does not authorize arbitrary model names.

`reasoning_effort` is a standard select with category `thought_level`. Its opaque
values bind the effective instance/model/effort, so a prior model's effort token
cannot modify a different model. It appears only for owner-advertised choices:
model `defaults.supported_reasoning_efforts` first, otherwise the provider's
public `reasoning_effort` choice ConfigField with satisfied model conditions.
No model-name heuristics or universal effort list are supplied. Other unknown
field dependencies hide the selector. The Provider default option removes the
explicit effort while retaining the pair. A pre-existing unadvertised effective
effort stays visible as current-only; reselecting it performs no new effect.
That current-only value and Provider default remain available even when discovery
fails or the model advertises no reasoning choices. Clearing the override keeps
the selected provider/model; it does not invent any new supported effort.

Provider metadata is bounded to 64 instances, 512 models per instance and 4,096
model values per projection. Projected model information allowlists identities,
display names, capabilities, numeric token limits and reasoning defaults; private
provider config or arbitrary model defaults are not copied into option metadata.
An invalid or oversized catalog is unavailable rather than silently truncated.

## Authority and acceptance

Selection is idle-only and validated against the current exact option mapping
under the existing session configuration lock. Stale removed model/effort values
refuse before selection. Catalog browsing writes no effective selection or
canonical history and creates no shared client editing state. Existing
`provider.select` persistence, native writer ownership and error semantics are
unchanged. Standard ACP has no additional durable command-receipt protocol here:
transport loss is not permission to replay effects or infer an original outcome
from current state. Reconnect projects the saved selection without replaying it.

The Core public ModelInfo/ConfigField contracts and retained provider class
inventory were inspected, including OpenAI/Anthropic declared choice fields,
Copilot model-specific `supported_reasoning_efforts`, and providers that expose
no configurable effort. Provider declarations remain responsible for their own
model compatibility. This does not add support for a provider that does not
advertise the necessary metadata, nor claim live model/account acceptance.

Focused tests exercise actual Core/Foundation subprocesses with inert providers:
private browse, cache reuse, exact atomic standard selection, selected reasoning
on an actual request, changed-model effort clearing, stale-token refusal,
failed-discovery pin preservation, and cold reconnection without replay.

New conversations may use the negotiated [initial selection extension](initial-selection.md) to validate and persist a complete provider/model/effort choice before starting.
