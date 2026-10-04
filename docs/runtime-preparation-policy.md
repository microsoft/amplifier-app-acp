# Immutable runtime preparation — v1

A trusted launcher can set `runtimeImmutable: true` in the native ACP JSON
configuration ([versioned field and marker schema](../schemas/launcher-runtime-policy.v1.json)).
This is not an ACP request field. It preserves the configured
bundle, sources and full Core/Foundation runtime; it prohibits Foundation
installation into the running Python environment. A missing module/dependency
must be prepared and qualified in a separate environment, never silently removed
or replaced with another provider.

The option is strictly boolean. The process and every native worker/probe child
inherit `AMPLIFIER_RUNTIME_IMMUTABLE=1`; a false/absent launcher option cannot
relax an inherited restriction. Malformed values refuse startup. Immutable mode
requires Foundation's public `dependency_installation_policy` and
`DependencyInstallationDenied` exports. A runtime without these exports refuses
before advertising protection or starting native work. Default standalone
mutable preparation remains unchanged.

Initialize's native metadata, native worker readiness, and resident `runtime.inspect` include:

```json
{"runtimePreparation":{"version":1,"immutable":true,"dependencyInstallation":false,"policy":"foundation-dependency-installation"}}
```

This describes the actual native process binding, not a sandbox or an attestation
of loaded code bytes. Version 1 is the native binding contract, not a guessed
Foundation package version. Readiness requires exact keys and field types;
Python's boolean/numeric equality does not establish compatibility. A worker lacking the exact requested readiness
marker is closed and cannot be used. The trusted launcher must still bind the
qualified executable/package; a JSON option cannot sandbox an arbitrary
replacement worker or attest its pre-handshake behavior. The absence of this marker does not prove that a
third-party launcher has no other restrictions. The public Foundation policy is
monotonic and captures restrictions for lazy activation/batches. The native
binding holds it over worker cleanup, administration and its post-response tasks,
metadata/default/auth/validation/destination probes, and read-only qualification.
Async tasks and `asyncio.to_thread` inherit it. A raw thread or subprocess must
explicitly bind its own process policy; native-owned subprocesses inherit and
rebind it. Arbitrary external module subprocesses/tools are not sandboxed here.

All seven native Foundation `Bundle.prepare` sites route through one helper:
root session preparation, explicit dependency staging, provider metadata,
provider authentication, module validation, draft provider defaults and
portability readiness. Source/cache resolution remains governed by Foundation;
`install_deps=False` is paired with the public lifetime policy so a secondary
activator cannot bypass the host's dependency-installation decision. Provider
request/login/model-discovery authorization is unchanged.

Only the existing trusted generation/profile builder may remove the inherited
flag from the environment of an explicitly new subprocess. It verifies a distinct
`.venv` beneath that owner's runtime root, never the serving `sys.prefix`.
Preparation runs there, freezes the candidate, then fresh `--read-only` probes
rebind protection. The helper does not relax the current Python context or grant
an ACP peer mutable execution. Failed staging does not select or deploy a runtime.

Qualification uses the exact Foundation policy wheel identified in the handoff,
actual Core/Foundation offline worker and public probes, a forbidden-installer
sentinel, unchanged installed-file inventory, and captured lazy activators. It
also checks false/invalid launcher options, old Foundation refusal and the
separate-prefix staging boundary. No account calls or live installation edits
are needed. This does not qualify every third-party module's direct filesystem
or arbitrary subprocess behavior.
