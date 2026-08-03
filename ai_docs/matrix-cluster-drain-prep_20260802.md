# matrix-tsv cluster drain — PREP complete (101/101 conflict-free)

Task: `matrix-tsv-schema-consolidation` (hermit-lander). Owner directive
(2026-08-02): work-ahead while the merge-gate ruleset flip is pending — PREP the
~100-PR backend-parity cluster so it lands instantly once unblocked, using 247's
exclude-before-rebase recipe onto the post-`matrix.tsv`-removal base.

## Result

**All 101 open cluster PRs prepped conflict-free** against the post-removal base
and **functionally validated** (each PR's test case reaches the *live*
`case_catalog` that `run_matrix.py --check`/`validate_catalog` reads):

- 100 PRs register 1 new case each: `--check` ptrace catalog **24 → 25**, 0 errors.
- 1 PR (#1477) registers 3 cases: catalog **24 → 27**, 0 errors.
- 0 prep branches still touch `tests/backend-parity/matrix.tsv`.

Prep branches are local refs `prep/<pr>` in `worktrees/lander/hermit` (shared
object store with primary `hermit`). They are **not pushed** — see "Why local".

Machine-readable manifest: `scratch/cluster-prep/prep-manifest.tsv`
(columns: `pr  prep_sha  ptrace_catalog  errs  files`).
Drain driver: `scratch/cluster-prep/drain.sh`.

## Post-removal base

247 (`matrix-tsv-relocate-to-parent`, PR #1498, head
`09d7bd0c6f9833a51e4681357c552d24b71b6cf1`) removes `tests/backend-parity/matrix.tsv`
and reshapes `run_matrix.py` so the case set derives from the in-code
`case_catalog()` (single source of truth) instead of the TSV. #1498 is OPEN /
not yet landed; its head is 1 commit behind current `origin/main`. Prep is done
onto `09d7bd0c` (247's proven base).

## What actually conflicts (and why the naive recipe is insufficient)

Directive premise was "drop the obsolete matrix.tsv hunk → conflict-free." That
holds for only the 6 already-11-col PRs. The 91 old-6-col PRs **also** conflict
on `tests/backend-parity/README.md` (the 6→11 migration reshaped its tables),
and 4 PRs additionally collide inside `run_matrix.py`. Correct per-surface
resolution:

| surface | state at base | resolution | rationale |
| --- | --- | --- | --- |
| `matrix.tsv` | deleted (modify/delete) | `git rm` | obsolete, removed by 247 |
| `README.md` | modify/modify | `git checkout --ours` (keep base) | doc table; **not** coupled to any CI gate (`run_matrix.py` never reads README) |
| `run_matrix.py` | modify/modify (4 PRs) | manual **union** (keep both dict entries) | positional collision at a shared insertion anchor; each PR adds a unique key |
| `fixtures/*.c`, `tests/c/*.c` | new file | keep | unique, never conflicts |

The 4 union PRs: **#1227, #1221, #1461, #1464** — all collided with the landed
`scheduler_policy_queries` entry at the same anchor; resolved by keeping both
complete entries. (Git's built-in `merge=union` driver works for single-line
entries like #1227 but corrupts multi-line dict entries — do not use it blanket.)

Dropping the README hunk leaves a cosmetic doc gap (the new test has no
detail-table row); it is harmless for CI and regenerable from the catalog later.

## Reproduce / re-run

```bash
# from worktrees/lander/hermit, with origin fetched
scratch/cluster-prep/drain.sh <pr> <head-sha>   # auto matrix+README; aborts on run_matrix.py collisions
# the 4 union PRs are resolved by keeping both dict entries (see prep/<pr> branches)
python3 tests/backend-parity/run_matrix.py --check   # validate: catalog count increments, 0 errors
```

## Land procedure (once #1498 lands on main → SHA `M`)

Each `prep/<pr>` is based on `09d7bd0c`. After #1498 merges:

```bash
git rebase --onto <M> 09d7bd0c prep/<pr>          # trivial: matrix already gone, README base-intact
with-proxy git push --force-with-lease origin prep/<pr>:refs/heads/<pr-branch>
```

### Why local (not pushed now)

`09d7bd0c` is not an ancestor of `main`, so pushing a prep branch now would make
its PR diff include 247's matrix.tsv removal. Prep is held local until #1498
lands; then the trivial rebase-onto-M + push makes each PR conflict-free vs true
main.

### Land-time caveat — residual `run_matrix.py` inter-PR collisions

Prep resolves each PR **against the base**. Landing them **sequentially** still
serializes on `run_matrix.py`: each landed case adds a dict entry, so a later
prep branch rebased onto the newer main can positionally collide with it — the
same serialization that plagued `matrix.tsv`, but far milder (inserts are
scattered by family, not all at one tail; only 4/101 collided against the base).
Handle at land time with per-collision union (as done here) or the coordinator's
deterministic-union tool; a blanket `merge=union` gitattribute is unsafe
(multi-line entries corrupt). This is a landing concern, not a prep defect.

## Blockers

1. #1498 (247) must land first — it defines true post-removal main.
2. Merge-gate ruleset flip (task `adopt-github-merge-queue`) — needed for the
   fast locally-validated parallel-land path; strict-up-to-date policy otherwise
   re-serializes via label invalidation on each synchronize push.
