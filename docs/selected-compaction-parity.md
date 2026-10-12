# Selected-model compaction

Native's root `SelectedProvider` now follows the upstream Unified 0.20.61
selection contract for `default_model`, `compact_context` and
`validate_compacted_context`. A summary retains its own output reservation and
explicit reasoning effort in both preflight and dispatch. Ordinary answer
limits and worker routing remain unchanged. Failure/cancellation does not retry.

Class review for this change used the current `microsoft/amplifier`
`docs/MODULES.md`, configured P31 source closure, current Unified main
`2a6479388c8b012ce942ee4790c1989944e3bacf`, Native's surface, execution-event and
native-response adapters, and local provider implementations. OpenAI
`699e9fc1` accepts a model keyword for compacted-context validation. The reviewed
Anthropic and Azure implementations use a one-argument validator; that calling
convention is retained. Configured ChatGPT, Gemini and Copilot sources do not
advertise these methods; the wrapper still raises AttributeError rather than
inventing compaction support. Community and locally configured providers that
do not offer native compaction continue through their normal completion path.
The current catalog's additional Chat Completions (`bd0de352`), vLLM
(`985dcf46`), Ollama (`f86ae6c7`), LiteLLM (`1908d0eb`) and mock (`a90821f6`)
provider packages were also inspected; none defines either compaction method.

The fixtures verify both validator forms, independent summary/output budgets,
preflight-to-dispatch request identity, unchanged defaults, absent capability,
and error/cancellation behavior. They do not establish physical voice, remote
account, or every uninstalled community provider's acceptance.
