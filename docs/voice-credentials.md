# Existing OpenAI credentials for voice

The launcher-authorized `voice.configuration`, `voice.configure` and server-only
`voice.credential` operations retain their existing schemas and key store. Media
resolves a credential only for an explicit connection or preview; passive reads
return sanitized presence metadata, never a key or account-validation claim.

Explicit `private` selection continues to use only `AMPLIFIER_VOICE_API_KEY`.
For `environment`, a nonempty canonical `OPENAI_API_KEY` wins. Otherwise the owner
uses one distinct strict `${ENV_VAR}` reference from enabled **global**
`provider-openai` connections using the default or official OpenAI endpoint.
Global disabled/tombstoned entries are excluded. Custom endpoints, Azure and
ChatGPT sign-in providers, literal key configuration, unrelated environment
variables and workspace-local connections are not implicit voice credentials.
Several connections referencing the same variable are one binding; multiple
distinct references remain unavailable even if only one currently has a key.

Configuration, environment-source save validation and explicit credential
resolution use the same selector and current owner key-file reload. An absent
selected private key never falls back to another connection. No secret is copied
to `OPENAI_API_KEY`, no new credential store is created, and configuration reads
do not probe accounts, load provider modules or start execution workers.

`environmentAvailable` describes the selected existing OpenAI binding's presence.
Optional `environmentVariable` names its bounded reference for diagnostics; a
client can simply say “Use an existing OpenAI connection”. Consumers should
refresh configuration after saving/changing a provider connection or voice source;
this owner does not push a new cross-owner notification contract.

Focused causals are in `tests/test_voice_connection_credentials.py`. Installed
public administration and Media preview acceptance have separate receipts. A
synthetic provider preview does not qualify real account access or heard audio.
