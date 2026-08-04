# Box REQ-2: is a ptraced guest a member of the step cgroup? (empirical)

**Date:** 2026-08-03
**Host:** dev box, 3pai_sandbox scope `run-p552682-i277842426.scope`
**For:** `dag-runner-core-allocator-with-irq-awareness`; hermit-ci is first customer.

## Question

hermit-ci's box REQ-2 kills a runaway step on **CPU-TIME** (cgroup `cpu.stat`
`usage_usec`), over the **whole subtree including the ptraced guest** — because a
wall timeout cannot distinguish "slow because busy" from "slow because genuinely
expensive," and the verify "hang" is a **slow drain, not a deadlock**
(`ai_docs/verify-hang-is-slow-drain-not-deadlock_20260803.md`).

The enforcement at `py/safe_ci_dag_runner/scheduler.py:442-446` reads
`self.cgroups.cpu_stats(step.tag)['usage_usec']` (cgroup-aggregate) then
`cgroup.kill` over the subtree. This is only correct **if the ptraced guest is a
member of the step's cgroup** — otherwise `usage_usec` under-counts (the guest is
where the CPU goes) and the kill misses the exact thing the box exists to bound.
Coordinator instruction: **prove membership empirically, do not infer from code.**

## Method

Exercise the box's actual assignment mechanism (`cgroup.py:1031`:
`echo $$ > <step>/cgroup.procs` then fork). A leader migrates itself into a step
child cgroup, then `exec strace -f -- <cpu burner>` so `strace` becomes a real
**ptracer** and the burner is its **ptraced tracee** (the guest analog). While it
runs, read every member's `/proc/<pid>/cgroup`, `TracerPid`, and the step
`cpu.stat usage_usec`.

Controllers delegated to this sandbox scope: `io memory pids` (NO `cpu`/`cpuset`
delegated here — `cpu.stat` accounting is nonetheless present in every cgroup).

## Result — MEMBERSHIP CONFIRMED

Live members of the step cgroup `.../run-p552682-i277842426.scope/membership-probe`:

| pid     | comm   | cgroup                | State | TracerPid   |
|---------|--------|-----------------------|-------|-------------|
| 3409707 | strace | .../membership-probe  | S     | 0           |
| 3409786 | bash   | .../membership-probe  | R     | **3409707** |

- The ptraced tracee (bash, pid 3409786) has `TracerPid: 3409707` = the strace
  pid — definitive ptrace relationship — AND its cgroup is the **same**
  `membership-probe` step cgroup as the tracer.
- `cpu.stat usage_usec` accumulated **4,710,060 µs (~4.71 CPU-s)** in the step
  cgroup *while the ptraced tracee burned CPU*; a first 3 s run reached
  1,098,753 µs. The guest's CPU flows into the aggregate the kill reads.

## Interpretation

REQ-2's load-bearing invariant holds on this host: a ptraced guest launched via
the box's `echo $$ > cgroup.procs` pattern is a cgroup member (membership inherits
at fork; ptrace does not change it), so cgroup-aggregate `usage_usec` includes the
guest and `cgroup.kill` reaches it. The CPU-time budget kill is sound for the
verify-hang repro. When the allocator's PIN is composed onto a step, keep the
pinned guest+supervisor in the SAME step cgroup so this property is preserved.

## Reproduction

`ignored/run.sh` — the two probe invocations (raw logs in `ignored/`, gitignored).
Not committed to parent `main` without coordinator authorization; the full result
is also carried in the task's tg notes.
