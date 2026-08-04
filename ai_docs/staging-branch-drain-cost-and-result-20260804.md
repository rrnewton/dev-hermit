# Staging-branch drain: measured result + cost both ways (2026-08-04)

Task `staging-branch-merge-all-prs-test-once` (hermit-lander). Analysis of the staging run
executed earlier today; states the cost serial-vs-staging the owner asked for, and reports the
attribution outcome (the design risk the owner flagged).

## What was run (prior agent, ~12:47–12:49)
- `staging/drain-all` cut from hermit `origin/main` `b384187`, 61 PRs merged `--no-ff`
  (11 patching-backend PRs excluded per owner "land LAST"): **18 rode in clean, 43 conflicted, 0 no-ref**.
- Reverie pin bumped ONCE `79517704` → `114b3df` (latest reverie main) at the staging tip.
- ONE FULL validate at commit-anchored `aff18cf` (systemd-run producer path, boxed).

## Result: RED — and cheaply attributed (the key finding)
Validate FAILED in **2m19s wall / 14m35s CPU across 316 cores**. Exact cause, from the DAG's
per-step report (no bisect needed):

```
[check.script_sigpipe] prelude-cache-key.sh: FAIL — prelude cache-key is stale in 1 consumer(s):
  scripts/validate.rs (have: MISSING, want: 088ae17fa4a1)
  Fix: scripts/lib/prelude-cache-key.sh --write   (then commit the result)
safe-ci-dag-runner: FAIL - 2 passed, 1 failed, 3 aborted, 41 skipped in 0.4s
```

- **A single mechanical lint**, not a product break, not a build/test failure. A merged PR changed
  `scripts/lib/rust_script_prelude.rs` (and added `prelude-cache-key.sh` — it is NOT on main) but
  `scripts/validate.rs` was not restamped. One-line fix + commit.
- **Attribution cost ≈ 0.** The feared failure mode ("staging is red, you don't know which PR, now
  bisect") did NOT occur: the DAG named the exact file and fix in 0.4s. The `keep_going` DAG runner
  gives free per-step attribution — this is the property that makes staging safe here.
- **The build blocker that stalled the serial drain appears CLEARED by the bump.** The privileged
  DAG *compiled `reverie-dbi` at `114b3df`* and passed 7/7 (96s). The portable DAG short-circuited
  at the 0.4s lint BEFORE its build ran, so full portable build/test green is not yet proven — but
  the reverie-dbi build panic that blocked drain at the old pin did not recur at 114b3df.

## Cost both ways (owner's explicit ask), today's numbers
Validate ≈ 528s median; backlog of ~21 orphaned+floor-clearing READY PRs; measured **100% receipt
orphan rate on rebase** (each landing rewrites the SHA the next PR's receipt is keyed to).

- **Serial drain.** Each landing rebases everything behind it and voids its receipt → forced
  re-validate. Not N×528s but roughly **Σₖ k·528 ≈ N²/2·528**. For N=21 that is ~1.1e5 s (**~32h**)
  worst case; even amortized it is several×11,000s, plus N rebases. This is the quadratic the owner
  identified; it is why serial draining is self-defeating.
- **Staging.** ONE conflict-resolution pass (concentrated on ~7 registry files, below) + ONE
  validate (~528–715s) + a bisect budget on failure. **Measured bisect budget this run = ~0** (lint
  named itself). Even pricing a full bisect (log₂61 ≈ 6 validates ≈ 3,200s), staging total is
  ~4,000–7,000s — **roughly ¼ to 1/20 of serial**, and O(1) in N for the validate dimension.

Staging wins decisively, and the empirical run refutes the attribution objection for this backlog.

## Honest scope limits (do not overclaim)
- **Landing value of THIS branch is small.** Of the 18 clean riders only **6 are READY**
  (1200 1213 1412 1430 1470 1514); 12 are DRAFTS (must not land). The 43 conflicted PRs — which
  include most READY floor-clearing candidates — are NOT in the branch.
- **Conflicts are a ~7-file registry-accumulator problem, not deep code.** Collision frequency:
  test-files.json 21, backend-parity/README.md 15, matrix.tsv 13, backend-parity-c.toml 10,
  ci/expected-e2e-plan.json 5, run_matrix.py 4, ci/dag/portable.json 3. TSV/markdown/toml are
  union-safe; the 3 JSON files need **format-aware merge**, not blind union. This is the single
  highest-leverage follow-up and it is file-level (7 files), not per-PR.
- **The branch is now STALE.** main advanced `b384187` → `f80b1c09`. Per the soft/hard-green rule,
  staging must be re-cut/rebased onto current main; a clean rebase → soft (inherited) green, a
  conflict-resolving rebase → green VOID, hard re-validate required.
- **Adversarial review already failed the reverie sub-batch 0/9** (member-specific blockers).
  Merging staging→main is a coordinator-gated landing, not a lander unilateral action; drafts and
  unreviewed members must not ride in.

## Recommended next actions (executable)
1. Re-cut `staging/drain-all` from current `f80b1c09`; merge the READY, review-passed set only.
2. Apply the one-line fix `scripts/lib/prelude-cache-key.sh --write` (restamp `scripts/validate.rs`)
   as part of the staging tip, then re-validate ONCE. Expect the portable DAG to then reach build;
   at 114b3df reverie-dbi builds, so a full green is now plausible.
3. Build the format-aware union merger for the 3 JSON registries — unblocks the 43 conflicted PRs
   that carry the real drain value.
4. Coordinate with the rebase-front work: rebased floor-blocked heads are the staging inputs;
   validate-after-push (push rewrites head → pre-push receipt is dead).

Existing origin staging refs: `staging/drain-all` (bd5272b), `staging/membership-passing-1470`,
`staging/membership-passing-1470-1551-1544`, `staging/patching-coalesce`.
