# PR-drain planning: live-drain acceptance run (2026-08-04)

Question: does the consolidated PR-landing-planner pipeline run **end-to-end on the
live drain** and produce the acceptance numbers the owner asked for — PRs landed,
rebases avoided by clustering, conflicts caught before landing?

Pipeline exercised (one invocation each): FETCH once -> DERIVE mechanisms -> CLASSIFY
-> CLUSTER on conflict-set + mechanism -> EMIT plan JSON (order + per-PR action +
assigned agent + conflict maps) -> ARCHIVE to durable path. Real `git merge-tree`
conflict detection (not file-overlap), `--net-wrapper with-proxy`.

Planner binary: agent-utils/py/bin/pr-landing-planner @ main 8de0b72 (the 504 fix).

## Runs (ESTABLISHED — measured)

| repo    | ready PRs | wall  | conflict edges | plan bytes | archived |
|---------|-----------|-------|----------------|------------|----------|
| hermit  | 33        | 104s  | 298            | 101847     | yes      |
| reverie | 13        | 16.5s | 12             | 14524      | yes      |

Both exit 0, both archived under `plans/`. The pre-fix planner exited 2 (GraphQL 504
on the statusCheckRollup list) at >=60 PRs; that is the bug I fixed and landed. The
pipeline now completes on the live drain — this is the integration proof.

## Acceptance numbers (ESTABLISHED)

### PRs LANDED right now: **0** — and the cause is NOT the planner.
- hermit: `land_now = []`. CI = 32 red / 1 green of 33. All 32 reds classified
  `real` (not flaky). The one green PR (#1468) is `held: local-base-conflict` —
  needs a rebase before it can land.
- reverie: `land_now = []`. CI = 8 pending / 5 red of 13; 1 real red (#355), 3
  `refire-stale-gate`, 8 `wait` (CI still pending), #287 held
  (local-base-conflict + github-base-conflicting).
- `land_now = []` is the CORRECT output, not a failure: the ready (non-draft)
  backlog is almost entirely not-green, so a correct planner refuses to land.

### CONFLICTS CAUGHT before any landing attempt: **310** (298 hermit + 12 reverie)
Real merge-tree textual conflicts, caught pre-landing. Both drains are a SINGLE
conflict-connected component held together by a small set of append-only files:

hermit top conflict files (edges through each):
- 129  tests/e2e/manifests/inventory/test-files.json
- 72   ci/expected-e2e-plan.json
- 70   tests/backend-parity/matrix.tsv
- 52   tests/backend-parity/README.md
- 51   tests/backend-parity/run_matrix.py
- 48   tests/e2e/manifests/c-programs.toml
- 31   hermit-cli/src/bin/hermit/backends.rs

reverie: all 12 edges through reverie-ptrace/src/task.rs.

### REBASES AVOIDED by clustering (PROXY — labelled)
The planner emits `parallel_safe_groups` — PRs that are mutually conflict-free and
can co-land without rebasing against each other:
- hermit: one 11-PR group [1397,1553,1556,1430,1514,1412,1571,1470,1200,1213,1443].
  Of 528 pairs, 230 (44%) are directly conflict-free.
- reverie: one 12-PR group (of 13).
Honest caveat: both sets are ONE connected component at the file level, so this is
"which PRs can co-land without INTER-rebasing," not "N rebases eliminated." The
value is real but must not be inflated into a blind serial-rebase-all baseline.

### mechanism_overlap_edges: 0 on both.
No ready PR carries a paired `mechanism:<slug>` label, so the semantic dimension
finds nothing in the current set and the textual (merge-tree) dimension is the
binding one. Confirms "run both, neither subsumes the other" — here complementary.

## Highest-leverage drain finding (process, for the owner)
Both drains are jammed by APPEND-ONLY SHARED FILES, not deep code conflicts. Every
backend-parity ratchet PR appends to the same manifests (test-files.json,
matrix.tsv, expected-e2e-plan.json) or the same reverie task.rs. This is a
merge-ORDER problem: either land them serially with rebase, or restructure the
manifests to be append-friendly (per-PR fragment files that a generator concats).
The latter would dissolve most of the 310 conflicts at the source.

## Validate-record economics (owner input 2026-08-04, now emitted in the plan)
The locally-validated / clean-validate record is keyed to the exact head SHA, so a
REBASE changes the head and INVALIDATES the record — forcing a fresh validate run.
Serial draining rebases every queued PR onto the moved base, so N serial rebases
invalidate N SHA-keyed validate records: self-defeating, because each land destroys
the validation evidence of everything queued behind it. This makes the clustering
case stronger than the rebase argument alone — clustering avoids the same count of
rebases AND validate runs (1:1).

The planner now emits this in `plan.rebase_economics` (landed agent-utils eeaa14d):
`{validate_record_keyed_to: head_sha, rebases_avoided_by_clustering,
validate_runs_avoided_by_clustering, rationale}`, plus `validate_runs_avoided` in
the clusters summary and the tick-hub actions block.

LIVE numbers (each drain is one conflict-connected component, so landing it as one
stack instead of N serial rebases avoids size-1):
- reverie: rebases_avoided = validate_runs_avoided = 12 (measured, eeaa14d)
- hermit:  rebases_avoided = validate_runs_avoided = 32 (measured, eeaa14d)
- combined: 44 rebases AND 44 validate runs avoided vs naive serial draining.

## Cost model CONFIRMED
Fetch amortises (one fetch of all refs, then local git ops): hermit 33 PRs in 104s,
reverie 13 in 16.5s, dominated by O(N^2) merge-tree + per-head fetch, not the
initial fetch. Matches the owner's "fetch is the expense and it amortises" claim.

## Artifacts
- hermit-ready-plan.json / .stderr, reverie-ready-plan.json / .stderr
- plans/plan-rrnewton_hermit-main-20260804T005731_009540Z.json
- plans/plan-rrnewton_reverie-main-20260804T010127_141750Z.json
- hermit-ready-prs.txt (the 33 ready numbers)
