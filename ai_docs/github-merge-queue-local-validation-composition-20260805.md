# GitHub merge queue with an exact local-validation leg

**Task:** `can-github-merge-queue-satisfy-our-local-validation-without-losing-the-local-loop`

**Date:** 2026-08-05

**Status:** local design complete; GitHub capability and timing acceptance deferred for egress

## Decision

A GitHub merge queue and the devbig local-validation loop are **compatible**, but
not through the current PR-head shortcut and not through a plain status posted by
the shared user token.

The safe composition is:

1. GitHub owns serialization and creates the exact merge-group candidate `Q` on
   current `main`.
2. A locally authorized ci-hub worker fetches and validates **that exact `Q`** on
   devbig.
3. The worker publishes an immutable, counted receipt keyed by `Q`.
4. A small trusted GitHub Actions job, triggered by `merge_group`, dereferences
   that receipt and emits the required check on `Q`.
5. GitHub lands only the candidate carrying that check; the closure gateway then
   proves the landed replay/tree and every batch constituent.

The Actions job is only a receipt verifier. It does not run Hermit, check out the
candidate, or arrange for GitHub to execute PR-controlled code on a Meta host.
The expensive and hardware-sensitive validation remains local.

This is not what the current merge-group gate does. Current
`hermit/.github/workflows/merge-gate.yml` resolves the constituent PRs and may
accept `locally-validated` receipts bound to their original PR heads. Those
receipts do not prove `Q` when `main` advanced, GitHub grouped more than one PR,
or integration changed the final tree. A PR-head receipt can be a scheduling
prior, but it must not satisfy the merge-group local-validation leg.

## What local sources establish

The conclusion above is based on the checked-in authorities, not a claim about
unobserved GitHub settings:

- `hermit/.github/workflows/merge-gate.yml` already handles
  `merge_group: checks_requested`, but its local leg calls the receipt verifier
  with each constituent `head_sha`, not `github.sha` / the merge-group head.
- `hermit/.github/workflows/ci-portable.yml` runs a full matrix on
  `merge_group`; hosted validation can therefore prove `Q`, independently of
  the proposed local alternative.
- `hermit/scripts/configure-merge-gate-ruleset.sh` requires
  `merge-gate-v4` from GitHub Actions integration ID `15368`. A commit status
  posted by the shared PAT is not that integration and cannot satisfy the
  current required-check tuple.
- `ci-hub/validate/preflight_validate.py` already accepts an exact SHA and
  refuses it unless freshly fetched `origin/main` is its ancestor. Once the
  queue ref has been fetched, that is the correct admission predicate for `Q`.
- `ci-hub/validation/verify_receipt.sh` already enforces exact SHA, full profile,
  clean result, nonzero execution, counted coverage, immutable receipt content,
  and receipt-branch ancestry. Its PR-comment discovery format needs a
  queue-specific sibling, but the evidence predicate should remain shared.

Two GitHub facts cannot be established from the local repository while egress
is disabled:

1. whether a merge queue accepts an externally posted commit-status context in
   every relevant configuration; and
2. how long this repository's queue will retain `Q` waiting for a required
   result.

The first fact is not on the recommended path: the present ruleset rejects a
non-Actions producer regardless. The second is a live acceptance requirement.

## Authority split

| Authority | Owns | Must not infer |
| --- | --- | --- |
| Consolidated PR planner | eligible members, review/policy state, conflicts, mechanisms, exact PR heads | queue SHA or validation |
| Local queue-admission record | permission for devbig to execute this exact member set | success of validation |
| GitHub merge queue | ordering, current-base candidate construction, invalidation/recreation of stale candidates | local validation |
| ci-hub local validator | full validation of exact queue SHA `Q` | GitHub landing |
| Immutable queue receipt | exact `Q`, counts, coverage, log digest, manifest identity | approval or ancestry on `main` |
| Trusted Actions relay | dereference the receipt and emit the required context on `Q` | product-test success from a label/status alone |
| Closure gateway | fresh landing/replay/tree and per-member proof | truth from `MERGED`, a PR label, or queue disappearance |

The local queue-admission record is a security boundary. A GitHub event must not
by itself cause arbitrary external PR code to execute on a devbig host. The
watcher may discover queue entries, but it runs a candidate only when an
archived local plan/manifest authorizes the repository, member heads, policy
class, and exact queue identity. This preserves the owner's prohibition on
attaching the Meta developer boxes as general GitHub runners.

## Exact candidate and receipt

For one queue generation, define:

- `B`: the target-main SHA used by GitHub to construct the group;
- `M`: an immutable member manifest of PR numbers and original head SHAs;
- `Q`: the merge-group head SHA;
- `TQ`: the tree OID at `Q`; and
- `R(Q)`: the qualifying local receipt for `Q`.

The minimum queue receipt extends the existing exact-head receipt with queue
identity rather than weakening its validation fields:

