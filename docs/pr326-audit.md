# Published PR326 extraction audit

Audited upstream `647e236703a74fc212e83a37ec99c7acd47341d2` (2026-10-02)
against original extraction `828d2bff97a77fbdbd0f06429369f7ff1f4abf42`.
No running environment or shared deployment was changed.

Carried applicable native runtime changes:

- `updates.selected_release` captures per-worker generation, including explicit
  baseline; Foundation registry and installer policy follow that selection.
- `host.config.read_config` accepts explicit registry_home without losing the
  adapter's existing launcher-authorized registry override.
- `host.session.apply_runtime_plan`, global_only/runtime_plan preparation inputs,
  and immutable runtime `install_deps=False` behavior preserve full native plans
  and prohibit installers mutating a declared immutable environment.
- `bundles.offered_profiles` is the single current picker/qualification profile
  authority.
- `generation_leases` retains live worker generation references; native launcher
  captures its generation before start and releases its lease only after exit.
- Inherited VIRTUAL_ENV is removed before child launch so another shell's active
  environment cannot redirect Foundation dependency installers.

Intentional owner boundary / remaining implementation:

The original RuntimeManager was not extracted (the adapter uses NativeWorker).
Its new promotion sweep, `runtime_profiles.ensure` custom-configuration
qualification, `update_plan`, shared source policy discovery, update retention,
and off-serving-loop package hashing belong to a separately configured native
installation/maintenance owner. They are **not implemented as ACP controls**.
The generation publisher must prepare/freeze/verify the environment and supply a
compatible explicit workerCommand. The adapter refuses a qualified generation
without that explicit launcher rather than silently importing another graph.
Declared immutable generations receive AMPLIFIER_RUNTIME_IMMUTABLE=1; missing
modules must be qualified by the owner, not installed into the shared runtime.
This carries consumption/lease semantics without pretending the broader
maintenance UI and update-generation owner have been ported.

The current development baseline still prepares configured modules in its owned
adapter environment, as before extraction. It is not evidence of qualified
shared-generation adoption. Fixture/native lifecycle checks and the separate
host schedule test prove application/protocol behavior only; source-generation
promotion, custom-profile preparation and installed deployment acceptance remain
separate gates until that owner is implemented.

Follow-up authority correction (identified upstream after PR326): historical
registry entries for amplifier-dev/exp-delegation/anchors-amp-dev must not become
offered executable profiles unless current settings register them. The adapter
now derives the offered set from current registrations and explicit bundle.added
only. A regression test covers stale cache-only aliases and explicit current
registration. Upstream's published follow-up commit was still pending when this
local correction was made; compare that final patch before claiming identical
published-release provenance.
