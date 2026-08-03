# validate.sh smart-selection: measured node/shard/cell reduction

**Date:** 2026-08-03
**Agent:** hermit-ci
**Branch/SHA:** `codex/validate-smart-selection` @ `3370d9c8c1caa85012ae10199293543c8871a5b9` (PR #1554, stacked on #1545)
**Tool measured:** `ci/select-tests.rs` (the selector `validate.sh` default-smart mode drives)
**Task:** `ci-hub-smart-selection-in-validate` — closes the "Report measured node/shard/cell reduction per mode" deliverable.

## What this measures (and what it does NOT)

This is a **static selection measurement** of the PORTABLE lane only: for a given
changed-file set, how many DAG nodes / test shards / e2e cells the selector marks
as needing to run, versus the full portable lane. It is NOT a wall-clock number —
wall-clock before/after is tracked separately (`wallclock-before-after-test-selection`)
and needs a real green base + full run.

The PRIVILEGED lane (PMU/CPUID/KVM, 7 nodes) is **never** modeled by select-tests
and `validate.sh` default-smart **always runs it in full** — the selector can never
prove it inert, so it is out of scope for reduction. Numbers below are portable-lane.

## Denominator (full portable lane)

| Unit  | Full count |
| ----- | ---------- |
| nodes | 47 |
| test shards | 11 |
| e2e cells | 61 |

Source of the full DAG: `ci/dag/portable.json` projected onto `ci/portable-shards.json`
+ `ci/expected-e2e-plan.json` (post-44df2944 shard/cell shape).

## Reduction by change class

Measured by feeding a representative one-file changeset to the selector:
`printf '%s\n' <path> | ci/select-tests.rs --files - --format json`.

| Change class (example path)              | decision  | nodes | shards | e2e cells | node red. | shard red. | cell red. |
| ---------------------------------------- | --------- | ----- | ------ | --------- | --------- | ---------- | --------- |
| docs-only (`README.md`, `docs/*.md`)     | skip      | 0/47  | 0/11   | 0/61      | 100%      | 100%       | 100%      |
| reverie-sabre (`reverie-sabre/src/lib.rs`) | selective | 8/47  | 2/11   | 0/61      | 83%       | 82%        | 100%      |
| detcore-dbi (`detcore-dbi/src/lib.rs`)   | selective | 14/47 | 4/11   | 8/61      | 70%       | 64%        | 87%       |
| detcore core (`detcore/src/scheduler.rs`)| selective | 46/47 | 11/11  | 61/61     | 2%        | 0%         | 0%        |
| CLI (`hermit-cli/src/main.rs`)           | selective | —     | 11/11  | 61/61     | ~0%       | 0%         | 0%        |
| test fixture (`tests/c/hello.c`)         | selective | —     | 4/11   | 61/61     | —         | 64%        | 0%        |
| unknown path (`some/random/new_file.xyz`)| **full**  | 47/47 | 11/11  | 61/61     | 0%        | 0%         | 0%        |
| harness (`validate.sh`, `ci/**`)         | **full**  | 47/47 | 11/11  | 61/61     | 0%        | 0%         | 0%        |

### Reading the table

- **Reduction is real and large for backend-localized and docs changes**: a
  sabre-only change drops to 2 shards / 0 e2e cells; a dbi-only change to 4 shards
  / 8 cells; a docs change skips the portable lane entirely.
- **Reduction is correctly ZERO for core, CLI, unknown, and harness changes** —
  these fan out to the full lane. This is the FAIL-OPEN contract working: unknown
  or broadly-scoped changes run everything.
- **This PR's own diff (`--base origin/main`) decides `full`** because it touches
  `validate.sh` (a `force_full` path). The selector cannot silently prune the CI
  harness itself. Verified: `ci/select-tests.rs --base origin/main` → `decision: full`,
  11/11 shards, 61/61 cells.

## Per-MODE interpretation

The two validate.sh modes differ only in which changed-file set they feed the
SAME selector; the reduction formula above is invariant:

- **`--shallow-select`** (baseline = `HEAD~1`): changeset = the single most-recent
  commit's files. Narrowest diff ⇒ **upper-bound reduction** for a given commit,
  but sound only if everything before `HEAD~1` is truly green.
- **default green-base** (baseline = last established-green SHA via ledger /
  `$HERMIT_LAST_GREEN_SHA` / `locally-validated` tag / green GH check): changeset =
  union of all commits since that base ∪ working tree. Wider diff ⇒ reduction
  **≤ shallow**, but sound against a real green anchor. With **no** trustworthy
  baseline the selector falls back to **full** (fail-open).

Realized reduction for either mode = the selection over its diff's union of change
classes (e.g. a green-base spanning a docs commit + a sabre commit selects the
sabre 2-shard set, not skip).

## Reproduce

```bash
cd worktrees/ci/hermit   # branch codex/validate-smart-selection @ 3370d9c8
./ci/select-tests.rs --self-test                        # 57 checks, 0 failures
./ci/select-tests.rs --base origin/main --format human  # this PR ⇒ full
for f in README.md reverie-sabre/src/lib.rs detcore-dbi/src/lib.rs \
         detcore/src/scheduler.rs some/random/new_file.xyz ; do
  printf '%s\n' "$f" | ./ci/select-tests.rs --files - --format json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['nodes']),d['decision'])"
done
```

## Caveat carried from the handoff

The footprint map (`ci/test-footprints.json`) is hand-maintained. A prior audit
found 11 footprints / 19 globs where cargo-derived truth was 20/56 — i.e. the map
can UNDER-declare, which for an untracked path yields **full** (safe), but a
mis-mapped tracked path could under-select. This is why the owner requires FREQUENT
FULL RUNS (nightly + chain-depth `VALIDATE_FULL_RUN_EVERY` + before release/pin-bump)
alongside selection. The reduction above is the *upside*; the full-run cadence is
the *safety net*.
