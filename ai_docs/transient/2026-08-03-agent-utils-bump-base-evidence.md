# agent-utils single-variable bump baseline

- Parent A: `ef665fcc32817cea15016b9ea51a2faa6963620c`
- agent-utils A: `1c0e9c3c4928dac192e0115291f114863bc03a0d`
- Proposed agent-utils B: `46308d4ea57f252a1a49ca8c5df4c4da092bc35d`
- Exact-A GitHub run: <https://github.com/rrnewton/dev-hermit/actions/runs/30834844162>
- Baseline status at capture: `UNKNOWN-CONFOUNDED` (queued, not green).

The full Hermit validation baseline is not clean: `test.detcore_misc` has a
pre-existing 16-23% load-dependent failure rate pending the coordinated
Reverie #355 pin update. That failure cannot be attributed to this agent-utils
gitlink bump. The A/B verdict therefore covers the authoritative agent-utils
repository contract only: embedded-guide consistency, both implementations,
formatting, mypy/clippy, Python and Rust tests, and the Python/Rust
differential. Full Hermit validation remains unknown until the independent
Reverie repair lands and its matched-load evidence is green.

The target was refreshed immediately before the protocol run. The task's
original `43d6884` snapshot had advanced by one fast-forward commit to
`46308d4`, which adds the complete agent-utils repository CI contract and
contains the requested per-step CPU-time work at `96ca7d65`.

## Protocol Results

- Isolated tested B: `a240e3d58c097e29cc31fdcada7cb708ae8b1a25`
- Changed path in B: `agent-utils` only
- Passing record: `20260803T170641Z-agent-utils-2885449`
- Actual cost: wall `155.950s`, CPU `148.017s`
- Python: mypy clean; `216 passed`
- Rust: clippy clean; `65 passed`; boxing smoke passed
- Differential: `377 checks across 41 fixtures agree`
- Upstream target workflow: <https://github.com/rrnewton/agent-utils/actions/runs/30834539740>

The append-only history also retains failed record
`20260803T170510Z-agent-utils-2799725`. Its tests and checks passed, but the
differential could not find the Rust binary because the verifier supplied an
external `CARGO_TARGET_DIR` that disagreed with the repository contract. The
corrected run removed that harness override and passed. This is classified as
a verifier configuration failure, not a target regression.