```json
{
  "schema_version": 1,
  "kind": "merge-group-local-validation",
  "repository": "rrnewton/hermit",
  "target_branch": "main",
  "queue_sha": "<Q>",
  "queue_tree": "<TQ>",
  "base_sha": "<B>",
  "member_manifest_sha256": "<digest-of-M>",
  "members": [{"pr": 123, "head_sha": "<40-hex>"}],
  "validation": {
    "profile": "full",
    "selection_mode": "full",
    "result": "PASS",
    "executed_tests": 1,
    "failures": 0,
    "coverage": {
      "planned_test_nodes": 1,
      "zero_executed_nodes": [],
      "absent_nodes": []
    }
  },
  "log_sha256": "<64-hex>",
  "started_at": "<RFC3339>",
  "finished_at": "<RFC3339>"
}
```

The actual counts come from the existing ledger row; the example values above
only show types and must not become constants. The receipt lives at a
deterministic immutable path such as
`merge-queue-receipts/rrnewton/hermit/<Q>.json` on the existing receipt branch.
Publishing different content to an occupied `Q` path is refused.

The queue verifier must be a distinct semantic entry point because queue
membership and tree identity are additional authorities. It should call the one
shared qualifying-receipt predicate for the nested validation row, then verify:

```text
receipt.queue_sha == merge_group.head_sha
receipt.queue_tree == git tree of merge_group.head_sha
receipt.base_sha is an ancestor of merge_group.head_sha
receipt.member_manifest_sha256 == digest(resolved live group membership)
every manifest member is still the exact queued PR head
```

A `locally-validated` PR label, a copied PR-head receipt, a matching status
description, or a tree hash without the manifest is not a queue receipt.

## End-to-end state machine

### 1. Admit members locally

Run the consolidated planning process first. Resolve review, policy, relevance,
mechanism overlap, and conflicts at exact PR heads. Archive a local authorization
record for either one fresh-flow PR or one staging-batch PR. Enqueueing is not
validation.

### 2. Let GitHub construct the candidate

Add only authorized work to the merge queue. GitHub serializes it behind the
current target and emits `merge_group` candidate `Q`. The local watcher resolves
the queue entry, exact base, exact member heads, and queue ref into `M`.

The watcher must not run an unrecognized queue entry. It records and waits for a
coordinator disposition instead.

### 3. Fetch and admit exact `Q`

Fetch the explicit `gh-readonly-queue/...` ref into an isolated ref and require
that it resolves byte-for-byte to the API/event SHA. Validate in a registered
slot detached at `Q`; never move a primary checkout.

Immediately before reserving the validation producer, run the existing
composite admission with `--head Q`. It refreshes `origin/main`, checks fixed
producer floors, and proves current main is an ancestor of `Q`.

If the queue regenerates the group while admission or validation is running,
the new candidate has a different SHA. Stop or finish for diagnostics, but do
not publish that run as authorization for the replacement. Receipts never
transfer across queue SHAs.

### 4. Validate and publish

Run one boxed full local validation at `Q`, under the existing validation lock.
Require a clean, counted, full-profile receipt with satisfied per-node coverage
and nonzero execution. Re-resolve the live queue entry before publication; if it
no longer names `Q` and `M`, preserve the log but do not publish an authorizing
queue receipt.

Publish `R(Q)` and its immutable manifest. This is the only network mutation the
devbig producer needs; GitHub never schedules work on the devbig host.

### 5. Relay through a trusted required check

The recommended relay is a trusted `merge_group` Actions job on a public/weak
control-plane runner. It checks out no PR code and executes only the verifier
bound to trusted `main` (or an immutable parent authority commit). It may poll
for `R(Q)` while the queue candidate remains live.

It succeeds only after dereferencing `R(Q)` and rechecking live queue membership.
Its required context remains pinned to the GitHub Actions integration. A missing
receipt stays pending or becomes a bounded `NO_RESULT`; it must never be turned
green by a raw label or external status.

The current `merge-gate-v4` behavior must change for merge-group events:

```text
pull_request event: PR-head CI or exact PR-head local receipt
merge_group event: hosted CI at Q or exact queue receipt R(Q)
```

Hosted terminal failure at `Q` remains a failure. A local queue receipt may
supply the alternate leg for hosted absence/cancellation according to policy;
it must not overwrite a genuine hosted product failure.

### 6. Land and close from observed identity

GitHub may land with rewritten commits. Therefore success of the required check
at `Q` is pre-land authorization, not constituent closure.

After landing, freshly fetch `main` and record the actual replay/merge OIDs. The
closure gateway must prove either:

- topology preservation: the validated queue commit and original member heads
  are ancestors of the landed target; or
- typed replay: the recorded landing span has the same terminal tree `TQ`, the
  same base/member manifest, and an audited per-member replay mapping with no
  unrecorded integration delta.

Patch IDs, PR `MERGED`, and a final main tip observed after unrelated later
landings are insufficient. Tree equality is checked at the recorded terminal
OID for this group, not at an arbitrarily newer `main` tip.

## Fresh-flow and stale-drain composition

### Fresh flow

Configure or operate the queue at group width one for ordinary PRs. Each
candidate is rebased/merged coherently by GitHub, locally validated at its exact
`Q`, and landed before the next group. This preserves the local loop but still
costs one full validation per PR.

