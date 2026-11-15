# Computation package delivery

`amplifier_kernels` is authored in the public Unified repository's
`components/kernels/` project. The adapter installs its published
[`kernels-v0.1.0` wheel](https://github.com/microsoft/amplifier-unified/releases/tag/kernels-v0.1.0)
instead of carrying another implementation or building a Unified source checkout.
That release binds source `a1854e12bc9c0ba8ee4186e8968d34a37bb6cb37` and its
historical `amplifier_kernels/` project in `kernels-source.json`. Both dependency
manifests select the wheel with SHA-256
`ff709a59c79b88c9050770894d675ba4a3962a130149fb46578f201435b97b78`.
Its four runtime files and license are identical to the prior selection at
`8f4c05c53e5343a39433bc4a64f0d9e0104ce0e8`. The GitHub artifact is not a PyPI
publication claim.

Both `pyproject.toml` and `native/runtime_deps/pyproject.toml` declare the same
public wheel and digest. A worker's qualified dependency environment
intentionally excludes the launcher environment: installing the dependency only
for the launcher is insufficient. The ordinary dependency preparation installs
it for each selected worker graph. Existing immutable generations stay unchanged;
qualify a fresh graph through normal preparation rather than modifying an old one.
The root lock records the selected artifact. This adds no dependency on the private
ACP repository to public Unified and does not install the Unified application.

The existing computation adapter retains process authorization, trusted executable
selection and durable observation. The package retains Python/Node state, exact
owned process and cell identities, interruption, reset and unknown-outcome behavior.
No cell is replayed by reconnect or package selection. Existing source verification,
worker identity and cancellation remain in their current owners.

For an update, select one reviewed published artifact in both manifests, resolve
the root lock without unrelated dependency changes, and qualify the installed
adapter plus isolated worker import and focused computation behavior. Keep the
canonical package's real Python/Node mechanism tests with that source; do not
copy them into this repository. Packaging should include both runner files and
exclude the former `amplifier_acp.native.kernels` copy. Historical `extraction.json`
remains the original extraction record, not current dependency selection.

Use a separate prefix from Unified itself: Unified still bundles the same
`amplifier_kernels` files, so coinstalling the standalone distribution with Unified
would give two distributions ownership of one package. Unified-backed protocol
servers need not install this native ACP application.
