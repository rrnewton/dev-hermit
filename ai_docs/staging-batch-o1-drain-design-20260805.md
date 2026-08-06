# O(1) validation drain with a staging batch

**Status:** local design complete; live execution deferred for egress

**Task:** `staging-branch-merge-all-prs-test-once`

**Date:** 2026-08-05

## Outcome

The drain can reduce successful-path validation from one full run per PR to one
full run per batch. It cannot make all work O(1): snapshotting, merge attempts,
eligibility checks, conflict recording, and post-land constituent verification
remain O(N), while an ambiguous red may require O(log N) diagnostic subsets.

The safe unit is not literally every open PR. It is every **landing-eligible
exact head in one frozen open-PR snapshot**. Drafts, already-upstream work,
unresolved adversarial findings, unapproved gate-policy changes, semantic
conflicts, and owner-designated-last work are classified and recorded but may
not silently ride in the landed batch.

A single green authorizes the **combined staging tree**, not N independent PR
heads. The staging batch must therefore land atomically. Per-PR review and
policy approval remain individual; only the test evidence is shared.

## The authority gap that must be resolved before execution

The current single-PR executor, `ci-hub/landing/land-pr.sh`, always uses GitHub
rebase-merge. Rebase-merge rewrites constituent commits, so the original PR
heads need not be ancestors of `main`. That breaks the strongest existing
constituent proof and prevents the ordinary closure gateway from proving that a
specific original head rode in the batch.

The recommended batch landing is therefore a dedicated, coordinator-approved,
**topology-preserving merge commit** for the staging PR:

```text
main base B ---- batch merge L ---- main
                  /
staging tip S ----
      / ... original PR heads are ancestors of S
```

It must use normal branch protection, never `--admin`, and must verify both `L`
and every included original head against freshly fetched `main`. This is a
batch-only exception to the current rebase-only executor contract and requires
explicit policy approval plus a tracked `land-batch` implementation before any
live use.

The alternative is a new verifier that binds each original member's tree delta
to its replayed commits after a rebase merge. Patch IDs or a GitHub `MERGED` flag
are too weak by themselves, particularly when integration conflict resolutions
exist. Until either the topology-preserving path or an equivalent typed replay
verifier exists, a green staging branch is test evidence but **not executable
landing authorization**.

## Typed batch manifest

Every batch is defined by an immutable manifest archived beside the consolidated
planner output. A branch name is not identity. The minimum record is:

```json
{
  "schema_version": 1,
  "repository": "rrnewton/hermit",
  "target_branch": "main",
  "snapshot_utc": "<RFC3339>",
  "planner_archive": "<path>",
  "planner_digest": "<sha256>",
  "base_sha": "<40-hex>",
  "staging_branch": "staging/stale-drain-<snapshot-id>",
  "members": [
    {
      "pr": 123,
      "head_sha": "<40-hex>",
      "merge_commit": "<40-hex>",
      "review": "passed",
      "policy": "ci-hygiene",
      "mechanisms": ["<stable-slug>"],
      "status": "included"
    }
  ],
  "excluded": [
    {"pr": 456, "head_sha": "<40-hex>", "reason": "draft"}
  ],
  "conflicts": [
    {"pr": 789, "paths": ["path"], "disposition": "separate-semantic-lane"}
  ],
  "integration_commits": [
    {"sha": "<40-hex>", "kind": "registry-union", "paths": ["path"]}
  ],
  "reverie_pin": {"old": "<40-hex>", "new": "<40-hex>"},
  "staging_head": "<40-hex>",
  "staging_tree": "<40-hex>",
  "validation_receipt": null,
  "landing_oid": null
}
```

The manifest is append-finalized, not rewritten silently. A changed base,
member head, merge order, resolution, pin, or staging head creates a new batch
identity and invalidates the old receipt.

## Membership predicate

The planner considers all open, main-targeted PRs in the frozen snapshot. A PR
enters the landing batch only when all of these are true at its exact frozen
head:

1. the head still matches the API/fetched identity and is not already an
   ancestor of current `main`;
2. the PR is ready, not draft, and has not become obsolete or empty against the
   snapshot base;
3. required adversarial review is resolved at that head;
4. `ci-hygiene` versus `gate-policy` is classified, and every gate-policy member
   has current rationale and coordinator approval;
5. every mechanism overlap has a recorded disposition;
6. no member-specific blocker is being hidden by the batch;
7. its exact head is actually included in the staging topology; and
8. it is not in an owner-designated later lane, such as the patching-backend
   cluster.

