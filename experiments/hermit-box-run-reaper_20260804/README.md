# hermit-box-run: a subtree-reaping box for ad-hoc `hermit run`

**Date:** 2026-08-04
**Agent:** hermit-liteinst (opus-4.8)
**Task:** `hermit_run_verify_hangs`
**Wrapper under test:** `scripts/hermit-box-run` (parent repo)

## Question

An ad-hoc `hermit run …` that hits a long-lived / livelock path forms a TWO-process
tree: an OUTER hermit (session leader, pipe-wait, cpu≈0) plus an INNER supervisor in its
OWN process group that burns a core / holds a PID namespace and reparents to `ppid=1`. A
launcher `kill -- -<PGID>` (what an agent recycle or a tool wall-cap does) reaches only
the OUTER and ORPHANS the inner — the reason ad-hoc runs leak cores/namespaces while
cgroup-scoped safe-ci-dag-runner nodes do not.

Can a low-risk wrapper make an ad-hoc command un-leakable — no orphaned inner, no leaked
core, no retained namespace — with NO new engine code and NO external kill?

## Method (Path A — reuse safe-ci-dag-runner verbatim)

`scripts/hermit-box-run` emits a ONE-step DAG (with a CPU-TIME budget) and drives it
through the canonical `agent-utils/bin/safe-ci-dag-runner` (tracked Python engine),
reusing verbatim:

- boxing ON by default + **exit 3** when cgroup-v2 / systemd `--user` scope is unavailable
  (`cli._resolve_cgroup_manager`);
- the per-step **CPU-time** budget kill in `scheduler._monitor` (reads cgroup
  `cpu.stat` `usage_usec`, user+system — immune to wall-clock slowdown under load);
- `teardown.reap()`'s **cgroup.kill-first** teardown, which SIGKILLs the WHOLE subtree
  atomically, including setsid / double-fork escapees a process-group kill misses.

No new engine code; a parent script only (no agent-utils change). The `pin-run`
subcommand was rejected as the base: it boxes cores but runs a plain `subprocess.run`
with NO CPU-budget and NO cgroup reaper. Only the `run --dag` path composes both.

The proof FORCES the pathology deterministically (owner's #359 template — inject a spin,
don't wait for ambient contention), on two specimens, boxing every run ourselves
(`systemd-run --user` transient scope inside the runner; K=1 cpuset). Both directions
bracketed; N stated. Invariant 15: only our own captured PIDs/PGIDs are ever killed, and
the KVM proof attributes our inner via a baseline diff so the pre-existing ambient orphan
(hermit-ptw's `2009586`) is never touched.

### Specimen 1 — synthetic (cores dimension, deterministic, cheap)

`leaky_spinner.sh`: an OUTER that `setsid`-spawns an INNER cpu-spinner in its own
session/pgid, then waits — the exact outer/inner shape. `cpu_timeout=3`, `--cores 1`.

### Specimen 2 — real KVM livelock (namespace dimension)

`hermit run --backend kvm --base-env=minimal --max-timeslice=disabled --tmp=/tmp --
/bin/sh -c 'exit 23'` (DEBUG binary) — a known ad-hoc startup busy-spin that never
completes and unshares a distinct PID namespace. `--cpu-budget 6`, `--cores 1`. AFTER is
scope-keyed (enumerate the run's own systemd scope cgroup.procs) so nothing is confounded
with ambient orphans.

## Results

| specimen | dimension | BEFORE (unboxed, launcher PGID-kill) | AFTER (through wrapper) |
|---|---|---|---|
| synthetic spinner | cores leaked | **3/3** inner orphaned to ppid=1, state R, still burning | **0/3** — CPU-budget kill + cgroup.kill reaped whole subtree, no external kill |
| KVM livelock | inner + PID ns | **2/2** inner orphaned to ppid=1, state R, holds distinct pid ns, survives PGID-kill | **0/2** cores leaked; inner reaped 2/2, scope cgroup removed 2/2 → ns released 2/2 |

Every AFTER run exited rc=1 with `CPU-TIMEOUT >Ns cpu` — the budget kill fired (a reaped
runaway is exactly the leak this prevents) — and needed NO external kill.

### Namespace-release methodology note (important)

PID-namespace **inode numbers are recycled** by the kernel once the ns is freed, so
counting `/proc/*/ns/pid` holders of a specific inode AFTER a reap is racy (one KVM AFTER
trial transiently showed 2 holders of a freed inode; seconds later 0 held it — a
different, newer namespace had briefly reused the inode). The **definitive** release
signal is: the inner (the ns's only member) is gone AND its systemd scope cgroup is
removed — a PID namespace is destroyed exactly when its last member process dies. Both
held 2/2.

## Interpretation

The gap identified for this task is closed with the lowest-risk change: a parent script
that composes the already-existing CPU-budget kill + subtree-cgroup reaper for an
arbitrary ad-hoc command. Boxed, an ad-hoc `hermit run` cannot orphan its inner
supervisor or leak a core/namespace; unboxed, it does so deterministically. This targets
the orphan-leak half the owner flagged as mattering most; it does NOT alter hermit/reverie
code (the ptrace `--verify` "hang" was already refuted as slow-drain, not a deadlock —
`ai_docs/verify-hang-is-slow-drain-not-deadlock_20260803.md`), so no core-review trigger.

## Reproduce

```bash
cd /home/newton/work/dev-hermit
# cores dimension (synthetic):
bash scratch/verify-hang-repro/wrapper-proof/before_after.sh 3 3
# namespace dimension (real KVM), both sides:
bash scratch/verify-hang-repro/wrapper-proof/kvm_before.sh 2 5
bash scratch/verify-hang-repro/wrapper-proof/kvm_after.sh  2 6
# the wrapper itself:
scripts/hermit-box-run --cpu-budget 3 --cores 1 -- bash scratch/verify-hang-repro/wrapper-proof/leaky_spinner.sh
```

(The scratch harnesses under `scratch/verify-hang-repro/wrapper-proof/` are gitignored;
copies of the two forcing specimens are kept beside this README for reproducibility.)
