# Native transfer lifetime

Configured `_amplifier/transfer` now negotiates
`transfer.lifecycle={version:1,heldIntake:true,nativeAdminWriter:true}`. A private
`_amplifier/transfer/lifecycle` request takes `{operation,args}`. It never starts a
Core session. Acquire is process-local; the separately registered native-home
admin participant supplies the cross-process exclusive writer proof.

- `inspect {}` returns `{version,processId,pid,intakeClosed,fence,requests,workerStarted:false}`.
- `acquire {fenceId,commandId,purpose,instanceId,dataScope}` closes this process's
  transfer intake atomically only when no transfer request remains. It returns
  `acquired:true` plus that inspection, or a known `executed:false` refusal.
- `release` takes those context fields plus `outcome` and the exact host-verified
  `proof`. Unknown retains intake. Unchanged binds the same instance; ready binds
  its authenticated successor. Exact in-process release retries use retained
  receipts. A replacement process does not claim old effects succeeded: the
  product's durable fence/receipt and home admin proof remain required.

Every ordinary transfer operation, including metadata/index initialization and
readiness checks, holds shared native-home maintenance and administration gates.
Cancelling its caller joins the complete effect before releasing either gate;
threads cannot keep writing behind a false idle acknowledgement. Shutdown also
joins owned transfer work. The process-local fence refuses even transfer inspection,
which could otherwise initialize a repairable index. Product receipt/review reads
remain passive in their own owner. There is no model-input replay.

Composition order is product durable intake, each configured transfer-process
fence, then the single exclusive admin participant for each native home. Release
uses authenticated coordinator proof. Unqualified generic transfer peers refuse
maintenance coverage. Do not register a second exclusive admin gate for the same
home. Ordinary native execution workers are retired by the host independently.

Tests cover complete actual Core/Foundation transfer, an offline readiness probe,
thread cancellation retaining both OS gates, process-local/home-gate ordering,
and no transfer-index side effects while held. Product tests separately cover the
paired host/owner/native stack with delayed evidence callbacks. No live account
or paid model request is implied by those fixtures.

The built-in readiness subprocess acquires independent native-home writer leases before loading a provider. They survive parent ACP process death and close with the child. An exclusive gate that wins before child admission makes the child refuse before provider execution. Custom `transferProbeCommand` launchers do not advertise `nativeAdminWriter` retirement coverage; their lifecycle acquisition explicitly refuses rather than inferring arbitrary child lifetime.
