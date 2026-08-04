# Merge Gate Branch-YAML Authority Audit

Date: 2026-08-04

## Verdict

The stale-workflow hole is **live**. No new authorization artifact was planted.
An existing production run proves that branch-local `workflow_dispatch` YAML can
emit the exact successful status context consumed by the current main ruleset
while enforcing weaker semantics than current main.

The sibling local-validation hole is also live on main: bare presence of the
`locally-validated` label satisfies the local leg without consulting a ledger or
durable exact-head receipt. PR #1578 has a validated evidence-binding fix, but it
has not landed. PR #1579's current head predates that integration and must not
land without it.

## Observed Consumer Proof

The active ruleset `20244443` requires one Actions status context:
`merge-gate` from app id `15368`. It does not carry a workflow-definition hash
or inspect job outputs.

Existing Actions run `30868091777` is the safe positive control:

| Field | Observed value |
| --- | --- |
| Event | `workflow_dispatch` |
| PR | open PR #1547 |
| Head | `6217ea5c25413767b68d2947255a1dff4a88dd34` |
| Workflow | `.github/workflows/merge-gate.yml` from that branch |
| Workflow blob | `2a9222382d705a2253dca2cfd99fd0ba76f3a314` |
| Emitted job | `merge-gate`, job `91864215699` |
| Conclusion | `success` |

The job log says only that the authoritative portable job passed and that the
PR was not a demo hot path. The branch workflow calls privileged CI an
"independent bonus signal" and never checks it. Current main says the GitHub
leg requires portable **and** privileged and implements a `ci-privileged.yml`
lookup. The ruleset nevertheless sees the same `merge-gate/success` signature.

This verifies the defect against the consumer rather than inferring it from
GitHub's workflow semantics. No dispatch, label, check, or merge was created by
this audit.

## Current Exposure

All open PR heads were fetched and their actual
`.github/workflows/merge-gate.yml` blobs inspected.

| Measure | Result |
| --- | ---: |
| Open PRs | 75 |
| Heads predating `bfb0a9ef` by ancestry | 56/75 |
| Heads carrying a weaker portable-only gate definition | **57/75** |
| Missing/unreadable workflow definitions | 0/75 |

Ancestry alone undercounts the exposure. PR #1543 contains `bfb0a9ef` but still
carries the older `2a922238` portable-only gate blob. Seven distinct stale
workflow blobs cover the 57 vulnerable heads. Every one exposes
`workflow_dispatch`, emits job name `merge-gate`, trusts the bare local label,
and lacks the current privileged-CI requirement.

The earlier PR #1579 implementation audit examined 27 dispatches after the
portable-plus-privileged tightening: 13 succeeded, 12 successes used stale
workflow YAML, and none of those stale-success PRs had merged. Thus the hole is
demonstrably usable and common, but that audited window found no completed land
that relied on it.

## Sibling Authority Failure

Current main's `merge-gate.yml:386-393` reads the PR labels and accepts
`locally-validated=true` directly. It does not read the ci-hub ledger, verify a
durable log, or bind a receipt to the exact head.

PR #1578 (`fe1a03f73477212feb0676a1afd783a8106c2485`) adds exact-head evidence
binding. Its planted negative run `30873169163` rejected a bare label, and its
full retry plus exact-head positive gate passed. It remains open and draft, so
main is still vulnerable.

PR #1579 (`4beaedf92826c89f8c2b2285f54ef8f653705e72`) still contains the old bare-label
predicate at its current head. Required order is therefore:

1. land #1578;
2. rebase #1579 onto fresh main while preserving the evidence predicate;
3. rerun full validation and both negative/positive controls at the new head;
4. change the required context only after the new definition is landed.

## Fix Shapes And Tradeoffs

### Trusted main-defined producer

Run the gate from a definition outside the PR and create the check on the PR
head with a GitHub App or equivalent controller. This is the strongest model:
the producer and consumer share a trusted definition, and branch YAML cannot
weaken it. It adds a credentialed service and operational ownership. A normal
main-ref `workflow_dispatch` alone is insufficient because its Actions check
attaches to the main SHA, not an arbitrary PR head.

GitHub's native required-workflow rule would provide this shape, but the live
attempt for this user-owned repository returned HTTP 422; required workflows
are an organization/enterprise feature here.

### Versioned context plus registered blob (PR #1579)

Require `merge-gate-v2` and register the expected workflow blob. This blocks all
unmodified stale-v1 branches and catches accidental v2 drift. It works in a
personal repository and has already rejected a deliberately changed blob in a
non-authorizing test path.

Its boundary is important: the guard is still PR-owned YAML. A deliberate
workflow edit can remove the guard and emit the same v2 context. Each semantic
change also requires a new context/blob rollout. GitHub ruleset PUT has no
conditional update, so the reconciler can detect but not eliminate a narrow
read-to-write race. This is a practical stale-branch mitigation, not a complete
trust-boundary fix.

### Reusable workflow pinned to a trusted ref

A reusable workflow in a separate trusted repository/ref centralizes the gate
logic and reduces drift. The PR-owned wrapper can still skip the call or mint a
lookalike context unless the required consumer authenticates the callee or uses
a versioned context. This reduces duplication but is not sufficient by itself.

### Definition hash or version in the result

Emitting a definition hash makes semantics visible, but the current ruleset
matches only app and context name; it cannot inspect outputs. The hash must be
part of a versioned context name or be verified by a trusted external check.
Otherwise it is diagnostic metadata, not enforcement.

## Disposition

The current fix is IN-FLIGHT, not in effect. PR #1579 is a useful personal-repo
mitigation after #1578 lands, but the owner must choose whether its scoped
protection is acceptable or whether the required check must move to a trusted
main-defined producer. No workflow or ruleset was modified by this audit.
