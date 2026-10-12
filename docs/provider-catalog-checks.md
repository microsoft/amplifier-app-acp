# Provider catalog checks and connection evidence

`providers.test` and mounted `configuration.providerTest` invoke the provider's public `list_models` API. A successful return reports `catalogAvailable: true`, `modelCount`, `method: provider.list_models`, `reachable: null`, and `inferenceVerified: false`. It cannot establish live connectivity or successful inference. Invalid return shapes and provider exceptions still fail. No replacement model is invented.

`providers.testMessage` remains the separate, explicit inference operation. It may send a billable request; reading or checking catalogs never invokes it. The Web client uses one result component in everyday and advanced settings, including when an older host returns `reachable: true` for a catalog.

## Class review

Reviewed the current official/community provider inventory and each available `list_models` implementation. The source evidence packet records complete method bodies and revisions. No provider-specific implementation was changed.

| Provider repository | Inspected revision |
| --- | --- |
| brycecutt-msft/amplifier-module-provider-bedrock | `61ef9ca84e9877c425258aaae7ded0c20ddf3281` |
| colombod/amplifier-module-provider-perplexity | `768fa6c1ca0371af660e1dbb0c6806943c579d79` |
| microsoft/amplifier-module-provider-anthropic | `5cdfd9f581103aee13d21ce62e50cfc18ec37c75` |
| microsoft/amplifier-module-provider-azure-openai | `3a67ed10e70d185bd1061ec7b0d4aa9bd361b136` |
| microsoft/amplifier-module-provider-chat-completions | `bd0de352072efca17ee332912d90a6b0633742bf` |
| microsoft/amplifier-module-provider-gemini | `b736b66529b0a9969ddabf69c3909516de22f9ca` |
| microsoft/amplifier-module-provider-github-copilot | `ed942e2c8a0c6d4ff6d878bc4ba480592f6c676a` |
| microsoft/amplifier-module-provider-litellm | `1908d0eb5f4560ca187c360c8618f75cbd55abb1` |
| microsoft/amplifier-module-provider-mock | `a90821f6b3767f8e52df5e754a96c20352684b43` |
| microsoft/amplifier-module-provider-ollama | `f86ae6c7bdf55745294cc1719b5df5dda4f84c2b` |
| microsoft/amplifier-module-provider-openai | `cab74f3fac672747da688a0ceb4ce231a9d1a6a3` |
| microsoft/amplifier-module-provider-openai-chatgpt | `dc96fbcadfa4b8417fea2d7486477347486cb983` |
| microsoft/amplifier-module-provider-vllm | `985dcf464a2ef917a3e56bc5ac0d82d39a19668d` |
| robotdad/amplifier-module-provider-openai-realtime | `6a27a86183d62d5122f09065cc1ff9144becdeab` |

Bedrock, Perplexity, LiteLLM, and Mock supply static/configured choices; Azure exposes deployment-specific empty catalog behavior. Chat Completions can fall back to configured choices after discovery errors; Copilot can return persistent cached choices; Ollama can return an empty catalog on a discovery error. OpenAI, Anthropic, Gemini, and vLLM expose live query paths. Realtime does not implement this method. Upstream ChatGPT still contains fallback behavior; the qualified candidate uses the separately tested strict discovery change at `07928e8`. These are observed implementations, not assumptions built into this change.

Both Native call sites now share `provider_status.catalog_check`. Native tests exercise empty/static catalogs, error propagation, resource closure, and mounted controls without sign-in or inference. The actual installed wheel and real Core control fixture are qualified separately. Web interaction tests exercise both settings views, older-host results, account-scoped result selection, loading/failure replacement, and absence of hidden test-message/sign-in dispatch.

This does not qualify live accounts for every provider or guarantee that a selected model can respond. It makes that missing evidence explicit.
