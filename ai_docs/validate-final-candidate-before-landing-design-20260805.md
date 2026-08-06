# Validate the final integration candidate before landing

Task: `validate-then-land-is-unsound-the-push-rewrites-the-head`

## Decision

The unsound workflow is more precisely **validate, mutate the candidate, then
land**:

```text
validate H0 -> rebase/push H1 -> land L
```

A qualifying receipt for `H0` says nothing authoritative about `H1` or `L`.
The sound workflow is one of:

1. construct and publish the final current-base head, validate that exact head,
   and land it with no later candidate rewrite; or
2. let a serialized merge queue construct the current-base integration
   candidate `Q`, validate exact `Q`, and require the queue to land that
   candidate or supply a typed replay proof.

Every operation that can change the tested object—rebase, conflict resolution,
push, force-with-lease update, staging merge, merge-group regeneration, or
integration onto a newer base—must happen before hard-green evidence is minted.
Soft green may order work; it never crosses this boundary.

## The objects are immutable; the references move

A Git commit SHA is immutable. The mutable object is the mapping from a PR or
branch reference to its head SHA. Define:

- `B0`: target-main base before landing preparation;
- `H0`: original PR head;
- `R(H0)`: a counted full-validation receipt whose `commit` is exactly `H0`;
- `B1`: freshly fetched target base used for the rebase;
- `H1 = rebase(H0, B1)`: the post-rebase head pushed to the PR;
- `B2`: target base actually used by the landing mechanism;
- `Q`: a queue-constructed integration candidate, when a merge queue is used;
- `L`: the commit or terminal replay actually placed on `main`.

The receipt verifier implements an identity predicate:

```text
qualifies(R(X), C) only if R(X).commit == C
```

Therefore:

```text
R(H0) qualifies H0
H1 != H0
-----------------
R(H0) does not qualify H1
```

This is not a conservative convention. It is required by the evidence model.
A conflict-resolving rebase can change source; a clean rebase changes the
parent/history and commit identity; a newer base can change dependencies,
generated inputs, feature selection, or runtime behavior. Patch ID, title,
branch name, and “same logical change” are all proxies for the tested object.

The live observation `qualifying_count: 0, disqualified_count: 0` is decisive:
the checked head had no candidate receipt at all. It was not a receipt rejected
on a technicality. The gate correctly refused to infer evidence across the
head rewrite.

## Authorization invariant

Pre-land authorization requires one of these proof shapes:

```text
exact-object:
  qualifying_receipt(C)
  && live_candidate == C
  && submitted_candidate == C
  && landed_object == C

typed-replay:
  qualifying_receipt(C)
  && live_candidate == C
  && replay(C, base, members) == L
  && replay_verifier(C, L, base, members) == PASS
```

The second shape must be an explicit authority, not an informal tree glance.
At minimum it carries the base, terminal tree, member manifest, ordered replay
mapping, and proof that no integration delta was added. If validation can
observe commit identity or history, tree equality alone is insufficient; the
policy must either require exact-object landing or revalidate `L`. The current
receipt contract is commit-scoped and must not silently be widened to
tree-scoped evidence.

An API `MERGED` flag, successful merge command, label, status description,
patch ID, or ancestry of an unrelated later tip is not either proof shape.

## Sound path A: publish, then validate, then no-rewrite land

This path works when the landing mechanism can preserve the validated
candidate, or when an approved typed replay verifier covers the integration.

1. **Serialize the landing epoch.** Acquire the landing lease so another
   lander cannot advance `main` between candidate construction and submission.
2. **Resolve immutable inputs.** Freshly fetch target `B1` and remote PR head
   `H0`; record both full SHAs.
3. **Rebase first.** Rebase `H0` onto `B1`. A conflict resolution changes
   reviewed code and must receive the required exact-head review again.
4. **Finish pre-evidence mutations.** Mark ready and perform any other workflow
   transition that can invalidate checks or review before the hard-green run.
5. **Push with a lease.** Push `H1`, fetch the remote ref back, and require
   `headRefOid == H1`. The fetched remote identity, not the local branch name,
   is the validation target.
