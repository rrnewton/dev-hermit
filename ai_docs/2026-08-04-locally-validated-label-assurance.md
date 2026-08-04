# `locally-validated` label assurance (2026-08-04)

The `locally-validated` label is a cache hint, not an authority. This audit
traces each consumer that can turn the label into a landing decision.

## Consumer matrix

- **Hermit merge gate — evidence-bound.** Hermit PR #1578 landed as replay
  `d74e0412c3374d34d3921882526540db708abd10`. Current main fetches a
  hash-pinned verifier from the parent, dereferences an immutable receipt for
  the exact PR head, and requires a counted, complete validation record. A label
  with no qualifying receipt does not satisfy the local-validation leg.
- **Parent lander — evidence-bound.** Parent commits
  `3538c4711c295050bb91fc3c17df394d4a6b2743`,
  `ee7a4571c970a198fed3cf8b51046a7b8afa8443`, and
  `f9e61247e83bb07c11297541b591606de24a89a8`
  make the label a derived cache: the lander checks the exact head through the
  canonical ledger predicate and only `apply-local-label` can materialize it.
- **Reverie merge gate — fail-closed without local authority.** Reverie main
  `9a7c0aa701d0d53413aaeb9c351377b0bc481918` states and implements that no
  remotely dereferenceable receipt authority exists, so a bare
  `locally-validated` label is never landing evidence. Reverie requires its
  repository CI instead.
- **Agent-utils landing planner — cache-only.** Agent-utils main
  `e74b4545d897e23c67f35fb7308b7e6fdd3345f1` still reports a raw label as an
  observed `LOCALLY_VALIDATED` cache state, but the action planner authorizes
  local landing only for a caller-supplied exact-head `CLEAN_VALIDATE_RECORD`.
  Authoritative CI remains independently sufficient.
- **Agent guidance — corrected.** Parent main
  `e558d3e028152de15b7d60623873c5ee166cc429` updates manual-CI guidance: a
  validate run produces a counted receipt; `apply-local-label` may derive the
  cache label; the merge gate dereferences the receipt rather than trusting
  label presence.

## Mutation bracket

- Hermit receipt fixtures admit 2/2 legitimate counted exact-head receipts and
  reject well-shaped nonexistent, digest-tampered, zero-executed, and incomplete
  schema-5 receipts.
- Parent lander fixtures reject an unbacked SHA with the label absent and present
  (2/2), and admit a ledger-backed SHA with the label absent and present (2/2).
- Agent-utils planner negative: 1/1 pending-CI PR with only a raw label is
  `REFIRE_CI`, not `LAND_NOW`.
- Agent-utils planner positives: 1/1 exact-head clean record and 1/1 authoritative
  CI result remain qualifying evidence.
- Agent-utils focused planner tests pass 11/11. Full repository checks pass:
  Python 295/295, Rust unit 68/68, Rust integration 7/7, mypy clean, and Clippy
  with `-D warnings` clean.

## Residue

No audited consumer authorizes landing from bare label presence. Shared GitHub
credentials are still not a cryptographic signer: a credential with enough
authority could deliberately publish false receipt content. That malicious-token
threat requires a dedicated signing identity; it is distinct from accidental
hand-application of the cache label, which this assurance closes.
