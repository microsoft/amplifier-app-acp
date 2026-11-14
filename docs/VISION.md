# Amplifier native agent vision

`amplifier-app-acp` makes the full modular Amplifier application runtime usable
through ACP by independently developed clients and hosts. Its CLI is
`amplifier-acp`; its Python package is `amplifier_acp`. It remains useful without
Amplifier Unified, a browser, or another application's private implementation.

This is the repository's interpretation of the approved direction, not a new
protocol or a statement that every deployment has reached it. The
[version 1 boundary contract](../contracts/native-agent.v1.md) maps that direction
to existing schemas, consumer obligations, falsifiers and acceptance tests.

## The intended result

- Foundation prepares bundles and Core `AmplifierSession` executes them.
  Providers, tools, hooks, contexts and loops retain their modular composition.
  A protocol adapter must not become an Amplifier-lite replacement engine.
- An ordinary compatible ACP client can prompt, observe tools, answer permission
  requests and cancel supported work. Standard capabilities come first;
  Amplifier configuration and application integrations remain negotiated extras.
- Native identity, canonical history and execution ownership survive changes of
  viewer, worker retirement and process restart. Recovery inspects uncertain
  effects; it does not reconstruct state by replaying old work.
- Historical existence does not imply a resident runtime. Selected passive
  history, metadata and receipt reads stay bounded. Large inventories are
  explicit maintenance operations, not the cost of opening a conversation.
- Accepted work retains its owner until it settles. A permission response
  resolves that work's exact outstanding decision; it is not another execution
  admission that can queue behind the operation waiting for it.

## What this repository owns

It owns ACP-to-native translation, the native application runtime, native write
leases and history access, and its explicitly configured administration and
maintenance surfaces. Core, Foundation and module implementations retain their
own contracts. Native schemas describe the optional public boundary, not a
license for clients to import private runtime objects.

A host owns authenticated multi-client access, its catalog and projections,
cross-owner action admission and application composition. A client owns unsent
drafts, local editor state and presentation caches. This adapter does not need
those private client states to execute accepted work. AHP semantics remain with
the host and upstream AHP, not a competing protocol inside this agent.

## Inherited direction and remaining work

The approved family [AP1–6 agent protocol](https://github.com/microsoft/amplifier-unified/blob/main/contracts/agent-protocol.v1.md),
[WS1–6 working set](https://github.com/microsoft/amplifier-unified/blob/main/contracts/working-set.v1.md)
and [component boundaries](https://github.com/microsoft/amplifier-unified/blob/main/contracts/component-boundaries.v1.md)
govern the corresponding promises when this agent participates in Unified.
Their [family vision](https://github.com/microsoft/amplifier-unified/blob/main/docs/architecture/VISION.md)
owns the broader product destination. This repository links that authority
without copying it or taking ownership of other components' obligations.

The implemented surface is the initialized capability advertisement plus its
versioned schemas. For example, question turns currently advertise
`active:false`; active question delivery is not established by the separate
anchored-steering capability. Module retention does not establish compatibility
with every community module, external client or live account. The
[acceptance inventory](coverage.md) and [maintenance parity ledger](maintenance-parity.md)
record limits; they must not be read as deployed acceptance receipts.

Changes should identify the affected contract and a test that could falsify the
promise. Source review, subprocess fixtures, installed artifacts, composed peers
and live deployment are distinct evidence. Qualification records exact versions
and rollback hashes; supported source tracking still advances through fresh
qualification rather than turning those records into standing pins.