6. **Recheck freshness.** Immediately before admission, freshly resolve
   `origin/main`. If current main is not an ancestor of `H1`, return to step 3.
   Fixed producer and gate floors must also pass.
7. **Validate exact `H1`.** Run the full counted profile at `H1`, publish
   `R(H1)`, and bind exact-head review/authorization to `H1`. A receipt on `H0`
   is at most a scheduling prior.
8. **CAS the authorization boundary.** Immediately before submission require:

   ```text
   live PR head == H1
   target base == B1
   receipt verifier accepts R(H1) for H1
   required exact-head review still names H1
   ```

   `--match-head-commit H1` supplies the first comparison. It does not compare
   the target base and is insufficient alone.
9. **Land without candidate reconstruction.** Prefer a fast-forward/exact-object
   update that makes `H1` the landed object. If the hosting mechanism insists
   on replaying commits, treat its result as `L` and require the typed replay
   proof before calling the pre-land evidence transferable.
10. **Freshly verify.** Fetch target main, record the actual landed OID, and
    prove exact-object ancestry or the typed replay mapping. Only then may the
    closure gateway close constituent tasks.

Any head or base mismatch invalidates the authorization attempt. It is a normal
regeneration event, never a reason to copy the old receipt forward.

### Why `gh pr merge --rebase --match-head-commit H1` is not the whole proof

`--match-head-commit` prevents a changed PR head from being merged. It does not
pin `B2`, and rebase merge creates replay commit OIDs by design. If `main`
advances after `R(H1)` is produced, GitHub may replay onto a different base.
Even when `B2 == B1`, the landed OID may be `L != H1`.

The current ancestry check proves that GitHub's reported `mergeCommit.oid` is
on fetched main. That is necessary landing evidence, but it does not prove that
the landed replay is the object that was validated. The post-land validation
obligation can detect a bad replay and fix forward; it does not retroactively
make the pre-land authorization sound.

## Sound path B: merge queue constructs, then local validation proves `Q`

A merge queue removes the base race only when the receipt binds to the queue's
candidate rather than to the original PR head.

1. The consolidated planner authorizes an immutable member manifest `M` of PR
   numbers and exact heads.
2. GitHub serializes the queue and constructs `Q` from current base `B` plus
   exactly `M`.
3. A locally authorized worker fetches the explicit queue ref and proves the
   ref, event/API SHA, base, member manifest, and terminal tree agree.
4. The worker runs full local validation at exact `Q` and publishes `R(Q)` with
   `queue_sha`, `queue_tree`, `base_sha`, and the digest of `M`.
5. A trusted Actions relay, running verifier code pinned to trusted main,
   dereferences `R(Q)` and emits the required check on `Q`.
6. If GitHub regenerates the merge group as `Q2`, `R(Q)` becomes historical
   evidence only. The worker validates `Q2`; no receipt follows a mutable queue
   reference.
7. GitHub lands the exact candidate, or the closure gateway verifies a typed
   replay from `Q` to `L` using the recorded base, tree, and member mapping.

For ordinary flow, queue width one gives one current-base candidate and one
validation per PR. For a typed staging batch, the staging PR is one queue member
whose manifest names every constituent; one validation proves the combined
`Q`, while constituent closure still requires the batch replay/topology proof.

The complete queue receipt and trusted-relay design is already specified in
https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/github-merge-queue-local-validation-composition-20260805.md.
This formalization makes its identity requirement explicit: PR-head evidence
cannot satisfy a merge-group check.

If the queue cannot retain `Q` for the local validation latency, use a
ci-hub-owned serialized landing epoch that constructs the current-base
candidate and holds ordering until submission. Reducing the identity proof to
fit a short queue timeout is not an acceptable fallback.

## Soft green, hard green, and mutable caches

The terms must remain distinct:

- **Soft green at `H0`**: useful evidence for scheduling, conflict-free rebase
  priority, or deciding which candidate to construct next. It is not landing
  authority for `H1`, `Q`, or `L`.
- **Hard green at `C`**: a counted, full, clean receipt dereferenced for the
  exact final candidate `C`, plus the required review bound to `C`.
