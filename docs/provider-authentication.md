# Provider authentication observations

`configuration.providers` preserves every mounted provider and the exact current
selection. A row whose `ProviderInfo.capabilities` declares the existing
`auth:oauth_device_code` or `auth:oauth_pkce` contract and supplies `auth_status()` can additionally
report `authentication`, described by
[`provider-authentication.schema.json`](../schemas/provider-authentication.schema.json).
`supports.authStatus` reports whether its synchronous status method is supported.
An undeclared method is never invoked. Status exceptions and unexpected values
produce bounded `unknown` observations without their raw contents.

This is a passive, selected-runtime read. It does not call login, refresh,
list_models or complete, persist state, choose another provider, or change module
validation. `expired` is an observation: the provider may refresh on deliberate
use. Even `authenticated` does not prove a model request will succeed. Existing
request-time provider behavior and typed authentication errors remain authoritative.

The ChatGPT provider's conditional non-mount could formerly turn missing or
expired credentials into Core `validation_failed`, blocking an unrelated selected
provider. Correcting that mount belongs to the provider package; this native
projection neither suppresses module failures nor provides a fallback. Other
providers' conditional non-mount contracts remain a distinct ecosystem issue.
