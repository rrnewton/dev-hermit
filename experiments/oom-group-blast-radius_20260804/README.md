# memory.oom.group per-step blast-radius verification (2026-08-04)

## Question
Task `oom-blast-radius-hits-neighbours-not-the-offender`: when a boxed DAG step exceeds
its `memory.max`, does the OOM kill land on the OFFENDING step as a whole, or does the
kernel pick a single victim process (leaving a half-dead step) or an innocent neighbour?

## Fix under test
safe-ci-dag-runner writes `memory.oom.group=1` on every per-step cgroup where `memory.max`
is set (agent-utils `py/.../cgroup.py` `enter_delegated_scope` + `Cgroups`; `rs/.../cgroup.rs`
`Cgroups`). Best-effort: a kernel without `oom.group` must not drop the swap/mem caps.

## Method
`systemd-run --user --scope -p Delegate=yes -p MemorySwapMax=0` gives a delegated cgroup with
the `memory` controller; enable `+memory` subtree_control; carve per-step child cgroups
(`memory.max=64MiB`, `swap.max=0`) exactly as the runner does. A bounded python allocator
plants an over-cap (200MiB) or under-cap (32MiB) load. `inside.sh` runs the whole bracket.

## Results (see results.csv)
- **CASE1 (fix, oom.group=1, over-cap):** co-tenant sentinel + allocator BOTH die;
  `oom_group_kill=1`. The step dies as a UNIT. -> mechanism FIRES (negative bracket).
- **CONTROL (oom.group=0, over-cap, TODAY):** allocator dies, **sentinel SURVIVES**;
  `oom_group_kill=0`. Half-dead step — the exact defect. -> proves the fix is NOT inert.
- **CASE2 (per-step caps):** offender breaches its cap -> OFFENDER DEAD (`oom_group_kill=1`),
  **NEIGHBOUR ALIVE** (`oom_kill=0`). -> blast radius contained to the offender.
- **CASE3 (positive control):** N=10 legitimate steps at 32MiB under a 64MiB cap run
  concurrently -> alive=10/10, total `oom_kill=0`. -> mechanism is NOT over-eager (N=10 stated).
- **CASE4 (cleanup):** all 14 child cgroups drained + rmdir'd, 0 residue.

## Attribution
`Cgroups.oom_kills(tag)` reads the step's OWN `memory.events` `oom_kill` counter, so the kill
is attributed to the offending step (offender>0, neighbour=0, per CASE2). With `oom.group` the
step's exit code and its `oom_kill` signal AGREE (whole step dies), removing the prior
half-dead ambiguity (leader exit 0 while `oom_kill>0`).

## Reproduce
    systemd-run --user --scope -p Delegate=yes -p MemorySwapMax=0 --unit=oomverify ./inside.sh
