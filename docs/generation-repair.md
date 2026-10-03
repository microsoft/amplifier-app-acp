# Recover a native environment without modifying a running one

Launcher-authorized native administration exposes two additional operations.
They require `adminGenerations:true` and an authorized workspace exactly like
the existing generation commands. No caller may select an interpreter, native
home, executable, package URL or replacement lock.

`generations.repair.preview {generation?}` reads the selected qualified receipts
and returns `generation`, `sourceHash`, the recorded `profiles`,
`distributionCount`, `expectedCurrent` and explicit preservation boundaries.
It reads at most nine receipt files, 8 MiB each and 32 MiB combined. The hash binds
their exact contents; it is not a claim that an existing environment is intact.
There is no worker start, provider execution, remote source check or historical
session scan. Legacy generations without complete versioned receipts are refused.

`generations.repair {commandId,generation,expectedSourceHash,expectedCurrent}`
reserves an immutable candidate identity in the durable command receipt before
creating files. It copies the generation's configuration into a private
candidate, installs its exact frozen lock into a new interpreter environment,
checks the installed distribution/source graph, and runs the recorded profiles
through the normal read-only Core/Foundation mount qualification. No dependency
refresh or model prompt is issued. A provider may still require its configured
credential/endpoint to construct a valid instance; absence is a qualification
failure, never permission to substitute a different provider or synthetic key.

Success returns `qualified:true` and the new `generation`, with
`activation:'separate-generation-promotion'` and `runningWorkersChanged:false`.
The existing `generations.promote` CAS is the separate activation operation for
future workers. Both the original pointer and any already running workers remain
unchanged by repair. Failed candidates and their source descriptions remain on
disk but have no completed qualification receipt. An interrupted command is
`unknown`; exact command inspection/repetition returns its candidate identity
without rerunning any installation or probe.

This repairs the interpreter environment, not changed or missing source inputs.
Recorded editable/cache inputs retain their original locations and content
checks. A missing source, changed tracked source content, or a profile requiring
dependency preparation fails explicitly. Original generations must therefore
remain retained; there is no source reclamation in this owner. Local package
inputs retain the same provenance limits as the existing qualification system.
Future updates continue to use the current branch manifest, never this recovery
lock as standing update policy.

Acceptance includes actual `uv` reconstruction of a deliberately damaged fixture
environment, separate profile acceptance/failure, source-receipt drift, symlink
refusal, interrupted work, exact no-replay receipts and preservation of originals.
A separate owned current-source graph reconstructed all 98 installed
distributions and passed its recorded Work profile through actual Core/Foundation
without a model prompt or promotion. The original pointer, eleven source receipt
files and eight canonical session files remained unchanged. The first attempt
correctly refused absent process-only provider credentials; the failed candidate
was retained, and a new explicit qualification used the existing configuration.

This is not distribution self-update, a private backup, selective reset, or
automatic retention. Those remaining product-owner responsibilities are listed
in [maintenance parity](maintenance-parity.md).
