---
name: validate-sh-cannot-be-green-on-devserver
description: "RETRACTED — validate.sh CAN exit 0 on a devserver (owner ran full → 5 gates passed/0 failed, 2026-08-03). No structural non-zero floor exists. Some detcore tests are host-sensitive: report a real host failure as a host limitation, never fake-green, but do NOT assume a blanket red baseline."
---

> **CI-HUB** — Current CI code, live query entrypoints, history, runner operations, and health truth are centralized at `ci-hub/README.md`. This memory records role/policy or historical context; do not treat dated paths or state below as the live tool location.

## RETRACTION (2026-08-03, hermit-226)

The old headline — "full `./validate.sh` CANNOT exit 0 on this development host"
— is **DISPROVEN and RETRACTED**. Evidence:

- The owner ran the full profile on a devserver to **5 gates passed / 0 failed**
  on 2026-08-03. The full profile issues exactly **5** top-level `run_check`
  gates (`validate.sh` init-submodules gate + `run_full_suite`'s portable lane
  {manifest-validate + DAG} + privileged lane {2}); each gate's DAG runs dozens
  of nodes internally but counts as one. (Quick issues 8 gates, portable-only 3
  — so "5 passed" is unambiguously the FULL profile, not quick.)
- There is **no structural non-zero floor**: the final verdict is only
  `((failures == 0))` (`validate.sh:4027`). There is no dirty-tree refusal, no
  baseline-failure floor, and no `--run-on-dirty-tree` flag — validate.sh runs
  and can pass regardless of tree cleanliness (verified against
  `worktrees/226v/hermit/validate.sh`, 2026-08-03).
- `futex_wait_bitset_timeout_is_absolute_and_removes_waiter` still exists
  (`detcore/tests/misc/mod.rs:1062`) and runs without-PMU
  (`det_test_fn_sequential_without_pmu`); it asserts FUTEX_WAIT_BITSET →
  ETIMEDOUT and does **not** depend on RDRAND/CPUID, so it is not in the
  host-sensitive RDRAND class the old note lumped it with. Whether it fails on a
  given host cannot be determined statically, but it is not a guaranteed red.

## What remains TRUE (salvaged doctrine)

- **The local-landing gate mechanism:** run `./validate.sh` on a PR's rebased
  SHA; if green, apply the `locally-validated` label (the legitimate substitute
  for green CI), then merge. Do NOT `--admin`-merge over a genuine red
  self-hosted failure, and never apply `human-approved` (owner-only).
- **Host-sensitive tests are real but specific:** some `tests_misc` cases
  (RDRAND/RDSEED class) can fail on hosts lacking the expected PMU/instruction
  behavior. When one does, PROVE it is baseline-environmental by running the same
  failing test on a clean `origin/main`, report it as a host limitation, and
  never weaken/delete/fake-green the test. This is a per-test judgment, **not**
  license to declare the whole suite unpassable.
- Bounded gate timeouts landed (PR #269, commit 26cd773): per-gate process-tree
  wall-clock kills (`GATE_TIMEOUT_SECONDS` / `VALIDATE_GATE_TIMEOUT_SECONDS`,
  `TIMEOUT_KILL_GRACE_SECONDS`) plus `--verbose`.
- Seed a warm `target/` into a slot with `cp -a --reflink=auto` to cut cold
  builds (subject to [[bpfjailer-blocks-reflink-cp]] and
  [[reflink-seed-cmake-cache-cross-worktree-pollution]]).

See [[validate-orchestrator-discipline]] (never claim green without a completed
durable log) and [[validate-sh-rr-compat-counter-conflict]]. Relates to
[[self-hosted-ci-sigsegv-blocks-all-prs]].