This predicate deliberately does not require an individual full-validation
receipt. Requiring N such receipts would recreate the cost the staging batch is
meant to remove. It does require all non-test authorization and enough focused
evidence to justify spending the one full batch validation.

Use a new typed evidence class such as `batch-clean-validate-record`; never copy
the batch receipt into N `locally-validated` labels. Those labels claim evidence
at the PR heads, which the batch did not test independently.

## Branch-construction procedure

### 1. Freeze and plan once

Use the consolidated process to fetch one live snapshot, derive mechanisms,
classify policy/evidence, compute real and semantic conflicts, emit the JSON,
and archive it with the fetched base SHA. Relevance-check every member against
that base before construction.

The batch manifest records four disjoint dispositions for the whole open set:

- `included`
- `already-upstream-or-obsolete`
- `held-not-authorized`
- `conflict-excluded`

Thus “all open PRs” means all are accounted for, not that drafts or known-bad
work bypass policy.

### 2. Use an isolated registered slot

Provision a canonical slot through `scripts/allocate-worktree.rs`; never mutate
the Hermit primary and never use a branch name as the worktree path. Create a
descriptive branch such as `staging/stale-drain-<snapshot-id>` at the manifest's
exact `base_sha`.

### 3. Merge exact heads in deterministic order

Use the planner's stable order, respecting mechanism dispositions and the
owner-designated patching-last boundary. For each included member:

```bash
git merge --no-ff --no-edit <exact-fetched-pr-head>
```

After each successful merge, record the PR number, original head, merge commit,
and resulting tree. Require:

```bash
git merge-base --is-ancestor <original-pr-head> HEAD
```

If the merge conflicts, abort that merge immediately, record its paths and
planner edges, and apply the conflict policy below. Never resolve an unexpected
conflict merely to keep the batch count high.

### 4. Apply integration-owned changes once

After member commits are fixed:

1. apply the tracked format-aware registry union once;
2. regenerate derived JSON rather than hand-union it;
3. repair only cross-member completeness invariants exposed by combination;
4. advance the Reverie pin once, through the documented pin-update mechanism,
   after coordinated Reverie work is on its intended target; and
5. run the pin-consistency checks.

These are integration commits, not anonymous conflict edits. Their full diff
requires an adversarial integration review because no constituent PR reviewed
the combined resolution. The live 2026-08-04 staging run demonstrated this
class: one PR introduced a prelude-stamp invariant while another introduced a
new unstamped consumer. Rebasing either member alone could not reveal or repair
that cross-member completeness failure.

### 5. Audit completeness before validation

The final staging delta must equal:

```text
all included member deltas
+ allowlisted, reviewed integration commits
+ one intentional Reverie-pin update
```

Refuse the batch if an original head is not an ancestor of staging, an
unexpected path appears, a member delta is dropped, or the manifest and branch
disagree. Record `staging_head` and `staging_tree` only after this audit.

## Conflict handling

Conflicts are output, not a failed experiment. They have three dispositions:

### Deterministic registry glue

Shared append/registry files may be resolved only through the tracked union
mechanism (`ci-hub/landing/union-rebase.sh` or its batch equivalent). It merges
TOML entries by stable ID, JSON inventory by path, and TSV by row; derived
`ci/expected-e2e-plan.json` is regenerated. The generated resolution is an
integration commit and is reviewed once.

### Cross-member completeness repair

A combination can violate a new invariant even when Git reports no conflict.
The repair is allowed only when the failing verifier identifies the invariant,
the fix is mechanical and allowlisted, and the integration diff receives
adversarial review. Any repair changes the staging SHA and therefore precedes
the sole final full validation.

### Semantic or unmanaged conflict

Abort and exclude the PR from this batch. Route it to its own conflict component
or feature agent with the exact paths and mechanism overlap. A resolution that
changes reviewed behavior must return to review and cannot be smuggled into the
batch integration commit.

The patching-backend cluster remains last, either as a separate staging batch or
as an explicitly deferred component. It must not block the primary stale-drain
batch.

## One validation and atomic landing

Preparation can happen speculatively, but the finalization window is serialized:

1. acquire the landing lock/drain epoch;
2. fetch target `main` and require it still equals `base_sha`;
3. if it moved, rebuild from the new base and issue a new batch identity;
4. push the already-final staging head and verify the remote head byte-for-byte;
5. pass the composite validation admission at the exact remote staging SHA; it
   refreshes `origin/main`, proves that base is an ancestor of staging, and also
   enforces every fixed producer/merge-gate floor;
