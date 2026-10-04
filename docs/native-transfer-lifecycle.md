# Native transfer lifetime

Configured `_amplifier/transfer` now negotiates
`transfer.lifecycle={version:1,heldIntake:true,nativeAdminWriter:true}`. A private
`_amplifier/transfer/lifecycle` request takes `{operation,args}`. It never starts a
Core session. Recovery and service-stop acquire retain their process-local
contract; the separately registered native-home admin participant supplies their
cross-process exclusive writer proof.

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

## Distribution admission abort

`transfer.lifecycle.admissionAbort={version:1}` additionally negotiates an exact
distribution-update request with `ownerId`, configured by the trusted product
launcher. The acquire fields are the five context fields above plus `ownerId`.
The existing launcher `transferAuthorityDirectory/owner.sqlite3` must already
contain the original `commands(scope,id,signature,body)` authority. Missing,
invalid, or legacy evidence refuses; this API never initializes or migrates it.

The private native-transfer namespace records each original acquisition or
no-effect refusal and an atomic native-home active-owner pointer in that existing
table. Exact retries observe the original result, including a busy refusal after
the writer finishes or the ACP process restarts. Refused, released, or aborted
attempts cannot reacquire. Each configured transfer peer has its own receipt;
one peer's settlement cannot stand in for the other peers' evidence.

`abortAdmission` takes the exact acquire fields plus `proof`. That proof has
exactly the five context fields, `kind:"distribution-admission-abort"`,
`verified:true`, and a bounded `receiptId`. The configured product host must
authenticate this distinct proof before forwarding it on this private channel.
Generic release proof and the old admission-refused rollback assertion do not
authorize abort. The result contains exactly `ownerId`, `status` (`released` or
`not-acquired`), `receiptId`, and `commandId/fenceId/instanceId/dataScope`.
Clearing that owner's active pointer and saving its abort receipt is one SQLite
transaction. A lost reply or replacement process observes the same receipt.
Changed context, owner, or proof refuses. An original acquired record without its
active pointer, an orphan uncertain intent, or a missing original disposition
remains unknown. A durable original admission-only rollback can be settled only
with the separately authenticated new abort proof and the original acquisition
plus exact `releaseRequest={outcome:"unchanged",proof:{kind:"admission-refused"}}`
records. Generic verified unchanged/ready lifecycle release keeps its ordinary
semantics and cannot become first admission-abort evidence. An old completed
receipt or conclusive original refusal/rollback is published without requesting
a native writer lock or changing a newer active pointer, including a newer
fence for the same stable owner.

Every transfer effect first holds its existing shared maintenance writer lease,
then checks the durable admission pointer, and retains its lease through the
complete shielded effect. Distribution acquire and abort obtain a transient
exclusive maintenance lease around their authority transaction. Actual writers
in another process or a readiness child therefore refuse settlement even if the
replacement ACP process has no local requests. They never request the separate
Admin exclusive lock: the later general Admin participant may still hold that
lock during unwind. This preserves product/transfer/Admin ordering. The pointer
is shared only among peers configured with the same launcher authority and
native home; the composition must qualify every configured peer's implementation.
The outer dispatcher skips its usual shared maintenance lease only for the
validated distribution acquire/abort path, whose backend owns that exclusion.
Malformed selectors and abort assertions refuse before that outer lease can
create state. Ordinary transfer effects and legacy recovery/service-stop keep
their existing dispatcher exclusion.
