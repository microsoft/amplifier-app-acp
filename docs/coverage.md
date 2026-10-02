# Extraction and acceptance boundaries

The worker, bundle preparation, modules, child lifecycle, naming, approvals,
native storage, process ownership, control state, budgets/goals, context
continuity and provider selection code were extracted at symbol boundaries.
No module class was replaced and no application mega-state is included.

Adapter-specific changes:

- ACP supervisor owns native subprocess lifetime and maps wire messages.
- Native imports use the new private package namespace. Small computation kernel
  implementation/resources are retained inside it pending independent extraction.
- App guidance/provider transformations and host admission require negotiated
  host support. Missing optional host features fail explicitly.
- Root steering uses the existing loop-live Input channel; no kernel change.
- Client MCP servers use the public ecosystem MCPManager and tool wrappers.
- Durable saves emit location-only hints. Passive history projects native records
  into ACP updates without engine startup, bounded by turns and bytes.
- Native worker caches are separate from history; settle-aware retirement keeps
  active children/approvals/operations alive.

| Area | Implementation | Evidence / remaining acceptance |
| --- | --- | --- |
| Core + Foundation + loop-live | Extracted full native graph | Actual subprocess with offline provider |
| Modular composition and child routing | Existing preparation/configurator/children | Ported native regression tests; live community matrix still required |
| Approvals and tools | ACP permission callback + existing native dispatch | Actual tool/denial/MCP subprocess checks |
| Completion/cancel | Completion events, cancellation and native teardown | Actual slow-turn cancel and safe continuation |
| Writer ownership | Foundation lease + native conflict guard | Two independent adapter processes race on one live session |
| Resume/full load | Same native IDs and storage | Actual close/resume/load; no prompt replay |
| Passive history | Complete-turn backward reader | Paging, stale cursor, bytes bound, backup, corrupt record tests |
| Standard modes/model | Existing native modes and configured provider choices | Wire schema + runtime selection regressions |
| Advanced native controls | Allowlisted wrapper over original RuntimeControls | Extracted source and targeted regressions; not every control received new end-to-end testing |
| Host application capabilities | Negotiated callback | Generic clients do not require it; host-level surfaces need host integration acceptance |
| Realtime voice | Not an ACP audio stream | Existing media transport needs its own host integration |
| External editor clients | Standard ACP + upstream schema | No stock Zed/JetBrains/Copilot client acceptance yet |
| Live models/accounts | Original provider modules retained | Not exercised; no production/account claims |

The copied native tests were restricted to retained runtime symbols. Broad UI
catalog/recovery/deployment tests stay with their original owner; the adapter
suite does not add those implementations just to satisfy old imports. The provider-native transport tests use mock network peers. The community
recipe probe additionally passed against the configured source checkout. Every test uses
an isolated native/config/history root.

`workerCommand` is a trusted launcher/testing override; it is not accepted from
ACP requests. It permits deterministic transport fixtures without adding a mock
production backend. The normal launch always uses the extracted full worker.

Native administration and Unified capability integration add the original
provider/routing, module/source and bundle manager behavior without AppService.
Their 100 ported regression tests verify private credentials, scope precedence,
registration/discovery/export validation, model catalogs and cancellation.
Native subprocess coverage includes queued configuration cancellation, actual
Core remount preserving history, and provider login retaining its worker until
completion/cancel. Live OAuth/accounts remain untested. Behavioral validation
requires pytest/pytest-asyncio installed in the configured runtime and fails
explicitly if absent; it does not install undeclared test runners mid-request.

Application access is now explicitly negotiated by operation and feature; see
`host-bridge.md`. Generic ACP sessions never receive Unified UI claims or default
Unified shell/imagegen behaviors. Explicit native settings remain authoritative.
Configuration remounts keep a private durable rollback journal: failed mounts
restore prior settings/runtime; restart recovers an interrupted transaction
without replaying input. Recovery failure remains explicit rather than success.
