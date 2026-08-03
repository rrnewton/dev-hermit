# validate.sh parallelism audit (2026-08-03)

## Question
Owner's green full local validate: real **8m18.665s** / user **13m09.104s** =
**1.58x** average parallelism on a 316-thread machine. Why so low, and how to
drive wall time down while keeping user/real high?

## Method (measurement, not inference)
1. Read the real-run ledger `ignored/validate-run-ledger.jsonl` (26 full-profile
   runs incl. the owner's exact run) for real/user/sys per run + per-gate durations.
2. Read the code path from source: `hermit/validate.sh` → `run_full_suite` →
   `run_ci_manifest_lane {portable,privileged}` → `ci/run-dag.sh <lane> -j 2 -v`
   → `agent-utils/rs/safe-ci-dag-runner`.
3. Parse the DAG manifests `ci/dag/{portable,privileged}.json` (nodes, deps,
   `hint.est_duration_s`, `hint.resources`, `resource_caps`).
4. Compute critical path + resource-constrained makespan via list-scheduling
   simulation honoring deps + `resource_caps` + `-j` (`crit.py`), matching the
   real scheduler (`safe-ci-dag-runner/src/scheduler.rs`, unit test
   `resource_cap_serializes_concurrent_steps`).

## Findings
- **The full validate is DAG-orchestrated, not 36 serial bash gates.** The owner
  run (`a034f39c`, PASS, real=499s, user=789.095s → 789/499 = **1.58x**) is FIVE
  bash gates: submodule-init 1s + manifest-inventory 9s + **portable DAG 455s** +
  manifest-inventory 8s + privileged DAG 26s. Portable DAG = **91%** of wall.
- **1.58x is under-dispatch, not I/O-block** (sys=319s ptrace = compute). Two
  compounding ceilings:
  - outer **`-j = 2`** (`CI_DAG_JOBS:-2`, local-only; CI calls run-dag.sh directly).
  - **`resource_caps.hermit_guest = 1`** serializes 16 single-threaded
    (`--test-threads=1`) hermit test nodes.
- **The runner parallelizes fine when work allows:** the *same* full profile on a
  COLD cache (`4f4b8722`) hit real=816/user=6272 = **9.12x** (cargo builds fan
  out). The owner's 1.58x is a **warm-cache** residual where the guest-serialized
  single-thread test nodes are the whole wall.
- **cgroup hypothesis DEAD:** the Rust runner runs steps UNBOXED (`--cgroups` is
  Python-only in 0.1); validate passes only `-j 2 -v`.
- **Critical path (portable, dep-only, est):** e2e.metadata(5)→build.workspace(360)
  →lint.clippy(300)→test.strict_compat(600) = **1265s**, theoretical max **4.24x**.
  But `hermit_guest=1` gives a **2640s** resource floor that dominates ⇒ plateau
  **~1.78x regardless of -j**. Widening the resource cap (not adding workers) is
  the fix. Sim: cap4/j8 = 5.55x, cap8/j16 = 6.83x.

## Fix (priority; details + patches in tg `validate-sh-should-be-dag-runner-orchestrated`)
- **F1** outer `-j` → host memory-aware (local-only, safe) — helps cold/mixed path.
- **F2** raise `hermit_guest` cap 1→2..4 (warm-1.58x mover) — GATED on CI-green +
  big-box A/B (real load-dependent timing flakes: vfork timeout / PMU-skid /
  timeslice-under-load; big-box-only, invisible to CI).
- **F3** prune `test.strict_compat` artificial dep fan-in (critical-path tail;
  it's a recursive `./validate.sh` call that builds its own hermit).

## Reproduction
```
cd hermit
jq -c 'select(.profile=="full")|{commit:.commit[0:8],res:.result,real:.real_seconds,user:.user_seconds}' \
  ../ignored/validate-run-ledger.jsonl        # real runs incl owner a034f39c
python3 ../experiments/validate_parallelism_audit_20260803/crit.py   # critical path + makespan sim
```
`portable_nodes.txt` / `privileged_nodes.txt` are `tag|est_s|class|deps|resources`
extracts of `ci/dag/*.json`. `ledger_full_runs.jsonl` is the 26-run extract.

## Caveats
- `est_duration_s` hints are ~5x pessimistic vs warm-real (sum 5615s vs owner
  wall 499s); the makespan **ratios** are valid, the **absolutes** are not — real
  wall-seconds-saved must be measured by the F2 A/B.
- Live per-second concurrency timeline + A/B deferred: machine was at load
  391/316 (fleet-saturated) during this audit; a competing full validate would
  both starve the fleet and contaminate the numbers.