- **`locally-validated` label**: a mutable cache pointing toward the receipt.
  The verifier, not label presence, decides.
- **Branch or PR head**: a mutable reference. Always resolve it to a full SHA
  again at the consumer boundary.
- **Landing state**: established by the actual landed OID on freshly fetched
  main plus exact-object or typed replay proof, never by the pre-rebase head.

This also applies to adversarial review. PR #1200 is the concrete pattern: a
receipt and review at `21ecb06b` could not cover the two later scheduler commits
at `81a59a16`. Both authorities died when the head moved.

## Audit of the current local lander

`ci-hub/landing/land-pr.sh` has useful fail-closed pieces but is not an atomic
implementation of either sound path:

| Current behavior | Disposition |
|---|---|
| Requires a clean receipt for original remote head `ORIG` | Safe as a precondition, but wasted when the following rebase changes the SHA |
| Rebases, pushes with force-with-lease, fetches `HEAD` | Correct mutation-before-final-evidence ordering |
| Requires a second exact-`HEAD` receipt and rejects absence | Correctly closes the old-receipt/new-head hole |
| Rechecks live PR head and uses `--match-head-commit HEAD` | Correct head compare-and-swap |
| Does not itself run/wait for validation between push and the second check | Operational gap: it normally stops unless an external producer already validated `HEAD` |
| Uses `gh pr merge --rebase` without a target-base compare-and-swap | Residual current-base/replay identity gap |
| Verifies only `mergeCommit.oid` ancestry after merge | Proves landing, not equivalence to the validated head |
| Arms exact landed-SHA validation after merge | Valuable detection/fix-forward, not pre-land proof |

The immediate workflow repair is to split preparation from submission around a
durable `awaiting-validation(H1)` state, or let one resumable coordinator own
the whole sequence. The pushed SHA must be handed to the validator, and the
submission step must consume the resulting exact-SHA receipt. Neither side may
infer that the other ran.

## State machine

```text
OBSERVED(H0, B0)
  -> REBASED(H1, B1)
  -> PUSHED_REMOTE(H1)
  -> VALIDATING(H1)
  -> HARD_GREEN(H1, R(H1), review(H1))
  -> SUBMIT_READY(H1, B1)
  -> LANDED_EXACT(H1)
       or LANDED_REPLAY(L, replay_proof(H1,L,B1))
  -> CLOSURE_VERIFIED
```

Transitions that change the head go back to `REBASED`; transitions that change
the target base go back to candidate construction. A missing receipt is
`AWAITING_VALIDATION`, not failure. An unreachable receipt authority is
`UNKNOWN`, not absence. A genuine exact-candidate red blocks that candidate.

The queue variant replaces `H1` with `Q` after `MERGE_GROUP_CREATED` and applies
the same invalidation rule whenever the queue generation changes.

## Acceptance brackets

Before calling either executor sound, test both sides of every authority:

1. A qualifying `R(H0)` is refused for rebased `H1` (the existing receipt
   fixture already brackets this).
2. A qualifying `R(H1)` is accepted only while the live PR head is exactly
   `H1`; a subsequent push refuses submission.
3. Advancing target main after hard green forces candidate regeneration unless
   an exact-object landing remains possible.
4. A well-shaped receipt for a PR head cannot satisfy a queue candidate `Q`.
5. Regenerating `Q -> Q2` refuses `R(Q)`.
6. A replay with an added/dropped commit, changed terminal tree, changed base,
   or changed member manifest is refused.
7. A qualifying exact-object landing or fully typed replay is accepted, then
   its actual OID is ancestry-verified on freshly fetched main.
8. Killing the coordinator after push leaves a durable
   `awaiting-validation(H1)` record that a replacement resumes without rebasing
   or copying a receipt.

Use inert fixtures or an isolated repository for negatives; never plant a real
authorization label or merge as a test artifact.

## Implementation boundary

This is a local design and code-path audit. It did not fetch, push, rebase,
validate, mutate a PR, or land anything. Implementing the executor requires a
separate code task. The merge-queue live capability/retention test also remains
deferred until egress returns.
