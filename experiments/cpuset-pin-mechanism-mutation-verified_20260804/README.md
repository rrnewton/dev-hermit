# cpuset core-pin mechanism — mutation-verified (2026-08-04)

## Question
The safe-ci-dag-runner READS `cpuset.cpus.effective` but never WRITES core affinity, so
boxed runs get memory limits and **no core isolation** (confirmed world (c), task
`cpu-affinity-has-no-allocator-boxed-runs-are-not-isolated`). The owner wants a **stateful
cpuset allocator** that pins a whole process tree to reserved cores. Before building it:
**which pin mechanism actually enforces a hard, inescapable, tree-wide bound in THIS
sandbox** — proven by mutation (request K cores; confirm a running child cannot reach a
K+1th core), not by reading back the value written?

## Method
Host devserver, `nproc=316` (cores 0-315). For each mechanism: pin to a core set, spawn a
child, read the child's `Cpus_allowed_list` from `/proc/<pid>/status`, then **attempt to
escape** the child to an excluded core via `taskset -pc <excluded> <child>` and re-read.
A hard bound masks the escape; a soft bound lets the child move.

- **A. `taskset` / `sched_setaffinity`** — the fallback PR #15 (agent-utils
  `codex/runner-cpuset-core-box`) ships.
- **B. `systemd-run --user --scope -p AllowedCPUs=<set>`** — sets `cpuset.cpus` in the
  transient scope's cgroup.

Positive control (K=2): confirm the tree uses *both* assigned cores (spinners' `processor`
field in `/proc/<pid>/stat`), i.e. not inertly stuck on one.

## Results
| mechanism | inherited by child | escape to excluded core | verdict |
|---|---|---|---|
| A `sched_setaffinity`/`taskset` | yes (100) | **SUCCEEDED** → child moved to 101 | SOFT / escapable — NOT a valid bound |
| B `AllowedCPUs` (systemd → `cpuset.cpus`) | yes (100) | **MASKED** → child stayed 100 | HARD / inescapable |

K=2 `AllowedCPUs=100-101`, clean readback from parent shell:
- `cpuset.cpus = 100-101`, `cpuset.cpus.effective = 100-101`
- scope `cgroup.controllers = cpuset io memory pids` (cpuset delegated under `app.slice`)
- contained proc `Cpus_allowed_list = 100-101`
- NEGATIVE: escape to 102 masked (stayed 100-101).
- POSITIVE: 3 spinners ran on cores 100, 101, 101 — used both, never spilled.

**Sandbox reality:** the agent's own scope
(`…/3pai_sandbox.slice/run-*.scope`) has controllers `io memory pids` only — **no cpuset
delegated**, so a cgroup cpuset cannot be written there. `systemd --user` transient scopes
land under `app.slice`, where cpuset **is** delegated. This is why the mechanism must go
through `systemd-run --user` (which the runner's `reexec_in_scope` already uses).

## Interpretation
Build the allocator on **`AllowedCPUs`**, not `sched_setaffinity`. PR #15's fallback does
not isolate (a child can widen its own affinity). `AllowedCPUs` writes `cpuset.cpus`, so the
runner's existing read of `cpuset.cpus.effective` (`cgroup.py:456,813`) finally aligns with
a written value. The stateful reservation ledger (disjoint concurrent cores, release-on-exit,
dead-holder reclaim) is still required on top; the mechanism trivially supports a distinct
`AllowedCPUs` per scope. The escape-attempt here must become the allocator's self-test.

## Reproduction
```
systemd-run --user --scope --collect -p AllowedCPUs=100-101 \
  bash -c 'sleep 9 & CH=$!; taskset -pc 102 $CH; grep Cpus_allowed_list /proc/$CH/status'
# -> child stays 100-101 (escape to 102 masked)
```
