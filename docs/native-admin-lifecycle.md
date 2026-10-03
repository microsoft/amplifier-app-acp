# Held native administration

The private `_amplifier/admin/lifecycle` extension prevents an application host from treating an idle request channel as proof that its administration process is safe to stop. Initialization advertises `agentCapabilities._meta['amplifier.dev/native'].admin.lifecycle = {version:1,heldIntake:true,crossProcessWriterFence:true}` when admin workspace roots are launcher-enabled. It uses the same negotiated native extension and workspace authority as administration. No Core session, model or session catalog is loaded.

The owner inventories actual behavior: generation check/prepare/repair/ensure/promotion await their complete work, including queued generation-file-lock acquisition and parallel source tasks; settings/registry/bundle/probe operations finish with their request; provider sign-in is a writer that outlives its initial response. Every queued or executing request is counted before its first await. Sign-in tasks retain both the ordinary native-home shared writer gate and the new admin writer gate until their completion. Cancelling a request joins its effect before releasing either gate, including underlying thread and subprocess work. Transport/process loss remains uncertain and never permits replay.

## Private requests

All requests are `{cwd:<configured-authorized-workspace>,operation,args}`. The caller is the trusted product administration bridge; these are not generic advertised browser actions.

- `inspect {}` returns `{version,adminInstanceId,pid,intakeClosed,lease,work:{requests,operations,providerSignIns,maintenanceLease},workerStarted:false}`. Operations contain counts only, no arguments or credentials.
- `acquire {fenceId,commandId,purpose:'recovery'|'distribution-update',instanceId,dataScope}` atomically checks queued requests, post-response sign-in tasks and active native recovery leases, then holds an exclusive native-home **administration** OS gate. A request or post-response writer in another current ACP admin process holds the matching shared gate and causes a known refusal. Native execution workers use a different gate and are retired separately by the generic host. No directory/catalog scan is performed.
- A successful acquisition returns `{acquired:true,...inspect}` with `lease:{fenceId,commandId,purpose,instanceId,dataScope,leaseId,adminInstanceId}`. A proven busy refusal returns `{acquired:false,executed:false,intakeClosed:false,reason,work?}`. A different already-held fence is not proof that intake is open.
- `release {fenceId,commandId,leaseId,outcome,proof?}` requires the exact lease. `unknown` retains the lease. `unchanged`/`ready` require the product's previously authenticated host proof; an aborted pre-effect host acquisition may use `{kind:'admission-refused'}`. Active requests or a native maintenance lease prevent release. A new admin process identity is never evidence that an old host operation succeeded.

The gate is an uninherited POSIX flock on `.amplifier-acp-admin-writers.lock` under the configured native home, separate from the recovery gate. Process death releases its descriptor naturally. No PID-based stale-file deletion occurs. This is held proof for participating ACP administration writers; legacy tools that write settings without this protocol are not silently covered. The product supervisor still owns its durable intake fence and launch/data-scope proof. Removing or restarting an admin process does not clear that host fence.

## Exact recovery exception

Holding admin intake must not block the recovery operation which intentionally uses that lease. Only the same private process may send `_amplifier/admin` requests with `_meta['amplifier.dev/admin'] = {fenceId,commandId,leaseId}` matching a held **recovery** fence. This permits only `maintenance.*` operations. It does not permit generation builds, provider changes, setup, registry edits or ordinary session execution. It also does not bypass the native recovery owner's separate reviewed manifest, exclusive writer leases and receipts.

A short allowlist of receipt/archive/lease and generation inspection reads remains usable on the holding connection without new effect authority. Other connections remain fenced by the OS gate. Request counts still protect release while one of these reads is active.

The independently packaged Unified native bridge exposes `AdminConnection.quiescenceParticipant` and `withMaintenanceFence(input, callback)`. Compose the latter inside `host.withQuiescenceMaintenance(input, callback)`. Its asynchronous scope expires when the callback completes: detached promises cannot inherit maintenance permission. A cold or replacement connection starts only a passive ACP process to acquire real cross-process writer proof; it never starts a runtime. A single shared private connection can cover both settings and maintenance owners. Multiple unrelated admin participants for the same native home must be composed as one authority, since two independent exclusive leases cannot both be held.

## Evidence and limits

Protocol subprocess tests prove cross-process refusal, private recovery snapshot while held, normal mutation refusal, active recovery lease blocking release, process-death lock release and no native session creation. A native owner fixture proves post-response sign-in accounting and cancelled-request lifetime joining. Node tests use the official ACP SDK and actual adapter process for held/replacement behavior and expired detached scopes. No credential/account operation or model turn is performed.

An unresponsive or lost process is an unknown outcome, never a known busy refusal. The product host must retain its durable fence. This extension does not authenticate a browser, approve maintenance, manage application installation, assert a new application is ready, or supply a complete cross-owner backup.

## Idle advisories

Lifecycle negotiation now adds `idleNotification:true`. After an acquisition is
refused for this process's queued request, sign-in, or maintenance lease, settling
that actual work emits one private `_amplifier/admin/idle {version:1}` notification.
It is only a prompt to recheck; it never opens intake, authenticates release, or
proves other processes are idle. The public bridge's `AdminOptions.onMayBeIdle`
also signals ordinary request completion. No dormant polling is introduced.

Each native transfer request now holds the same shared admin writer gate for its
whole lifetime, so the one native-home exclusive admin participant also detects
transfer work in another cooperating ACP process. Its separate transfer intake
fence must be acquired first; it does not take another exclusive admin gate.