6. acquire the validation producer lock and run one boxed full validation at the
   exact remote staging SHA (the lock repeats the same moving-base admission);
7. require a counted, full-profile, nonzero-execution receipt bound to that SHA;
8. re-fetch `main` immediately before landing and require the base is unchanged;
9. land the staging PR atomically through the approved batch executor; and
10. fetch `main` again and perform all ancestry proofs before releasing the epoch.

Holding the landing epoch across final validation intentionally pauses other
landings for one validation duration. Without that freeze, a main advance would
change the integration tree and void the only receipt.

A red is not authorization. Use the DAG's named failing node first. Classify a
known common-cause infrastructure failure separately from a member/integration
failure. If attribution is ambiguous, use bounded subset/bisect branches for
diagnosis; after any fix, the final complete staging tip still needs one fresh
full receipt. The success path is O(1) validations in N; red diagnosis is not
falsely advertised as O(1).

## Mapping one green to per-PR authorization

For each member, pre-land batch authorization is the conjunction:

```text
exact frozen head
AND individually review/policy/relevance eligible
AND original head is an ancestor of exact green staging head
AND every integration delta is recorded and reviewed
AND exact staging head has a qualifying full receipt
AND the staging tree will land atomically
```

The green does not make a draft ready, approve gate policy, resolve adversarial
findings, or authorize separate per-PR merges. It proves the included changes
work together in one exact integration context.

After landing, closure additionally requires:

```text
fresh target fetch
AND batch landing OID is an ancestor of target main
AND original member head is an ancestor of target main
AND recorded staging tree is the tree that landed
```

Only then may each constituent task pass through the verified closure gateway,
using its original head or the typed batch manifest as evidence. A GitHub
`MERGED` state, branch deletion, PR comment, batch label, or clean dry-run is not
proof.

## Composition with the consolidated planning process

The staging batch is an execution mode inside `stale-drain`; it is not a second
planner:

```text
SNAPSHOT ONCE
  -> DERIVE / CLASSIFY
  -> CLUSTER and EMIT the canonical plan
  -> ARCHIVE plan + base SHA
  -> SELECT stale-drain/coalesced-batch
  -> FREEZE typed batch manifest
  -> CONSTRUCT exact-head staging topology
  -> RECORD conflicts and integration commits
  -> REVIEW member gates + integration delta
  -> VALIDATE exact staging head once
  -> ATOMIC BATCH LAND
  -> FETCH + VERIFY batch and every constituent
  -> CLOSE each task through the gateway
```

Fresh-flow remains serial and moving; do not hold new PRs merely to enlarge a
batch. Conflict-free stale singletons can also remain on the existing serial
soft-green path when that is cheaper. Coalescing is most valuable for stable
conflict components of at least three members, where conflict-resolving serial
rebases repeatedly void exact-head evidence.

Separate eligible batches land as they ripen. A green, executable component is
not held for a slower sibling or an all-backlog ceremony. Its landing advances
main and therefore forces every remaining batch manifest to refresh its base;
that invalidation cost is preferable to leaving proven work stale. Explicit
owner ordering such as patching-backend-last remains binding.

The planner archive remains the authority for `conflict_edges`,
`mechanism_overlap_edges`, look-ahead, assigned agents, and policy classes. The
batch manifest adds only construction and shared-evidence facts; it must never
reclassify planner output from prose.

## Stop conditions

Do not validate or land when any of these holds:

- egress is unavailable or the snapshot cannot be refreshed;
- a fetched head or target base differs from the manifest;
- a member is draft, obsolete, unresolved in adversarial review, or lacks
  required gate-policy approval;
- an unexpected or semantic conflict is unresolved;
- a generated registry union or pin update is not reviewed and reproducible;
- staging does not contain every recorded original head;
- the remote staging head differs from the validated SHA;
- target main moves during the finalization epoch;
- the receipt is incomplete, zero-execution, stale, or tampered;
- the topology-preserving batch executor and closure verifier are not approved;
  or
- any post-land ancestry or tree check fails.

## Deferred live acceptance

No branch, merge, fetch, push, PR, or validation operation was performed for
this design. When egress returns, acceptance requires a fresh planner snapshot,
an archived batch manifest, conflict/exclusion counts, exactly one successful
full receipt at the final staging head, an ancestry-verified atomic landing,
and constituent-by-constituent ancestry results. Report observed counts, not the
historical 2026-08-04 drain figures.
