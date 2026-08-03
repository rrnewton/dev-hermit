# Adversarial review: hermit-ci CI-recovery process (wall-clock waste)

- **Date:** 2026-08-02
- **Reviewer:** hermit-coord (co-coordinator), Opus 4.8
- **Subject:** PR #1488 `ci/build-recovery-fast-parallelism` recovery loop
- **Trigger:** owner complaint — recovery is "wasting huge wall-clock time
  without results"; hypothesis: a factored DAG should allow iterating on ONE
  shard instead of re-running the whole 40–50 min suite each cycle.
- **Method:** read-only inspection of the CI DAG structure, `validate.sh`, the
  recovery branch commit cadence, and the GitHub Actions run history for the
  branch. Did **not** touch PR #1488 (dbi owns it).

## Verdict

The owner's suspicion is **substantially correct, with one honest caveat.** The
recovery loop is a `push → wait 34–37 min full-DAG → read fail → push again`
cycle, and the DAG is explicitly factored so this is avoidable for a large
fraction of the fixes. Caveat: roughly **half** the 26 recovery commits fix
GitHub *runner-environment* problems a local single-shard run genuinely cannot
reproduce — but even those were handled wastefully (cancel-thrash, no batching).

## Evidence (quantified)

### 1. Cancellation thrash — the dominant waste
Portable-lane run history on the branch (durations, UTC):
```
2m 4m 9m 4m 15m 6m 16m 4m 12m  → all CANCELLED (21:00–21:55)
37m → failure (22:03)   34m → failure (22:49)   19m → in_progress (23:11)
```
Nine consecutive portable runs cancelled in ~1 hour, each killed by the *next*
commit before completing. Every cancel burned 2–16 min of runner + wall-clock
and produced **zero usable signal**. This is spray-commit-and-restart, not
iterate.

### 2. Full-workflow re-fire per commit
Each of the 26 commits triggers the entire set — `CI (portable)` +
`CI (privileged)` + `P0 Demo Gate` + `Merge Gate` — at 34–37 min for a complete
portable cycle. 26 commits at that cadence is the ~40–50 min/iteration complaint.

### 3. Watching a placeholder as signal
The run list is full of back-to-back `Merge Gate → failure`. Per the hermit-ci
role skill, "merge-gate is a re-fire placeholder that is red until CI completes."
Re-firing/reading merge-gate red before portable finishes is noise.

### 4. Local validation, if run, ran the whole lane
`validate.sh:2960` → `./ci/run-dag.sh "$lane" -j "$jobs" -v` runs **all ~45
nodes**, and `validate.sh` had **no single-node selector**. A local sanity check
via `validate.sh` rebuilt/reran the whole lane, not the one failing shard.

### 5. The terminal failure was a single, node-targetable shard
Last completed portable failure (run 30770946939): `FAILED: test: sabre` →
DAG node `test.sabre_examples`, directly runnable in isolation via
`ci/run-node.sh`. It was "fixed" by making it non-gating, not by node iteration.

## Honest caveat (review cuts both ways)

Categorizing the 26 commits by *where the failure lives*:

- **GitHub-runner-env only (~13) — local single-shard CANNOT catch these:**
  bpftool on mixed-kernel runners (5), user-namespaces in release shards,
  hosted 64-core parallelism / `CARGO_BUILD_JOBS` (2), load-sensitive KVM probes
  made "occasional" (2), cross-job DBI-runtime artifact packaging for the debug
  fan-out (4). A GitHub round-trip is genuinely required for these.
- **Locally reproducible (~13) — `run-node.sh` iterates in minutes:**
  version-provenance fixes (3), empty-manifest lanes, the SaBRe node, DynamoRIO
  job caps, reverie pin/build bumps.

So ~half the loop was unavoidably GitHub-bound — but for that half the correct
discipline is batch + let it finish + rerun only the failed job, which was not
done. For the other half a factored local path existed and was unused.

## Process failures (ranked)

1. Cancel-thrash loop (new commits killing in-flight runs) — dominant sink.
2. No use of the single-node local entrypoint for locally-reproducible failures;
   `validate.sh` re-runs the whole lane.
3. Full-workflow re-push instead of targeted GitHub rerun
   (`gh run rerun --failed` / `--job`) for env-only failures.
4. Treating merge-gate red as diagnostic signal.
5. One-fix-per-commit-per-push for independent env fixes that could be batched.

## Recommendations (concrete)

**A. Iterate locally on ONE node (seconds–minutes, no GitHub round-trip):**
```bash
cd ~/work/dev-hermit/hermit
ci/run-dag.sh portable            # build the tree ONCE
ci/run-node.sh portable test.sabre_examples          # then loop one shard
ci/run-node.sh portable e2e.manifest_backend_parity_c,test.dbi_parity
```
`jq` + the compiled `agent-utils/rs/bin/safe-ci-dag-runner` are present today.

**B. Stop the thrash.** If a GitHub cycle is unavoidable (env-only fix), let it
finish and re-test only the failed job — never push a new commit over an
in-flight run you care about:
```bash
with-proxy gh run rerun <run-id> -R rrnewton/hermit --failed
with-proxy gh run rerun -R rrnewton/hermit --job <job-id>
```

**C. Batch env-only fixes** into one commit → one CI cycle.

**D. Ignore merge-gate as a diagnostic**; read the portable rollup's per-job
failures directly (`gh run view <id> --json jobs`).

**E. (implemented) Make single-shard iteration first-class in `validate.sh`.**
Add `--only <lane> <group.job>[,...]` that delegates straight to
`ci/run-node.sh` and exits, bypassing the heavy harness:
```bash
./validate.sh --only portable test.sabre_examples
```
Filed as a direct PR against `rrnewton/hermit:main` (see task
`main-green-recovery`).

## Codified as a skill

The tight-iteration lesson from this review is now a first-class Hermit task
skill: `hermit/.claude/skills/ci-debugging.md` (also surfaced via
`.llms/skills/ci-debugging.md`), cross-linked from the `hermit-ci` role skill.
Filed as a direct docs PR against `rrnewton/hermit:main`
([PR #1493](https://github.com/rrnewton/hermit/pull/1493)); it pairs with the
`validate.sh --only` selector ([PR #1492](https://github.com/rrnewton/hermit/pull/1492)).

## Bottom line

Wall-clock is burned by cancel-thrash and full-DAG-per-iteration, not by a
missing capability — the shard-level tooling (`ci/run-node.sh`) exists and sat
idle. Fix = discipline (batch, don't cancel, rerun `--failed`) + routing local
iteration through `run-node.sh` for the locally-reproducible half. The env-only
half (~50% of #1488) genuinely required GitHub round-trips; that must be stated
honestly rather than blamed on tooling.