The previously validated PR head remains useful as a review/debugging prior. It
does not authorize `Q`. Rebase-before-validate admission still prevents wasting
local validation on an already stale head before enqueueing, while the queue
receipt closes the later race between that head and final integration.

### O(1) stale drain

Use the typed staging branch as **one queue item**, not N independent PRs whose
head receipts are copied onto a group. The staging manifest proves all original
members and reviewed integration commits; GitHub then constructs one current-base
candidate `Q` for that staging PR. Validate `Q` once and publish one queue
receipt bound to the staging-manifest digest.

GitHub's queue supplies the serialization/fresh-base portion that the staging
design otherwise obtains by holding a landing epoch. It does not supply the
constituent closure proof. The staging batch still needs the topology-preserving
executor or typed replay verifier and extended closure gateway described in the
staging design.

Land independent staging clusters as they ripen. Once one queue group lands,
every remaining manifest must re-resolve against the new target. GitHub will
regenerate their queue SHAs; old receipts remain historical evidence only.

If GitHub is configured to group multiple ordinary PRs into one `Q`, that group
becomes a batch and must meet the same typed-manifest and closure rules. Do not
silently treat N PR-head labels as one group green.

## Does this eliminate the concurrent-admin-merge class?

It eliminates the stale-base/force-push chase only if all ordinary main writes
go through the queue:

- GitHub, not each lander, owns ordering and candidate regeneration.
- A main advance invalidates or regenerates queued candidates; exact-SHA
  receipts cannot follow them accidentally.
- Landers no longer force-push rebased shared branches to race one another.

It does **not** structurally prevent an administrator from bypassing the queue if
the ruleset still grants bypass. The local ruleset helper is explicitly named
`main check gating (admin-bypassable)` and preserves `bypass_actors`; it does not
verify the separate merge-queue setting. A complete structural fix therefore
requires the live ruleset/branch configuration to require the merge queue and
remove ordinary bypass actors, with emergency bypass separately audited. A
written “do not use `--admin`” convention is not equivalent.

## Latency condition

The composition works only when the queue retains `Q` long enough:

```text
T_queue_wait
  > T_discovery + T_fetch + T_admission + T_validate_p99
    + T_publish + T_relay + safety_margin
```

The local portable gate is dominated by a short serial chain and a one-wide
guest lane; the measured warm scale is about 484–499 seconds, so widening alone
does not make this constraint disappear. The actual queue timeout and observed
discovery/publication overhead require a live test. Do not substitute the old
32–45 minute hosted estimate or an undocumented GitHub default.

If the repository cannot configure enough wait time, the two mechanisms conflict
operationally even though their evidence models compose. The fallback is the
ci-hub-owned serialized landing epoch: ci-hub constructs the current-base
candidate, validates it locally, then invokes the normal GitHub merge while
holding ordering. That is not needless reinvention; it is the scheduler needed
when GitHub cannot wait for our external validator.

## Live acceptance deferred for egress

No fetch, push, status, workflow dispatch, queue mutation, PR operation, or
validation run was performed for this design. When egress returns, use an inert
throwaway PR and bracket all of these:

1. queue emits a resolvable exact `Q` and retains it while the required check is
   pending;
2. unrecognized/unapproved queue membership never triggers devbig execution;
3. a well-shaped nonexistent or PR-head-only receipt cannot satisfy the check;
4. a counted full receipt for exact `Q` makes the Actions relay succeed on `Q`;
5. changing/recreating `Q` invalidates the earlier receipt;
6. measured end-to-end latency fits within queue retention with margin;
7. concurrent ordinary enqueueing advances through GitHub serialization without
   a force-push;
8. the landed terminal tree/replay and every staging constituent pass the typed
   closure gateway; and
9. an admin bypass is absent or produces a separately audited emergency event.

Also test an external commit status only to answer the platform question, not
as the default design. Under the current Actions-pinned ruleset it should remain
non-qualifying. If a future dedicated GitHub App is considered, its integration
identity, least-privilege token, negative forgery bracket, and ruleset binding
must be designed before changing the required context.

## Relationship to the canonical process

This is an execution adapter inside the existing pipeline, not a second planner:

```text
SNAPSHOT / CLASSIFY / REVIEW
  -> REBASE-ADMIT PR OR BUILD TYPED STAGING BATCH
  -> AUTHORIZE MEMBER SET LOCALLY
  -> ENQUEUE
  -> RESOLVE EXACT MERGE-GROUP Q
  -> LOCAL FULL VALIDATE Q
  -> PUBLISH R(Q)
  -> TRUSTED ACTIONS RELAY PASSES AT Q
  -> GITHUB SERIALIZED LANDING
  -> FETCH + TYPED REPLAY/TREE VERIFICATION
  -> CLOSE THROUGH THE GATEWAY
```

The consolidated process remains authoritative for selection and policy. The
staging design remains authoritative for batch membership and per-member proof.
The stale-base admission remains authoritative at every receipt-producing local
run. GitHub's queue contributes ordering and candidate construction; it does not
replace any of those authorities.
